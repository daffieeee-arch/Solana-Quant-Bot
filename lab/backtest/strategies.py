"""Strategy families (week2-strategy-specs.md, v1): F7, F1 and the controls N1, N2.

Signals are evaluated on a PastView, which only exposes events with slot <= the decision slot t
(whole slots, whole transactions). Candidates come from cheap SQL on single events; every filter
that looks at other events runs on the PastView, so lookahead is impossible by construction.

Time windows are defined in milliseconds (addendum 2026-10-10) and converted to slots per epoch
with the slot time measured in the store (Clock). The v1 windows were set in slots at 267.3 ms,
so they are kept as that many milliseconds and give the same slot counts in the 267 ms regime.
Pool signals work on the SOL side of a pool (token_buy, sol_amount, sol_depth), so normal pools
(token/SOL) and reversed pools (WSOL/token) read the same.
"""

import bisect
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from .costs import LAMPORTS

SOL = LAMPORTS
MAYHEM_AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
SOL_QUOTES = ("11111111111111111111111111111111", "So11111111111111111111111111111111111111112")
REF_MS = 267.3  # slot time the v1 windows were set at
EPOCH_SLOTS = 432_000


def ms(slots):
    """A v1 window given in slots, as milliseconds."""
    return slots * REF_MS


HOUR_MS = 3_600_000
BURN_IN_MS = ms(324_000)  # ~1 day without entries while token state warms up
END_GUARD_MS = ms(20_000)  # no decisions this close to the end of the data
F7_INFLOW_MS, F7_CREATOR_MS, F7_GAP_MS = ms(50), ms(200), ms(20)
F6_FARM_MS = 3_600_000  # a pool is flagged when farmers made > F6_FARM_SHARE of its SOL volume in this window
F6_FARM_SHARE = 0.5


class Clock:
    """Milliseconds to slots per epoch, from the store's measured ms per slot."""

    def __init__(self, ms_per_slot=None):
        self.per_epoch = {int(k): float(v) for k, v in (ms_per_slot or {}).items()}

    @classmethod
    def from_store(cls, con):
        meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
        return cls(meta.get("ms_per_slot"))

    def ms_per_slot(self, slot):
        return self.per_epoch.get(slot // EPOCH_SLOTS, REF_MS)

    def slots(self, duration_ms, at_slot):
        return max(1, round(duration_ms / self.ms_per_slot(at_slot)))

    def regimes(self, lo, hi):
        """[(lo, hi, ms_per_slot)]: runs of epochs whose slot time differs by less than 5 ms."""
        out = []
        for e in range(lo // EPOCH_SLOTS, (hi - 1) // EPOCH_SLOTS + 1):
            a, b, m = max(lo, e * EPOCH_SLOTS), min(hi, (e + 1) * EPOCH_SLOTS), self.ms_per_slot(e * EPOCH_SLOTS)
            if out and abs(out[-1][2] - m) < 5:
                out[-1] = (out[-1][0], b, out[-1][2])
            else:
                out.append((a, b, m))
        return out

    def regime_label(self, slot):
        return f"{round(self.ms_per_slot(slot) / 10) * 10:.0f}ms"


HOUR_SLOTS = round(HOUR_MS / REF_MS)  # default when no clock is given


class PastView:
    """The part of a stream a strategy may see at decision slot t."""

    def __init__(self, stream, t):
        self.stream = stream
        self.t = t
        cs = [l.slot for l in stream.curve]
        ps = [l.slot for l in stream.pool_legs]
        self.curve = stream.curve[: bisect.bisect_right(cs, t)]
        self.pool = stream.pool_legs[: bisect.bisect_right(ps, t)]

    def curve_window(self, lo, hi):
        """Curve legs with lo <= slot <= hi (hi capped at t)."""
        hi = min(hi, self.t)
        return [l for l in self.curve if lo <= l.slot <= hi]

    def pool_window(self, lo, hi):
        hi = min(hi, self.t)
        return [l for l in self.pool if lo <= l.slot <= hi and l.kind in ("buy", "sell")]


@dataclass(frozen=True, slots=True)
class Signal:
    family: str
    variant: str
    mint: str
    t: int
    venue: str  # curve | pool
    depth: int  # pre-trade depth at the trigger (curve real SOL or pool real SOL), lamports
    params: tuple = ()
    ref: tuple = None  # N2 only: (mint or pool, t) of the family trigger this control is matched to
    pool: str = None  # pool signals: the pool (mint is the token mint when known, else the pool)
    orientation: str = None  # pool signals: normal | reversed
    flagged: bool = None  # pool signals: the pool was flagged for volume farming in the hour before t
    trigger: str = None  # F7: the wallet whose sell triggered the signal
    trigger_farmer: bool = None  # F7 pools: that wallet was volume farming in the pool


# ----------------------------------------------------------------------------- exit rules


class ExitRule:
    """An exit rule: called with a mark context, returns a reason or None. `deadline(entry_slot)`
    tells the replay at which slot a time condition fires, so it is evaluated inside quiet gaps
    without trades instead of at the next trade."""

    def deadline(self, entry_slot):
        return None


class TpSlTime(ExitRule):
    def __init__(self, tp, sl, max_slots, scn):
        self.tp, self.sl, self.max_slots, self.scn = tp, sl, max_slots, scn

    def deadline(self, entry_slot):
        return entry_slot + self.max_slots

    def __call__(self, ctx):
        ret = self.scn.exit_net(ctx["mark"]) / self.scn.entry_total(ctx["cost"]) - 1
        if ret >= self.tp:
            return "take_profit"
        if ret <= self.sl:
            return "stop_loss"
        if ctx["slots_held"] >= self.max_slots:
            return "time"
        return None


class UntilSlot(ExitRule):
    """Sell so that the fill lands at target_slot (decision at target_slot - d)."""

    def __init__(self, target_slot, d):
        self.target, self.d = target_slot, d

    def deadline(self, entry_slot):
        return self.target - self.d

    def __call__(self, ctx):
        return "horizon" if ctx["slot"] + self.d >= self.target else None


class F1Exit(ExitRule):
    """Real SOL fell 10 SOL below its level at the decision slot (before our own buy), or time."""

    def __init__(self, decision_rq, max_slots):
        self.ref, self.max_slots = decision_rq, max_slots

    def deadline(self, entry_slot):
        return entry_slot + self.max_slots

    def __call__(self, ctx):
        if "curve" in ctx and ctx["curve"].rq <= self.ref - 10 * SOL:
            return "real_sol_drop"
        if ctx["slots_held"] >= self.max_slots:
            return "time"
        return None


def tp_sl_time(tp, sl, max_slots, scn):
    return TpSlTime(tp, sl, max_slots, scn)


def until_slot(target_slot, d):
    return UntilSlot(target_slot, d)


def f1_exit(decision_rq, max_slots):
    return F1Exit(decision_rq, max_slots)


# ----------------------------------------------------------------------------- universe


def universe_sql(cfg, dev_start, dev_end, clock=None):
    clock = clock or Clock()
    quotes = ",".join(f"'{q}'" for q in SOL_QUOTES)
    return (f"NOT m.mayhem AND m.quote_mint IN ({quotes}) AND m.create_slot >= {dev_start + clock.slots(BURN_IN_MS, dev_start)} "
            f"AND m.create_slot < {dev_end}")


def key_ranges(con, table, col, n):
    """n contiguous [lo, hi] ranges of the sorted distinct keys. The event tables are stored
    sorted by mint / pool, so a range predicate lets DuckDB skip row groups (a hash bucket
    would read the whole table for every bucket)."""
    return con.execute(f"""SELECT min(k), max(k) FROM (SELECT k, ntile({n}) OVER (ORDER BY k) AS b
                           FROM (SELECT DISTINCT {col} AS k FROM {table})) GROUP BY b ORDER BY 1""").fetchall()


def pool_universe_sql():
    """SOL markets of either orientation, mayhem pools excluded (pools of any age)."""
    return "p.quote_class IN ('sol', 'reversed') AND NOT COALESCE(p.mayhem, false)"


def decision_bounds(cfg, dev_start, dev_end, clock=None):
    clock = clock or Clock()
    return dev_start + clock.slots(BURN_IN_MS, dev_start), dev_end - clock.slots(END_GUARD_MS, dev_end - 1)


# ----------------------------------------------------------------------------- F7


def f7_signals_sql(con, cfg, dev_start, dev_end, only_mints=None, buckets=16, clock=None):
    """See _f7_bucket; evaluated per slot-time regime and hash bucket of the mint/pool to bound
    memory. Returns (venue, mint, t, depth, pool, orientation, trigger wallet, trigger farmer,
    pool flagged for farming in the hour before t)."""
    clock = clock or Clock()
    lo, hi = decision_bounds(cfg, dev_start, dev_end, clock)
    out = []
    mint_r, pool_r = key_ranges(con, "mints", "mint", buckets), key_ranges(con, "pools", "pool", buckets)
    for rlo, rhi, m in clock.regimes(lo, hi + 1):
        win = (round(F7_INFLOW_MS / m), round(F7_CREATOR_MS / m), round(F7_GAP_MS / m), round(F6_FARM_MS / m))
        for b in range(max(len(mint_r), len(pool_r))):
            mr = mint_r[b] if b < len(mint_r) else None
            pr = pool_r[b] if b < len(pool_r) else None
            out += _f7_bucket(con, cfg, dev_start, dev_end, only_mints, mr, pr, (rlo, rhi - 1), win, clock)
    return out


def _f7_bucket(con, cfg, dev_start, dev_end, only_mints, mint_range, pool_range, trig_range, win, clock):
    """F7 triggers that pass every filter, computed with backward-looking SQL windows only.

    Equivalent to f7_signal() on a PastView (tests/test_lookahead.py checks this on a sample):
    net SOL inflow over [t-w1, t) > 0; no creator sell in [t-w2, t]; no other trigger-sized sell
    in [t-w3, t). Curve inflow = change of virtual quote; pool inflow = SOL paid by token buyers
    minus SOL received by token sellers."""
    n_in, n_cr, n_gap, n_farm = win
    t_lo, t_hi = trig_range
    u = universe_sql(cfg, dev_start, dev_end, clock) + (
        f" AND m.mint BETWEEN '{mint_range[0]}' AND '{mint_range[1]}'" if mint_range else " AND false")
    pu = pool_universe_sql() + (f" AND p.pool BETWEEN '{pool_range[0]}' AND '{pool_range[1]}'" if pool_range else " AND false")
    crange = f" AND c.mint BETWEEN '{mint_range[0]}' AND '{mint_range[1]}'" if mint_range else ""
    prange = f" AND e.pool BETWEEN '{pool_range[0]}' AND '{pool_range[1]}'" if pool_range else ""
    if only_mints is not None:
        con.execute("CREATE OR REPLACE TEMP TABLE only_mints (mint VARCHAR)")
        con.executemany("INSERT INTO only_mints VALUES (?)", [(m,) for m in only_mints])
        u += " AND m.mint IN (SELECT mint FROM only_mints)"
        pu += " AND COALESCE(p.mint, p.pool) IN (SELECT mint FROM only_mints)"
    span = f"slot BETWEEN {t_lo - n_cr} AND {t_hi}"
    pspan = f"slot BETWEEN {t_lo - max(n_cr, n_farm)} AND {t_hi}"
    curve = con.execute(f"""
        WITH ev AS (
          SELECT c.mint, c.slot, c.tx_index, c.outer_ix, c.inner_ix, c.trader, m.creator, c.rq + c.q AS pre_rq,
                 CASE WHEN c.is_buy THEN c.q ELSE -c.q END AS dq,
                 (NOT c.is_buy AND c.q >= GREATEST({3 * SOL}, 0.04 * (c.vq + c.q))) AS trig_size,
                 (NOT c.is_buy AND c.trader = m.creator) AS creator_sell
          FROM curve c JOIN mints m USING (mint) WHERE {u} AND c.{span}{crange}),
        w AS (
          SELECT *,
            SUM(dq) OVER (PARTITION BY mint ORDER BY slot RANGE BETWEEN {n_in} PRECEDING AND 1 PRECEDING) AS inflow,
            SUM(creator_sell::INT) OVER (PARTITION BY mint ORDER BY slot RANGE BETWEEN {n_cr} PRECEDING AND CURRENT ROW) AS csell,
            SUM(trig_size::INT) OVER (PARTITION BY mint ORDER BY slot RANGE BETWEEN {n_gap} PRECEDING AND 1 PRECEDING) AS trig_before
          FROM ev)
        SELECT 'curve', mint, slot, pre_rq, NULL, NULL, trader, NULL, NULL FROM w
        WHERE trig_size AND trader <> creator AND trader <> '{MAYHEM_AGENT}'
          AND pre_rq BETWEEN {20 * SOL} AND {75 * SOL} AND slot BETWEEN {t_lo} AND {t_hi}
          AND COALESCE(inflow, 0) > 0 AND COALESCE(csell, 0) = 0 AND COALESCE(trig_before, 0) = 0
        QUALIFY row_number() OVER (PARTITION BY mint, slot ORDER BY tx_index, outer_ix, inner_ix) = 1
    """).fetchall()
    pool = con.execute(f"""
        WITH ev AS (
          SELECT e.pool, COALESCE(p.mint, p.pool) AS mint, p.orientation, e.slot, e.tx_index, e.outer_ix, e.inner_ix,
                 e.trader, COALESCE(m.creator, p.coin_creator) AS creator, p.coin_creator, e.sol_depth, e.farmer,
                 e.sol_amount,
                 CASE WHEN e.token_buy THEN e.sol_amount ELSE -e.sol_amount END AS dq,
                 (NOT e.token_buy AND e.sol_amount >= GREATEST({3 * SOL}, 0.04 * e.sol_depth)) AS trig_size,
                 (NOT e.token_buy AND e.trader = COALESCE(m.creator, p.coin_creator)) AS creator_sell
          FROM pool e JOIN pools p USING (pool) LEFT JOIN mints m ON m.mint = p.mint
          WHERE {pu} AND e.kind IN ('buy', 'sell') AND e.{pspan}{prange}),
        w AS (
          SELECT *,
            SUM(dq) OVER (PARTITION BY pool ORDER BY slot RANGE BETWEEN {n_in} PRECEDING AND 1 PRECEDING) AS inflow,
            SUM(creator_sell::INT) OVER (PARTITION BY pool ORDER BY slot RANGE BETWEEN {n_cr} PRECEDING AND CURRENT ROW) AS csell,
            SUM(trig_size::INT) OVER (PARTITION BY pool ORDER BY slot RANGE BETWEEN {n_gap} PRECEDING AND 1 PRECEDING) AS trig_before,
            SUM(CASE WHEN farmer THEN sol_amount ELSE 0 END)
              OVER (PARTITION BY pool ORDER BY slot RANGE BETWEEN {n_farm} PRECEDING AND 1 PRECEDING) AS farm_vol,
            SUM(sol_amount) OVER (PARTITION BY pool ORDER BY slot RANGE BETWEEN {n_farm} PRECEDING AND 1 PRECEDING) AS vol
          FROM ev)
        SELECT 'pool', mint, slot, sol_depth, pool, orientation, trader, farmer,
               COALESCE(farm_vol > {F6_FARM_SHARE} * vol, false) FROM w
        WHERE trig_size AND trader <> COALESCE(creator, '') AND trader <> COALESCE(coin_creator, '')
          AND sol_depth >= {150 * SOL} AND slot BETWEEN {t_lo} AND {t_hi}
          AND COALESCE(inflow, 0) > 0 AND COALESCE(csell, 0) = 0 AND COALESCE(trig_before, 0) = 0
        QUALIFY row_number() OVER (PARTITION BY pool, slot ORDER BY tx_index, outer_ix, inner_ix) = 1
    """).fetchall()
    return curve + pool


def _inflow_curve(legs):
    return sum(l.post.vq - l.pre.vq for l in legs)


def sol_side(leg, orientation):
    """(token_buy, sol_amount, sol_depth) of a pool leg, as the store's SOL-side columns."""
    if orientation == "reversed":
        return leg.kind == "sell", leg.base, leg.pre.b
    return leg.kind == "buy", leg.quote_net, leg.pre.e - leg.pre.virt


def _inflow_pool(legs, orientation="normal"):
    return sum(a if tb else -a for tb, a, _ in (sol_side(l, orientation) for l in legs))


def f7_signal(view, venue, depth, creator, coin_creator=None, clock=None):
    """Reference implementation of the F7 filters on a PastView (see f7_signals_sql)."""
    t, clock = view.t, clock or Clock()
    n_in, n_cr, n_gap = (clock.slots(x, t) for x in (F7_INFLOW_MS, F7_CREATOR_MS, F7_GAP_MS))
    if venue == "curve":
        trig_size = lambda l: (not l.is_buy) and l.q >= max(3 * SOL, 0.04 * l.pre.vq)  # noqa: E731
        if _inflow_curve(view.curve_window(t - n_in, t - 1)) <= 0:
            return False
        if any((not l.is_buy) and l.trader == creator for l in view.curve_window(t - n_cr, t)):
            return False
        if any(trig_size(l) for l in view.curve_window(t - n_gap, t - 1)):
            return False
        return True
    o = view.stream.orientation

    def trig_size(l):
        tb, a, dep = sol_side(l, o)
        return (not tb) and a >= max(3 * SOL, 0.04 * dep)

    if _inflow_pool(view.pool_window(t - n_in, t - 1), o) <= 0:
        return False
    if any((not sol_side(l, o)[0]) and l.trader == creator for l in view.pool_window(t - n_cr, t)):
        return False
    if any(trig_size(l) for l in view.pool_window(t - n_gap, t - 1)):
        return False
    return True


F7_GRID = [(tp, T) for tp in (0.04, 0.06, 0.10) for T in (150, 600, 2_400)]

# ----------------------------------------------------------------------------- F1


def f1_candidates(con, cfg, dev_start, dev_end, band_sol, clock=None):
    lo, hi = decision_bounds(cfg, dev_start, dev_end, clock)
    u = universe_sql(cfg, dev_start, dev_end, clock)
    b = band_sol * SOL
    return con.execute(f"""
        SELECT c.mint, c.slot
        FROM curve c JOIN mints m USING (mint)
        WHERE {u} AND c.is_buy AND c.rq - c.q < {b} AND c.rq >= {b} AND c.slot > m.create_slot
          AND c.slot BETWEEN {lo} AND {hi}
        QUALIFY row_number() OVER (PARTITION BY c.mint ORDER BY c.slot, c.tx_index, c.outer_ix, c.inner_ix) = 1
    """).fetchall()


F1_INFLOW_MS = ms(100)


def f1_signal(view, creator, size, clock=None, flow=True):
    """F1 filters; flow=False drops the inflow rule (control N3)."""
    t = view.t
    legs = view.curve
    if flow and _inflow_curve(view.curve_window(t - (clock or Clock()).slots(F1_INFLOW_MS, t), t)) < 5 * SOL:
        return False
    buyers = {l.trader for l in legs if l.is_buy and l.trader != creator and l.trader != MAYHEM_AGENT}
    if len(buyers) < 30:
        return False
    if any((not l.is_buy) and l.trader == creator for l in legs):
        return False
    held, bought = {}, 0
    for l in legs:
        if l.is_buy:
            bought += l.t
            held[l.trader] = held.get(l.trader, 0) + l.t
        else:
            held[l.trader] = held.get(l.trader, 0) - l.t
    top5 = sum(sorted((v for v in held.values() if v > 0), reverse=True)[:5])
    if bought == 0 or top5 > 0.5 * bought:
        return False
    if legs and size > (85.005 * SOL - legs[-1].post.rq - 1 * SOL):
        return False
    return True


# ----------------------------------------------------------------------------- N1 / N2


def n1_candidates(con, cfg, dev_start, dev_end, clock=None):
    lo, hi = decision_bounds(cfg, dev_start, dev_end, clock)
    u = universe_sql(cfg, dev_start, dev_end, clock)
    return con.execute(f"SELECT m.mint, m.create_slot FROM mints m WHERE {u} AND m.create_slot BETWEEN {lo} AND {hi}").fetchall()


def _seed(*parts):
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "little")


def _mix(x):
    """splitmix64 finalizer on a uint64 array: a deterministic pseudo-random key."""
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def n2_matches(con, triggers, k=5, clock=None, max_age_ms=None):
    """k random matches per trigger (spec N2), one query per hour, venue and pool orientation.

    triggers: iterable of (family, key, t, venue, depth, orientation); key is the mint (curve) or
    the pool (pool). Returns {trigger: [(key, slot, depth)]}: k different SOL-market tokens of the
    same venue (and pool orientation), non-mayhem, with an event in the same hour at a similar real
    SOL depth (curve: within +-5 SOL; pool: within +-20%), each at one of those events drawn at
    random. The draws are deterministic per trigger.

    max_age_ms (N2_M1): pool matches are young pools instead (opened at most this long before the
    event, created in the data), with no depth band."""
    clock = clock or Clock()
    quotes = ",".join(repr(q) for q in SOL_QUOTES)
    by_hour = defaultdict(list)
    for tr in triggers:
        h = clock.slots(HOUR_MS, tr[2])
        by_hour[(tr[3], tr[5], tr[2] - tr[2] % h, h)].append(tr)
    out = {}
    with np.errstate(over="ignore"):
        for (venue, orientation, h0, h), trs in sorted(by_hour.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])):
            if venue == "curve":
                sql = f"""SELECT c.mint AS k, c.slot, c.tx_index, c.rq::DOUBLE AS depth FROM curve c JOIN mints m USING (mint)
                          WHERE c.slot >= ? AND c.slot < ? AND NOT m.mayhem AND m.quote_mint IN ({quotes})"""
                params = [h0, h0 + h]
            else:
                young = (f" AND p.created_in_period AND e.slot - p.create_slot BETWEEN 0 AND {clock.slots(max_age_ms, h0)}"
                         if max_age_ms else "")
                sql = f"""SELECT e.pool AS k, e.slot, e.tx_index, e.sol_depth::DOUBLE AS depth
                          FROM pool e JOIN pools p USING (pool)
                          WHERE e.slot >= ? AND e.slot < ? AND e.kind IN ('buy', 'sell') AND {pool_universe_sql()}
                            AND p.orientation = ?{young}"""
                params = [h0, h0 + h, orientation]
            ev = con.execute(sql, params).fetchnumpy()
            if not len(ev["slot"]):
                out.update({tr: [] for tr in trs})
                continue
            names, mid = np.unique(np.asarray(ev["k"], dtype=object), return_inverse=True)
            mkey = np.array([_seed(m) for m in names], dtype=np.uint64)
            slots = np.asarray(ev["slot"], dtype=np.int64)
            pos = slots.astype(np.uint64) * np.uint64(1_000_003) + np.asarray(ev["tx_index"], dtype=np.uint64)
            depth = np.asarray(ev["depth"], dtype=np.float64)
            for tr in trs:
                fam, key, t, _, dep, _ = tr
                lo, hi = ((dep - 5 * SOL, dep + 5 * SOL) if venue == "curve" else
                          (-np.inf, np.inf) if max_age_ms else (dep * 0.8, dep * 1.2))
                own = np.searchsorted(names, key)
                own = own if own < len(names) and names[own] == key else -1
                idx = np.nonzero((depth >= lo) & (depth <= hi) & (mid != own))[0]
                if not len(idx):
                    out[tr] = []
                    continue
                seed = np.uint64(_seed(fam, key, t))
                order = np.lexsort((_mix(pos[idx] ^ seed), mid[idx]))  # per token, a random event first
                m_sorted = mid[idx][order]
                first = np.ones(len(order), dtype=bool)
                first[1:] = m_sorted[1:] != m_sorted[:-1]
                pick = idx[order[first]]
                chosen = pick[np.argsort(_mix(mkey[mid[pick]] ^ seed), kind="stable")[:k]]
                out[tr] = [(sys.intern(names[mid[j]]), int(slots[j]), int(depth[j])) for j in chosen]
    return out


# ----------------------------------------------------------------------------- F6 (families v2)
# Trend in deep pools (week2-strategy-specs.md F6). Windows in ms (the spec's slot counts at
# 267 ms are 1 h, 30 min, 5 min and 4 h). Flow and buyer counts use organic trades only: trades
# whose wallet was volume farming in the pool are excluded (store.py `farmer`).

F6_HIGH_MS, F6_DEPTH_MS, F6_FLOW_MS, F6_MAX_HOLD_MS = 3_600_000, 1_800_000, 300_000, 14_400_000
F6_GRID = [0.05, 0.08, 0.12]  # trailing stop from the peak mark
# Deviation (speed): only the first qualifying trigger of a pool per UTC day is traded. On a fixed
# sample of pool-days every qualifying trigger is counted, to report what the cap leaves out.
F6_SAMPLE_EVERY = 20
F6_DAY_COUNTS = []


def f6_min_depth(size_sol):
    """Q_min(S): the pool's real SOL depth must stay at least this high over the depth window."""
    if size_sol <= 5:
        return 150 * SOL
    return 254 * SOL if size_sol <= 10 else 603 * SOL


def f6_signals_sql(con, cfg, dev_start, dev_end, buckets=16, clock=None, only_pools=None):
    """F6 triggers with backward-looking windows only. Returns (mint, pool, orientation, t,
    depth, min_depth, flagged): depth = real SOL at t, min_depth = lowest real SOL depth over
    [t - 30 min, t] (the size filter is applied per size), flagged = farming share > 0.5 in the
    hour before t."""
    clock = clock or Clock()
    lo, hi = decision_bounds(cfg, dev_start, dev_end, clock)
    out = []
    F6_DAY_COUNTS.clear()
    pool_r = key_ranges(con, "pools", "pool", buckets)
    for rlo, rhi, m in clock.regimes(lo, hi + 1):
        n_high, n_depth, n_flow, n_farm = (round(x / m) for x in (F6_HIGH_MS, F6_DEPTH_MS, F6_FLOW_MS, F6_FARM_MS))
        con.execute(f"""CREATE OR REPLACE TEMP TABLE f6_deep AS SELECT DISTINCT pool FROM pool
                        WHERE sol_depth >= {150 * SOL} AND slot BETWEEN {rlo} AND {rhi - 1}""")
        for plo, phi in pool_r:
            rng = f"BETWEEN '{plo}' AND '{phi}'"
            pu = pool_universe_sql() + f" AND p.pool {rng}"
            if only_pools is not None:
                con.execute("CREATE OR REPLACE TEMP TABLE only_pools (pool VARCHAR)")
                con.executemany("INSERT INTO only_pools VALUES (?)", [(x,) for x in only_pools])
                pu += " AND p.pool IN (SELECT pool FROM only_pools)"
            # Phase 1: cheap windows (price high, depth, organic inflow, farming share) per pool.
            con.execute(f"""
              CREATE OR REPLACE TEMP TABLE f6_cand AS
              WITH ev AS (
                SELECT e.pool, COALESCE(p.mint, p.pool) AS mint, p.orientation, e.slot, e.tx_index, e.outer_ix, e.inner_ix,
                       e.token_buy, e.farmer, e.sol_amount, e.sol_depth,
                       CASE WHEN p.orientation = 'normal'
                            THEN (e.e + e.e_delta)::DOUBLE / (CASE WHEN e.token_buy THEN e.b - e.base ELSE e.b + e.base END)
                            ELSE (CASE WHEN e.token_buy THEN e.b + e.base ELSE e.b - e.base END)::DOUBLE / (e.e + e.e_delta)
                       END AS price
                FROM pool e JOIN pools p USING (pool)
                WHERE {pu} AND e.kind IN ('buy', 'sell') AND e.slot BETWEEN {rlo - n_high} AND {rhi - 1}
                  AND e.pool {rng} AND e.pool IN (SELECT pool FROM f6_deep)),
              w AS (
                SELECT *,
                  MAX(price) OVER (PARTITION BY pool ORDER BY slot RANGE BETWEEN {n_high} PRECEDING AND 1 PRECEDING) AS high,
                  MIN(sol_depth) OVER (PARTITION BY pool ORDER BY slot RANGE BETWEEN {n_depth} PRECEDING AND CURRENT ROW) AS min_depth,
                  SUM(CASE WHEN farmer THEN 0 WHEN token_buy THEN sol_amount ELSE -sol_amount END)
                    OVER (PARTITION BY pool ORDER BY slot RANGE BETWEEN {n_flow} PRECEDING AND CURRENT ROW) AS inflow,
                  SUM(CASE WHEN farmer THEN sol_amount ELSE 0 END)
                    OVER (PARTITION BY pool ORDER BY slot RANGE BETWEEN {n_farm} PRECEDING AND 1 PRECEDING) AS farm_vol,
                  SUM(sol_amount) OVER (PARTITION BY pool ORDER BY slot RANGE BETWEEN {n_farm} PRECEDING AND 1 PRECEDING) AS vol
                FROM ev),
              last AS (SELECT * FROM w QUALIFY row_number() OVER (PARTITION BY pool, slot ORDER BY tx_index DESC, outer_ix DESC,
                                                                                  inner_ix DESC) = 1)
              SELECT mint, pool, orientation, slot, sol_depth, min_depth, COALESCE(farm_vol > {F6_FARM_SHARE} * vol, false) AS flagged
              FROM last
              WHERE slot BETWEEN {max(lo, rlo)} AND {min(hi, rhi - 1)} AND price > high
                AND min_depth >= {150 * SOL} AND inflow >= 0.02 * sol_depth""")
            # Phase 2: distinct organic buyers over [t - 5 min, t], only for the candidates, per pool.
            # The unit is the token-day (spec): the first trigger of a pool per UTC day.
            cands = defaultdict(list)
            # (the UTC day is looked up afterwards: joining blocks inside the window query is very slow)
            for row in con.execute("""SELECT c.mint, c.pool, c.orientation, c.slot, c.sol_depth, c.min_depth, c.flagged,
                                             epoch_ms(b.block_time * 1000)::DATE
                                      FROM f6_cand c JOIN blocks b ON b.slot = c.slot ORDER BY c.pool, c.slot""").fetchall():
                cands[row[1]].append(row)
            for pool_id, rows in cands.items():
                ts = [r[3] for r in rows]
                ev = con.execute(f"""SELECT slot, trader FROM pool WHERE pool = ? AND kind IN ('buy', 'sell') AND token_buy
                                       AND NOT farmer AND slot BETWEEN ? AND ? ORDER BY slot""",
                                 [pool_id, min(ts) - n_flow, max(ts)]).fetchall()
                slots, done, counts = [x[0] for x in ev], set(), defaultdict(int)
                for r in rows:
                    sampled = _seed("f6-day", pool_id, r[7]) % F6_SAMPLE_EVERY == 0  # full count on a fixed sample
                    if r[7] in done and not sampled:
                        continue
                    i, j = bisect.bisect_left(slots, r[3] - n_flow), bisect.bisect_right(slots, r[3])
                    if len({ev[k][1] for k in range(i, j)}) >= 10:
                        if r[7] not in done:
                            out.append(r[:7])
                            done.add(r[7])
                        if sampled:
                            counts[r[7]] += 1
                F6_DAY_COUNTS.extend(counts.values())
    return out


class FlowIndex:
    """Organic net SOL flow of a pool stream over slot windows (prefix sums over the legs)."""

    def __init__(self, stream):
        self.slots, self.cum = [], [0]
        for l in stream.pool_legs:
            if l.kind not in ("buy", "sell"):
                continue
            tb, a, _ = sol_side(l, stream.orientation)
            self.slots.append(l.slot)
            self.cum.append(self.cum[-1] + (0 if l.farmer else (a if tb else -a)))

    def flow(self, lo, hi):
        """Net organic SOL inflow over lo <= slot <= hi."""
        i, j = bisect.bisect_left(self.slots, lo), bisect.bisect_right(self.slots, hi)
        return self.cum[j] - self.cum[i]


class F6Exit(ExitRule):
    """Trailing stop from the peak mark, organic outflow over the last 5 min, or time."""

    def __init__(self, trail, flows, n_flow, max_slots, orientation):
        self.trail, self.flows, self.n_flow, self.max_slots = trail, flows, n_flow, max_slots
        self.orientation, self.peak = orientation, 0

    def deadline(self, entry_slot):
        return entry_slot + self.max_slots

    def __call__(self, ctx):
        self.peak = max(self.peak, ctx["mark"])
        if ctx["mark"] <= self.peak * (1 - self.trail):
            return "trailing_stop"
        pool = ctx.get("pool")
        if pool is not None:
            depth = pool.b if self.orientation == "reversed" else pool.real
            if self.flows.flow(ctx["slot"] - self.n_flow, ctx["slot"]) < -0.01 * depth:
                return "outflow"
        if ctx["slots_held"] >= self.max_slots:
            return "time"
        return None


# ----------------------------------------------------------------------------- N3 / N4 controls

N3_HOLD_MS = ms(324_000)  # mark to market on the curve after ~1 day if the token has not graduated
N4_CRANK_GAP = 44  # slots between boost cranks (spec)
N4_MAX_HOLD = 400  # slots; fallback when no further crank comes


def n4_signals_sql(con, cfg, dev_start, dev_end, sample, clock=None):
    """N4 boost-crank scalp: decide at last crank + 42 so the buy lands just before the expected
    next crank (d = 1: +43, d = 2: +44); sell right after the next actual crank (N4Exit).
    A deterministic sample of cranks in SOL-market pools. Returns (mint, pool, orientation, t, depth)."""
    lo, hi = decision_bounds(cfg, dev_start, dev_end, clock)
    return con.execute(f"""
        WITH c AS (
          SELECT e.pool, COALESCE(p.mint, p.pool) AS mint, p.orientation, e.slot, e.vault AS depth
          FROM pool e JOIN pools p USING (pool)
          WHERE e.kind = 'boost' AND {pool_universe_sql()} AND e.slot BETWEEN {lo} AND {hi}
          QUALIFY row_number() OVER (PARTITION BY e.pool, e.slot ORDER BY e.tx_index) = 1)
        SELECT mint, pool, orientation, slot + {N4_CRANK_GAP - 2} AS t, depth FROM c
        ORDER BY hash(pool, slot) LIMIT {int(sample)}""").fetchall()


class N4Exit(ExitRule):
    """Sell right after the first boost crank that happens after our entry."""

    def __init__(self, crank_slots, max_slots):
        self.cranks, self.max_slots = crank_slots, max_slots

    def deadline(self, entry_slot):
        return entry_slot + self.max_slots

    def __call__(self, ctx):
        i = bisect.bisect_right(self.cranks, ctx["entry_slot"])
        if i < len(self.cranks) and self.cranks[i] <= ctx["slot"]:
            return "after_crank"
        return "time" if ctx["slots_held"] >= self.max_slots else None


# ----------------------------------------------------------------------------- M1 (owner idea)
# Fast runner after graduation (week2-specs-M1-2026-10-10.md): the pool's price (market cap)
# reaches k x its price at graduation within 6 h of the pool opening, with organic buy flow,
# organic buyers and a small insider share (O7). Normal SOL pools of pump tokens created in the
# data, no mayhem, no graduation in the create slot.

M1_GRID = [(k, x) for k in (2, 3) for x in (0.20, 0.35)]  # (k x graduation price, trailing stop)
M1_AGE_MS, M1_FLOW_MS, M1_BUYERS_MS = 6 * 3_600_000, 300_000, 900_000
M1_MAX_HOLD_MS = 6 * 3_600_000
M1_FLOW_SHARE, M1_MIN_BUYERS, M1_HARD_STOP = 0.03, 20, -0.25
M1_SIZES = (0.25, 0.5, 1.0, 2.0)


def m1_candidates_sql(con, cfg, dev_start, dev_end, clock=None):
    """Phase 1 of M1 (cheap windows): events of young normal SOL pools where the post-trade price
    is >= 2 x the pool's opening price and organic net buy flow over the last 5 min >= 3% of the
    real SOL depth. Returns (mint, pool, t, depth, ratio, create_slot) sorted by pool and slot."""
    clock = clock or Clock()
    lo, hi = decision_bounds(cfg, dev_start, dev_end, clock)
    out = []
    for rlo, rhi, m in clock.regimes(lo, hi + 1):
        n_age, n_flow = round(M1_AGE_MS / m), round(M1_FLOW_MS / m)
        out += con.execute(f"""
          WITH young AS (
            SELECT p.pool, p.mint, p.create_slot, p.q0::DOUBLE / p.b0 AS open_price
            FROM pools p JOIN mints m ON m.mint = p.mint
            WHERE p.quote_class = 'sol' AND NOT COALESCE(p.mayhem, false) AND p.created_in_period AND p.b0 > 0
              AND NOT m.mayhem AND COALESCE(m.complete_slot, -1) <> m.create_slot
              AND p.create_slot BETWEEN {rlo - n_age} AND {rhi - 1}),
          ev AS (
            SELECT e.pool, y.mint, y.create_slot, y.open_price, e.slot, e.tx_index, e.outer_ix, e.inner_ix, e.sol_depth,
                   (e.e + e.e_delta)::DOUBLE / (CASE WHEN e.token_buy THEN e.b - e.base ELSE e.b + e.base END) AS price,
                   CASE WHEN e.farmer THEN 0 WHEN e.token_buy THEN e.sol_amount ELSE -e.sol_amount END AS dq
            FROM pool e JOIN young y USING (pool)
            WHERE e.kind IN ('buy', 'sell') AND e.slot BETWEEN y.create_slot AND y.create_slot + {n_age}),
          w AS (SELECT *, SUM(dq) OVER (PARTITION BY pool ORDER BY slot RANGE BETWEEN {n_flow} PRECEDING AND CURRENT ROW) AS inflow
                FROM ev)
          SELECT mint, pool, slot, sol_depth, price / open_price AS ratio, create_slot FROM w
          WHERE slot BETWEEN {max(lo, rlo)} AND {min(hi, rhi - 1)} AND price >= 2 * open_price
            AND inflow >= {M1_FLOW_SHARE} * sol_depth
          QUALIFY row_number() OVER (PARTITION BY pool, slot ORDER BY tx_index DESC, outer_ix DESC, inner_ix DESC) = 1
          ORDER BY pool, slot""").fetchall()
    return out


class M1Exit(ExitRule):
    """Trailing stop from the peak mark, hard stop at -25% from the entry cost, or time."""

    def __init__(self, trail, max_slots):
        self.trail, self.max_slots, self.peak = trail, max_slots, 0

    def deadline(self, entry_slot):
        return entry_slot + self.max_slots

    def __call__(self, ctx):
        self.peak = max(self.peak, ctx["mark"])
        if ctx["mark"] <= ctx["cost"] * (1 + M1_HARD_STOP):
            return "stop_loss"
        if ctx["mark"] <= self.peak * (1 - self.trail):
            return "trailing_stop"
        if ctx["slots_held"] >= self.max_slots:
            return "time"
        return None
