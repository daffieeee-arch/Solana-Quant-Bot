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
"""

import argparse
import bisect
import hashlib
import json
import os
import shutil
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
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
    F7_GRID,
    HOUR_MS,
    MAYHEM_AGENT,
    SOL,
    SOL_QUOTES,
    Clock,
    _mix,
    _seed,
    decision_bounds,
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


@dataclass
class Open:
    p: Pending
    r: object
    sim: object
    opened: int
    grad: dict = field(default_factory=dict)  # graduation: waiting for the pool to open


class Engine:
    def __init__(self, feed, journal, risk_path, *, variants=F7_GRID, d=1, q=0.5, tol=0.05, tau="empirical",
                 dev_bounds=None, n2_k=1, ignore_risk=False, cfg=None, clock=None, backup=True, segments=None,
                 farm="all", watchlist=None):
        """segments: trade F7 only there ('curve', 'pool_normal', 'pool_reversed'; None: everywhere);
        farm: 'organic' trades only triggers in pools that were not flagged for volume farming in the
        hour before t (the backtest's `farm` label), 'all' trades every trigger (the label is journaled);
        watchlist: a paper.watchlist_live.LiveWatchlist fed with the pool trades seen (live: it writes the
        recorder's watchlist-current.json every minute)."""
        if farm not in ("all", "organic"):
            raise ValueError(f"farm must be 'all' or 'organic', not {farm!r}")
        self.feed, self.j, self.risk_path = feed, journal, risk_path
        self.segments, self.farm, self.watch = (set(segments) if segments else None), farm, watchlist
        self.farm_win = defaultdict(list)  # pool -> sorted [(slot, order, sol, farmer sol)] of its trades, the last ~hour
        self.variants = [(tp, T) for tp, T in variants]
        self.d, self.q, self.tol, self.tau = d, q, tol, replay.Tau(tau)
        self.cfg = cfg or load_defaults()
        self.clock = clock or Clock()
        lo, hi = dev_bounds or feed.meta_bounds
        self.dev = (lo, hi)
        self.dlo, self.dhi = decision_bounds(self.cfg, lo, hi, self.clock)
        self.burn_slot = lo + self.clock.slots(ms(324_000), lo)
        self.n2_k, self.ignore_risk, self.backup = n2_k, ignore_risk, backup
        self.scn = Scenario.from_cfg("base", self.cfg)
        self.recent = defaultdict(deque)  # key -> recent legs
        self.last_state = {}  # key -> state after its last event
        self.last_seen = {}  # key -> slot of its last event
        self.open = defaultdict(list)  # key -> [Open]
        self.pending = defaultdict(list)  # entry slot -> [Pending]
        self.busy = {}  # (family, variant, key) -> 'open' | slot of the last exit
        self.hour = None
        self.hour_events = defaultdict(list)  # (venue, orientation) -> [(key, slot, depth)] this hour
        self.risk, self.risk_reason, self.risk_minute = None, "not loaded", None
        self.day, self.day_loss = None, 0
        self.minute, self.events_seen, self.last_event, self.last_slot = None, 0, None, None
        self.slot_block_time = None  # block time of the slot being decided (signal latency)
        self.slot_arrivals = {}  # (slot, tx_index) -> Event.arrival of the slot being decided (live only)
        self.seed_journaled = None  # the feed's farm_seed last written to the journal
        self.grad_wait = defaultdict(list)  # pool -> [Open] waiting for open + 1
        self.kept = None  # the pools last written for the recorder
        self.cool = {}  # pool -> slot until which no new entry (risk: pool_cooldown_h after a total loss)
        self.wallet = {"start": int(10 * SOL), "realized": 0, "txs": deque(maxlen=50)}  # paper wallet (wallet.json)

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
                # LiveFeed's farmer history: seeded from the store over [lo, hi), complete from slot
                # complete_from on (farm labels and the live watchlist's organic verdict before it lack history).
                self.j.state("farmer_seed", json.dumps(seed, sort_keys=True))
            self.events_seen += 1
            self.last_event = f"{ev.slot}:{ev.tx_index}:{ev.outer_ix}:{ev.inner_ix}"
        if cur is not None:
            self.slot_arrivals = arr
            self._end_slot(cur, by_key, bt)
        self._end_of_data()
        self.j.commit()

    def _quiet_entries(self, before):
        """Entries due in slots without any event (before the next event's slot): the token's state
        is unchanged there, so the entry fills on its last state."""
        for e in sorted(x for x in self.pending if x < before):
            for p in self.pending.pop(e):
                self._enter(p, e, [])

    def _end_slot(self, slot, by_key, block_time):
        self.slot_block_time = block_time
        self._clock(slot, block_time)
        # 1. open positions on tokens with events in this slot
        for (venue, key), legs in by_key.items():
            for o in list(self.open.get(key, ())):
                if o.opened < slot:
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
                self._farm_add(pool, legs)
                if self.watch is not None and self.minute is not None:
                    self.watch.history(pool, legs)
        # 5. time passes for tokens without events in this slot
        active = {k for _, k in by_key}
        for key, opens in list(self.open.items()):
            for o in list(opens):
                if key not in active and o.opened < slot:
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
            self.day, self.day_loss = day, 0
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
        if self.ignore_risk:
            self.risk, self.risk_reason = R.Risk(0.5, 10**9, 1e12, 1e12, False, ("F7",)), None
            return
        self.risk, self.risk_reason = R.load(self.risk_path)

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
        if self.segments is not None and (venue if venue == "curve" else f"pool_{orientation}") not in self.segments:
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
        lat = self._latency(leg)
        for tp, T in self.variants:
            self._signal("F7", f"tp{int(tp * 100)}_T{T}", key, venue, orientation, t, (tp, T), int(depth), leg.trader, farm,
                         lat)
        for mkey in self._n2(venue, orientation, key, t, depth):
            for tp, T in self.variants:
                self._signal("N2_F7", f"tp{int(tp * 100)}_T{T}", mkey, venue, orientation, t, (tp, T), int(depth), None,
                             farm, lat)

    def _latency(self, leg):
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
                "d_ms": round(self.d * self.clock.ms_per_slot(leg.slot))}

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
        if venue == "curve":  # N2 universe as in the backtest: non-mayhem SOL-market tokens
            tok = self.feed.token(key)
            if tok is not None and not tok.mayhem and tok.quote_mint in SOL_QUOTES:
                self.hour_events[("curve", "curve")].append((key, slot, legs[-1].post.rq))
        else:
            p = self.feed.pool(key)
            if p is not None and not p.mayhem:
                self.hour_events[("pool", p.orientation)].append((key, slot, sol_side(legs[-1], p.orientation)[2]))

    def _n2(self, venue, orientation, key, t, depth):
        """N2 shadow: n2_k random tokens of the same venue (and pool orientation) that traded this hour
        at a similar depth (curve +-5 SOL real, pool +-20% real SOL), deterministic per trigger."""
        if not self.n2_k:
            return []
        cands = self.hour_events.get((venue, orientation), [])
        lo, hi = (depth - 5 * SOL, depth + 5 * SOL) if venue == "curve" else (0.8 * depth, 1.2 * depth)
        keys = sorted({k for k, _, dep in cands if k != key and lo <= dep <= hi})
        if not keys:
            return []
        seed = np.uint64(_seed("paper-N2", key, t))
        mixed = _mix(np.array([_seed(k) for k in keys], dtype=np.uint64) ^ seed)
        return [keys[i] for i in np.argsort(mixed, kind="stable")[:self.n2_k]]

    def _signal(self, family, variant, key, venue, orientation, t, params, depth, trader, farm=None, lat=None):
        """farm: the trigger's label (an N2 signal carries its F7 trigger's); lat: its arrival (_latency)."""
        b = self.busy.get((family, variant, key))
        row = dict(family=family, variant=variant, key=key, t=t, day=self.day, venue=venue, orientation=orientation,
                   depth=depth, trigger_trader=trader, farm=farm, **(lat or {}))
        if self.farm == "organic" and farm == "flagged":
            return self.j.put("signals", **row, status="skipped", reason="farm flagged")
        if b == "open" or (b is not None and t <= b):
            return self.j.put("signals", **row, status="skipped", reason="busy")
        reason = self._risk_block(family, key, t)
        if reason:
            return self.j.put("signals", **row, status="skipped", reason=reason)
        self.busy[(family, variant, key)] = "open"
        self.pending[t + self.d].append(Pending(family, variant, key, venue, orientation, t, t + self.d, params, depth))
        self.j.put("signals", **row, status="entered", reason=None)

    def _risk_block(self, family, key=None, t=None):
        if self.risk is None:
            return self.risk_reason or "risk config missing"
        if self.risk.kill_switch:
            return "kill switch"
        if family.startswith("N2_"):
            return None  # the shadow control is not limited (except by the kill switch)
        if family not in self.risk.families:
            return "family not enabled"
        if key is not None and t is not None and self.cool.get(key, -1) > t:
            return "pool cooldown"
        opens = [o for v in self.open.values() for o in v if not o.p.family.startswith("N2_")]
        pend = [p for v in self.pending.values() for p in v if not p.family.startswith("N2_")]
        if len(opens) + len(pend) >= self.risk.max_open_positions:
            return "max open positions"
        exposure = sum(o.r.cost for o in opens) + self.risk.size_lamports * (len(pend) + 1)
        if exposure > self.risk.max_exposure_sol * 1e9:
            return "max exposure"
        if -self.day_loss >= self.risk.daily_loss_limit_sol * 1e9:
            return "daily loss limit"
        return None

    # ------------------------------------------------------------------ positions

    def _hist_state_fn(self, key):
        """Historical (no-us) state at our insertion point in a fill slot, for hist_mid_exit."""

        def f(slot, slot_legs):
            if slot_legs:
                idx = _insertion_index(slot_legs, slot, self.q)
                if idx > 0:
                    return slot_legs[idx - 1].post
            return self.last_state.get(key)

        return f

    def _enter(self, p, slot, slot_legs):
        if not self.ignore_risk:
            # The risk file can change between the signal and the entry: re-read it, and enter only
            # with a valid file and the kill switch off (an invalid file opens nothing, N2 included).
            self._load_risk()
            why = (self.risk_reason or "risk config missing") if self.risk is None else ("kill switch" if self.risk.kill_switch else None)
            if why:
                self.j.put("orders", family=p.family, variant=p.variant, key=p.key, t=p.t, day=self.day, entry_slot=slot,
                           size=0, status="skipped", reason=f"at entry: {why}")
                self.busy[(p.family, p.variant, p.key)] = p.t
                return
        size = self.risk.size_lamports if (self.risk and not p.family.startswith("N2_")) else int(0.5 * SOL)
        if self.ignore_risk:
            size = int(self.risk.size_sol * SOL)
        tp, T = p.params
        rule = tp_sl_time(tp, -0.08, self.clock.slots(ms(T), p.t), self.scn)
        if p.venue == "curve":
            hist = self.feed.curve_history(p.key, slot)
            tok = self.feed.token(p.key)
            stream = SimpleNamespace(mint=p.key, curve=hist, pool_legs=[], pool_open_slot=None,
                                     complete_slot=tok.complete_slot if tok else None, orientation="normal")
            holder = {}
            r, sim, i = replay.open_curve_position(stream, p.t, size, self.d, self.q, rule, self.tol, self.tau,
                                                   on_complete=self._on_complete(p, holder, tok),
                                                   hist_state=self._hist_state_fn(p.key))
            legs = hist
        else:
            legs = [l for l in self.recent[p.key]] + list(slot_legs)
            if not self.recent[p.key] and p.key in self.last_state:  # events older than the recent window
                st = self.last_state[p.key]
                legs.insert(0, SimpleNamespace(slot=self.last_seen[p.key], tx=-1, kind="state", pre=st, post=st))
            stream = SimpleNamespace(mint=p.key, pool_legs=legs, orientation=p.orientation)
            r, sim, i = replay.open_pool_position(stream, p.t, size, self.d, self.q, rule, self.tol, self.tau,
                                                  hist_state=self._hist_state_fn(p.key))
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
                           reverted_direct=0, reverted_router=0, sells_dropped=0, sells_scaled=0)
            self.busy[(p.family, p.variant, p.key)] = p.t
            return
        self.j.put("fills", family=p.family, variant=p.variant, key=p.key, t=p.t, side="buy", day=self.day, slot=slot,
                   tokens=r.tokens, quote=r.cost)
        o = Open(p, r, sim, slot)
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
            if tok is None or tok.complete_slot is None or slot < tok.complete_slot:
                r.cf_graduation = True
            r.exit_reason = "graduation"
            pool = tok.pool if tok else None
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
                   sells_scaled=r.sells_scaled)
        self.j.delete("positions", family=p.family, variant=p.variant, key=p.key, t=p.t)
        self._book_loss(p, pnl)
        self._wallet_tx(p, "sell", r.exit_slot or slot, pnl + self.scn.entry_total(r.cost) if r.tokens else pnl, settled=pnl)
        if (not p.family.startswith("N2_") and self.risk is not None and self.risk.pool_cooldown_h > 0 and r.tokens
                and (r.proceeds or 0) < 0.1 * r.cost):  # a total loss: the pool is closed for a while
            end = (r.exit_slot or slot) + self.clock.slots(self.risk.pool_cooldown_h * 3_600_000, slot)
            self.cool[p.key] = max(self.cool.get(p.key, 0), end)
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
        """The paper wallet (F7 only; the N2 shadow is not traded): 10 SOL start, every paper buy and sell."""
        if p.family.startswith("N2_"):
            return
        if settled is not None:
            self.wallet["realized"] += settled
        self.wallet["txs"].appendleft({"signature": None, "slot": slot, "time_unix": None, "kind": kind, "mint": p.key,
                                       "symbol": None, "amount_lamports": int(amount), "fee_lamports": self.scn.per_tx(),
                                       "tip_lamports": None, "status": "paper",
                                       "landed_slot_delta": self.d if kind == "buy" else None,
                                       "note": f"{p.family} {p.variant}"})
        self._wallet_dirty = True

    def _write_wallet(self, block_time=None):
        """<journal dir>/wallet.json in the UI's format (lab/ui/web/README.md), marked as paper: no
        address, no signatures; balance = 10 SOL + realized P&L - open positions at entry cost."""
        if not getattr(self, "_wallet_dirty", False):
            return
        open_cost = sum(self.scn.entry_total(o.r.cost) for v in self.open.values() for o in v if not o.p.family.startswith("N2_"))
        w = {"mode": "paper", "address": None, "slot": self.last_slot, "time_unix": block_time,
             "balance_lamports": self.wallet["start"] + self.wallet["realized"] - open_cost, "txs": list(self.wallet["txs"]),
             "note": "paper trading: no wallet, no transactions; amounts are the engine's simulated fills after costs"}
        path = os.path.join(self.j.dir, "wallet.json")
        with open(path + ".tmp", "w") as f:
            json.dump(w, f)
        os.replace(path + ".tmp", path)
        self._wallet_dirty = False

    def _book_loss(self, p, pnl):
        if not p.family.startswith("N2_"):
            self.day_loss += pnl

    def _end_of_data(self):
        self._quiet_entries(float("inf"))
        for key, opens in list(self.open.items()):
            for o in list(opens):
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


def load_config(path):
    """(sha256, config) of a paper config file; risk_path is resolved next to the file."""
    import yaml

    with open(path, "rb") as f:
        raw = f.read()
    conf = yaml.safe_load(raw)
    missing = [k for k in ("segments", "farm", "variants", "d", "q", "risk") if k not in conf]
    if missing:
        raise SystemExit(f"paper config {path}: missing {', '.join(missing)}")
    conf["risk_path"] = os.path.join(os.path.dirname(os.path.abspath(path)), conf["risk"])
    return hashlib.sha256(raw).hexdigest(), conf


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
    ap.add_argument("--store", default="/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")
    ap.add_argument("--d", type=int, default=1)
    ap.add_argument("--q", type=float, default=0.5)
    ap.add_argument("--variants", default="", help="e.g. 0.04:600 (default: the F7 grid)")
    ap.add_argument("--ignore-risk", action="store_true", help="no limits: replay parity runs only (refused with --config or --live)")
    ap.add_argument("--segments", default="", help="F7 only there, e.g. pool_reversed (default: everywhere)")
    ap.add_argument("--farm", choices=["all", "organic"], default="all", help="organic: skip triggers in farming pools")
    ap.add_argument("--no-backup", action="store_true")
    a = ap.parse_args(argv)
    if a.ignore_risk and (a.config or a.live):
        # A paper run (a config, or live data) always has its risk limits.
        ap.error("--ignore-risk is for replay parity runs only: not with --config or --live")
    if a.live and not (a.store and os.path.isfile(a.store)):
        # Without the snapshot every pool created before the recorder started would be unknown.
        ap.error(f"--live needs a readable --store (the snapshot of tokens and pools; PAPER_STORE in live.env), got {a.store!r}")
    cfg_sha = None
    if a.config:
        cfg_sha, conf = load_config(a.config)
        given = [x for x in ("variants", "segments") if getattr(a, x)] + (["farm"] if a.farm != "all" else [])
        if given:
            raise SystemExit(f"--config fixes the strategy: do not also pass {', '.join('--' + x for x in given)}")
        a.variants, a.segments, a.farm = ",".join(conf["variants"]), ",".join(conf["segments"]), conf["farm"]
        a.d, a.q, a.risk = int(conf["d"]), float(conf["q"]), conf["risk_path"]
        n2_k = int(conf.get("n2_k", 1))
    else:
        n2_k = 1
    if a.live:
        from .live import LiveFeed

        feed = LiveFeed(a.live, store=a.store, follow=not a.catch_up, seed_farmers=True)
    elif a.lo is None or a.hi is None:
        raise SystemExit("give --from and --to (replay) or --live <recorder dir>")
    else:
        feed = ReplayFeed(a.store, a.lo, a.hi)
    clock = feed.clock if a.live else Clock.from_store(feed.con)
    variants = [tuple(float(x) if i == 0 else int(x) for i, x in enumerate(v.split(":"))) for v in a.variants.split(",")
                if v] or F7_GRID
    j = Journal(a.journal)
    if cfg_sha:
        j.state("config", os.path.abspath(a.config))
        j.state("config_sha256", cfg_sha)
        with open(a.risk, "rb") as f:
            j.state("risk_sha256_at_start", hashlib.sha256(f.read()).hexdigest())
        j.commit()
    watch = None
    if a.live and not a.no_watchlist:
        from .watchlist_live import LiveWatchlist

        watch = LiveWatchlist(feed, a.live, clock=feed.clock)
    eng = Engine(feed, j, a.risk, variants=variants, d=a.d, q=a.q, ignore_risk=a.ignore_risk, n2_k=n2_k,
                 clock=clock, backup=not a.no_backup, segments=[x for x in a.segments.split(",") if x] or None, farm=a.farm,
                 watchlist=watch)
    t0 = time.time()
    eng.run()
    print(f"{eng.events_seen} events, {time.time() - t0:.0f}s, journal {a.journal}")


if __name__ == "__main__":
    main()
