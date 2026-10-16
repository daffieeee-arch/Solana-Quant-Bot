"""Paper engine v0: processes an event feed as if live, trades F7 on paper with an N2 shadow.

  python -m paper.engine --from 450142000 --to 450361000 --journal <dir> [--risk lab/paper/risk.yaml]

Slot-synchronous: the engine collects the events of one slot and then, in this order,
  1. feeds open positions the slot's events of their token (re-executed against the state with
     our position: the replay's counterfactual overlay, the same PositionSim code);
  2. opens positions whose entry slot this is (t + d, after a fraction q of the slot's txs);
  3. updates each token's recent history;
  4. evaluates F7 on the slot (decision at the end of slot t, the same reference filters as the
     backtest, on a view of the past only) and schedules entries at t + d;
  5. lets positions of tokens without events in this slot see the time pass (deadlines, exits).
So the engine trades exactly what the replay would, and its journal is the paper record.

Differences to the backtest, by design: risk limits (risk.yaml) apply; the N2 shadow enters now in
a random token of the same venue, orientation, depth band and hour that already traded this hour
(the backtest draws a random event of the whole hour, which is not knowable live).

Variant groups (research/week4-spec-paper-h4h5-2026-10-16.md, sha256 abc0f29f): the config's top level is
the primary group (K3); its `groups` section adds groups of their own (H: flow300_H6 and flow300_tp100_H6 on
the organic F7 triggers). Every group has its variants, farm filter, segments, delay, q, N2, risk file, daily
loss counter, pool cooldown and paper wallet; they share the F7 trigger and the feed. A group's busy state is
its own (organic-only for a group with farm organic: a flagged trigger opens and blocks nothing there), and
a group with require_farm_complete opens only on triggers whose farm label had full history.
"""

import argparse
import bisect
import hashlib
import json
import os
import shutil
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace

import numpy as np

from backtest import replay
from backtest import venues as V
from backtest.costs import Scenario, settle
from backtest.replay import _hist_state, _insertion_index
from backtest.store import load_defaults
from backtest.strategies import (
    F6_FARM_MS,
    F6_FARM_SHARE,
    F7_BIG_GRID,
    F7_EXIT_GRID,
    F7_GRID,
    F7_LONG_GRID,
    FLOW_KINDS,
    HOUR_MS,
    MAYHEM_AGENT,
    SOL,
    SOL_QUOTES,
    Clock,
    _mix,
    _seed,
    decision_bounds,
    f7_exit_rule,
    f7_exit_variant,
    f7_signal,
    ms,
    sol_side,
    tp_sl_time,
)

from . import risk as R
from .journal import Journal

BACKUP = "/home/chupa/Solana-project/data-old-faithful-one/lab/backup"
HISTORY_SLOTS = 400  # recent events kept per token (F7 windows look back 200 slots)
GAP_SLOTS = 25  # a jump of more slots between events is logged as a feed gap
# The F7 exit variants a group can name (spec E, the long exits, the big take-profits): name -> params.
EXIT_VARIANTS = {f7_exit_variant(*g): g for g in F7_EXIT_GRID + F7_LONG_GRID + F7_BIG_GRID}
SINCE_CREATION_MS = 60_000  # a pool first seen within this of its creation was followed since it was created
POOL_SEEN_KEEP_MS = 24 * 3_600_000  # pool_seen of a pool without a trade for this long is forgotten


def variant(spec):
    """(name, params) of a variant: '0.04:600' (tp/T, the v1 grid) or an exit variant's name (flow300_H6)."""
    if isinstance(spec, tuple):
        if isinstance(spec[0], str):  # (name, params)
            return spec
        tp, T = spec
        return f"tp{int(tp * 100)}_T{T}", (tp, T)
    if ":" in spec:
        tp, T = spec.split(":")
        return variant((float(tp), int(T)))
    if spec not in EXIT_VARIANTS:
        raise ValueError(f"unknown variant {spec!r}: tp:T or one of {sorted(EXIT_VARIANTS)}")
    return spec, EXIT_VARIANTS[spec]


class LiveFlowIndex:
    """strategies.FlowIndex for a live position: the pool's organic net SOL flow over slot windows, fed leg
    by leg (the engine's recent legs at the entry, then every slot's legs before the position sees them).
    A query at decision slot s asks for [s - n_flow, s]; decision slots only grow, so the legs before a
    query's lower end are dropped (FlowTime evaluates from n_flow slots held on: only legs since the entry)."""

    def __init__(self, orientation):
        self.orientation = orientation
        self.slots, self.cum, self.seen = [], [0], set()

    def add(self, legs):
        for l in legs:
            if l.kind not in ("buy", "sell") or l.order in self.seen:
                continue
            self.seen.add(l.order)
            tb, a, _ = sol_side(l, self.orientation)
            v = 0 if l.farmer else (a if tb else -a)
            if self.slots and l.slot < self.slots[-1]:  # a late leg: in its place, the sums after it again
                i = bisect.bisect_right(self.slots, l.slot)
                self.slots.insert(i, l.slot)
                steps = [b - a_ for a_, b in zip(self.cum[i:], self.cum[i + 1:])]
                self.cum[i + 1:] = []
                for x in [v] + steps:
                    self.cum.append(self.cum[-1] + x)
                continue
            self.slots.append(l.slot)
            self.cum.append(self.cum[-1] + v)

    def flow(self, lo, hi):
        """Net organic SOL inflow over lo <= slot <= hi."""
        i, j = bisect.bisect_left(self.slots, lo), bisect.bisect_right(self.slots, hi)
        v = self.cum[j] - self.cum[i]
        if i > 256 and i > len(self.slots) // 2:  # drop what no later query reaches
            self.seen.difference_update(o for o in list(self.seen) if o[0] < lo)
            self.slots, self.cum = self.slots[i:], self.cum[i:]
        return v


@dataclass
class Group:
    """A variant group: its strategy (variants as (name, params), farm, segments, delay, q, N2), its risk file
    and paper wallet, and its state (risk, daily loss, pool cooldown, wallet)."""
    name: str
    variants: list
    farm: str = "all"
    segments: set = None
    d: int = 1
    d_ms: int = None
    q: float = 0.5
    n2_k: int = 1
    risk_path: str = None
    wallet_file: str = "wallet.json"
    require_farm_complete: bool = False
    risk: object = None
    risk_reason: str = "not loaded"
    day_loss: int = 0
    cool: dict = field(default_factory=dict)  # pool -> slot until which no new entry (pool_cooldown_h after a total loss)
    wallet: dict = field(default_factory=lambda: {"start": int(10 * SOL), "realized": 0, "txs": deque(maxlen=50)})
    wallet_dirty: bool = False

    def __post_init__(self):
        if self.farm not in ("all", "organic"):
            raise ValueError(f"farm must be 'all' or 'organic', not {self.farm!r}")
        if self.d_ms is not None and self.d_ms <= 0:
            raise ValueError(f"d_ms must be > 0, not {self.d_ms}")
        self.variants = [variant(v) for v in self.variants]
        self.segments = set(self.segments) if self.segments else None
        flow = [n for n, p in self.variants if isinstance(p[0], str) and p[0] in FLOW_KINDS]
        if flow and (self.segments is None or "curve" in self.segments):
            raise ValueError(f"group {self.name}: flow exits {flow} are pool exits: give segments without curve")


def _day(block_time):
    return time.strftime("%Y-%m-%d", time.gmtime(block_time)) if block_time else None


class RecentView:
    """The F7 reference filters' view of a token: its recent events up to slot t."""

    def __init__(self, legs, t, orientation):
        self.t, self.stream = t, SimpleNamespace(orientation=orientation)
        self.legs = [l for l in legs if l.slot <= t]
        self.curve = self.pool = self.legs

    def curve_window(self, lo, hi):
        hi = min(hi, self.t)
        return [l for l in self.legs if lo <= l.slot <= hi]

    def pool_window(self, lo, hi):
        hi = min(hi, self.t)
        return [l for l in self.legs if lo <= l.slot <= hi and l.kind in ("buy", "sell")]


@dataclass
class Pending:
    family: str
    variant: str
    key: str
    venue: str
    orientation: str
    t: int
    entry_slot: int
    params: tuple
    depth: int
    group: str = None  # the variant group (None: the primary group)


@dataclass
class Open:
    p: Pending
    r: object
    sim: object
    opened: int
    grad: dict = field(default_factory=dict)  # graduation: waiting for the pool to open
    rule: object = None  # the exit rule (its windows go into the exits row)
    flows: object = None  # LiveFlowIndex of a flow exit, fed with the token's legs


class Engine:
    def __init__(self, feed, journal, risk_path, *, variants=F7_GRID, d=1, q=0.5, tol=0.05, tau="empirical",
                 dev_bounds=None, n2_k=1, ignore_risk=False, cfg=None, clock=None, backup=True, segments=None,
                 farm="all", watchlist=None, d_ms=None, name="K3", groups=()):
        """The arguments up to d_ms are the primary group's (name: its name; groups: further Group objects).
        d: entry (and exit) delay in slots; d_ms, when given, replaces it with milliseconds that the
        clock turns into slots at each decision (_d_at), so the delay follows the slot time per epoch;
        variants: (tp, T), '0.04:600', an exit variant's name (flow300_H6) or (name, params);
        segments: trade F7 only there ('curve', 'pool_normal', 'pool_reversed'; None: everywhere);
        farm: 'organic' trades only triggers in pools that were not flagged for volume farming in the
        hour before t (the backtest's `farm` label), 'all' trades every trigger (the label is journaled);
        watchlist: a paper.watchlist_live.LiveWatchlist fed with the pool trades seen (live: it writes the
        recorder's watchlist-current.json every minute)."""
        self.feed, self.j, self.watch = feed, journal, watchlist
        self.primary = Group(name, list(variants), farm=farm, segments=segments, d=d, d_ms=d_ms, q=q, n2_k=n2_k,
                             risk_path=risk_path)
        self.groups = [self.primary, *groups]
        self.group_of = {g.name: g for g in self.groups}
        names = [n for g in self.groups for n, _ in g.variants]
        if len(self.group_of) != len(self.groups) or len(names) != len(set(names)):
            raise ValueError(f"groups and their variants must have distinct names: {[(g.name, g.variants) for g in self.groups]}")
        self.farm_win = defaultdict(list)  # pool -> sorted [(slot, order, sol, farmer sol)] of its trades, the last ~hour
        self.pool_seen, self.pool_last, self.backfilled = {}, {}, set()  # pool -> first / last slot seen; pools with a backfill
        self.first_slot = None
        self.tol, self.tau = tol, replay.Tau(tau)
        self.cfg = cfg or load_defaults()
        self.clock = clock or Clock()
        lo, hi = dev_bounds or feed.meta_bounds
        self.dev = (lo, hi)
        self.dlo, self.dhi = decision_bounds(self.cfg, lo, hi, self.clock)
        self.burn_slot = lo + self.clock.slots(ms(324_000), lo)
        self.ignore_risk, self.backup = ignore_risk, backup
        self.scn = Scenario.from_cfg("base", self.cfg)
        self.recent = defaultdict(deque)  # key -> recent legs
        self.last_state = {}  # key -> state after its last event
        self.last_seen = {}  # key -> slot of its last event
        self.open = defaultdict(list)  # key -> [Open]
        self.pending = defaultdict(list)  # entry slot -> [Pending]
        self.busy = {}  # (family, variant, key) -> 'open' | slot of the last exit (variant names are distinct per group)
        self.hour = None
        self.hour_events = defaultdict(list)  # (venue, orientation) -> [(key, slot, depth)] this hour
        self.risk_minute = None
        self.day = None
        self.minute, self.events_seen, self.last_event, self.last_slot = None, 0, None, None
        self.slot_block_time = None  # block time of the slot being decided (signal latency)
        self.slot_arrivals = {}  # (slot, tx_index) -> Event.arrival of the slot being decided (live only)
        self.seed_journaled = None  # the feed's farm_seed last written to the journal
        self.grad_wait = defaultdict(list)  # pool -> [Open] waiting for open + 1
        self.kept = None  # the pools last written for the recorder

    # The primary group's settings and state under the engine's own names (the K3 runs, tests and reports).
    variants = property(lambda self: [p for _, p in self.primary.variants])
    farm = property(lambda self: self.primary.farm)
    segments = property(lambda self: self.primary.segments)
    d = property(lambda self: self.primary.d)
    d_ms = property(lambda self: self.primary.d_ms)
    q = property(lambda self: self.primary.q)
    n2_k = property(lambda self: self.primary.n2_k)
    risk_path = property(lambda self: self.primary.risk_path)
    risk = property(lambda self: self.primary.risk)
    risk_reason = property(lambda self: self.primary.risk_reason)
    cool = property(lambda self: self.primary.cool)
    wallet = property(lambda self: self.primary.wallet)
    day_loss = property(lambda self: self.primary.day_loss)

    def _group(self, p):
        return self.group_of[p.group] if p.group else self.primary

    # ------------------------------------------------------------------ main loop

    def run(self):
        cur, by_key, bt, arr = None, {}, None, {}
        for ev in self.feed.events():
            if ev.slot != cur:
                if cur is not None:
                    self.slot_arrivals = arr
                    self._end_slot(cur, by_key, bt)
                self._quiet_entries(ev.slot)
                cur, by_key, bt, arr = ev.slot, {}, ev.block_time, {}
            by_key.setdefault((ev.venue, ev.key), []).append(ev.leg)
            if ev.arrival is not None:
                arr[(ev.slot, ev.tx_index)] = ev.arrival
            seed = getattr(self.feed, "farm_seed", None)
            if seed is not None and seed is not self.seed_journaled:
                self.seed_journaled = seed
                self._journal_seed(seed)
            self.events_seen += 1
            self.last_event = f"{ev.slot}:{ev.tx_index}:{ev.outer_ix}:{ev.inner_ix}"
        if cur is not None:
            self.slot_arrivals = arr
            self._end_slot(cur, by_key, bt)
        self._end_of_data()
        self.j.commit()

    def _journal_seed(self, seed):
        """LiveFeed's farmer history: seeded from the store over [lo, hi), complete from slot complete_from on (farm
        labels and the live watchlist's organic verdict before it lack history). The state 'farmer_seed' keeps the
        first start's: a restart after the retention (3 days) replays from a later segment and seeds its own window,
        whose complete_from lies later, but the rows from before it keep the first start's labels (LAB review B6).
        'farmer_seeds' lists every start's seed once, in order."""
        if self.j.state("farmer_seed") is None:
            self.j.state("farmer_seed", json.dumps(seed, sort_keys=True))
        seeds = json.loads(self.j.state("farmer_seeds") or "[]")
        if not seeds or seeds[-1] != json.loads(json.dumps(seed)):
            seeds.append(seed)
            self.j.state("farmer_seeds", json.dumps(seeds, sort_keys=True))

    def _quiet_entries(self, before):
        """Entries due in slots without any event (before the next event's slot): the token's state
        is unchanged there, so the entry fills on its last state."""
        for e in sorted(x for x in self.pending if x < before):
            for p in self.pending.pop(e):
                self._enter(p, e, [])

    def _end_slot(self, slot, by_key, block_time):
        self.slot_block_time = block_time
        if self.first_slot is None:
            self.first_slot = slot
        self._clock(slot, block_time)
        # 1. open positions on tokens with events in this slot
        for (venue, key), legs in by_key.items():
            for o in list(self.open.get(key, ())):
                if o.sim is None:  # graduated on our own buy: waits for its pool (grad_wait)
                    continue
                if o.opened < slot:
                    if o.flows is not None:  # the slot's legs before the position decides on them
                        o.flows.add(legs)
                    o.sim.on_slot(slot, legs)
                    self._settle_if_done(o, slot)
                    if not o.sim.done:
                        self._put_position(o)
        # 2. entries due in this slot
        for p in self.pending.pop(slot, []):
            self._enter(p, slot, by_key.get((p.venue, p.key), []))
        # graduations waiting for this pool's open + 1
        for (venue, key), legs in by_key.items():
            if venue == "pool" and key in self.grad_wait:
                self._graduation_exits(key, slot, legs)
        # 3. history
        for (venue, key), legs in by_key.items():
            if venue == "pool":
                self._see_pool(key, legs)
            dq = self.recent[key]
            dq.extend(legs)
            while dq and dq[0].slot < slot - HISTORY_SLOTS:
                dq.popleft()
            self.last_state[key] = legs[-1].post
            self.last_seen[key] = slot
        # 4. signals
        for (venue, key), legs in by_key.items():
            self._f7(venue, key, legs, slot)
            if venue == "pool" or venue == "curve":
                self._hour_index(venue, key, legs, slot)
        # 4b. the pools' farming windows (after the signals: a decision at t looks at [t - 1 h, t - 1]),
        # and the live watchlist's minute aggregates
        for (venue, key), legs in by_key.items():
            if venue == "pool":
                self._farm_add(key, legs)
                if self.watch is not None and self.minute is not None:
                    self.watch.trades(key, self.minute, legs)
        if hasattr(self.feed, "drain_history"):  # trades the feed has as history only (a recorder backfill)
            for pool, legs in self.feed.drain_history():
                self._see_pool(pool, legs, backfill=True)
                self._farm_add(pool, legs)
                if self.watch is not None and self.minute is not None:
                    self.watch.history(pool, legs)
        # 5. time passes for tokens without events in this slot
        active = {k for _, k in by_key}
        for key, opens in list(self.open.items()):
            for o in list(opens):
                if key not in active and o.opened < slot and o.sim is not None:
                    o.sim.advance(slot)
                    self._settle_if_done(o, slot)
        for pool in list(self.grad_wait):
            if ("pool", pool) not in by_key:
                self._graduation_exits(pool, slot, [])
        self.last_slot = slot
        self._keep_pools()

    # ------------------------------------------------------------------ clock, risk, heartbeat

    def _clock(self, slot, block_time):
        if block_time is None:
            return
        minute = block_time // 60
        if self.watch is not None:
            self.watch.slot(slot, minute)
            if minute != self.minute:
                self.watch.minute(minute, slot)
        day = _day(block_time)
        if self.day != day:
            if self.day is not None:
                self._close_day(self.day)
            self.day = day
            for g in self.groups:
                g.day_loss = 0
        if minute != self.minute:
            gap = slot - self.last_slot - 1 if self.last_slot is not None else 0
            self.j.put("heartbeat", minute=minute, day=day, last_slot=self.last_slot, last_event=self.last_event,
                       events=self.events_seen, open_positions=sum(len(v) for v in self.open.values()),
                       gap_slots=max(0, gap), note="")
            self.minute = minute
            for opens in self.open.values():
                for o in opens:
                    self._put_position(o)
            self._write_wallet(block_time)
            self._load_risk()
            self._live_minute(minute)
            self.j.commit()
        if self.last_slot is not None and slot - self.last_slot > GAP_SLOTS:
            self.j.put("heartbeat", minute=minute, day=day, last_slot=self.last_slot, last_event=self.last_event,
                       events=self.events_seen, open_positions=sum(len(v) for v in self.open.values()),
                       gap_slots=slot - self.last_slot - 1, note=f"feed gap {self.last_slot}..{slot}")

    def _live_minute(self, minute):
        """Live feeds: journal the recorder's data usage, and keep the pools of open positions on
        the recorder's watchlist (the watchlist itself comes from LAB research's builder)."""
        if hasattr(self.feed, "drain_usage"):
            for u in self.feed.drain_usage():
                m = u.get("minute", minute)
                self.j.put("usage", minute=m, day=_day(m * 60), stream=u.get("stream"), bytes=u.get("bytes"),
                           messages=u.get("messages"), seq=u.get("seq"), note=u.get("note"))
        self._keep_pools(force=True)

    def _keep_pools(self, force=False):
        """Pools of open and pending positions for the recorder (open-pools.json): every minute and
        as soon as the set changes, so a pool leaving the watchlist keeps its transactions."""
        if not hasattr(self.feed, "keep_pools"):
            return
        pools = sorted({o.p.key for v in self.open.values() for o in v if o.p.venue == "pool"}
                       | {p.key for v in self.pending.values() for p in v if p.venue == "pool"}
                       | set(self.grad_wait))
        if force or pools != self.kept:
            self.kept = pools
            self.feed.keep_pools(pools)

    def _load_risk(self):
        for g in self.groups:
            if self.ignore_risk:
                g.risk, g.risk_reason = R.Risk(0.5, 10**9, 1e12, 1e12, False, ("F7",)), None
            else:
                g.risk, g.risk_reason = R.load(g.risk_path)

    def _close_day(self, day):
        from .report import daily_report

        out = self.j.export_day(day)
        daily_report(self.j, day, watch_log=self.watch.log_path if self.watch is not None else None)
        if self.backup:
            dest = os.path.join(BACKUP, os.path.basename(self.j.dir.rstrip("/")), day)
            os.makedirs(dest, exist_ok=True)
            for f in os.listdir(out):
                shutil.copy2(os.path.join(out, f), dest)

    # ------------------------------------------------------------------ signals

    def _universe(self, venue, key):
        """(ok, creator, coin_creator, orientation) for a token or pool."""
        if venue == "curve":
            tok = self.feed.token(key)
            if tok is None or tok.mayhem or tok.quote_mint not in SOL_QUOTES:
                return False, None, None, None
            if not (self.burn_slot <= tok.create_slot < self.dev[1]):
                return False, None, None, None
            return True, tok.creator, None, "curve"
        p = self.feed.pool(key)
        if p is None or p.mayhem:
            return False, None, None, None
        tok = self.feed.token(p.mint)
        return True, (tok.creator if tok else p.coin_creator), p.coin_creator, p.orientation

    def _farm_add(self, pool, legs):
        p = self.feed.pool(pool)
        if p is None:
            return
        xs = self.farm_win[pool]
        for l in legs:
            if l.kind not in ("buy", "sell"):
                continue
            _, amount, _ = sol_side(l, p.orientation)
            item = (l.slot, l.order, amount, amount if l.farmer else 0)
            i = bisect.bisect_left(xs, item)
            if i < len(xs) and xs[i][:2] == item[:2]:
                continue  # the same trade twice (a backfill overlapping the recorded transactions)
            xs.insert(i, item)
        if xs and xs[0][0] < xs[-1][0] - 2 * self.clock.slots(F6_FARM_MS, xs[-1][0]):
            del xs[:bisect.bisect_left(xs, (xs[-1][0] - self.clock.slots(F6_FARM_MS, xs[-1][0]),))]

    def _pool_flagged(self, pool, t):
        """The backtest's farming flag (strategies.f7_signals_sql): farmers made more than
        F6_FARM_SHARE of the pool's SOL volume over slots [t - 1 h, t - 1]."""
        xs = self.farm_win.get(pool, [])
        vol = farm = 0
        for slot, _, a, f in xs[bisect.bisect_left(xs, (t - self.clock.slots(F6_FARM_MS, t),)):]:
            if slot >= t:
                break
            vol += a
            farm += f
        return farm > F6_FARM_SHARE * vol

    def _f7(self, venue, key, legs, t):
        if not (self.dlo <= t <= self.dhi):
            return
        ok, creator, coin_creator, orientation = self._universe(venue, key)
        if not ok:
            return
        seg = venue if venue == "curve" else f"pool_{orientation}"
        groups = [g for g in self.groups if g.segments is None or seg in g.segments]
        if not groups:
            return
        trig = None
        for l in legs:  # the slot's first event that passes the event-local trigger conditions
            if venue == "curve":
                pre_rq = l.post.rq + l.q if not l.is_buy else None
                if (not l.is_buy and l.q >= max(3 * SOL, 0.04 * (l.post.vq + l.q)) and l.trader != creator
                        and l.trader != MAYHEM_AGENT and 20 * SOL <= pre_rq <= 75 * SOL):
                    trig = (l, pre_rq)
                    break
            elif l.kind in ("buy", "sell"):
                tb, amount, depth = sol_side(l, orientation)
                if (not tb and amount >= max(3 * SOL, 0.04 * depth) and depth >= 150 * SOL
                        and l.trader != (creator or "") and l.trader != (coin_creator or "")):
                    trig = (l, depth)
                    break
        if trig is None:
            return
        view = RecentView(self.recent[key], t, orientation if venue == "pool" else "normal")
        if not f7_signal(view, venue, 0, creator, clock=self.clock):
            return
        leg, depth = trig
        farm = "n/a" if venue == "curve" else ("flagged" if self._pool_flagged(key, t) else "organic")
        for g in groups:
            lat = self._latency(leg, g)
            for name, params in g.variants:
                self._signal("F7", name, key, venue, orientation, t, params, int(depth), leg.trader, farm, lat, group=g)
            for mkey in self._n2(venue, orientation, key, t, depth, g.n2_k):
                for name, params in g.variants:
                    self._signal("N2_F7", name, mkey, venue, orientation, t, params, int(depth), None, farm, lat, group=g)

    def _latency(self, leg, group=None):
        """Measured latency of the trigger transaction (live only; replay: None), next to the
        simulated d: block time of slot t, local receive time (and the server's created_at), receive
        minus block time, the segment's close (written_unix: when the engine can first see it) minus
        block time, and d in ms. block_time and written_unix have a 1 s resolution, so one signal is
        +-1 s; the report's medians and p90 over many signals are the measure. Reporting only."""
        a = self.slot_arrivals.get((leg.slot, leg.tx))
        bt = self.slot_block_time
        if a is None or bt is None or (a[0] is None and a[2] is None):
            return None
        received, created, written = a
        return {"block_time": bt, "received_unix_ns": received, "created_unix_ns": created,
                "latency_ms": None if received is None else round(received / 1e6 - bt * 1000),
                "segment_written_unix": written, "segment_lag_ms": None if written is None else (written - bt) * 1000,
                "d_ms": round((self._d_at(leg.slot) if group is None else self._d_at(leg.slot, group))
                              * self.clock.ms_per_slot(leg.slot))}

    def _d_at(self, t, group=None):
        """Entry and exit delay in slots of a decision at t: the group's d, or d_ms in the clock's slots at t
        (Clock.slots: rounded, at least 1). K3's d_ms = 600 (owner decision 2026-10-14) is 2 slots at
        266-316 ms (the history, the pre-registered d = 2) and 3 slots at 200-240 ms (epoch 1053 on)."""
        g = self if group is None else group  # the engine's own d / d_ms are the primary group's
        return self.clock.slots(g.d_ms, t) if g.d_ms else g.d

    def _sweep_history(self, before):
        """Hourly: drop the recent legs and last state of tokens without an event since `before` and
        with nothing open, pending or waiting for a graduation (bounded memory in a live run)."""
        keep = set(self.open) | {p.key for v in self.pending.values() for p in v} | set(self.grad_wait)
        keep |= {o.p.key for v in self.grad_wait.values() for o in v}
        for k in [k for k, s in self.last_seen.items() if s < before and k not in keep]:
            self.recent.pop(k, None)
            self.last_state.pop(k, None)
            del self.last_seen[k]
        for k in [k for k, dq in self.recent.items() if not dq and k not in self.last_seen]:
            del self.recent[k]  # empty entries left by lookups

    def _hour_index(self, venue, key, legs, slot):
        h = self.clock.slots(HOUR_MS, slot)
        hour = slot - slot % h
        if hour != self.hour:
            self.hour, self.hour_events = hour, defaultdict(list)
            n = self.clock.slots(F6_FARM_MS, slot)  # pools quiet for an hour leave the farming windows
            for pool in [p for p, xs in self.farm_win.items() if not xs or xs[-1][0] < slot - n]:
                del self.farm_win[pool]
            self._sweep_history(slot - max(HISTORY_SLOTS, h))
            old = slot - self.clock.slots(POOL_SEEN_KEEP_MS, slot)
            for pool in [p for p, last in self.pool_last.items() if last < old]:
                del self.pool_last[pool]
                self.pool_seen.pop(pool, None)
                self.backfilled.discard(pool)
        if venue == "curve":  # N2 universe as in the backtest: non-mayhem SOL-market tokens
            tok = self.feed.token(key)
            if tok is not None and not tok.mayhem and tok.quote_mint in SOL_QUOTES:
                self.hour_events[("curve", "curve")].append((key, slot, legs[-1].post.rq))
        else:
            p = self.feed.pool(key)
            if p is not None and not p.mayhem:
                self.hour_events[("pool", p.orientation)].append((key, slot, sol_side(legs[-1], p.orientation)[2]))

    def _n2(self, venue, orientation, key, t, depth, k=None):
        """N2 shadow: k (the primary group's n2_k) random tokens of the same venue (and pool orientation) that
        traded this hour at a similar depth (curve +-5 SOL real, pool +-20% real SOL), deterministic per trigger."""
        k = self.n2_k if k is None else k
        if not k:
            return []
        cands = self.hour_events.get((venue, orientation), [])
        lo, hi = (depth - 5 * SOL, depth + 5 * SOL) if venue == "curve" else (0.8 * depth, 1.2 * depth)
        keys = sorted({k for k, _, dep in cands if k != key and lo <= dep <= hi})
        if not keys:
            return []
        seed = np.uint64(_seed("paper-N2", key, t))
        mixed = _mix(np.array([_seed(k) for k in keys], dtype=np.uint64) ^ seed)
        return [keys[i] for i in np.argsort(mixed, kind="stable")[:k]]

    def _signal(self, family, variant, key, venue, orientation, t, params, depth, trader, farm=None, lat=None, group=None):
        """farm: the trigger's label (an N2 signal carries its F7 trigger's); lat: its arrival (_latency);
        group: the variant group (None: the primary group)."""
        g = group or self.primary
        b = self.busy.get((family, variant, key))
        seed = getattr(self.feed, "farm_seed", None)
        complete = None if seed is None else int(t >= seed["complete_from"])
        window = self._farm_window_complete(key, t) if venue == "pool" else None
        row = dict(family=family, variant=variant, key=key, t=t, day=self.day, venue=venue, orientation=orientation,
                   depth=depth, trigger_trader=trader, farm=farm, **(lat or {}), farm_complete=complete,
                   pool_seen_slot=self.pool_seen.get(key) if venue == "pool" else None, farm_window_complete=window)
        if g.farm == "organic" and farm == "flagged":
            return self.j.put("signals", **row, status="skipped", reason="farm flagged")
        if g.require_farm_complete and complete == 0:
            return self.j.put("signals", **row, status="skipped", reason="farm history incomplete")
        if g.require_farm_complete and window == 0:
            return self.j.put("signals", **row, status="skipped", reason="farm window incomplete")
        if b == "open" or (b is not None and t <= b):
            return self.j.put("signals", **row, status="skipped", reason="busy")
        reason = self._risk_block(family, key, t, g)
        if reason:
            return self.j.put("signals", **row, status="skipped", reason=reason)
        self.busy[(family, variant, key)] = "open"
        d = self._d_at(t, g)
        self.pending[t + d].append(Pending(family, variant, key, venue, orientation, t, t + d, params, depth,
                                           None if g is self.primary else g.name))
        self.j.put("signals", **row, status="entered", reason=None)

    def _farm_window_complete(self, pool, t):
        """1 when the pool's trades over the farm label's hour [t - 1 h, t) are all in the feed: a feed of every
        pool (replay) from an hour before t on; live, a pool seen for an hour, or followed since its creation
        (a backfill from the creation slot, or first seen within SINCE_CREATION_MS of it); else 0."""
        hour = self.clock.slots(F6_FARM_MS, t)
        if getattr(self.feed, "all_pools", False):
            return int(self.first_slot is not None and t - self.first_slot >= hour)
        seen = self.pool_seen.get(pool)
        if seen is None:
            return 0
        if t - seen >= hour or pool in self.backfilled:
            return 1
        info = self.feed.pool(pool)
        created = getattr(info, "create_slot", None)
        return int(created is not None and seen - created <= self.clock.slots(SINCE_CREATION_MS, t))

    def _see_pool(self, pool, legs, backfill=False):
        if not legs:
            return
        lo, hi = min(l.slot for l in legs), max(l.slot for l in legs)
        self.pool_seen[pool] = min(self.pool_seen.get(pool, lo), lo)
        self.pool_last[pool] = max(self.pool_last.get(pool, hi), hi)
        if backfill:
            self.backfilled.add(pool)

    def _risk_block(self, family, key=None, t=None, group=None):
        """The group's risk limits (its own file, open positions, exposure, daily loss and pool cooldown)."""
        g = group or self.primary
        if g.risk is None:
            return g.risk_reason or "risk config missing"
        if g.risk.kill_switch:
            return "kill switch"
        if family.startswith("N2_"):
            return None  # the shadow control is not limited (except by the kill switch)
        if family not in g.risk.families:
            return "family not enabled"
        if key is not None and t is not None and g.cool.get(key, -1) > t:
            return "pool cooldown"
        opens = [o for v in self.open.values() for o in v if not o.p.family.startswith("N2_") and self._group(o.p) is g]
        pend = [p for v in self.pending.values() for p in v if not p.family.startswith("N2_") and self._group(p) is g]
        if len(opens) + len(pend) >= g.risk.max_open_positions:
            return "max open positions"
        exposure = sum(o.r.cost for o in opens) + g.risk.size_lamports * (len(pend) + 1)
        if exposure > g.risk.max_exposure_sol * 1e9:
            return "max exposure"
        if -g.day_loss >= g.risk.daily_loss_limit_sol * 1e9:
            return "daily loss limit"
        return None

    # ------------------------------------------------------------------ positions

    def _hist_state_fn(self, key, q=None):
        """Historical (no-us) state at our insertion point in a fill slot, for hist_mid_exit."""
        q = self.q if q is None else q

        def f(slot, slot_legs):
            if slot_legs:
                idx = _insertion_index(slot_legs, slot, q)
                if idx > 0:
                    return slot_legs[idx - 1].post
            return self.last_state.get(key)

        return f

    def _enter(self, p, slot, slot_legs):
        g = self._group(p)
        if not self.ignore_risk:
            # The risk file can change between the signal and the entry: re-read it, and enter only
            # with a valid file and the kill switch off (an invalid file opens nothing, N2 included).
            self._load_risk()
            why = (g.risk_reason or "risk config missing") if g.risk is None else ("kill switch" if g.risk.kill_switch else None)
            if why:
                self.j.put("orders", family=p.family, variant=p.variant, key=p.key, t=p.t, day=self.day, entry_slot=slot,
                           size=0, status="skipped", reason=f"at entry: {why}")
                self.busy[(p.family, p.variant, p.key)] = p.t
                return
        size = g.risk.size_lamports if (g.risk and not p.family.startswith("N2_")) else int(0.5 * SOL)
        if self.ignore_risk:
            size = int(g.risk.size_sol * SOL)
        flows = None
        if isinstance(p.params[0], str):  # an exit variant (kind, x, sl[, cap_ms]): the backtest's own rule
            flows = LiveFlowIndex(p.orientation) if p.params[0] in FLOW_KINDS else None
            rule = f7_exit_rule(p.params, self.scn, p.t, self.clock, flows)
        else:
            tp, T = p.params
            rule = tp_sl_time(tp, -0.08, self.clock.slots(ms(T), p.t), self.scn)
        if p.venue == "curve":
            hist = self.feed.curve_history(p.key, slot)
            tok = self.feed.token(p.key)
            stream = SimpleNamespace(mint=p.key, curve=hist, pool_legs=[], pool_open_slot=None,
                                     complete_slot=tok.complete_slot if tok else None, orientation="normal")
            holder = {}
            r, sim, i = replay.open_curve_position(stream, p.t, size, p.entry_slot - p.t, g.q, rule, self.tol, self.tau,
                                                   on_complete=self._on_complete(p, holder, tok),
                                                   hist_state=self._hist_state_fn(p.key, g.q))
            legs = hist
        else:
            legs = [l for l in self.recent[p.key]] + list(slot_legs)
            if not self.recent[p.key] and p.key in self.last_state:  # events older than the recent window
                st = self.last_state[p.key]
                legs.insert(0, SimpleNamespace(slot=self.last_seen[p.key], tx=-1, kind="state", pre=st, post=st))
            stream = SimpleNamespace(mint=p.key, pool_legs=legs, orientation=p.orientation)
            if flows is not None:  # the pool's legs up to and with the entry slot, then each slot's (_end_slot)
                flows.add(legs)
            r, sim, i = replay.open_pool_position(stream, p.t, size, p.entry_slot - p.t, g.q, rule, self.tol, self.tau,
                                                  hist_state=self._hist_state_fn(p.key, g.q))
        status = "skipped" if r.skipped else ("entry_failed" if r.entry_failed else "filled")
        self.j.put("orders", family=p.family, variant=p.variant, key=p.key, t=p.t, day=self.day, entry_slot=slot,
                   size=size, status=status, reason=r.exit_reason if status != "filled" else None)
        if status != "filled":
            if r.entry_failed:  # the transaction landed and failed: it costs the failed-tx fee
                pnl, _, _ = settle(r, self.scn)
                self._book_loss(p, pnl)
                self._wallet_tx(p, "buy", slot, pnl, settled=pnl)
                self.j.put("exits", family=p.family, variant=p.variant, key=p.key, t=p.t, day=self.day, entry_slot=slot,
                           exit_decision_slot=None, exit_slot=None, exit_reason=r.exit_reason, tokens=0, cost=0, proceeds=0,
                           pnl=pnl, ret=None, exit_attempts=0, cf_graduation=0, seed_pool=0, vault_capped=0,
                           reverted_direct=0, reverted_router=0, sells_dropped=0, sells_scaled=0, **_windows(rule))
            self.busy[(p.family, p.variant, p.key)] = p.t
            return
        self.j.put("fills", family=p.family, variant=p.variant, key=p.key, t=p.t, side="buy", day=self.day, slot=slot,
                   tokens=r.tokens, quote=r.cost)
        o = Open(p, r, sim, slot, rule=rule, flows=flows)
        if p.venue == "curve":
            holder["open"] = o
            if "grad" in holder:  # graduated on our own buy: wait for the pool
                o.grad = holder["grad"]
                self.grad_wait[o.grad["pool"]].append(o)
        self._put_position(o)
        self._wallet_tx(p, "buy", slot, -self.scn.entry_total(r.cost))
        if sim is None:  # graduated on our own buy
            if o.grad:
                self.open[p.key].append(o)
            else:
                self._settle_if_done(o, slot, force=True)
            return
        self.open[p.key].append(o)
        a = bisect.bisect_left([l.slot for l in legs], slot)  # first event of the entry slot
        if i < len(legs):
            sim.on_slot(slot, legs[a:], start=i - a)
        self._settle_if_done(o, slot)

    def _on_complete(self, p, holder, tok):
        """Curve completed while we hold: sell at the pool's open + 1 once it trades, or in the
        standard seed pool when the token has no pool."""

        def on_complete(slot, finish):
            r = finish()  # the position's Result (finish closes the PositionSim)
            # What the feed knows now: a completion or migration learned after the entry (live, a
            # later segment) must not send the sale into the seed pool.
            tok_now = self.feed.token(p.key) or tok
            if tok_now is None or tok_now.complete_slot is None or slot < tok_now.complete_slot:
                r.cf_graduation = True
            r.exit_reason = "graduation"
            pool = tok_now.pool if tok_now else None
            info = self.feed.pool(pool) if pool else None
            if info is None or info.create_slot is None:
                r.seed_pool, r.exit_decision_slot, r.exit_slot = True, slot, slot + 1
                f = V.pool_sell(replay.SEED_POOL, r.tokens)
                r.proceeds = f.trader_quote if f else 0
                r.hist_mid_exit = replay._price(replay.SEED_POOL)
                r.exit_attempts += 1
            else:
                holder["grad"] = {"pool": pool, "open_slot": info.create_slot}
                if "open" in holder:
                    holder["open"].grad = holder["grad"]
                    self.grad_wait[pool].append(holder["open"])
            return r

        return on_complete

    def _graduation_exits(self, pool, slot, legs):
        for o in list(self.grad_wait.get(pool, ())):
            target = o.grad["open_slot"] + 1
            if slot < target:
                continue
            r = o.r
            if slot == target and legs:
                idx = _insertion_index(legs, target, self.q)
                pstate = legs[idx - 1].post if idx > 0 else self.last_state.get(pool)
            else:
                pstate = self.last_state.get(pool)
            r.exit_decision_slot, r.exit_slot = o.grad["open_slot"], target
            f = V.pool_sell_capped(pstate, r.tokens) if pstate is not None else None
            r.proceeds = f.trader_quote if f else 0
            r.vault_capped = f is not None and f.tokens < r.tokens
            r.hist_mid_exit = replay._price(pstate)
            r.exit_attempts += 1
            self.grad_wait[pool].remove(o)
            o.grad = {}
            self._settle_if_done(o, slot, force=True)
        if not self.grad_wait.get(pool):
            self.grad_wait.pop(pool, None)

    def _settle_if_done(self, o, slot, force=False):
        if not force and (o.sim is None or not o.sim.done):
            return
        if o.grad:  # waiting for the pool to open
            return
        p, r = o.p, o.r
        pnl, ret, _ = settle(r, self.scn)
        self.j.put("fills", family=p.family, variant=p.variant, key=p.key, t=p.t, side="sell", day=self.day,
                   slot=r.exit_slot, tokens=r.tokens, quote=r.proceeds)
        self.j.put("exits", family=p.family, variant=p.variant, key=p.key, t=p.t, day=self.day, entry_slot=r.entry_slot,
                   exit_decision_slot=r.exit_decision_slot, exit_slot=r.exit_slot, exit_reason=r.exit_reason,
                   tokens=r.tokens, cost=r.cost, proceeds=r.proceeds, pnl=pnl, ret=ret, exit_attempts=r.exit_attempts,
                   cf_graduation=int(r.cf_graduation), seed_pool=int(r.seed_pool), vault_capped=int(r.vault_capped),
                   reverted_direct=r.reverted_direct, reverted_router=r.reverted_router, sells_dropped=r.sells_dropped,
                   sells_scaled=r.sells_scaled, **_peak(r), **_windows(o.rule))
        self.j.delete("positions", family=p.family, variant=p.variant, key=p.key, t=p.t)
        self._book_loss(p, pnl)
        self._wallet_tx(p, "sell", r.exit_slot or slot, pnl + self.scn.entry_total(r.cost) if r.tokens else pnl, settled=pnl)
        g = self._group(p)
        if (not p.family.startswith("N2_") and g.risk is not None and g.risk.pool_cooldown_h > 0 and r.tokens
                and (r.proceeds or 0) < 0.1 * r.cost):  # a total loss: the pool is closed for a while (for the group)
            end = (r.exit_slot or slot) + self.clock.slots(g.risk.pool_cooldown_h * 3_600_000, slot)
            g.cool[p.key] = max(g.cool.get(p.key, 0), end)
        self.busy[(p.family, p.variant, p.key)] = r.exit_slot or p.t
        if o in self.open.get(p.key, ()):
            self.open[p.key].remove(o)
            if not self.open[p.key]:
                del self.open[p.key]

    def _put_position(self, o):
        """The open position with its mark: the last sale value the position computed (PositionSim's
        decision mark, gross), after the exit's costs, and the P&L it would give."""
        p, r = o.p, o.r
        mark_slot, mark = r.marks[-1] if r.marks else (None, None)
        net = self.scn.exit_net(mark) if mark is not None else None
        self.j.put("positions", family=p.family, variant=p.variant, key=p.key, t=p.t, entry_slot=r.entry_slot, cost=r.cost,
                   tokens=r.tokens, status="open", mark_proceeds=net, mark_slot=mark_slot,
                   mark_pnl=None if net is None else net - self.scn.entry_total(r.cost))

    def _wallet_tx(self, p, kind, slot, amount, settled=None):
        """The group's paper wallet (F7 only; the N2 shadow is not traded): 10 SOL start, every paper buy and sell."""
        if p.family.startswith("N2_"):
            return
        g = self._group(p)
        if settled is not None:
            g.wallet["realized"] += settled
        g.wallet["txs"].appendleft({"signature": None, "slot": slot, "time_unix": None, "kind": kind, "mint": p.key,
                                       "symbol": None, "amount_lamports": int(amount), "fee_lamports": self.scn.per_tx(),
                                       "tip_lamports": None, "status": "paper",
                                       "landed_slot_delta": p.entry_slot - p.t if kind == "buy" else None,
                                       "note": f"{p.family} {p.variant}"})
        g.wallet_dirty = True

    def _write_wallet(self, block_time=None):
        """Per group <journal dir>/<wallet_file> (wallet.json, wallet-h.json) in the UI's format
        (lab/ui/web/README.md), marked as paper: no address, no signatures; balance = 10 SOL + realized P&L -
        the group's open positions at entry cost."""
        for g in self.groups:
            if not g.wallet_dirty:
                continue
            open_cost = sum(self.scn.entry_total(o.r.cost) for v in self.open.values() for o in v
                            if not o.p.family.startswith("N2_") and self._group(o.p) is g)
            w = {"mode": "paper", "address": None, "slot": self.last_slot, "time_unix": block_time,
                 "balance_lamports": g.wallet["start"] + g.wallet["realized"] - open_cost, "txs": list(g.wallet["txs"]),
                 "note": "paper trading: no wallet, no transactions; amounts are the engine's simulated fills after costs"}
            path = os.path.join(self.j.dir, g.wallet_file)
            with open(path + ".tmp", "w") as f:
                json.dump(w, f)
            os.replace(path + ".tmp", path)
            g.wallet_dirty = False

    def _book_loss(self, p, pnl):
        if not p.family.startswith("N2_"):
            self._group(p).day_loss += pnl

    def _end_of_data(self):
        self._quiet_entries(float("inf"))
        for key, opens in list(self.open.items()):
            for o in list(opens):
                if o.sim is not None:  # a position waiting for its pool is settled below
                    o.sim.end()
                    self._settle_if_done(o, self.last_slot)
        for pool, waiting in list(self.grad_wait.items()):
            for o in list(waiting):
                r = o.r
                r.seed_pool, r.exit_slot = True, (r.exit_decision_slot or self.last_slot) + 1
                f = V.pool_sell(replay.SEED_POOL, r.tokens)
                r.proceeds = f.trader_quote if f else 0
                r.exit_attempts += 1
                o.grad = {}
                self._settle_if_done(o, self.last_slot, force=True)
        self.grad_wait.clear()
        self._write_wallet()
        if self.day:
            self._close_day(self.day)


def _peak(r):
    """The highest mark since the entry and its first slot (as tournament._peak)."""
    marks = getattr(r, "marks", None) or []
    if not marks:
        return {"peak_mark": None, "peak_slot": None}
    slot, mark = max(marks, key=lambda x: (x[1], -x[0]))
    return {"peak_mark": int(mark), "peak_slot": int(slot)}


def _windows(rule):
    """The exit rule's windows in slots: the flow window (flow exits) and the time cap."""
    return {"n_flow_slots": getattr(rule, "n_flow", None), "cap_slots": getattr(rule, "max_slots", None)}


def load_config(path):
    """(sha256, config) of a paper config file; risk_path is resolved next to the file."""
    import yaml

    with open(path, "rb") as f:
        raw = f.read()
    conf = yaml.safe_load(raw)
    missing = [k for k in ("segments", "farm", "variants", "q", "risk") if k not in conf]
    if ("d" in conf) == ("d_ms" in conf):
        missing.append("exactly one of d (slots) and d_ms (milliseconds)")
    elif "d_ms" in conf and not (isinstance(conf["d_ms"], int) and conf["d_ms"] > 0):
        missing.append(f"d_ms > 0 (got {conf['d_ms']!r})")
    if missing:
        raise SystemExit(f"paper config {path}: missing {', '.join(missing)}")
    if "groups" in conf:
        raise SystemExit(f"paper config {path}: groups beside the primary group come from a file of their own (--groups)")
    conf["risk_path"] = os.path.join(os.path.dirname(os.path.abspath(path)), conf["risk"])
    return hashlib.sha256(raw).hexdigest(), conf


def load_groups(path):
    """(sha256, conf) of a groups file (`groups:` name -> settings, e.g. paper/h-arms-2026-10-16.yaml): the further
    variant groups beside the primary group's config, whose file and sha stay as they are. Risk paths resolve next
    to the file."""
    import yaml

    with open(path, "rb") as f:
        raw = f.read()
    conf = yaml.safe_load(raw)
    here = os.path.dirname(os.path.abspath(path))
    if not isinstance(conf, dict) or not conf.get("groups"):
        raise SystemExit(f"groups file {path}: no `groups`")
    for name, g in conf["groups"].items():
        bad = [k for k in ("families", "segments", "farm", "variants", "q", "risk", "wallet") if k not in g]
        if ("d" in g) == ("d_ms" in g):
            bad.append("exactly one of d and d_ms")
        if g.get("families") not in (None, ["F7"]):
            bad.append("families [F7]")
        if g.get("wallet") in (None, "wallet.json"):
            bad.append("a wallet file of its own")
        if bad:
            raise SystemExit(f"groups file {path}: group {name}: {', '.join(bad)}")
        g["risk_path"] = os.path.join(here, g["risk"])
    return hashlib.sha256(raw).hexdigest(), conf


def config_groups(conf):
    """A groups file's groups (load_groups) as Group objects."""
    return [Group(name, list(g["variants"]), farm=g["farm"], segments=g["segments"], d=int(g.get("d", 1)),
                  d_ms=int(g["d_ms"]) if "d_ms" in g else None, q=float(g["q"]), n2_k=int(g.get("n2_k", 0)),
                  risk_path=g["risk_path"], wallet_file=g["wallet"], require_farm_complete=bool(g.get("require_farm_complete")))
            for name, g in (conf.get("groups") or {}).items()]


def groups_state(eng):
    """The groups as the journal keeps them (state 'groups'): name -> variants, farm, segments, risk, wallet."""
    return json.dumps({g.name: {"primary": g is eng.primary, "variants": [n for n, _ in g.variants], "farm": g.farm,
                                "segments": sorted(g.segments) if g.segments else None, "d": g.d, "d_ms": g.d_ms, "q": g.q,
                                "n2_k": g.n2_k, "risk": g.risk_path, "wallet": g.wallet_file,
                                "require_farm_complete": g.require_farm_complete} for g in eng.groups}, sort_keys=True)


def main(argv=None):
    from .feed import ReplayFeed

    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", type=int, help="replay: first slot")
    ap.add_argument("--to", dest="hi", type=int, help="replay: end slot (exclusive)")
    ap.add_argument("--live", help="live: the recorder's output directory (LiveFeed; tokens and pools from --store)")
    ap.add_argument("--catch-up", action="store_true", help="live: stop after the last complete segment")
    ap.add_argument("--no-watchlist", action="store_true",
                    help="live: do not write the recorder's watchlist-current.json (paper.watchlist_live)")
    ap.add_argument("--journal", required=True)
    ap.add_argument("--config", help="paper config (YAML, e.g. paper/k3-paper-2026-10-12.yaml): segments, farm, variants, "
                                     "d, q, n2_k and the risk file; its sha256 goes into the journal")
    ap.add_argument("--risk", default=os.path.join(os.path.dirname(__file__), "risk.yaml"))
    ap.add_argument("--groups", help="a groups file (e.g. paper/h-arms-2026-10-16.yaml): variant groups beside the config's "
                                     "(primary) group, with their own risk files and wallets; its sha256 goes into the journal")
    ap.add_argument("--store", default="/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")
    ap.add_argument("--d", type=int, help="entry and exit delay in slots (default 1)")
    ap.add_argument("--d-ms", type=int, help="the delay in milliseconds instead (> 0), in slots per epoch by the clock")
    ap.add_argument("--q", type=float, default=0.5)
    ap.add_argument("--variants", default="", help="e.g. 0.04:600 or flow300_H6 (default: the F7 grid)")
    ap.add_argument("--ignore-risk", action="store_true", help="no limits: replay parity runs only (refused with --config or --live)")
    ap.add_argument("--segments", default="", help="F7 only there, e.g. pool_reversed (default: everywhere)")
    ap.add_argument("--farm", choices=["all", "organic"], default="all", help="organic: skip triggers in farming pools")
    ap.add_argument("--no-backup", action="store_true")
    a = ap.parse_args(argv)
    if a.d is not None and a.d_ms is not None:
        ap.error("give the delay once: --d (slots) or --d-ms (milliseconds), not both")
    if a.d_ms is not None and a.d_ms <= 0:
        ap.error(f"--d-ms must be > 0, got {a.d_ms}")
    if a.ignore_risk and (a.config or a.live):
        # A paper run (a config, or live data) always has its risk limits.
        ap.error("--ignore-risk is for replay parity runs only: not with --config or --live")
    if a.live and not (a.store and os.path.isfile(a.store)):
        # Without the snapshot every pool created before the recorder started would be unknown.
        ap.error(f"--live needs a readable --store (the snapshot of tokens and pools; PAPER_STORE in live.env), got {a.store!r}")
    cfg_sha, groups, name = None, [], "K3"
    if a.config:
        cfg_sha, conf = load_config(a.config)
        given = [x for x in ("variants", "segments") if getattr(a, x)] + (["farm"] if a.farm != "all" else []) \
            + (["d"] if a.d is not None else []) + (["d-ms"] if a.d_ms is not None else [])
        if given:
            raise SystemExit(f"--config fixes the strategy: do not also pass {', '.join('--' + x for x in given)}")
        a.variants, a.segments, a.farm = ",".join(conf["variants"]), ",".join(conf["segments"]), conf["farm"]
        a.d, a.d_ms = int(conf.get("d", 1)), (int(conf["d_ms"]) if "d_ms" in conf else None)
        a.q, a.risk = float(conf["q"]), conf["risk_path"]
        n2_k = int(conf.get("n2_k", 1))
        name = str(conf.get("candidate", "K3"))
    else:
        n2_k = 1
        a.d = 1 if a.d is None else a.d
    groups_sha = None
    if a.groups:
        groups_sha, gconf = load_groups(a.groups)
        groups = config_groups(gconf)
    if a.live:
        from .live import LiveFeed

        feed = LiveFeed(a.live, store=a.store, follow=not a.catch_up, seed_farmers=True)
    elif a.lo is None or a.hi is None:
        raise SystemExit("give --from and --to (replay) or --live <recorder dir>")
    else:
        feed = ReplayFeed(a.store, a.lo, a.hi)
    clock = feed.clock if a.live else Clock.from_store(feed.con)
    variants = [v for v in a.variants.split(",") if v] or F7_GRID
    j = Journal(a.journal)
    if cfg_sha:
        j.state("config", os.path.abspath(a.config))
        j.state("config_sha256", cfg_sha)
        with open(a.risk, "rb") as f:
            j.state("risk_sha256_at_start", hashlib.sha256(f.read()).hexdigest())
    if groups_sha:
        j.state("groups_file", os.path.abspath(a.groups))
        j.state("groups_sha256", groups_sha)
        for g in groups:
            with open(g.risk_path, "rb") as f:
                j.state(f"risk_sha256_at_start:{g.name}", hashlib.sha256(f.read()).hexdigest())
    j.commit()
    watch = None
    if a.live and not a.no_watchlist:
        from .watchlist_live import LiveWatchlist

        watch = LiveWatchlist(feed, a.live, clock=feed.clock)
    eng = Engine(feed, j, a.risk, variants=variants, d=a.d, d_ms=a.d_ms, q=a.q, ignore_risk=a.ignore_risk, n2_k=n2_k,
                 clock=clock, backup=not a.no_backup, segments=[x for x in a.segments.split(",") if x] or None, farm=a.farm,
                 watchlist=watch, name=name, groups=groups)
    j.state("groups", groups_state(eng))
    # For the stop rule's basis (paper/stoprule.py assessment): live or replay, and the cost scenario the exits
    # were settled with.
    j.state("feed", json.dumps({"kind": "live" if a.live else "replay", "from": a.lo, "to": a.hi}))
    j.state("cost_scenario", json.dumps(asdict(eng.scn), sort_keys=True))
    j.commit()
    t0 = time.time()
    eng.run()
    print(f"{eng.events_seen} events, {time.time() - t0:.0f}s, journal {a.journal}")


if __name__ == "__main__":
    main()
