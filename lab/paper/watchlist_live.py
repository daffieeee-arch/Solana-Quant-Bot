"""The live watchlist: LAB research's #180 state machine (wallets.watchlist) fed by the paper engine.

The engine hands every pool trade it sees (buy and sell legs, with the farmer flag) and the trades
the recorder backfilled for new probes. Per (pool, UTC minute) they become the rows the batch
measurement takes from the store (wallets.watchlist.aggregates_sql): trades, SOL volume, farmers'
SOL volume and the last trade's real SOL depth (before the trade). When a minute is over, every
minute up to it is stepped (silent ones too: the idle rules count them) and the list valid for the
next minute is written atomically to <recorder root>/watchlist-current.json (format v1, which the
recorder reads), plus one line per minute in watchlist-log.jsonl.

Limitation: the engine only sees what the recorder received: pools on the list, open positions,
probes (with their backfill from creation) and graduation and creation transactions. A pool that
is not received cannot enter by its own trades; F7 pools come in as probes (99% of F7 triggers are
in pools created the same day), M1 pools from their graduation. A pool that left the list is only
seen again as a probe, an open position or in a received transaction of another pool.
"""

import bisect
import json
import os
from collections import defaultdict

from backtest.strategies import Clock, sol_side
from wallets import watchlist as W

SOL = 1e9


class LiveWatchlist:
    def __init__(self, feed, root, rules=None, wallets=(), clock=None, log=True):
        self.feed, self.root = feed, root
        self.path = os.path.join(root, "watchlist-current.json")
        self.log_path = os.path.join(root, "watchlist-log.jsonl") if log else None
        self.wl = W.Watchlist(rules or W.Rules(), wallets)
        self.clock = clock
        self.rows = defaultdict(lambda: [0, 0.0, 0.0, None, None])  # (pool, minute) -> trades, sol, farmer sol, depth, order
        self.stepped = None  # the last minute stepped
        self.slots, self.minutes = [], []  # (slot, minute) of the slots seen: creation minutes of new pools
        self.added = set()
        self._pending = []  # pools learned by the feed, added at their creation slot
        self._static = None  # feeds that know all pools up front (replay): (create_slot, pool) sorted
        self._new_seen = 0
        self.history_dropped = 0  # backfill legs with no minute at all (before any slot was seen)

    # ------------------------------------------------------------------ pools

    def _minute_of(self, slot):
        i = bisect.bisect_right(self.slots, slot) - 1
        if i >= 0:
            return self.minutes[i]
        return None

    def _add(self, pool):
        """Put a pool in the state machine. A pool it pruned stays known while archived (a trade there
        restores its verdict); once its archive entry expired it is added again with a fresh state,
        as the batch measurement re-adds it (wallets.watchlist, #211)."""
        if pool in self.added and (pool in self.wl.pools or pool in getattr(self.wl, "archive", ())):
            return True
        p = self.feed.pool(pool)
        if p is None or p.quote_class not in ("sol", "reversed"):
            return False
        tok = self.feed.token(p.mint)
        mayhem = bool(p.mayhem or (tok is not None and tok.mayhem))
        grad = bool(tok is not None and tok.complete_slot is not None and tok.complete_slot == tok.create_slot)
        cmin = self._minute_of(p.create_slot) if p.create_slot is not None and self.slots and p.create_slot >= self.slots[0] else None
        self.wl.add_pool(W.PoolInfo(pool, p.mint, p.quote_class, p.orientation, mayhem, cmin, grad))
        self.added.add(pool)
        return True

    def _new_pools(self, upto_slot):
        """Pools created up to this slot (M1 pools are on the list from their creation, before any trade)."""
        new = getattr(self.feed, "new_pools", None)
        if new is not None:  # LiveFeed: the pools it learned, in order
            for pool in new[self._new_seen:]:
                p = self.feed.pool(pool)
                if p is not None:
                    self._pending.append((p.create_slot or 0, pool))
            self._new_seen = len(new)
            self._pending.sort()
            while self._pending and self._pending[0][0] <= upto_slot:
                self._add(self._pending.pop(0)[1])
            return
        if self._static is None:  # a replay knows every pool: add each at its creation
            pools = getattr(self.feed, "pools", {})
            start = self.slots[0] if self.slots else upto_slot
            self._static = sorted((p.create_slot, k) for k, p in pools.items() if p.create_slot is not None and p.create_slot >= start)
        i = bisect.bisect_right(self._static, (upto_slot, "￿"))
        for _, pool in self._static[:i]:
            self._add(pool)
        del self._static[:i]

    # ------------------------------------------------------------------ input

    def slot(self, slot, minute):
        if not self.slots or slot > self.slots[-1]:
            self.slots.append(slot)
            self.minutes.append(minute)
            if len(self.slots) > 200_000:  # ~14 h at 250 ms: enough for the 6 h M1 age
                del self.slots[:100_000], self.minutes[:100_000]

    def trades(self, pool, minute, legs, late=False):
        """Pool legs of one minute. late: history the recorder delivered after the minute (backfill)."""
        if not self._add(pool):
            return
        o = self.wl.pools[pool].orientation
        if late and self.stepped is not None and minute <= self.stepped:
            n = sol = farm = 0
            for l in legs:
                if l.kind in ("buy", "sell"):
                    _, a, _ = sol_side(l, o)
                    n, sol, farm = n + 1, sol + a / SOL, farm + (a / SOL if l.farmer else 0)
            self.wl.add_history(pool, minute, n, sol, farm)
            return
        r = self.rows[(pool, minute)]
        for l in legs:
            if l.kind not in ("buy", "sell"):
                continue
            _, a, depth = sol_side(l, o)
            r[0] += 1
            r[1] += a / SOL
            r[2] += a / SOL if l.farmer else 0
            if r[4] is None or l.order > r[4]:
                r[3], r[4] = depth / SOL, l.order

    def history(self, pool, legs):
        """Trades the recorder delivered after their minute (a probe's backfill), each in its own
        minute: a minute already stepped goes to the watchlist's history, a later one to its rows.
        A leg from before the first slot seen has no known block time: its minute is estimated
        back from the first one with the clock's slot time. Never the current minute by default."""
        clock = self.clock or Clock()
        for l in legs:
            m = self._minute_of(l.slot)
            if m is None:
                if not self.slots:
                    self.history_dropped += 1
                    continue
                m = self.minutes[0] - int((self.slots[0] - l.slot) * clock.ms_per_slot(l.slot) // 60_000)
            self.trades(pool, m, [l], late=True)

    def minute(self, minute, slot):
        """Minute `minute` begins (at `slot`): step every minute before it and write the list for it."""
        self._new_pools(slot)
        if self.stepped is None:
            have = [m for _, m in self.rows]
            self.stepped = min(have + [minute]) - 1
        if minute <= self.stepped + 1:
            return None
        members = None
        for m in range(self.stepped + 1, minute):
            rows = [(p, r[0], r[1], r[2], r[3]) for (p, mm), r in self.rows.items() if mm == m]
            members = self.wl.step(m, rows)
            for p, _ in [k for k in self.rows if k[1] == m]:
                del self.rows[(p, m)]
            self.stepped = m
        snap = self.wl.snapshot(valid_from_slot=slot, generated_slot=slot)
        snap["source"] = {"by": "paper.engine (live watchlist)", "received_pools_only": True}
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, self.path)
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"minute": minute, "slot": slot, "pools": sorted(members or {}),
                                    "counts": snap["counts"]}) + "\n")
        return members
