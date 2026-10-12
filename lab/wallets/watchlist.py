"""Dynamic watchlist for live paper trading (owner request via the coordinator, 2026-10-12 night).

Triton delivers full transactions only for pools and wallets we can actually trade or follow
(triton-edge-2026-10-11.md §6); the size of that list drives the cost per day. This module holds
the point-in-time rules per strategy, one state machine that both the recorder and the batch
measurement use, and the measurement over the development period.

Rules (everything decided from data strictly before the minute the list is valid for):
  F7-reversed  reversed PumpSwap pool, not mayhem. Enters when the last seen real SOL depth is
               >= 120 SOL and the pool is organic: farmers made <= 50 % of its SOL volume over the
               trailing 60 min (addendum §5; judged only when that volume is >= 1 SOL, otherwise
               the previous verdict stands). Leaves when the depth drops below 100 SOL (hysteresis),
               when the trailing hour turns farming, or after 6 h without a trade. The F7 trigger
               itself stays at 150 SOL: entering at the trigger threshold missed 26 % of the organic
               triggers (the pool fills from ~85 to 150 SOL and the trigger sell is the first trade
               that sees 150); 120 / 100 covers 95.6 % for +1.9 GB/day (coordinator decision
               2026-10-12, lab/reports/2026-10-12-watchlist-meting.md).
  M1 / M1+     normal SOL-quote PumpSwap pool, not mayhem, graduation outside the create slot
               (dev bundles never trigger M1). Enters when the pool is created (the new-pool
               account stream), leaves at 6 h of age (M1 universe) or after 60 min without a trade;
               a trade (or vault update) inside the 6 h brings it back.
  wallets      followed wallets: a fixed list for now (the step-1 scores fill it later).
A pool with an open paper position must stay on the list until the position is closed; that
is the paper engine's job (it knows its positions), so it is not modelled here.
Pruning (Lab Backtest, #165 gate point, 2026-10-13): over a paper run of weeks every pool ever seen
would stay in `pools` / `state` and `step()` would walk all of them every minute. A pool that has
been quiet for `prune_idle_minutes` (24 h since its last trade; a pool that never traded counts
from its creation minute, or from the minute it was first seen when the creation minute is unknown)
leaves both dicts; its PoolInfo and its last organic verdict go to a compact `archive`. A trade on
an archived pool brings it back with the verdict intact, so the F7 rule sees the same `organic` it
would have seen without pruning (the trailing window is empty, the previous verdict stands until
the hour holds >= 1 SOL, as before): members per minute and verdicts are identical to the unpruned
machine (differential test). The archive never expires by default (`archive_minutes` = 0: an entry
is one PoolInfo and a bool); an explicit expiry makes a returning pool unknown until add_pool (live,
`LiveWatchlist._add` would never re-add it) or fresh (batch, re-added from the store), which both
differ from the unpruned machine. Members are never pruned: both rules need a trade within 6 h.

Time steps are UTC minutes (block_time // 60). The list computed after minute m is valid for
minute m + 1: `Watchlist.step(m, rows)` returns the members for m + 1.

Batch (the NAS measurement, python -m wallets.watchlist --from-slot A --to-slot B --out DIR):
per-(pool, minute) aggregates from the store (one scan of the pool table), the same state
machine, then per UTC day: members per minute (median / p95 / max), the events that land on
the list, the F7 and M1 coverage. --report renders the Markdown over all day files.

Universe with a refresh lag (coordinator question, 2026-10-12): live, new reversed pools cannot be
seen cheaply (lab/nas-results/creation-filter), so the F7 candidate set would be refreshed daily
from OF1 with 1-2 days delay. --build-history writes per reversed pool the minute it was created
and the first minute it showed >= 50 / >= 150 SOL real depth; a day run with --history then counts
which F7 candidate sells and tournament triggers fall in a pool that, at the end of day D-1 or D-2,
already existed / was that deep, and which of those the dynamic list also carried.
"""

import argparse
import collections
import dataclasses
import glob
import json
import os
import statistics
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq

from .common import OUT_ROOT, SOL, Clock, connect, git_commit, guard

POS = 1 << 20
WARMUP_MINUTES = int(os.environ.get("LAB_WATCHLIST_WARMUP_MINUTES", 6 * 60))  # state carried into a day: the longest look-back
TX_BYTES = {"full_tx_3kb": 3072, "deshred_1kb": 1024}  # §6: full transaction with logs ~3 KB; raw ~1 KB
UNIVERSE_LAGS = (1, 2)          # the OF1 refresh is 1 or 2 days behind: the universe for day D knows data through day D - lag
UNIVERSE_DEPTHS = (0, 50, 150)  # 0: the pool existed; else: it had shown >= X SOL real depth
MINUTES_PER_DAY = 1440


@dataclasses.dataclass(frozen=True)
class Rules:
    f7_depth_enter_sol: float = 120.0   # below the 150 SOL trigger threshold on purpose (2026-10-12)
    f7_depth_exit_sol: float = 100.0
    farmer_share_max: float = 0.50
    organic_min_sol: float = 1.0
    organic_window_minutes: int = 60
    f7_idle_minutes: int = 6 * 60
    m1_max_age_minutes: int = 6 * 60
    m1_idle_minutes: int = 60
    prune_idle_minutes: int = 24 * 60     # quiet pools leave the state machine (verdict archived)
    archive_minutes: int = 0              # how long an archived verdict is kept; 0 = forever (default: an entry is one PoolInfo and a bool;
                                          # with an expiry a returning pool would lose its verdict and differ from the unpruned machine)


@dataclasses.dataclass
class PoolInfo:
    pool: str
    mint: str
    quote_class: str      # 'sol' | 'reversed' | 'other'
    orientation: str      # 'normal' | 'reversed'
    mayhem: bool
    create_minute: int | None   # None: created before the data we have (older than any look-back)
    grad_in_create: bool

    @property
    def f7_eligible(self):
        return self.quote_class == "reversed" and not self.mayhem

    @property
    def m1_eligible(self):
        return self.quote_class == "sol" and self.orientation == "normal" and not self.mayhem and not self.grad_in_create


@dataclasses.dataclass
class _State:
    depth: float = 0.0
    last_trade: int | None = None
    organic: bool | None = None          # last verdict with enough volume
    window: collections.deque = dataclasses.field(default_factory=collections.deque)  # (minute, sol, farmer_sol)
    win_sol: float = 0.0
    win_farmer: float = 0.0
    f7: bool = False
    m1: bool = False
    first_seen: int | None = None        # minute the pool entered the state machine (for pruning never-traded pools)


class Watchlist:
    """One state machine for the recorder and the batch measurement."""

    def __init__(self, rules=None, wallets=()):
        self.rules = rules or Rules()
        self.pools = {}
        self.state = {}
        self.archive = {}  # pool -> (PoolInfo, organic verdict, minute archived): pruned pools, compact
        self.wallets = list(dict.fromkeys(wallets))
        self.minute = None
        self.members = {}  # pool -> ("f7_rev", "m1") tags, valid for self.minute + 1
        self.pruned_total = 0
        self.restored_total = 0

    def add_pool(self, info):
        self.pools[info.pool] = info
        self.state.setdefault(info.pool, _State(first_seen=self.minute))
        self.archive.pop(info.pool, None)

    def _restore(self, pool, minute):
        info, organic, _ = self.archive.pop(pool)
        self.pools[pool] = info
        self.state[pool] = _State(organic=organic, first_seen=minute)
        self.restored_total += 1

    def _prune(self, minute):
        """Move pools quiet for prune_idle_minutes to the archive; drop archive entries older than archive_minutes."""
        r = self.rules
        for pool in [p for p in self.pools if p not in self.members]:
            s, info = self.state[pool], self.pools[pool]
            if s.first_seen is None:
                s.first_seen = minute  # added before any step: counts from the first step
            if s.last_trade is not None:
                ref = s.last_trade  # quiet since its last trade
            else:
                ref = info.create_minute if info.create_minute is not None else s.first_seen  # never traded: since its creation, else since it was added
            if minute - ref >= r.prune_idle_minutes:
                self.archive[pool] = (info, s.organic, minute)
                del self.pools[pool]
                del self.state[pool]
                self.pruned_total += 1
        if self.archive and r.archive_minutes > 0:  # 0: archived verdicts never expire (the recorder's choice; PoolInfo is small)
            for pool in [p for p, (_, _, at) in self.archive.items() if minute - at >= r.archive_minutes]:
                del self.archive[pool]

    def step(self, minute, rows):
        """Apply the aggregates of `minute` (iterable of (pool, trades, sol, farmer_sol, depth_last_sol))
        and return the members valid for minute + 1 as {pool: tags}."""
        r = self.rules
        touched = set()
        for pool, trades, sol, farmer_sol, depth in rows:
            if pool not in self.pools:
                if pool in self.archive and trades:
                    self._restore(pool, minute)
                else:
                    continue
            s = self.state[pool]
            if trades:
                s.last_trade = minute
                s.depth = float(depth)
                s.window.append((minute, float(sol), float(farmer_sol)))
                s.win_sol += float(sol)
                s.win_farmer += float(farmer_sol)
            touched.add(pool)
        members = {}
        for pool, info in self.pools.items():
            s = self.state[pool]
            # trailing organic window
            while s.window and s.window[0][0] <= minute - r.organic_window_minutes:
                _, a, b = s.window.popleft()
                s.win_sol -= a
                s.win_farmer -= b
            if s.win_sol >= r.organic_min_sol:
                s.organic = s.win_farmer / s.win_sol <= r.farmer_share_max
            tags = []
            if info.f7_eligible:
                idle = s.last_trade is None or minute - s.last_trade >= r.f7_idle_minutes
                if s.f7:
                    s.f7 = s.depth >= r.f7_depth_exit_sol and s.organic is not False and not idle
                else:
                    s.f7 = s.depth >= r.f7_depth_enter_sol and s.organic is True and not idle
                if s.f7:
                    tags.append("f7_rev")
            if info.m1_eligible and info.create_minute is not None:
                age = minute + 1 - info.create_minute
                fresh = s.last_trade is not None and minute - s.last_trade < r.m1_idle_minutes
                s.m1 = 0 <= age <= r.m1_max_age_minutes and (fresh or age <= r.m1_idle_minutes)
                if s.m1:
                    tags.append("m1")
            if tags:
                members[pool] = tuple(tags)
        self.minute = minute
        self.members = members
        self._prune(minute)
        return members

    def add_history(self, pool, minute, trades, sol, farmer_sol):
        """Trades of an earlier minute that arrive after that minute was stepped (live: a recorder
        backfill of a new probe pool). They enter the trailing organic window where they belong and
        count as activity; the depth stays the last stepped one (newer). Next step() uses them."""
        if pool not in self.pools or not trades or self.minute is None:
            return
        s = self.state[pool]
        if minute <= self.minute - self.rules.organic_window_minutes:
            return  # already outside the window
        i = len(s.window)
        while i and s.window[i - 1][0] > minute:
            i -= 1
        s.window.insert(i, (minute, float(sol), float(farmer_sol)))
        s.win_sol += float(sol)
        s.win_farmer += float(farmer_sol)
        s.last_trade = minute if s.last_trade is None else max(s.last_trade, minute)

    def snapshot(self, valid_from_slot=None, generated_slot=None):
        """The list for the recorder: pools with their rule tags, the followed wallets, counts."""
        return {"version": 1, "valid_for_minute": None if self.minute is None else self.minute + 1,
                "valid_from_slot": valid_from_slot, "generated_slot": generated_slot,
                "pools": [{"pool": p, "mint": self.pools[p].mint, "rules": list(t)} for p, t in sorted(self.members.items())],
                "wallets": [{"wallet": w, "source": "fixed"} for w in self.wallets],
                "counts": {"pools": len(self.members),
                           **{tag: sum(1 for t in self.members.values() if tag in t) for tag in ("f7_rev", "m1")},
                           "wallets": len(self.wallets), "tracked": len(self.pools), "archived": len(self.archive),
                           "pruned_total": self.pruned_total, "restored_total": self.restored_total}}


# ----------------------------------------------------------------------------- batch over the store


def pools_sql(lo, hi):
    """Pools with an event in [lo, hi) or created in it, with what the rules need to know."""
    return f"""
      WITH active AS (SELECT DISTINCT pool FROM pool WHERE slot >= {lo} AND slot < {hi} AND kind IN ('buy', 'sell')
                      UNION SELECT pool FROM pools WHERE create_slot >= {lo} AND create_slot < {hi})
      SELECT p.pool, p.mint, p.quote_class, p.orientation, COALESCE(p.mayhem, false) OR COALESCE(m.mayhem, false) AS mayhem,
             p.create_slot, COALESCE(m.complete_slot = m.create_slot, false) AS grad_in_create
      FROM pools p SEMI JOIN active USING (pool) LEFT JOIN mints m ON m.mint = p.mint"""


def aggregates_sql(lo, hi):
    """Per (pool, UTC minute): trades, SOL volume, farmer SOL volume, last pre-trade real SOL depth."""
    return f"""
      SELECT e.pool, b.block_time // 60 AS minute, count(*) AS trades, sum(e.sol_amount) / {SOL} AS sol,
             COALESCE(sum(e.sol_amount) FILTER (WHERE e.farmer), 0) / {SOL} AS farmer_sol,
             arg_max(e.sol_depth, e.slot * {POS} + e.tx_index) / {SOL} AS depth_last,
             min(e.slot) AS first_slot
      FROM pool e JOIN blocks b USING (slot)
      WHERE e.slot >= {lo} AND e.slot < {hi} AND e.kind IN ('buy', 'sell')
      GROUP BY 1, 2 ORDER BY 2, 1"""


def f7_candidates_sql(lo, hi):
    """Sells that could be an F7 trigger in a reversed pool: >= max(3 SOL, 4 % of the real SOL depth),
    depth >= 150 SOL (the inflow / creator filters come on top, so this is a superset)."""
    return f"""
      SELECT e.pool, e.slot, b.block_time // 60 AS minute
      FROM pool e JOIN blocks b USING (slot) JOIN pools p USING (pool)
      WHERE e.slot >= {lo} AND e.slot < {hi} AND e.kind = 'sell' AND p.quote_class = 'reversed'
        AND e.sol_depth >= {150 * SOL} AND e.sol_amount >= GREATEST({3 * SOL}, 0.04 * e.sol_depth)"""


def pool_history_sql(lo, hi):
    """Per reversed pool: the minute it was created (NULL: before the store), the SOL it was created with (the WSOL side
    of a reversed pool is its base reserve b0), and, over events in [lo, hi), the first minute it traded and the first
    minute it showed >= 50 / >= 150 SOL real depth. One scan per measurement."""
    return f"""
      WITH ev AS (
        SELECT e.pool, min(b.block_time) // 60 AS first_minute,
               min(b.block_time) FILTER (WHERE e.sol_depth >= {50 * SOL}) // 60 AS deep50_minute,
               min(b.block_time) FILTER (WHERE e.sol_depth >= {150 * SOL}) // 60 AS deep150_minute
        FROM pool e JOIN blocks b USING (slot)
        WHERE e.slot >= {lo} AND e.slot < {hi} AND e.kind IN ('buy', 'sell')
          AND e.pool IN (SELECT pool FROM pools WHERE quote_class = 'reversed')
        GROUP BY 1)
      SELECT p.pool, cb.block_time // 60 AS create_minute, ev.first_minute, ev.deep50_minute, ev.deep150_minute,
             CASE WHEN p.orientation = 'reversed' THEN p.b0 ELSE p.q0 END / {SOL} AS sol_create
      FROM pools p LEFT JOIN blocks cb ON cb.slot = p.create_slot LEFT JOIN ev USING (pool)
      WHERE p.quote_class = 'reversed'"""


def build_history(con, lo, hi, path):
    tbl = con.execute(pool_history_sql(lo, hi)).to_arrow_table()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    pq.write_table(tbl, path)
    with open(os.path.splitext(path)[0] + ".json", "w") as f:
        json.dump({"from_slot": lo, "to_slot": hi, "pools": tbl.num_rows, "commit": git_commit()}, f, indent=1)
    return tbl.num_rows


def load_history(path):
    """{pool: {0: create_minute, 50: deep50_minute, 150: deep150_minute, "sol_create": SOL at creation}} (None: never /
    before the store)."""
    t = pq.read_table(path)
    names = t.schema.names
    cols = {k: t.column(k).to_pylist() if k in names else [None] * t.num_rows
            for k in ("pool", "create_minute", "deep50_minute", "deep150_minute", "sol_create")}
    return {p: {0: c, 50: d50, 150: d150, "sol_create": sc} for p, c, d50, d150, sc
            in zip(cols["pool"], cols["create_minute"], cols["deep50_minute"], cols["deep150_minute"], cols["sol_create"])}


def in_universe(minute, lag, qualifying_minute, unknown_is_in=False):
    """A universe refreshed with `lag` days delay knows data through the end of UTC day D - lag, D the event's day:
    the pool is in it when it qualified before the start of day D - lag + 1."""
    cutoff = (minute // MINUTES_PER_DAY - (lag - 1)) * MINUTES_PER_DAY
    if qualifying_minute is None:
        return unknown_is_in
    return qualifying_minute < cutoff


def universe_membership(hist, pool, minute):
    """{(lag, depth): bool} for an event in `pool` at `minute`. A pool created before the store counts as existing;
    a pool missing from the history is in no universe."""
    h = hist.get(pool)
    out = {}
    for lag in UNIVERSE_LAGS:
        for depth in UNIVERSE_DEPTHS:
            out[(lag, depth)] = h is not None and in_universe(minute, lag, h[depth], unknown_is_in=(depth == 0))
    return out


def minute_of_slot(con, slots):
    rows = con.execute("SELECT slot, block_time // 60 FROM blocks WHERE slot IN (SELECT unnest(?))", [list(slots)]).fetchall()
    return dict(rows)


def load_triggers(trigger_dir, con, lo, hi):
    """F7 pool triggers of a tournament run (parts/*.parquet) with t in [lo, hi): distinct (pool, t, farm) of one
    variant / size / d (the trigger set is the same for every variant)."""
    files = sorted(glob.glob(os.path.join(trigger_dir, "parts", "part-*.parquet")))
    if not files:
        return []
    return con.execute(f"""
      SELECT DISTINCT pool, t, farm FROM read_parquet({files!r}, union_by_name = true)
      WHERE family = 'F7' AND venue = 'pool' AND size_sol = 0.5 AND d = 1 AND t >= {lo} AND t < {hi}
        AND variant = (SELECT min(variant) FROM read_parquet({files!r}, union_by_name = true) WHERE family = 'F7')""").fetchall()


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def _universe_counters():
    return {f"d-{lag}": {str(depth): collections.Counter() for depth in UNIVERSE_DEPTHS} for lag in UNIVERSE_LAGS}


def pool_origin(h, deep_sol=150.0):
    """How a pool reached the F7 depth: 'at_create' (created with >= 150 SOL), 'later' (created shallower, deep later),
    'prestore' (created before the store), 'unknown' (no creation SOL / never deep in the history)."""
    if h is None or h[0] is None:
        return "prestore"
    if h["sol_create"] is not None and h["sol_create"] >= deep_sol:
        return "at_create"
    if h[150] is not None:
        return "later"
    return "unknown"


def _note_origin(d, later_minutes, hist, kind, pool):
    h = hist.get(pool)
    origin = pool_origin(h)
    d["origin"][kind][origin] += 1
    if origin == "later" and pool not in later_minutes[kind]:
        later_minutes[kind][pool] = h[150] - h[0]


def _note_first_age(first_age, hist, kind, pool, minute, day):
    """Minutes between the pool's creation and its first event of this kind (pools created inside the store only)."""
    h = hist.get(pool)
    if h is None or h[0] is None:
        return
    age = minute - h[0]
    if pool not in first_age[kind] or age < first_age[kind][pool][1]:
        first_age[kind][pool] = (day, age)


def _count_universe(d, hist, pool, minute, on_list, kind, organic=False):
    for (lag, depth), inside in universe_membership(hist, pool, minute).items():
        c = d["universe"][f"d-{lag}"][str(depth)]
        c[kind] += 1
        if inside:
            c[kind + "_in"] += 1
            c[kind + "_in_on_list"] += on_list
        if organic:
            c[kind + "_organic"] += 1
            if inside:
                c[kind + "_organic_in"] += 1
                c[kind + "_organic_in_on_list"] += on_list


def run(con, lo, hi, out_dir, rules, wallets, trigger_dir=None, clock=None, history=None):
    """Measure [lo, hi): members per minute, events on the list, coverage. State warms up over the 6 h before lo.
    `history`: path of the pool history (--build-history) for the universe-with-lag counts."""
    t0 = time.time()
    hist = load_history(history) if history else None
    first_age = {"cand": {}, "trig": {}, "trig_organic": {}}  # pool -> (day, minutes after creation) of its first event
    later_minutes = {"cand": {}, "trig": {}, "trig_organic": {}}  # pool -> minutes from creation to >= 150 SOL (pools created shallower)
    clock = clock or Clock.from_store(con)
    warm_lo = max(lo - clock.slots(WARMUP_MINUTES * 60_000, lo), 0)
    pools = con.execute(pools_sql(warm_lo, hi)).fetchall()
    create_min = minute_of_slot(con, {r[5] for r in pools if r[5] is not None and r[5] >= warm_lo})
    wl = Watchlist(rules, wallets)
    infos = {pool: PoolInfo(pool, mint, qc, orient, bool(mayhem), create_min.get(cslot), bool(gic)) for pool, mint, qc, orient, mayhem, cslot, gic in pools}
    for info in infos.values():
        wl.add_pool(info)
    first_minute = con.execute(f"SELECT min(block_time) // 60 FROM blocks WHERE slot >= {lo}").fetchone()[0]
    agg = con.execute(aggregates_sql(warm_lo, hi)).to_arrow_table()
    cands = con.execute(f7_candidates_sql(lo, hi)).fetchall()
    cand_by_minute = collections.defaultdict(list)
    for pool, slot, minute in cands:
        cand_by_minute[minute].append(pool)
    trig = load_triggers(trigger_dir, con, lo, hi) if trigger_dir else []
    trig_minute = minute_of_slot(con, {t for _, t, _ in trig}) if trig else {}
    trig_by_minute = collections.defaultdict(list)
    for pool, t, farm in trig:
        if t in trig_minute:
            trig_by_minute[trig_minute[t]].append((pool, farm))
    # M1 decision points: open + 15 min of every eligible pool created in [lo, hi)
    m1_points = collections.defaultdict(list)
    for p in wl.pools.values():
        if p.m1_eligible and p.create_minute is not None and p.create_minute >= first_minute:
            m1_points[p.create_minute + 15].append(p.pool)

    cols = {k: agg.column(k).to_pylist() for k in ("pool", "minute", "trades", "sol", "farmer_sol", "depth_last")}
    n = agg.num_rows
    i = 0
    day_stats = collections.defaultdict(lambda: {"minutes": 0, "members": [], "f7_rev": [], "m1": [],
                                                 "events_all": 0, "events_on_list": 0, "events_f7_rev": 0, "events_m1": 0,
                                                 "events_reversed_all": 0, "events_normal_all": 0,
                                                 "f7_cand": 0, "f7_cand_covered": 0,
                                                 "f7_trig": 0, "f7_trig_covered": 0, "f7_trig_organic": 0, "f7_trig_organic_covered": 0,
                                                 "m1_points": 0, "m1_points_covered": 0, "m1_events_young": 0, "m1_events_young_covered": 0,
                                                 "universe": _universe_counters(),
                                                 "origin": {k: collections.Counter() for k in ("cand", "trig", "trig_organic")}})
    members_rows = []
    # every minute is stepped, also a silent one: the idle rules count minutes without trades
    # (a decision point or trigger beyond the last minute with data is not stepped: it belongs to the next range)
    minutes = range(min(cols["minute"]), max(cols["minute"]) + 1) if n else []
    members = {}
    for m in minutes:
        rows = []
        while i < n and cols["minute"][i] == m:
            rows.append((cols["pool"][i], cols["trades"][i], cols["sol"][i], cols["farmer_sol"][i], cols["depth_last"][i]))
            i += 1
        if m >= first_minute:  # the list valid for m was decided after m - 1
            day = time.strftime("%Y-%m-%d", time.gmtime(m * 60))
            d = day_stats[day]
            d["minutes"] += 1
            d["members"].append(len(members))
            d["f7_rev"].append(sum(1 for t in members.values() if "f7_rev" in t))
            d["m1"].append(sum(1 for t in members.values() if "m1" in t))
            for pool, trades, _, _, _ in rows:
                info = wl.pools.get(pool)
                d["events_all"] += trades
                if info is not None and info.quote_class == "reversed":
                    d["events_reversed_all"] += trades
                elif info is not None and info.quote_class == "sol":
                    d["events_normal_all"] += trades
                tags = members.get(pool)
                if tags:
                    d["events_on_list"] += trades
                    if "f7_rev" in tags:
                        d["events_f7_rev"] += trades
                    if "m1" in tags:
                        d["events_m1"] += trades
                if info is not None and info.m1_eligible and info.create_minute is not None \
                        and 0 <= m - info.create_minute <= rules.m1_max_age_minutes:
                    d["m1_events_young"] += trades
                    if tags and "m1" in tags:
                        d["m1_events_young_covered"] += trades
            for pool in cand_by_minute.get(m, ()):
                d["f7_cand"] += 1
                cov = "f7_rev" in members.get(pool, ())
                d["f7_cand_covered"] += cov
                if hist is not None:
                    _count_universe(d, hist, pool, m, cov, "cand")
                    _note_first_age(first_age, hist, "cand", pool, m, day)
                    _note_origin(d, later_minutes, hist, "cand", pool)
            for pool, farm in trig_by_minute.get(m, ()):
                d["f7_trig"] += 1
                cov = "f7_rev" in members.get(pool, ())
                d["f7_trig_covered"] += cov
                if farm == "organic":
                    d["f7_trig_organic"] += 1
                    d["f7_trig_organic_covered"] += cov
                if hist is not None:
                    _count_universe(d, hist, pool, m, cov, "trig", organic=(farm == "organic"))
                    _note_first_age(first_age, hist, "trig", pool, m, day)
                    _note_origin(d, later_minutes, hist, "trig", pool)
                    if farm == "organic":
                        _note_first_age(first_age, hist, "trig_organic", pool, m, day)
                        _note_origin(d, later_minutes, hist, "trig_organic", pool)
            for pool in m1_points.get(m, ()):
                d["m1_points"] += 1
                d["m1_points_covered"] += "m1" in members.get(pool, ())
            for pool, tags in members.items():
                members_rows.append((m, pool, "f7_rev" in tags, "m1" in tags))
                if hist is not None and "f7_rev" in tags:
                    _count_universe(d, hist, pool, m, True, "f7_minutes")
        for row in rows:  # a pruned pool whose archive entry expired is still known to the store: re-add it (fresh verdict)
            if row[0] not in wl.pools and row[0] not in wl.archive and row[0] in infos and row[1]:
                wl.add_pool(infos[row[0]])
        members = wl.step(m, rows)
    os.makedirs(out_dir, exist_ok=True)
    tbl = pa.table({"minute": pa.array([r[0] for r in members_rows], pa.int64()),
                    "pool": pa.array([r[1] for r in members_rows], pa.string()),
                    "f7_rev": pa.array([r[2] for r in members_rows], pa.bool_()),
                    "m1": pa.array([r[3] for r in members_rows], pa.bool_())})
    pq.write_table(tbl, os.path.join(out_dir, f"watchlist-{lo}.parquet"))
    store_first_day = time.strftime("%Y-%m-%d", time.gmtime(con.execute("SELECT min(block_time) FROM blocks").fetchone()[0]))
    out = {"from_slot": lo, "to_slot": hi, "commit": git_commit(), "rules": dataclasses.asdict(rules), "wallets": len(wallets),
           "pools_seen": len(wl.pools), "trigger_dir": trigger_dir, "history": history, "store_first_day": store_first_day,
           "seconds": round(time.time() - t0), "days": {}}
    last = max(day_stats, default=None)  # the pools that became deep later go in once, on the last day (the report sums them)
    out_later = {k: sorted(v.values()) for k, v in later_minutes.items()}
    for day, d in sorted(day_stats.items()):
        out["days"][day] = {k: v for k, v in d.items() if not isinstance(v, list) and k not in ("universe", "origin")}
        if hist is not None:
            out["days"][day]["universe"] = d["universe"]
            out["days"][day]["origin"] = d["origin"]
            out["days"][day]["first_age_minutes"] = {
                kind: sorted(age for dd, age in ages.values() if dd == day) for kind, ages in first_age.items()}
            out["days"][day]["deep_later_minutes"] = out_later if day == last else {k: [] for k in later_minutes}
        for k in ("members", "f7_rev", "m1"):
            out["days"][day][k] = {"median": statistics.median(d[k]) if d[k] else None, "p95": pct(d[k], 0.95), "max": max(d[k]) if d[k] else None}
    with open(os.path.join(out_dir, f"watchlist-stats-{lo}.json"), "w") as f:
        json.dump(out, f, indent=1)
    return out


def report(out_dir):
    """Markdown over all watchlist-stats-*.json files."""
    days = {}
    store_first_day = None
    for f in sorted(glob.glob(os.path.join(out_dir, "watchlist-stats-*.json"))):
        j = json.load(open(f))
        store_first_day = store_first_day or j.get("store_first_day")
        for day, d in j["days"].items():
            if d["minutes"] >= 1000:  # whole days only (a range boundary leaves a partial day in two files)
                days[day] = d
            elif day in days:
                pass
            else:
                days.setdefault("_partial", {})[day] = d
    partial = days.pop("_partial", {})
    lines = ["# Watchlist over de ontwikkelperiode: omvang, dekking en inname per dag", "",
             "Lijst per minuut uit data vóór die minuut (`lab/wallets/watchlist.py`). F7-reversed: reversed pools, "
             f"reële SOL-diepte ≥ {Rules.f7_depth_enter_sol:.0f} SOL (uit < {Rules.f7_depth_exit_sol:.0f}), organisch (farmers ≤ 50 % van het SOL-volume in het laatste uur). "
             "M1: normale pools ≤ 6 u oud, graduatie buiten de create-slot, niet 60 min stil.", "",
             "| dag | pools op de lijst (mediaan / p95 / max) | F7-rev | M1 | events/dag op de lijst | van alle pool-events | "
             "F7-kandidaatsells gedekt | F7-triggers (toernooi) gedekt, organisch | M1 +15 min gedekt | M1-trades ≤ 6 u gedekt | GB/dag (3 KB/tx) |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    tot = collections.Counter()
    for day, d in sorted(days.items()):
        def cov(a, b):
            return f"{d[a] / d[b]:.1%} ({d[b]})" if d[b] else "–"
        m = d["members"]
        lines.append(f"| {day} | {m['median']:.0f} / {m['p95']} / {m['max']} | {d['f7_rev']['median']:.0f} / {d['f7_rev']['max']} | "
                     f"{d['m1']['median']:.0f} / {d['m1']['max']} | {d['events_on_list']:,} | "
                     f"{d['events_on_list'] / d['events_all']:.1%} | {cov('f7_cand_covered', 'f7_cand')} | "
                     f"{cov('f7_trig_covered', 'f7_trig')}, {cov('f7_trig_organic_covered', 'f7_trig_organic')} | "
                     f"{cov('m1_points_covered', 'm1_points')} | {cov('m1_events_young_covered', 'm1_events_young')} | "
                     f"{d['events_on_list'] * TX_BYTES['full_tx_3kb'] / 1e9:.2f} |")
        for k in ("events_on_list", "events_all", "f7_cand", "f7_cand_covered", "f7_trig", "f7_trig_covered", "f7_trig_organic",
                  "f7_trig_organic_covered", "m1_points", "m1_points_covered", "m1_events_young", "m1_events_young_covered"):
            tot[k] += d[k]
    lines.append("")
    if days:
        n = len(days)
        lines.append(f"**Totaal over {n} dagen:** {tot['events_on_list']:,} events op de lijst van {tot['events_all']:,} pool-events "
                     f"({tot['events_on_list'] / max(tot['events_all'], 1):.1%}); gemiddeld {tot['events_on_list'] / n:,.0f} events/dag = "
                     f"{tot['events_on_list'] / n * TX_BYTES['full_tx_3kb'] / 1e9:.2f} GB/dag als volledige transacties (3 KB), "
                     f"{tot['events_on_list'] / n * TX_BYTES['deshred_1kb'] / 1e9:.2f} GB/dag als ruwe transacties (1 KB). "
                     f"Dekking F7-kandidaatsells {tot['f7_cand_covered'] / max(tot['f7_cand'], 1):.1%} ({tot['f7_cand']:,}); "
                     f"toernooi-triggers {tot['f7_trig_covered'] / max(tot['f7_trig'], 1):.1%} ({tot['f7_trig']:,}), organisch "
                     f"{tot['f7_trig_organic_covered'] / max(tot['f7_trig_organic'], 1):.1%} ({tot['f7_trig_organic']:,}); "
                     f"M1-beslismomenten {tot['m1_points_covered'] / max(tot['m1_points'], 1):.1%} ({tot['m1_points']:,}); "
                     f"M1-trades ≤ 6 u {tot['m1_events_young_covered'] / max(tot['m1_events_young'], 1):.1%}.")
        lines.append("")
        lines.append("Niet meegeteld: account-updates van de vault-balansen van alle pools (§6 regel 2, ~8 bytes data per update), "
                     "de curve en de gevolgde wallets (nog een vaste lijst). Een pool met een open positie blijft bij de paper-engine op de lijst; "
                     "dat zit niet in deze telling.")
    if any("universe" in d for d in days.values()):
        lines += ["", *universe_section(days, store_first_day)]
    if partial:
        lines.append(f"\nGedeeltelijke dagen weggelaten: {', '.join(sorted(partial))}.")
    return "\n".join(lines)


def universe_section(days, store_first_day):
    """What a candidate universe refreshed from OF1 with 1 or 2 days delay misses: F7 candidate sells and tournament
    triggers in pools that did not yet exist / were not yet that deep at the refresh."""
    import datetime

    # the depth history starts with the store: on its first two days a pool deep before the store looks new
    biased = set()
    if store_first_day:
        d0 = datetime.date.fromisoformat(store_first_day)
        biased = {(d0 + datetime.timedelta(days=k)).isoformat() for k in range(max(UNIVERSE_LAGS))}
    crits = [(lag, depth) for lag in UNIVERSE_LAGS for depth in UNIVERSE_DEPTHS]
    label = {0: "bestond", 50: "≥ 50 SOL", 150: "≥ 150 SOL"}
    lines = ["## Universum met ververs-vertraging: wat missen we zonder stream op nieuwe reversed pools?", "",
             "Live zien we nieuwe reversed pools niet goedkoop; de F7-kandidatenset zou dagelijks uit OF1 komen, 1–2 dagen achter. "
             "Per dag D: het aandeel F7-kandidaatsells (≥ 150 SOL diep, sell ≥ max(3 SOL, 4 %)) en toernooi-F7-triggers "
             "(alle / organisch) in een pool die aan het eind van dag D−1 resp. D−2 al bestond, al ≥ 50 SOL of al ≥ 150 SOL "
             "reële diepte had laten zien. Pools van vóór de store tellen als bestaand. De laatste kolom van de totalen: het aandeel van de "
             "F7-pool-minuten op de dynamische lijst waarvan de pool in dat universum zat.", "",
             "| dag | " + " | ".join(f"D−{lag} {label[depth]}" for lag, depth in crits) + " |",
             "|---|" + "---|" * len(crits)]
    tot = {k: collections.Counter() for k in crits}
    n_days = 0
    for day, d in sorted(days.items()):
        u = d.get("universe")
        if not u:
            continue
        cells = []
        for lag, depth in crits:
            c = u[f"d-{lag}"][str(depth)]
            cells.append(f"{_share(c, 'cand')} / {_share(c, 'trig')} ({_share(c, 'trig_organic')})")
            if day not in biased or depth == 0:
                tot[(lag, depth)].update(c)
        n_days += day not in biased
        lines.append(f"| {day}{' *' if day in biased else ''} | " + " | ".join(cells) + " |")
    lines += ["", "Cel: kandidaatsells / triggers (organische triggers) in het universum. "
              + (f"\\* {', '.join(sorted(biased))}: diepte-historie begint met de store, daarom alleen 'bestond' in de totalen. " if biased else "")
              + f"**Totaal over {n_days} dagen** (in het universum, en daarvan ook op de dynamische lijst):", ""]
    c1 = tot[(1, 0)]
    if c1.get("trig"):
        lines.append(f"Op de dag van aanmaak zelf (niet in het D−1-universum 'bestond'): {1 - c1['trig_in'] / c1['trig']:.1%} van de "
                     f"toernooi-triggers, {1 - c1['trig_organic_in'] / max(c1['trig_organic'], 1):.1%} van de organische, "
                     f"{1 - c1['cand_in'] / max(c1['cand'], 1):.1%} van de kandidaatsells.")
    ages = {k: [] for k in ("cand", "trig", "trig_organic")}
    for day, d in days.items():
        for k, xs in d.get("first_age_minutes", {}).items():
            ages[k] += xs
    label_age = {"cand": "eerste kandidaatsell", "trig": "eerste toernooi-trigger", "trig_organic": "eerste organische trigger"}
    for k, xs in ages.items():
        if xs:
            lines.append(f"Minuten tussen aanmaak van de pool en zijn {label_age[k]} (pools aangemaakt binnen de store, n={len(xs):,}): "
                         f"p10 {pct(xs, 0.10):,.0f}, p25 {pct(xs, 0.25):,.0f}, mediaan {statistics.median(xs):,.0f}, p75 {pct(xs, 0.75):,.0f}, p90 {pct(xs, 0.90):,.0f}.")
    origin = {k: collections.Counter() for k in ("cand", "trig", "trig_organic")}
    later = {k: [] for k in origin}
    for day, d in days.items():
        for k, c in d.get("origin", {}).items():
            origin[k].update(c)
        for k, xs in d.get("deep_later_minutes", {}).items():
            later[k] += xs
    label_kind = {"cand": "kandidaatsells", "trig": "toernooi-triggers", "trig_organic": "organische triggers"}
    for k, c in origin.items():
        n = sum(c.values())
        if n:
            lines.append(f"Hoe de pool diep werd, {label_kind[k]} (n={n:,}): aangemaakt met ≥ 150 SOL {c['at_create'] / n:.1%}, "
                         f"later diep geworden {c['later'] / n:.1%}, pool van vóór de store {c['prestore'] / n:.1%}"
                         + (f", onbekend {c['unknown'] / n:.1%}" if c["unknown"] else "") + "."
                         + (f" Later-diepe pools: minuten van aanmaak tot ≥ 150 SOL (n={len(later[k]):,}) p10 {pct(later[k], 0.10):,.0f}, "
                            f"mediaan {statistics.median(later[k]):,.0f}, p90 {pct(later[k], 0.90):,.0f}." if later[k] else ""))
    lines.append("")
    lines += ["| universum | F7-kandidaatsells | … ook op de lijst | toernooi-triggers | … ook op de lijst | organische triggers | … ook op de lijst | F7-pool-minuten op de lijst |",
              "|---|---|---|---|---|---|---|---|"]
    for lag, depth in crits:
        c = tot[(lag, depth)]
        lines.append(f"| D−{lag} {label[depth]} | {_share(c, 'cand')} | {_share(c, 'cand', '_in_on_list')} | {_share(c, 'trig')} | "
                     f"{_share(c, 'trig', '_in_on_list')} | {_share(c, 'trig_organic')} | {_share(c, 'trig_organic', '_in_on_list')} | "
                     f"{_share(c, 'f7_minutes')} |")
    return lines


def _share(c, kind, suffix="_in"):
    return f"{c.get(kind + suffix, 0) / c[kind]:.1%} ({c[kind]:,})" if c.get(kind) else "–"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "watchlist-v1"))
    ap.add_argument("--triggers", help="tournament run directory with parts/ (F7 pool triggers for the coverage check)")
    ap.add_argument("--wallets", help="CSV with a `wallet` column: the followed wallets (fixed list for now)")
    ap.add_argument("--report", action="store_true", help="render the Markdown over the stats files in --out")
    ap.add_argument("--build-history", metavar="PARQUET", help="write the per-pool history (creation, first >= 50 / 150 SOL) over [from, to) and stop")
    ap.add_argument("--history", metavar="PARQUET", help="pool history from --build-history: adds the universe-with-lag counts")
    a = ap.parse_args()
    if a.report:
        print(report(a.out))
        return
    guard(a.to_slot)
    con = connect(memory=os.environ.get("LAB_WATCHLIST_MEMORY", os.environ.get("LAB_WALLETS_MEMORY", "1000MB")))
    if a.build_history:
        t0 = time.time()
        n = build_history(con, a.from_slot, a.to_slot, a.build_history)
        print(f"history {a.from_slot}-{a.to_slot}: {n} reversed pools -> {a.build_history}, {time.time() - t0:.0f}s", file=sys.stderr)
        return
    wallets = []
    if a.wallets:
        wallets = [r[0] for r in con.execute(f"SELECT wallet FROM read_csv({a.wallets!r}, header = true)").fetchall()]
    rules = Rules(f7_depth_enter_sol=float(os.environ.get("LAB_WATCHLIST_F7_ENTER", Rules.f7_depth_enter_sol)),
                  f7_depth_exit_sol=float(os.environ.get("LAB_WATCHLIST_F7_EXIT", Rules.f7_depth_exit_sol)))
    out = run(con, a.from_slot, a.to_slot, a.out, rules, wallets, a.triggers, history=a.history)
    days = out["days"]
    print(f"{a.from_slot}-{a.to_slot}: {out['pools_seen']} pools seen, days {list(days)}, "
          + "; ".join(f"{d}: median {v['members']['median']} pools, {v['events_on_list']:,}/{v['events_all']:,} events" for d, v in days.items())
          + f", {out['seconds']}s", file=sys.stderr)


if __name__ == "__main__":
    main()
