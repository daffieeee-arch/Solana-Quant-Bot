"""LiveFeed: paper-engine events from the live recorder (lab/recorder), as the replay's own legs.

The recorder (Rust, Triton Yellowstone) decodes every transaction with the extractor's code and
writes rolling segments in the OF1 chunk layout, one directory per segment:

  <root>/segments/<seq:09d>/
    pump/<Event>/*.parquet, pump_amm/<Event>/*.parquet   event rows: the extractor's columns
    blocks/*.parquet          slot, parent_slot, block_time, block_height, ... (block meta)
    arrivals/*.parquet        per transaction: slot, tx_index, signature, received_unix_ns, stream
    watchlist.json            the watchlist the transaction filter used for this segment
    _segment.json             written last: seq, first_slot, last_slot, usage, budget, complete

LiveFeed turns each segment's raw rows into the dev store's rows with the store's own SQL
(store.Chunk, _pool_trades, _pool_other; the curve select and the pool join mirror store.build),
computes the farmer flag from its own 24 h history exactly as the store does (window [t - 24 h,
t - 1 slot] per pool and wallet), and yields Events in chain order. Rows of a slot are only
released once a later slot was seen (lag), so a segment boundary inside a slot cannot reorder
events. Tokens and pools come from a store snapshot plus the ones created live.

Parity with ReplayFeed on the same slots is tested (tests/test_live.py).
"""

import glob
import heapq
import json
import os
import time
from collections import defaultdict, deque

import duckdb
import pyarrow as pa

from backtest import data, pda, streams
from backtest.strategies import Clock
from backtest.store import (DAY_MS, FARMER_MIN_TRADES, FARMER_RATIO, H, POOL_SCHEMA, Chunk, _pool_other,
                            _pool_trades)

from .feed import Event, Feed, PoolInfo, TokenInfo

WSOL = pda.WSOL
SOL_MARKETS = ("sol", "reversed")


def segment_dirs(root):
    """Complete segments in order: (seq, dir, meta)."""
    out = []
    for m in glob.glob(os.path.join(root, "segments", "*", "_segment.json")):
        with open(m) as f:
            meta = json.load(f)
        if meta.get("complete"):
            out.append((int(meta["seq"]), os.path.dirname(m), meta))
    return sorted(out)


def curve_select(c, mints_table):
    """store.build's curve rows for one chunk or segment (mints created in the data only)."""
    T = "pump/TradeEvent"
    trade = c.src(T)
    if not trade:
        return None
    ix_case = " ".join(f"WHEN '{d}' THEN '{n}'" for d, n in data.PUMP_IX.items())
    return f"""
      SELECT t.mint, t.slot, t.tx_index, t.outer_ix, t.inner_ix,
        CASE t.parent_ix_disc {ix_case} ELSE 'other' END AS variant,
        t.is_buy, t.token_amount{H} AS t, COALESCE(t.quote_amount, t.sol_amount){H} AS q,
        t.fee{H} AS fee, t.creator_fee{H} AS creator_fee, t.fee_basis_points::INTEGER AS fee_bps,
        t.creator_fee_basis_points::INTEGER AS creator_bps,
        COALESCE({c.col(T, 'cashback_fee_basis_points', 'INTEGER')}, 0) AS cashback_bps,
        t.virtual_token_reserves{H} AS vt, COALESCE(t.virtual_quote_reserves, t.virtual_sol_reserves){H} AS vq,
        t.real_token_reserves{H} AS rt, COALESCE(t.real_quote_reserves, t.real_sol_reserves){H} AS rq,
        t."user" AS trader, NULLIF(t.fee_payer, t."user") AS fee_payer, t.outer_program, t.priority_fee,
        t.jito_tip, t.tx_fee,
        COALESCE(t.mayhem_mode, false) AS mayhem_trade,
        {c.arg(T, 'amount')} AS arg_amount,
        COALESCE({c.arg(T, 'spendable_sol_in')}, {c.arg(T, 'spendable_quote_in')}) AS arg_budget,
        {c.arg(T, 'max_sol_cost')} AS arg_max_cost,
        {c.arg(T, 'min_tokens_out')} AS arg_min_tokens,
        {c.arg(T, 'min_sol_output')} AS arg_min_quote_out,
        {c.arg(T, 'partial_fill', 'BOOLEAN')} AS arg_partial_fill
      FROM {trade} t SEMI JOIN {mints_table} m ON m.mint = t.mint"""


# The raw columns of the store's pool table, typed: a segment without e.g. withdraw or boost
# events still yields every column (the store gets them from its fixed table schema).
_DERIVED = {"e", "orientation", "token_buy", "sol_amount", "sol_depth", "token_amount", "farmer"}
_RAW_TYPED = ", ".join(f"NULL::{t} AS {n}" for n, t in (c.strip().split(None, 1) for c in POOL_SCHEMA.split(","))
                       if n not in _DERIVED)


def pool_select(c, pools_table):
    """store.build's pool rows for one chunk or segment, before the farmer flag (SOL markets only)."""
    trades, other = _pool_trades(c), _pool_other(c)
    parts = [x for x in (trades, other) if x]
    if not parts:
        return None
    src = " UNION ALL BY NAME ".join(parts + [f"SELECT {_RAW_TYPED} WHERE false"])
    return f"""
      SELECT x.*, x.vault + x.vq AS e, p.orientation,
        CASE WHEN x.kind IN ('buy', 'sell') THEN (x.kind = 'buy') = (p.orientation = 'normal') END AS token_buy,
        CASE WHEN p.orientation = 'normal' THEN x.quote_user ELSE x.base END AS sol_amount,
        CASE WHEN p.orientation = 'normal' THEN x.vault ELSE x.b END AS sol_depth,
        CASE WHEN x.kind IN ('buy', 'sell') THEN
          CASE WHEN p.orientation = 'normal' THEN x.base ELSE x.quote_user END END AS token_amount
      FROM ({src}) x JOIN {pools_table} p USING (pool)
      WHERE p.quote_class IN ('sol', 'reversed')"""


class FarmerFlags:
    """The store's farmer flag, incrementally: a trade's wallet farms a pool when its own trades in
    the pool over slots [t - h24, t - 1] number at least FARMER_MIN_TRADES and their net token delta
    is below FARMER_RATIO x their gross token volume."""

    def __init__(self):
        self.hist = defaultdict(deque)  # (pool, trader) -> deque of (slot, signed, gross)

    def flag(self, pool, trader, slot, h24):
        dq = self.hist[(pool, trader)]
        while dq and dq[0][0] < slot - h24:
            dq.popleft()
        n = net = gross = 0
        for s, signed, g in dq:
            if s >= slot:
                break
            n, net, gross = n + 1, net + signed, gross + g
        return n >= FARMER_MIN_TRADES and abs(net) < FARMER_RATIO * gross

    def add(self, pool, trader, slot, token_buy, token_amount):
        """Add a trade; one older than the wallet's last trade in the pool (a backfill) is inserted
        in slot order."""
        dq = self.hist[(pool, trader)]
        item = (slot, token_amount if token_buy else -token_amount, token_amount)
        if not dq or dq[-1][0] <= slot:
            dq.append(item)
            return
        i = len(dq)
        while i > 0 and dq[i - 1][0] > slot:
            i -= 1
        dq.insert(i, item)

    def prune(self, slot, h24):
        """Drop wallets whose last trade in a pool left the window (live memory stays bounded)."""
        for k in [k for k, dq in self.hist.items() if not dq or dq[-1][0] < slot - h24]:
            del self.hist[k]

    def seed(self, rows):
        """Rows (pool, trader, slot, token_buy, token_amount) in chain order, e.g. the store's last 24 h."""
        for pool, trader, slot, tb, amount in rows:
            self.add(pool, trader, slot, tb, amount)


class LiveFeed(Feed):
    """Events of the recorder's segments, in chain order (see the module docstring).

    root: the recorder's output directory; store: a store snapshot for tokens and pools known
    before the recorder started (optional); follow: keep polling for new segments (live) or stop
    after the last complete one (tests, catch-up); h24: slots of the farmer window (default from
    the segments' block times); lag_slots: a slot's events are released once this many later
    slots were seen."""

    def __init__(self, root, store=None, follow=True, poll_s=1.0, h24=None, lag_slots=1, from_seq=0,
                 memory="1GB", meta_bounds=None, snapshot_before=None):
        self.root, self.follow, self.poll_s, self.fixed_h24, self.lag = root, follow, poll_s, h24, lag_slots
        self.next_seq = from_seq
        self.con = duckdb.connect()
        self.con.execute(f"SET memory_limit = '{memory}'; SET threads TO 2")
        self.con.execute("SET preserve_insertion_order = false")
        self.con.execute("CREATE TABLE live_mints (mint VARCHAR)")
        self.con.execute("CREATE TABLE live_pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR)")
        self.tokens, self.pools, self.pool_class = {}, {}, {}
        if store:
            self._load_snapshot(store, snapshot_before)
        self.farmers = FarmerFlags()
        self.ctx = {}  # per pool: pool_leg context (last virtual quote, fee tiers)
        self.curve_hist = defaultdict(list)  # mint -> curve legs seen live (curve_history), pruned after a quiet day
        self.usage = []  # recorder usage rows not yet handed to the engine
        self.ms_window = deque(maxlen=200)  # (slot, block_time) samples for the slot time
        # The engine's Clock: ms per slot per epoch, measured from the block times received.
        self.clock = Clock()
        self.epoch_span = {}  # epoch -> [(first slot, time), (last slot, time)]
        self.stats = {"segments": 0, "late_rows": 0, "unknown_pools": 0, "backfill_rows": 0, "backfill_duplicates": 0}
        # Pools with a backfill: positions of their trades already in the farmer history, so the
        # backfill's overlap with the recorded transactions counts once.
        self.farm_keys = defaultdict(set)
        self.backfill_pending = []  # backfill rows past the segment they came with
        self.history = []  # backfill trades for the engine's farming windows (drain_history)
        self.meta_bounds = meta_bounds or (0, 2**63 - 1)

    # ------------------------------------------------------------------ metadata

    def _load_snapshot(self, store, before=None):
        """Tokens and pools of a store. before: only what was known before that slot (a live start
        there): tokens and pools created earlier, without a later graduation."""
        con = streams.open_store(store, threads=2, memory="1GB")
        for r in con.execute("SELECT mint, create_slot, creator, mayhem, quote_mint, complete_slot, pool FROM mints").fetchall():
            if before is not None:
                if r[1] is not None and r[1] >= before:
                    continue
                if r[5] is not None and r[5] >= before:
                    r = r[:5] + (None, None)
            self.tokens[r[0]] = TokenInfo(*r)
        rows = con.execute("""SELECT pool, COALESCE(mint, pool), orientation, quote_class, COALESCE(mayhem, false),
                                     coin_creator, create_slot FROM pools""").fetchall()
        if before is not None:
            rows = [r for r in rows if r[6] is None or r[6] < before]
        con.close()
        for r in rows:
            self.pool_class[r[0]] = (r[3], r[2])
            if r[3] in SOL_MARKETS:
                self.pools[r[0]] = PoolInfo(*r)
        self.con.register("snap_mints", pa.table({"mint": list(self.tokens)}))
        self.con.execute("INSERT INTO live_mints SELECT mint FROM snap_mints")
        self.con.register("snap_pools", pa.table({"pool": list(self.pool_class),
                                                  "quote_class": [v[0] for v in self.pool_class.values()],
                                                  "orientation": [v[1] for v in self.pool_class.values()]}))
        self.con.execute("INSERT INTO live_pools SELECT pool, quote_class, orientation FROM snap_pools")
        self.con.unregister("snap_mints")
        self.con.unregister("snap_pools")

    def token(self, mint):
        return self.tokens.get(mint)

    def pool(self, pool):
        return self.pools.get(pool)

    def curve_history(self, mint, upto_slot):
        return [l for l in self.curve_hist.get(mint, ()) if l.slot <= upto_slot]

    def keep_pools(self, pools):
        """Pools with an open paper position: written for the recorder, which keeps them on its
        transaction filter next to the watchlist (open-pools.json, replaced atomically)."""
        path = os.path.join(self.root, "open-pools.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"written_unix": int(time.time()), "pools": list(pools)}, f)
        os.replace(tmp, path)

    def drain_usage(self):
        out, self.usage = self.usage, []
        return out

    def _new_tokens_and_pools(self, c):
        """Tokens and pools created in this segment (the store's mints and create_pool rules)."""
        create = c.src("pump/CreateEvent")
        if create:
            rows = self.con.execute(f"""
              SELECT mint, slot, "user", COALESCE(is_mayhem_mode, false), COALESCE(quote_mint, '{pda.DEFAULT_KEY}')
              FROM {create} QUALIFY row_number() OVER (PARTITION BY mint ORDER BY slot, tx_index) = 1""").fetchall()
            new = [r for r in rows if r[0] not in self.tokens]
            for mint, slot, creator, mayhem, quote in new:
                self.tokens[mint] = TokenInfo(mint, slot, creator, mayhem, quote, None, None)
            if new:
                self.con.executemany("INSERT INTO live_mints VALUES (?)", [(r[0],) for r in new])
        done = c.src("pump/CompleteEvent")
        if done:
            for mint, slot in self.con.execute(f"SELECT mint, min(slot) FROM {done} GROUP BY mint").fetchall():
                t = self.tokens.get(mint)
                if t and t.complete_slot is None:
                    self.tokens[mint] = TokenInfo(t.mint, t.create_slot, t.creator, t.mayhem, t.quote_mint, slot, t.pool)
        mig = c.src("pump/CompletePumpAmmMigrationEvent")
        if mig:
            for mint, pool in self.con.execute(f"SELECT mint, any_value(pool) FROM {mig} GROUP BY mint").fetchall():
                t = self.tokens.get(mint)
                if t and t.pool is None:
                    self.tokens[mint] = TokenInfo(t.mint, t.create_slot, t.creator, t.mayhem, t.quote_mint, t.complete_slot, pool)
        # New pools: from their CreatePoolEvent, else from the recorder's pool account streams
        # (pool_creations, pool_accounts: the same mints and coin creator, slot = first seen).
        for table, sql in (("pump_amm/CreatePoolEvent", """
              SELECT pool, base_mint, quote_mint, slot, coin_creator, COALESCE(is_mayhem_mode, false)
              FROM {src} QUALIFY row_number() OVER (PARTITION BY pool ORDER BY slot) = 1"""),
                           ("pool_creations", None), ("pool_accounts", None)):
            src = c.src(table)
            if not src:
                continue
            sql = sql or """SELECT pool, any_value(base_mint), any_value(quote_mint), min(slot), any_value(coin_creator),
                                   bool_or(COALESCE(is_mayhem_mode, false)) FROM {src} GROUP BY pool"""
            for pool, base, quote, slot, creator, mayhem in self.con.execute(sql.format(src=src)).fetchall():
                if pool in self.pool_class:
                    continue
                cls, orient = (("reversed", "reversed") if base == WSOL else ("sol", "normal") if quote == WSOL
                               else ("other", "normal"))
                mint = quote if base == WSOL else base
                self.pool_class[pool] = (cls, orient)
                self.con.execute("INSERT INTO live_pools VALUES (?, ?, ?)", [pool, cls, orient])
                if cls in SOL_MARKETS:
                    self.pools[pool] = PoolInfo(pool, mint, orient, cls, mayhem, creator, slot)

    # ------------------------------------------------------------------ events

    def _h24(self):
        if self.fixed_h24:
            return self.fixed_h24
        if len(self.ms_window) >= 2:
            (s0, t0), (s1, t1) = self.ms_window[0], self.ms_window[-1]
            if s1 > s0 and t1 > t0:
                return round(DAY_MS / ((t1 - t0) * 1000.0 / (s1 - s0)))
        return round(DAY_MS / 400)

    def _backfill_rows(self, seg, meta):
        """Pool rows of the segment's backfill/ (earlier transactions of the recorder's new probes),
        as (pool, token_buy, token_amount, *POOL_COLS without farmer), with the backfill rows still
        pending from earlier segments, in chain order."""
        rows = self.backfill_pending
        self.backfill_pending = []
        done = [b for b in meta.get("backfills", ()) if b.get("status") in ("complete", "failed") and b.get("rows")]
        bdir = os.path.join(seg, "backfill")
        if done and os.path.isdir(bdir):
            c = Chunk((min(b["from_slot"] for b in done), max(b["to_slot"] for b in done) + 1, bdir))
            self._new_tokens_and_pools(c)
            sql = pool_select(c, "live_pools")
            if sql:
                cols = streams.POOL_COLS.replace(", farmer", "")
                rows = rows + [tuple(r) for r in self.con.execute(f"SELECT pool, token_buy, token_amount, {cols} FROM ({sql})").fetchall()]
        return sorted(rows, key=lambda r: (r[4], r[5], r[6], r[7]))

    def _history(self, pool, token_buy, token_amount, row):
        """A backfill trade: farmer history and the pool's leg context, no event; the engine gets
        it for the pool's farming window (drain_history). Counted once."""
        key = (row[1], row[2], row[3], row[4])
        if key in self.farm_keys[pool]:
            self.stats["backfill_duplicates"] += 1
            return
        self.farm_keys[pool].add(key)
        self.stats["backfill_rows"] += 1
        farmer = False
        if row[0] in ("buy", "sell"):
            farmer = self.farmers.flag(pool, row[22], row[1], self._h24())
            self.farmers.add(pool, row[22], row[1], token_buy, token_amount)
        leg = streams.pool_leg(tuple(row) + (farmer,), self.ctx.setdefault(pool, {}))
        if row[0] in ("buy", "sell"):
            self.history.append((pool, [leg]))

    def drain_history(self):
        """Backfill trades since the last call, as (pool, [leg]): history only, never events."""
        out, self.history = self.history, []
        return out

    def segment_events(self, seq, seg, meta):
        """The Events of one segment, sorted (curve and pool merged)."""
        c = Chunk((meta["first_slot"], meta["last_slot"] + 1, seg))
        self._new_tokens_and_pools(c)
        # Backfill rows before the segment are history now; those inside it are merged into its
        # rows in chain order (no events); those after it wait for the next segment.
        back = self._backfill_rows(seg, meta)
        for pool, tb, ta, *row in (r for r in back if r[4] < meta["first_slot"]):
            self._history(pool, tb, ta, row)
        inside = [r for r in back if meta["first_slot"] <= r[4] <= meta["last_slot"]]
        self.backfill_pending = [r for r in back if r[4] > meta["last_slot"]]
        bt = {}
        blocks = c.src("blocks")
        if blocks:
            for slot, t in self.con.execute(f"""SELECT slot, any_value(block_time) FROM {blocks}
                                               WHERE NOT COALESCE(skipped, false) GROUP BY slot ORDER BY slot""").fetchall():
                bt[slot] = t
                if t is not None:
                    self.ms_window.append((slot, t))
                    span = self.epoch_span.setdefault(slot // 432_000, [(slot, t), (slot, t)])
                    span[1] = (slot, t)
                    if span[1][0] - span[0][0] >= 1000:
                        self.clock.per_epoch[slot // 432_000] = (span[1][1] - span[0][1]) * 1000.0 / (span[1][0] - span[0][0])
        h24 = self._h24()
        out = []
        sql = curve_select(c, "live_mints")
        if sql:
            for r in self.con.execute(f"SELECT mint, {streams.CURVE_COLS} FROM ({sql}) "
                                      "ORDER BY slot, tx_index, outer_ix, inner_ix").fetchall():
                leg = streams.curve_leg(r[1:])
                self.curve_hist[r[0]].append(leg)
                out.append(Event(r[1], r[2], r[3], r[4], bt.get(r[1]), "curve", r[0], leg))
        sql = pool_select(c, "live_pools")
        if sql:
            cols = streams.POOL_COLS.replace(", farmer", "")
            rows = [tuple(r) + (True,) for r in self.con.execute(f"""SELECT pool, token_buy, token_amount, {cols} FROM ({sql})
                                        ORDER BY slot, tx_index, outer_ix, inner_ix""").fetchall()]
        else:
            rows = []
        if inside:
            have = {(r[4], r[5], r[6], r[7]) for r in rows}
            rows = sorted(rows + [r + (False,) for r in inside if (r[4], r[5], r[6], r[7]) not in have],
                          key=lambda r: (r[4], r[5], r[6], r[7]))
        if rows:
            for pool, token_buy, token_amount, *row in rows:
                recorded = row.pop()
                if not recorded:
                    self._history(pool, token_buy, token_amount, row)
                    continue
                kind, slot, trader = row[0], row[1], row[22]
                farmer = False
                seen = False
                if pool in self.farm_keys:  # a backfilled pool: its overlap with the backfill counts once
                    key = (slot, row[2], row[3], row[4])
                    seen = key in self.farm_keys[pool]
                    self.farm_keys[pool].add(key)
                if kind in ("buy", "sell"):
                    farmer = self.farmers.flag(pool, trader, slot, h24)
                    if not seen:
                        self.farmers.add(pool, trader, slot, token_buy, token_amount)
                leg = streams.pool_leg(tuple(row) + (farmer,), self.ctx.setdefault(pool, {}))
                out.append(Event(slot, row[2], row[3], row[4], bt.get(slot), "pool", pool, leg))
        out.sort(key=lambda e: e.order)
        self.usage += [dict(u, seq=seq) for u in meta.get("usage", ())]
        self.stats["segments"] += 1
        if self.stats["segments"] % 100 == 0 and out:  # bounded memory in a long live run
            last = out[-1].slot
            self.farmers.prune(last, h24)
            for m in [m for m, legs in self.curve_hist.items() if legs[-1].slot < last - h24]:
                del self.curve_hist[m]
        return out

    def _segments(self):
        """Complete segments from next_seq on (the shadow recorder writes them one by one instead)."""
        return [s for s in segment_dirs(self.root) if s[0] >= self.next_seq]

    def events(self):
        """Events of all complete segments in chain order; with follow, keep polling for new ones.
        A slot is released once a later slot has been seen (lag_slots), the rest at the end."""
        heap, last_out, top = [], None, None
        while True:
            n = 0
            for seq, seg, meta in self._segments():
                n += 1
                for e in self.segment_events(seq, seg, meta):
                    if last_out is not None and e.slot < last_out:
                        self.stats["late_rows"] += 1  # arrived after its slot was released
                        continue
                    heapq.heappush(heap, (e.order, e.venue, e.key, e))
                    top = e.slot if top is None else max(top, e.slot)
                self.next_seq = seq + 1
                while heap and heap[0][0][0] <= top - self.lag:
                    e = heapq.heappop(heap)[3]
                    last_out = e.slot
                    yield e
            if not self.follow:
                break
            if not n:
                time.sleep(self.poll_s)
        while heap:
            yield heapq.heappop(heap)[3]
