"""Strategy families (week2-strategy-specs.md, v1): F7, F1 and the controls N1, N2.

Signals are evaluated on a PastView, which only exposes events with slot <= the decision slot t
(whole slots, whole transactions). Candidates come from cheap SQL on single events; every filter
that looks at other events runs on the PastView, so lookahead is impossible by construction.
"""

import bisect
import hashlib
import sys
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from .costs import LAMPORTS

SOL = LAMPORTS
MAYHEM_AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
SOL_QUOTES = ("11111111111111111111111111111111", "So11111111111111111111111111111111111111112")
HOUR_SLOTS = 13_468  # 3600 s / 0.2673 s


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
    depth: int  # pre-trade depth at the trigger (curve real SOL or pool Q_eff), lamports
    params: tuple = ()
    ref: tuple = None  # N2 only: (mint, t) of the family trigger this control is matched to


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


def universe_sql(cfg, dev_start, dev_end):
    burn = cfg["windows"]["development"]["burn_in_slots"]
    quotes = ",".join(f"'{q}'" for q in SOL_QUOTES)
    return (f"NOT m.mayhem AND m.quote_mint IN ({quotes}) AND m.create_slot >= {dev_start + burn} "
            f"AND m.create_slot < {dev_end}")


def decision_bounds(cfg, dev_start, dev_end, guard=20_000):
    burn = cfg["windows"]["development"]["burn_in_slots"]
    return dev_start + burn, dev_end - guard


# ----------------------------------------------------------------------------- F7


def f7_signals_sql(con, cfg, dev_start, dev_end, only_mints=None, buckets=16):
    """See _f7_bucket; evaluated per hash bucket of the mint to bound memory."""
    out = []
    for b in range(buckets):
        out += _f7_bucket(con, cfg, dev_start, dev_end, only_mints, f"hash(m.mint) % {buckets} = {b}")
    return out


def _f7_bucket(con, cfg, dev_start, dev_end, only_mints, bucket_sql):
    """F7 triggers that pass every filter, computed with backward-looking SQL windows only.

    Equivalent to f7_signal() on a PastView (tests/test_lookahead.py checks this on a sample):
    net inflow over [t-50, t) > 0; no creator sell in [t-200, t]; no other trigger-sized sell
    in [t-20, t). Curve inflow = change of virtual quote; pool inflow = buyers' quote paid minus
    sellers' quote received. Returns (venue, mint, t, depth)."""
    lo, hi = decision_bounds(cfg, dev_start, dev_end)
    u = universe_sql(cfg, dev_start, dev_end) + f" AND {bucket_sql}"
    if only_mints is not None:
        con.execute("CREATE OR REPLACE TEMP TABLE only_mints (mint VARCHAR)")
        con.executemany("INSERT INTO only_mints VALUES (?)", [(m,) for m in only_mints])
        u += " AND m.mint IN (SELECT mint FROM only_mints)"
    curve = con.execute(f"""
        WITH ev AS (
          SELECT c.mint, c.slot, c.tx_index, c.outer_ix, c.inner_ix, c.trader, m.creator, c.rq + c.q AS pre_rq,
                 CASE WHEN c.is_buy THEN c.q ELSE -c.q END AS dq,
                 (NOT c.is_buy AND c.q >= GREATEST({3 * SOL}, 0.04 * (c.vq + c.q))) AS trig_size,
                 (NOT c.is_buy AND c.trader = m.creator) AS creator_sell
          FROM curve c JOIN mints m USING (mint) WHERE {u}),
        w AS (
          SELECT *,
            SUM(dq) OVER (PARTITION BY mint ORDER BY slot RANGE BETWEEN 50 PRECEDING AND 1 PRECEDING) AS inflow,
            SUM(creator_sell::INT) OVER (PARTITION BY mint ORDER BY slot RANGE BETWEEN 200 PRECEDING AND CURRENT ROW) AS csell,
            SUM(trig_size::INT) OVER (PARTITION BY mint ORDER BY slot RANGE BETWEEN 20 PRECEDING AND 1 PRECEDING) AS trig_before
          FROM ev)
        SELECT 'curve', mint, slot, pre_rq FROM w
        WHERE trig_size AND trader <> creator AND trader <> '{MAYHEM_AGENT}'
          AND pre_rq BETWEEN {20 * SOL} AND {75 * SOL} AND slot BETWEEN {lo} AND {hi}
          AND COALESCE(inflow, 0) > 0 AND COALESCE(csell, 0) = 0 AND COALESCE(trig_before, 0) = 0
        QUALIFY row_number() OVER (PARTITION BY mint, slot ORDER BY tx_index, outer_ix, inner_ix) = 1
    """).fetchall()
    pool = con.execute(f"""
        WITH ev AS (
          SELECT p.mint, e.slot, e.tx_index, e.outer_ix, e.inner_ix, e.trader, m.creator, p.coin_creator, e.e,
                 CASE WHEN e.kind = 'buy' THEN e.quote_net ELSE -e.quote_net END AS dq,
                 (e.kind = 'sell' AND e.quote_net >= GREATEST({3 * SOL}, 0.04 * e.e)) AS trig_size,
                 (e.kind = 'sell' AND e.trader = m.creator) AS creator_sell
          FROM pool e JOIN pools p USING (pool) JOIN mints m ON m.mint = p.mint
          WHERE {u} AND e.kind IN ('buy', 'sell')),
        w AS (
          SELECT *,
            SUM(dq) OVER (PARTITION BY mint ORDER BY slot RANGE BETWEEN 50 PRECEDING AND 1 PRECEDING) AS inflow,
            SUM(creator_sell::INT) OVER (PARTITION BY mint ORDER BY slot RANGE BETWEEN 200 PRECEDING AND CURRENT ROW) AS csell,
            SUM(trig_size::INT) OVER (PARTITION BY mint ORDER BY slot RANGE BETWEEN 20 PRECEDING AND 1 PRECEDING) AS trig_before
          FROM ev)
        SELECT 'pool', mint, slot, e FROM w
        WHERE trig_size AND trader <> creator AND trader <> COALESCE(coin_creator, '')
          AND e >= {150 * SOL} AND slot BETWEEN {lo} AND {hi}
          AND COALESCE(inflow, 0) > 0 AND COALESCE(csell, 0) = 0 AND COALESCE(trig_before, 0) = 0
        QUALIFY row_number() OVER (PARTITION BY mint, slot ORDER BY tx_index, outer_ix, inner_ix) = 1
    """).fetchall()
    return curve + pool


def _inflow_curve(legs):
    return sum(l.post.vq - l.pre.vq for l in legs)


def _inflow_pool(legs):
    return sum(l.quote_net if l.kind == "buy" else -l.quote_net for l in legs)


def f7_signal(view, venue, depth, creator, coin_creator=None):
    """Reference implementation of the F7 filters on a PastView (see f7_signals_sql)."""
    t = view.t
    if venue == "curve":
        trig_size = lambda l: (not l.is_buy) and l.q >= max(3 * SOL, 0.04 * l.pre.vq)  # noqa: E731
        if _inflow_curve(view.curve_window(t - 50, t - 1)) <= 0:
            return False
        if any((not l.is_buy) and l.trader == creator for l in view.curve_window(t - 200, t)):
            return False
        if any(trig_size(l) for l in view.curve_window(t - 20, t - 1)):
            return False
        return True
    trig_size = lambda l: l.kind == "sell" and l.quote_net >= max(3 * SOL, 0.04 * l.pre.e)  # noqa: E731
    if _inflow_pool(view.pool_window(t - 50, t - 1)) <= 0:
        return False
    if any(l.kind == "sell" and l.trader == creator for l in view.pool_window(t - 200, t)):
        return False
    if any(trig_size(l) for l in view.pool_window(t - 20, t - 1)):
        return False
    return True


F7_GRID = [(tp, T) for tp in (0.04, 0.06, 0.10) for T in (150, 600, 2_400)]

# ----------------------------------------------------------------------------- F1


def f1_candidates(con, cfg, dev_start, dev_end, band_sol):
    lo, hi = decision_bounds(cfg, dev_start, dev_end)
    u = universe_sql(cfg, dev_start, dev_end)
    b = band_sol * SOL
    return con.execute(f"""
        SELECT c.mint, c.slot
        FROM curve c JOIN mints m USING (mint)
        WHERE {u} AND c.is_buy AND c.rq - c.q < {b} AND c.rq >= {b} AND c.slot > m.create_slot
          AND c.slot BETWEEN {lo} AND {hi}
        QUALIFY row_number() OVER (PARTITION BY c.mint ORDER BY c.slot, c.tx_index, c.outer_ix, c.inner_ix) = 1
    """).fetchall()


def f1_signal(view, creator, size):
    t = view.t
    legs = view.curve
    if _inflow_curve(view.curve_window(t - 100, t)) < 5 * SOL:
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


def n1_candidates(con, cfg, dev_start, dev_end):
    lo, hi = decision_bounds(cfg, dev_start, dev_end)
    u = universe_sql(cfg, dev_start, dev_end)
    return con.execute(f"SELECT m.mint, m.create_slot FROM mints m WHERE {u} AND m.create_slot BETWEEN {lo} AND {hi}").fetchall()


def _seed(*parts):
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "little")


def _mix(x):
    """splitmix64 finalizer on a uint64 array: a deterministic pseudo-random key."""
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def n2_matches(con, triggers, k=5):
    """k random matches per trigger (spec N2), one query per hour and venue.

    triggers: iterable of (family, mint, t, venue, depth). Returns {trigger: [(mint, slot, depth)]}:
    k different SOL-quoted, non-mayhem tokens with an event in the same hour at a similar depth
    (curve: real SOL within +-5 SOL; pool: Q_eff within +-20%), each at one of those events drawn
    at random. The draws are deterministic per trigger."""
    quotes = ",".join(repr(q) for q in SOL_QUOTES)
    by_hour = defaultdict(list)
    for tr in triggers:
        by_hour[(tr[3], tr[2] - tr[2] % HOUR_SLOTS)].append(tr)
    out = {}
    with np.errstate(over="ignore"):
        for (venue, h0), trs in sorted(by_hour.items()):
            if venue == "curve":
                sql = f"""SELECT c.mint, c.slot, c.tx_index, c.rq::DOUBLE AS depth FROM curve c JOIN mints m USING (mint)
                          WHERE c.slot >= ? AND c.slot < ? AND NOT m.mayhem AND m.quote_mint IN ({quotes})"""
            else:
                sql = f"""SELECT p.mint, e.slot, e.tx_index, e.e::DOUBLE AS depth
                          FROM pool e JOIN pools p USING (pool) JOIN mints m ON m.mint = p.mint
                          WHERE e.slot >= ? AND e.slot < ? AND e.kind IN ('buy', 'sell')
                            AND NOT m.mayhem AND m.quote_mint IN ({quotes})"""
            ev = con.execute(sql, [h0, h0 + HOUR_SLOTS]).fetchnumpy()
            if not len(ev["slot"]):
                out.update({tr: [] for tr in trs})
                continue
            names, mid = np.unique(np.asarray(ev["mint"], dtype=object), return_inverse=True)
            mkey = np.array([_seed(m) for m in names], dtype=np.uint64)
            slots = np.asarray(ev["slot"], dtype=np.int64)
            pos = slots.astype(np.uint64) * np.uint64(1_000_003) + np.asarray(ev["tx_index"], dtype=np.uint64)
            depth = np.asarray(ev["depth"], dtype=np.float64)
            for tr in trs:
                fam, mint, t, _, dep = tr
                lo, hi = (dep - 5 * SOL, dep + 5 * SOL) if venue == "curve" else (dep * 0.8, dep * 1.2)
                own = np.searchsorted(names, mint)
                own = own if own < len(names) and names[own] == mint else -1
                idx = np.nonzero((depth >= lo) & (depth <= hi) & (mid != own))[0]
                if not len(idx):
                    out[tr] = []
                    continue
                seed = np.uint64(_seed(fam, mint, t))
                order = np.lexsort((_mix(pos[idx] ^ seed), mid[idx]))  # per token, a random event first
                m_sorted = mid[idx][order]
                first = np.ones(len(order), dtype=bool)
                first[1:] = m_sorted[1:] != m_sorted[:-1]
                pick = idx[order[first]]
                chosen = pick[np.argsort(_mix(mkey[mid[pick]] ^ seed), kind="stable")[:k]]
                out[tr] = [(sys.intern(names[mid[j]]), int(slots[j]), int(depth[j])) for j in chosen]
    return out
