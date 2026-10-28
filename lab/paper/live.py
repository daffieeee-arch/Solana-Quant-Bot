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
(store.Chunk, _curve_trades, _pool_trades, _pool_other; the pool join mirrors store.build),
computes the farmer flag from its own 24 h history exactly as the store does (window [t - 24 h,
t - 1 slot] per pool and wallet), and yields Events in chain order. Rows of a slot are only
released once a later slot was seen (lag), so a segment boundary inside a slot cannot reorder
events. Tokens and pools come from a store snapshot plus the ones created live.

Parity with ReplayFeed on the same slots is tested (tests/test_live.py).
"""

import bisect
import glob
import heapq
import itertools
import json
import logging
import os
import sys
import time
from array import array
from collections import defaultdict, deque

import duckdb
import pyarrow as pa

from backtest import data, pda, streams
from backtest.strategies import Clock
from backtest.store import (DAY_MS, FARMER_MIN_TRADES, FARMER_RATIO, POOL_SCHEMA, Chunk, _curve_trades, _pool_other,
                            _pool_trades)

from .feed import Event, Feed, PoolInfo, TokenInfo

WSOL = pda.WSOL
SOL_MARKETS = ("sol", "reversed")
# Row keys (pool or mint, slot, tx, outer, inner) of the last this many slots are remembered, so a
# row that comes twice counts once: a probe's backfill delivered after its first recorded rows (the
# overlap is a few hundred slots: backfill margin plus wait), or slots recorded again after a restart.
SEEN_KEEP_SLOTS = 3_000
# A mint's curve legs are dropped this many slots after its completion: no curve entry is possible
# then (an entry comes d slots after its decision), and an open position holds its own copy.
CURVE_DONE_SLOTS = 1_000
# Tokens learned from create transactions (the recorder's --pump-creates, ~50k a day) are dropped once
# they are this many days old without a graduation: F7 needs a token's creator only for its pool.
TOKEN_KEEP_DAYS = 3
log = logging.getLogger("paper.live")
# u64 guard (deploy-u64guard-2026-10-24; the store's rule since #555): PumpSwap amounts are u64 and pool_select casts
# them to BIGINT, so one row outside its range (a token with a raw supply above 2^63 - 1: E20's base reserves of
# 9,991,111,111,970,383,772 in epoch 1061) raised there, and lab-paper would exit on the same segment at every restart.
# A pool with such a row is left out from that segment on, inside the table scan (before any cast): its rows give no
# event and no history, it is counted (int64_pools; int64_pool_rows: all its rows a chunk leaves out) and logged. Not
# kept across a restart: the 36 h replay finds the pool again only while the row is inside it. Also as #555's store:
# virtual quote reserves of 2^63 or more (which _pool_trades' TRY_CAST turned into 0, the pool trading on) leave the pool
# out, and so does a boost whose quote amount exceeds the reserves after it (the vault's UBIGINT subtraction would
# raise). The curve tables need no guard: _curve_trades casts to HUGEINT (a pump token's supply is fixed at 1e15).
I64_MAX = 2 ** 63 - 1
POOL_TABLES = ("pump_amm/BuyEvent", "pump_amm/SellEvent", "pump_amm/BoostBuyAndBurnEvent", "pump_amm/WithdrawEvent",
               "pump_amm/DepositEvent")


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
    return _curve_trades(c, mints=mints_table)


# The raw columns of the store's pool table, typed: a segment without e.g. withdraw or boost
# events still yields every column (the store gets them from its fixed table schema).
_DERIVED = {"e", "orientation", "token_buy", "sol_amount", "sol_depth", "token_amount", "farmer"}
_RAW_TYPED = ", ".join(f"NULL::{t} AS {n}" for n, t in (c.strip().split(None, 1) for c in POOL_SCHEMA.split(","))
                       if n not in _DERIVED)


def _fits(*exprs):
    """SQL: every expression (NULL passes) fits a BIGINT, checked in HUGEINT (so the check itself cannot overflow)."""
    return " AND ".join(f"COALESCE(({e})::HUGEINT, 0) BETWEEN {-I64_MAX - 1} AND {I64_MAX}" for e in exprs)


def _sql_list(pools):
    return ", ".join("'" + p.replace("'", "''") + "'" for p in sorted(pools))


def int64_select(c):
    """(kind, pool) of the chunk's pool rows with an amount pool_select casts to BIGINT outside its range, without a
    cast that can raise: the rows and expressions of store._pool_trades / _pool_other (b, vault, vq, e = vault + vq,
    base, e_delta, quote_user; boost and withdraw/deposit their own), as #555's _pool_trades(dropped=True)."""
    parts = []
    exact_out = ",".join(f"'{d}'" for d in data.POOL_EXACT_OUT)
    for kind, t, base, e_delta, quote_user, where in (
            ("buy", "pump_amm/BuyEvent", "base_amount_out", "quote_amount_in_with_lp_fee",
             f"CASE WHEN parent_ix_disc IN ({exact_out}) THEN user_quote_amount_in ELSE quote_amount_in END",
             f"parent_ix_disc IS DISTINCT FROM '{data.BOOST_BUY_DISC}' AND "),
            ("sell", "pump_amm/SellEvent", "base_amount_in", "quote_amount_out_without_lp_fee", "user_quote_amount_out", "")):
        src = c.src(t)
        if src:
            g = _fits("pool_base_token_reserves", "pool_quote_token_reserves", c.col(t, "virtual_quote_reserves", "HUGEINT"),
                      f"pool_quote_token_reserves::HUGEINT + COALESCE({c.col(t, 'virtual_quote_reserves', 'BIGINT')}, 0)",
                      base, e_delta, quote_user)
            parts.append(f"SELECT '{kind}' AS kind, pool FROM {src} WHERE {where}NOT ({g})")
    boost = c.src("pump_amm/BoostBuyAndBurnEvent")
    if boost:
        g = _fits("base_reserves_after::HUGEINT + base_amount_burned", "real_quote_reserves_after::HUGEINT - quote_amount_in_used",
                  "TRY_CAST(virtual_quote_reserves AS HUGEINT)",
                  "(real_quote_reserves_after::HUGEINT - quote_amount_in_used) + COALESCE(TRY_CAST(virtual_quote_reserves AS BIGINT), 0)",
                  "base_amount_burned", "quote_amount_in_used", "boost_vault_remaining")
        # the vault is a UBIGINT difference: below 0 it raises before any cast
        g += " AND COALESCE(real_quote_reserves_after >= quote_amount_in_used, true)"
        parts.append(f"SELECT 'boost' AS kind, pool FROM {boost} WHERE NOT ({g})")
    for kind, t, base, quote, lp in (("withdraw", "pump_amm/WithdrawEvent", "base_amount_out", "quote_amount_out", "lp_token_amount_in"),
                                     ("deposit", "pump_amm/DepositEvent", "base_amount_in", "quote_amount_in", "lp_token_amount_out")):
        src = c.src(t)
        if src:
            g = _fits("pool_base_token_reserves", "pool_quote_token_reserves", base, quote, lp, "lp_mint_supply")
            parts.append(f"SELECT '{kind}' AS kind, pool FROM {src} WHERE NOT ({g})")
    return " UNION ALL ".join(parts) if parts else None


class _Without:
    """A chunk whose PumpSwap pool tables leave the given pools out inside the scan, so no cast sees their rows."""

    def __init__(self, c, pools):
        self.c, self.pools = c, _sql_list(pools)

    def src(self, table):
        s = self.c.src(table)
        return f"(SELECT * FROM {s} WHERE pool NOT IN ({self.pools}))" if s and table in POOL_TABLES else s

    def col(self, *a):
        return self.c.col(*a)

    def arg(self, *a, **kw):
        return self.c.arg(*a, **kw)


def pool_select(c, pools_table, exclude=()):
    """store.build's pool rows for one chunk or segment, before the farmer flag (SOL markets only), without the
    pools in exclude (the u64 guard; none: the unchanged SQL)."""
    if exclude:
        c = _Without(c, exclude)
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


class _Trades:
    """One wallet's trades in one pool, in slot order: [slot, signed, gross] triples in one
    array('q') (24 bytes a trade, not a ~300-byte deque of tuples). Triples before `start` left the
    window. len() and iteration give the trades still in the window, as (slot, signed, gross).
    Token amounts are u64: a wallet with an amount of 2^63 or more keeps a plain list instead (the
    same index code; dev2's largest amount is 7.46e18, 81 % of 2^63).
    The window of the last flag query is [start, end), with its count and sums kept (n, net, gross): the next
    query moves both ends instead of summing the window again (a bot with thousands of trades in a pool made
    every flag O(window): 60 % of a restart's replay, profile 2026-10-17). `end` and the sums are the state of the
    last query, not of the trades now: a trade added after it at or past `end` is outside them until the next query
    moves `end` over it; one inserted before `end` is added to them at once (add)."""

    __slots__ = ("start", "a", "end", "n", "net", "gross")

    def __init__(self):
        self.start, self.a = 0, array("q")
        self.end = self.n = self.net = self.gross = 0

    def __len__(self):
        return (len(self.a) - self.start) // 3

    def __iter__(self):
        a = self.a
        for i in range(self.start, len(a), 3):
            yield a[i], a[i + 1], a[i + 2]

    def compact(self):
        if self.start:
            del self.a[:self.start]
            self.end -= self.start
            self.start = 0


def _intern(s):
    """sys.intern for the strings that repeat in every key; anything else (a NULL trader) as it is."""
    return sys.intern(s) if type(s) is str else s


class FarmerFlags:
    """The store's farmer flag, incrementally: a trade's wallet farms a pool when its own trades in
    the pool over slots [t - h24, t - 1] number at least FARMER_MIN_TRADES and their net token delta
    is below FARMER_RATIO x their gross token volume. Per (pool, trader) a _Trades window; pool and
    trader strings are interned (every key repeats them)."""

    def __init__(self):
        self.hist = {}  # (pool, trader) -> _Trades

    def flag(self, pool, trader, slot, h24):
        t = self.hist.get((pool, trader))
        if t is None:
            return False
        a, i, lo = t.a, t.start, slot - h24
        while i < len(a) and a[i] < lo:  # trades that left the window (start only moves forward)
            if i < t.end:
                t.n, t.net, t.gross = t.n - 1, t.net - a[i + 1], t.gross - a[i + 2]
            i += 3
        t.start = i
        if t.end < i:  # the whole last window left: empty, sums 0
            t.end = i
        if i > 96 and 2 * i > len(a):  # mostly expired: give the memory back
            t.compact()
        e = t.end
        while e < len(a) and a[e] < slot:  # the window's end moves to the first trade at or after `slot`
            t.n, t.net, t.gross = t.n + 1, t.net + a[e + 1], t.gross + a[e + 2]
            e += 3
        while e > t.start and a[e - 3] >= slot:  # an earlier query (a backfill trade): back
            e -= 3
            t.n, t.net, t.gross = t.n - 1, t.net - a[e + 1], t.gross - a[e + 2]
        t.end = e
        return t.n >= FARMER_MIN_TRADES and abs(t.net) < FARMER_RATIO * t.gross

    def add(self, pool, trader, slot, token_buy, token_amount):
        """Add a trade; one older than the wallet's last trade in the pool (a backfill) is inserted
        in slot order, after the trades of its own slot."""
        t = self.hist.get((pool, trader))
        if t is None:  # interned once, for the new key (the seed adds millions of trades)
            t = self.hist[(_intern(pool), _intern(trader))] = _Trades()
        if token_amount >= 1 << 63 and isinstance(t.a, array):
            t.a = list(t.a)  # beyond array('q'): checked first, so no triple is ever half added
        a = t.a
        item = (slot, token_amount if token_buy else -token_amount, token_amount)
        if len(a) == t.start or a[-3] <= slot:
            a.extend(item)
            return
        i = len(a)
        while i > t.start and a[i - 3] > slot:
            i -= 3
        a[i:i] = array("q", item) if isinstance(a, array) else list(item)
        if i < t.end:  # inside the last query's window (its slot is before that query's)
            t.end += 3
            t.n, t.net, t.gross = t.n + 1, t.net + item[1], t.gross + item[2]

    def prune(self, slot, h24):
        """Drop wallets whose last trade in a pool left the window (live memory stays bounded), and give
        back the memory of the trades that left it."""
        lo = slot - h24
        for k in [k for k, t in self.hist.items() if len(t.a) == t.start or t.a[-3] < lo]:
            del self.hist[k]
        for t in self.hist.values():
            t.compact()

    def entries(self):
        return sum(len(t) for t in self.hist.values())

    def seed(self, rows):
        """Rows (pool, trader, slot, token_buy, token_amount) in chain order, e.g. the store's last 24 h."""
        for pool, trader, slot, tb, amount in rows:
            self.add(pool, trader, slot, tb, amount)


class _Keys:
    """Row keys (slot, tx, outer, inner) of one backfilled pool, for counting a backfill's overlap with
    recorded rows once: packed into one sorted array('Q') (8 bytes a key; slot < 2^30, tx < 2^16,
    outer < 2^8, inner < 2^10), a key that does not fit in a plain set. Behaves as a set of tuples."""

    __slots__ = ("a", "extra")

    def __init__(self, keys=()):
        self.a, self.extra = array("Q"), set()
        for k in keys:
            self.add(k)

    @staticmethod
    def _pack(k):
        slot, tx, outer, inner = k
        if (isinstance(slot, int) and isinstance(tx, int) and isinstance(outer, int) and isinstance(inner, int)
                and 0 <= slot < 1 << 30 and 0 <= tx < 1 << 16 and 0 <= outer < 1 << 8 and 0 <= inner < 1 << 10):
            return slot << 34 | tx << 18 | outer << 10 | inner
        return None

    def add(self, k):
        p = self._pack(k)
        if p is None:
            self.extra.add(k)
            return
        a = self.a
        if not a or a[-1] < p:
            a.append(p)
            return
        i = bisect.bisect_left(a, p)
        if i == len(a) or a[i] != p:
            a.insert(i, p)

    def __contains__(self, k):
        p = self._pack(k)
        if p is None:
            return k in self.extra
        i = bisect.bisect_left(self.a, p)
        return i < len(self.a) and self.a[i] == p

    def __len__(self):
        return len(self.a) + len(self.extra)

    def __iter__(self):
        for p in self.a:
            yield (p >> 34, (p >> 18) & 0xFFFF, (p >> 10) & 0xFF, p & 0x3FF)
        yield from self.extra

    def __ior__(self, keys):
        for k in keys:
            self.add(k)
        return self

    def __eq__(self, other):
        return set(self) == set(other)

    def drop_before(self, slot):
        """Keep the keys of slot >= `slot`."""
        if slot > 0:
            del self.a[:bisect.bisect_left(self.a, min(slot, 1 << 30) << 34)]
        self.extra = {k for k in self.extra if k[0] is not None and k[0] >= slot}


class LiveFeed(Feed):
    """Events of the recorder's segments, in chain order (see the module docstring).

    root: the recorder's output directory; store: a store snapshot for tokens and pools known
    before the recorder started (optional); follow: keep polling for new segments (live) or stop
    after the last complete one (tests, catch-up); h24: slots of the farmer window (default from
    the segments' block times); lag_slots: a slot's events are released once this many later
    slots were seen."""

    def __init__(self, root, store=None, follow=True, poll_s=1.0, h24=None, lag_slots=1, from_seq=0,
                 memory="1GB", meta_bounds=None, snapshot_before=None, seed_farmers=False, learned=()):
        self.root, self.follow, self.poll_s, self.fixed_h24, self.lag = root, follow, poll_s, h24, lag_slots
        self.store = store
        # The farmer window before the first live slot from the store, per pool when it first trades
        # live (_seed_pool); farm_seed records the range and from which slot the labels are complete.
        self.seed_farmers, self.seed_con, self.farm_seed, self.seeded = bool(seed_farmers and store), None, None, set()
        # _check_snapshot_before checks it against the first live slot, for every feed with a store (seeded or not)
        self.snapshot_before, self.bound_checked = snapshot_before, not store
        self.next_seq = from_seq
        self.con = duckdb.connect()
        self.con.execute(f"SET memory_limit = '{memory}'; SET threads TO 2")
        # A segment's files are read a few times when it arrives (its pool table three times, its curve
        # table twice) and never again: DuckDB's file cache (on by default) would only keep them, up to the
        # memory limit (shadow-1052-a8e69ed: ~0.86 MB per segment).
        self.con.execute("SET preserve_insertion_order = false; SET enable_external_file_cache = false")
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
        self.stats = {"segments": 0, "late_rows": 0, "unknown_pools": 0, "unknown_pool_rows": 0, "unknown_pool_other_rows": 0,
                      "unknown_mints": 0, "unknown_mint_rows": 0, "backfill_rows": 0,
                      "backfill_duplicates": 0, "duplicate_rows": 0, "seeded_pools": 0, "tokens_dropped": 0,
                      "farmer_flags_fixed": 0, "pools_seen_without_creation": 0, "late_probes": 0,
                      "creation_detection_off_segments": 0, "probes_off_segments": 0, "int64_pools": 0, "int64_pool_rows": 0}
        # Pools only a pool_creations / pool_accounts row named (no CreatePoolEvent): not learned (creation-filters
        # spec v0.2 M1), counted once each (capped as the unknown sets)
        self.no_creation_seen = set()
        self.int64_pools = set()  # the u64 guard's pools (int64_select), left out from their first such row on
        self.unknown_seen = set()  # pools whose rows were dropped (not in the snapshot, creation not received)
        self.unknown_mints_seen = set()  # mints whose curve rows were dropped (the same, for tokens)
        self.seen_keys, self.seen_order, self.seen_hi = {}, deque(), 0  # key -> "rec" | "hist" (SEEN_KEEP_SLOTS)
        # Pools with a backfill: positions of their trades already in the farmer history, so the
        # backfill's overlap with the recorded transactions counts once.
        self.farm_keys = defaultdict(_Keys)
        self.backfill_pending = []  # backfill rows past the segment they came with
        self.history = []  # backfill trades for the engine's farming windows (drain_history)
        # Recorded trades of the last SEEN_KEEP_SLOTS slots, (slot, pool, (slot, tx, outer, inner), trader, farmer flag):
        # a backfill that brings a pool's earlier trades after them recomputes their flags (_reflag, drain_flag_fixes).
        self.recorded, self.flag_fixes = deque(), []
        self.new_pools = []  # pools learned live, in order (the live watchlist adds them at creation)
        self.meta_bounds = meta_bounds or (0, 2**63 - 1)
        # What this feed learns that a restart needs (the journal's 'learned' rows, drain_learned): new pools,
        # graduated tokens and the first block of each epoch (the clock's span). seq: the segment it came from.
        self.learned, self.seq = [], None
        # Pools whose probe backfill reached past Triton's replay window (creation-filters spec v0.2 §4 option A): their
        # farmer history starts at the probe, so the engine counts their farm window incomplete for a day (engine.py
        # _farm_window_complete). Learned once each ('late_probe' rows), kept over a restart.
        self.late_probe_pools = set()
        if learned:
            self._load_learned(learned)

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

    def drain_learned(self):
        """What was learned since the last call: (kind, key, seq, slot, info JSON) rows for the journal's 'learned'."""
        out, self.learned = self.learned, []
        return out

    def _learn(self, kind, key, slot, info):
        self.learned.append((kind, key, self.seq, None if slot is None else int(slot), json.dumps(info)))

    def _learn_token(self, t):
        self._learn("token", t.mint, t.complete_slot, [t.mint, t.create_slot, t.creator, t.mayhem, t.quote_mint,
                                                       t.complete_slot, t.pool])

    def _load_learned(self, rows):
        """What earlier runs learned (the journal's 'learned' rows). Graduated tokens and epoch spans always (their
        last state; a token's graduation is only used once it happened). A pool only when it was learned from a
        segment before the first one this feed reads: the others it learns again in order (new_pools, the live
        watchlist's creation minute)."""
        segs = self._segments()
        first = segs[0][0] if segs else None
        mints, pools = [], []
        for r in rows:
            info = json.loads(r["info"])
            if r["kind"] == "token":
                if info[0] not in self.tokens:
                    mints.append(info[0])
                self.tokens[info[0]] = TokenInfo(*info)
            elif r["kind"] == "epoch":
                self.epoch_span.setdefault(int(r["key"]), [tuple(info), tuple(info)])
            elif r["kind"] == "late_probe":
                self.late_probe_pools.add(r["key"])
            elif r["kind"] == "pool" and r["key"] not in self.pool_class and (first is None or r["seq"] is None
                                                                             or r["seq"] < first):
                cls, orient, *pool = info
                self.pool_class[r["key"]] = (cls, orient)
                pools.append((r["key"], cls, orient))
                if pool:
                    self.pools[r["key"]] = PoolInfo(*pool)
        if mints:
            self.con.executemany("INSERT INTO live_mints VALUES (?)", [(m,) for m in mints])
        if pools:
            self.con.executemany("INSERT INTO live_pools VALUES (?, ?, ?)", pools)
        self.stats["learned_loaded"] = len(mints) + len(pools)

    def _new_tokens_and_pools(self, c):
        """Tokens and pools created in this segment (the store's mints and create_pool rules). Each query has its own
        ORDER BY (first slot, then key): DuckDB returns GROUP BY and QUALIFY rows in an order that differs between
        processes (threads 2, preserve_insertion_order off), and these rows are learned (the journal's 'learned', the
        live watchlist's new_pools) in the order they come. Only the order is fixed; which rows are learned is not
        changed (M3-09a found it, 2026-10-20)."""
        create = c.src("pump/CreateEvent")
        if create:
            rows = self.con.execute(f"""
              SELECT mint, slot, "user", COALESCE(is_mayhem_mode, false), COALESCE(quote_mint, '{pda.DEFAULT_KEY}')
              FROM {create} QUALIFY row_number() OVER (PARTITION BY mint ORDER BY slot, tx_index) = 1
              ORDER BY slot, mint""").fetchall()
            new = [r for r in rows if r[0] not in self.tokens]
            for mint, slot, creator, mayhem, quote in new:
                self.tokens[mint] = TokenInfo(mint, slot, creator, mayhem, quote, None, None)
            if new:
                self.con.executemany("INSERT INTO live_mints VALUES (?)", [(r[0],) for r in new])
        done = c.src("pump/CompleteEvent")
        if done:
            for mint, slot in self.con.execute(f"SELECT mint, min(slot) FROM {done} GROUP BY mint ORDER BY 2, 1").fetchall():
                t = self.tokens.get(mint)
                if t and t.complete_slot is None:
                    self.tokens[mint] = TokenInfo(t.mint, t.create_slot, t.creator, t.mayhem, t.quote_mint, slot, t.pool)
                    self._learn_token(self.tokens[mint])
        mig = c.src("pump/CompletePumpAmmMigrationEvent")
        if mig:
            for mint, pool in self.con.execute(f"SELECT mint, any_value(pool) FROM {mig} GROUP BY mint "
                                               "ORDER BY min(slot), mint").fetchall():
                t = self.tokens.get(mint)
                if t and t.pool is None:
                    self.tokens[mint] = TokenInfo(t.mint, t.create_slot, t.creator, t.mayhem, t.quote_mint, t.complete_slot, pool)
                    self._learn_token(self.tokens[mint])
        # New pools: only from their CreatePoolEvent (creation-filters spec v0.2 M1). The recorder's pool account streams
        # (pool_creations, pool_accounts) see every pool write, old pools included (the "fresh fees" filter is refuted):
        # a pool they alone name is not learned (_no_creation counts it).
        for table, sql in (("pump_amm/CreatePoolEvent", """
              SELECT pool, base_mint, quote_mint, slot, coin_creator, COALESCE(is_mayhem_mode, false)
              FROM {src} QUALIFY row_number() OVER (PARTITION BY pool ORDER BY slot) = 1 ORDER BY slot, pool"""),):
            src = c.src(table)
            if not src:
                continue
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
                    self.new_pools.append(pool)
                    self._learn("pool", pool, slot, [cls, orient, pool, mint, orient, cls, mayhem, creator, slot])
                else:
                    self._learn("pool", pool, slot, [cls, orient])

    # ------------------------------------------------------------------ events

    def _seen(self, key, slot, source):
        """How a row key was first seen in the last SEEN_KEEP_SLOTS slots: "rec" (a recorded row, an
        event) or "hist" (a backfill row, history only); None the first time (then remembered as source)."""
        prev = self.seen_keys.get(key)
        if prev is not None:
            return prev
        if slot >= self.seen_hi - SEEN_KEEP_SLOTS:
            self.seen_keys[key] = source
            self.seen_order.append((slot, key))
            if slot > self.seen_hi:
                self.seen_hi = slot
                while self.seen_order and self.seen_order[0][0] < slot - SEEN_KEEP_SLOTS:
                    self.seen_keys.pop(self.seen_order.popleft()[1], None)
        return None

    def close(self):
        """Close the feed's DuckDB connections (its own and the seed store's)."""
        self.con.close()
        if self.seed_con is not None:
            self.seed_con.close()
            self.seed_con = None

    def _check_snapshot_before(self, first_slot):
        """A start knows the store only before its first live slot (LAB review, Codex finding 3 on 383a24d; lab-live's
        follow::check_snapshot_before), for every feed with a store, seeded or not (LAB review on #478): a snapshot_before
        after it, or none while the store's pool rows reach it, would have loaded tokens and pools created after the start
        (_load_snapshot). paper.shadow's lo may lie just before. A store that ends before the start is taken whole."""
        if self.snapshot_before is not None:
            if self.snapshot_before > first_slot:
                raise SystemExit(f"LiveFeed: snapshot_before {self.snapshot_before} is after the first live slot {first_slot}: the "
                                 f"start would know the tokens and pools created in [{first_slot}, {self.snapshot_before}): refused")
            return
        con = streams.open_store(self.store, threads=1, memory="256MB")
        try:
            end = con.execute("SELECT max(slot) + 1 FROM pool").fetchone()[0] or 0
        finally:
            con.close()
        if end > first_slot:
            raise SystemExit(f"LiveFeed: the store's pool rows reach slot {end - 1}, at or after the first live slot {first_slot}, and "
                             f"no snapshot_before: the start would know tokens and pools created after it: give snapshot_before "
                             f"{first_slot}, or a store that ends before the start")

    def _init_seed(self, first_slot, h24):
        """Seed window at the first live slot: [first_slot - h24, first_slot), as far as the store
        reaches. The farm labels are complete from first_slot when it reaches it, else a day later. The first segment
        checked the bound before (_check_snapshot_before)."""
        self.seed_con = streams.open_store(self.store, threads=1, memory="256MB")
        start, end = self.seed_con.execute("SELECT min(slot), max(slot) + 1 FROM pool").fetchone()
        start, end = start or 0, end or 0
        lo, hi = max(first_slot - h24, start), min(first_slot, end)
        # Complete once the window [t - h24, t) holds no slot without history: from the store's start
        # on when it reaches the first live slot, else from the first live slot on.
        complete = max(first_slot, start + h24) if end >= first_slot else first_slot + h24
        self.farm_seed = {"store": os.path.abspath(self.store), "lo": int(lo), "hi": int(max(lo, hi)), "h24": int(h24),
                          "first_live_slot": int(first_slot), "complete_from": int(complete)}
        log.info("farmer history seeded from the store over [%d, %d), complete from slot %d",
                 self.farm_seed["lo"], self.farm_seed["hi"], self.farm_seed["complete_from"])

    def _seed_pool(self, pool):
        """The pool's trades in the seed window, into the farmer history (once per pool)."""
        if self.farm_seed is None or pool in self.seeded:
            return
        self.seeded.add(pool)
        lo, hi = self.farm_seed["lo"], self.farm_seed["hi"]
        if hi <= lo:
            return
        for trader, slot, tb, amount in self.seed_con.execute(
                """SELECT trader, slot, token_buy, token_amount FROM pool WHERE pool = ? AND kind IN ('buy', 'sell')
                   AND slot >= ? AND slot < ? ORDER BY slot, tx_index, outer_ix, inner_ix""", [pool, lo, hi]).fetchall():
            self.farmers.add(pool, trader, slot, tb, amount)
        self.stats["seeded_pools"] += 1

    def _no_creation(self, c):
        """Pools a pool_creations or pool_accounts row of the segment names that no creation made known (M1): counted
        once each in pools_seen_without_creation (capped at 1,000,000 as the unknown sets); their number in this
        segment."""
        new = 0
        for table in ("pool_creations", "pool_accounts"):
            src = c.src(table)
            if not src:
                continue
            for (pool,) in self.con.execute(f"SELECT pool FROM {src} GROUP BY pool ORDER BY min(slot), pool").fetchall():
                if pool in self.pool_class or pool in self.no_creation_seen:
                    continue
                new += 1
                self.stats["pools_seen_without_creation"] += 1
                if len(self.no_creation_seen) < 1_000_000:
                    self.no_creation_seen.add(pool)
        return new

    def _segment_notes(self, seq, meta, no_creation):
        """The segment's own usage rows for the engine's journal (creation-filters spec v0.2 M1, M4, S3), after the
        recorder's: pools named without a creation, the filter without creation detection (new_reversed false: not
        configured or dropped by a downscale), without probes (tx_probes dropped), and probes whose backfill reached
        past Triton's replay window (late probes, their farmer history starts at the probe)."""
        rows = []
        if no_creation:
            rows.append({"stream": "pools_seen_without_creation", "bytes": 0, "messages": no_creation,
                         "note": "pools named only by pool_creations / pool_accounts: not learned"})
        f = meta.get("filter")
        if isinstance(f, dict) and f.get("new_reversed") is False:
            self.stats["creation_detection_off_segments"] += 1
            rows.append({"stream": "creation_detection_off", "bytes": 0, "messages": 1,
                         "note": f"segment {seq}: new reversed pools not detected (dropped: {', '.join(f.get('dropped') or []) or '-'})"})
        if isinstance(f, dict) and "tx_probes" in (f.get("dropped") or ()):
            self.stats["probes_off_segments"] += 1
            rows.append({"stream": "probes_off", "bytes": 0, "messages": 1, "note": f"segment {seq}: tx_probes dropped by a downscale"})
        late = sorted(b.get("pool") or "" for b in meta.get("backfills", ())
                      if b.get("status") == "failed" and "not available" in str(b.get("error") or ""))
        if late:
            self.stats["late_probes"] += len(late)
            rows.append({"stream": "late_probe", "bytes": 0, "messages": len(late),
                         "note": "backfill past the replay window: " + " ".join(late)})
            for pool in late:  # §4 option A: the pool in the learned info, once
                if pool and pool not in self.late_probe_pools:
                    self.late_probe_pools.add(pool)
                    self._learn("late_probe", pool, meta.get("first_slot"), {})
        return [dict(r, seq=seq) for r in rows]

    def _int64_scan(self, c, seq, what):
        """The u64 guard: pools of this chunk with a row int64_select finds join int64_pools (logged once each)."""
        sql = int64_select(c)
        if not sql:
            return
        for pool, n, kinds in self.con.execute(f"""SELECT pool, count(*), string_agg(DISTINCT kind, ',' ORDER BY kind)
                                                   FROM ({sql}) GROUP BY pool ORDER BY pool""").fetchall():
            if pool not in self.int64_pools:
                self.int64_pools.add(pool)
                self.stats["int64_pools"] += 1
                log.warning("segment %s (%s): pool %s (%s) has %d row(s) with an amount outside BIGINT (%s): "
                            "left out from here on (%d such pools)", seq, what, pool,
                            self.pool_class.get(pool, ("not known",))[0], n, kinds, self.stats["int64_pools"])

    def _int64_rows(self, c):
        """All rows of the guard's pools this chunk leaves out, in range or not (every pool table, the boost crank's
        inner buys aside)."""
        n = 0
        for t in POOL_TABLES if self.int64_pools else ():
            src = c.src(t)
            if src:
                skip = f" AND parent_ix_disc IS DISTINCT FROM '{data.BOOST_BUY_DISC}'" if t == "pump_amm/BuyEvent" else ""
                n += self.con.execute(f"SELECT count(*) FROM {src} WHERE pool IN ({_sql_list(self.int64_pools)}){skip}").fetchone()[0]
        self.stats["int64_pool_rows"] += n

    def _unknown_pools(self, c, seq):
        """Count (and log the new) pools whose rows pool_select drops: not in the snapshot and their creation not
        received (a store that ends long before the recorder started, or a creation the recorder missed). Their buys and
        sells are unknown_pool_rows, their withdraw, deposit and boost rows unknown_pool_other_rows (M3-09a's note v0.3:
        2 such withdraws were the last of job 91's 11,717). The u64 guard's pools aside (their rows are int64_pool_rows)."""
        new = 0
        cc = _Without(c, self.int64_pools) if self.int64_pools else c
        for sql, stat in ((_pool_trades(cc), "unknown_pool_rows"), (_pool_other(cc), "unknown_pool_other_rows")):
            if not sql:
                continue
            for pool, n in self.con.execute(f"SELECT pool, count(*) FROM ({sql}) x ANTI JOIN live_pools USING (pool) GROUP BY pool").fetchall():
                self.stats[stat] += n
                if pool not in self.unknown_seen:
                    new += 1
                    self.stats["unknown_pools"] += 1
                    if len(self.unknown_seen) < 1_000_000:
                        self.unknown_seen.add(pool)
        if new:
            log.warning("segment %d: %d new unknown pools, their rows dropped (%d unknown pools, %d trade and %d other rows "
                        "so far)", seq, new, self.stats["unknown_pools"], self.stats["unknown_pool_rows"],
                        self.stats["unknown_pool_other_rows"])

    def _unknown_mints(self, c, seq):
        """Count (and log the new) mints whose curve rows curve_select drops: not in the snapshot and their CreateEvent
        not received, or pruned (TOKEN_KEEP_DAYS). M3-09a's note v0.3: 11,606 of job 91's 11,717 (the recording ran
        without --pump-creates); before this, LiveFeed dropped them without a count."""
        sql = _curve_trades(c, mints=None)
        if not sql:
            return
        new = 0
        for mint, n in self.con.execute(f"SELECT mint, count(*) FROM ({sql}) x ANTI JOIN live_mints USING (mint) GROUP BY mint").fetchall():
            self.stats["unknown_mint_rows"] += n
            if mint not in self.unknown_mints_seen:
                new += 1
                self.stats["unknown_mints"] += 1
                if len(self.unknown_mints_seen) < 1_000_000:
                    self.unknown_mints_seen.add(mint)
        if new:
            log.warning("segment %d: %d new unknown mints, their curve rows dropped (%d unknown mints, %d rows so far)",
                        seq, new, self.stats["unknown_mints"], self.stats["unknown_mint_rows"])

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
            self._int64_scan(c, self.seq, "backfill")
            sql = pool_select(c, "live_pools", self.int64_pools)
            if sql:
                cols = streams.POOL_COLS.replace(", farmer", "")
                rows = rows + [tuple(r) for r in self.con.execute(f"SELECT pool, token_buy, token_amount, {cols} FROM ({sql})").fetchall()]
        rows = [r for r in rows if r[0] not in self.int64_pools]  # pending rows of a pool the guard found since
        return sorted(rows, key=lambda r: (r[4], r[5], r[6], r[7]))

    def _history(self, pool, token_buy, token_amount, row):
        """A backfill trade: farmer history and the pool's leg context, no event; the engine gets
        it for the pool's farming window (drain_history). Counted once."""
        key = (row[1], row[2], row[3], row[4])
        if key in self.farm_keys[pool] or self._seen(("p", pool) + key, row[1], "hist") is not None:
            self.stats["backfill_duplicates"] += 1
            return
        self._seed_pool(pool)
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

    def _reflag(self, pools, h24):
        """A backfill brought these pools' earlier trades after their first recorded ones (a probe followed mid-life):
        the recorded trades' farmer flags were computed without that history (shadow job 91, pool 4reQ...: 10 trades,
        the pool's farm label organic instead of flagged). Recompute them now that the farmer history has the
        backfill, and hand the changed ones to the engine's farming windows (drain_flag_fixes). Recorded trades older
        than SEEN_KEEP_SLOTS are not looked at; the legs already handed out keep their flag (a position's flow window,
        the live watchlist's past minutes). The recomputation is FarmerFlags.flag's, whose window start only moves
        forward: a trade up to SEEN_KEEP_SLOTS old may miss the oldest up to 3,000 slots of its 24 h window (trades a
        later query already dropped), rarely deciding."""
        for i, (slot, pool, key, trader, flag) in enumerate(self.recorded):
            if pool in pools:
                new = self.farmers.flag(pool, trader, slot, h24)
                if new != flag:
                    self.recorded[i] = (slot, pool, key, trader, new)
                    self.flag_fixes.append((pool, key, new))
                    self.stats["farmer_flags_fixed"] += 1

    def drain_flag_fixes(self):
        """Recorded trades whose farmer flag a late backfill changed, since the last call: (pool, order, farmer)."""
        out, self.flag_fixes = self.flag_fixes, []
        return out

    def segment_events(self, seq, seg, meta):
        """The Events of one segment, sorted (curve and pool merged)."""
        c = Chunk((meta["first_slot"], meta["last_slot"] + 1, seg))
        if not self.bound_checked:  # the first segment: before anything of it is learned or handed out
            self._check_snapshot_before(meta["first_slot"])
            self.bound_checked = True
        self.seq = seq
        self._new_tokens_and_pools(c)
        self._int64_scan(c, seq, "segment")
        # Backfill rows before the segment are history now; those inside it are merged into its
        # rows in chain order (no events); those after it wait for the next segment.
        back = self._backfill_rows(seg, meta)
        no_creation = self._no_creation(c)  # after the backfill's creations
        late = set()
        for pool, tb, ta, *row in (r for r in back if r[4] < meta["first_slot"]):
            self._history(pool, tb, ta, row)
            late.add(pool)
        if late and self.recorded:  # earlier trades of pools already recorded: their flags again
            self._reflag(late, self._h24())
        inside = [r for r in back if meta["first_slot"] <= r[4] <= meta["last_slot"]]
        self.backfill_pending = [r for r in back if r[4] > meta["last_slot"]]
        arrival = self._arrival_of(c, meta)
        bt = {}
        blocks = c.src("blocks")
        if blocks:
            for slot, t in self.con.execute(f"""SELECT slot, any_value(block_time) FROM {blocks}
                                               WHERE NOT COALESCE(skipped, false) GROUP BY slot ORDER BY slot""").fetchall():
                bt[slot] = t
                if t is not None:
                    self.ms_window.append((slot, t))
                    if slot // 432_000 not in self.epoch_span:  # the epoch's first block: its clock span starts here
                        self._learn("epoch", str(slot // 432_000), slot, [slot, t])
                    span = self.epoch_span.setdefault(slot // 432_000, [(slot, t), (slot, t)])
                    span[1] = (slot, t)
                    if span[1][0] - span[0][0] >= 1000:
                        self.clock.per_epoch[slot // 432_000] = (span[1][1] - span[0][1]) * 1000.0 / (span[1][0] - span[0][0])
        h24 = self._h24()
        if self.seed_farmers and self.farm_seed is None:
            self._init_seed(meta["first_slot"], h24)
        self._unknown_pools(c, seq)
        self._unknown_mints(c, seq)
        out = []
        sql = curve_select(c, "live_mints")
        if sql:
            for r in self.con.execute(f"SELECT mint, {streams.CURVE_COLS} FROM ({sql}) "
                                      "ORDER BY slot, tx_index, outer_ix, inner_ix").fetchall():
                if self._seen(("c", r[0], r[1], r[2], r[3], r[4]), r[1], "rec") is not None:
                    self.stats["duplicate_rows"] += 1  # recorded twice (e.g. a restart below the last segment)
                    continue
                leg = streams.curve_leg(r[1:])
                self.curve_hist[r[0]].append(leg)
                out.append(Event(r[1], r[2], r[3], r[4], bt.get(r[1]), "curve", r[0], leg, arrival(r[1], r[2])))
        self._int64_rows(c)
        sql = pool_select(c, "live_pools", self.int64_pools)
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
                prev = self._seen(("p", pool, slot, row[2], row[3], row[4]), slot, "rec")
                if prev == "rec":
                    self.stats["duplicate_rows"] += 1  # recorded twice (e.g. a restart below the last segment)
                    continue
                self._seed_pool(pool)
                farmer = False
                seen = prev == "hist"  # a backfill delivered it first: in the farmer history already
                if pool in self.farm_keys:  # a backfilled pool: its overlap with the backfill counts once
                    key = (slot, row[2], row[3], row[4])
                    seen = seen or key in self.farm_keys[pool]
                    self.farm_keys[pool].add(key)
                if kind in ("buy", "sell"):
                    farmer = self.farmers.flag(pool, trader, slot, h24)
                    if not seen:
                        self.farmers.add(pool, trader, slot, token_buy, token_amount)
                    rec = self.recorded
                    rec.append((slot, pool, (slot, row[2], row[3], row[4]), trader, farmer))
                    while rec[0][0] < slot - SEEN_KEEP_SLOTS:
                        rec.popleft()
                leg = streams.pool_leg(tuple(row) + (farmer,), self.ctx.setdefault(pool, {}))
                out.append(Event(slot, row[2], row[3], row[4], bt.get(slot), "pool", pool, leg, arrival(slot, row[2])))
        out.sort(key=lambda e: e.order)
        self.usage += [dict(u, seq=seq) for u in meta.get("usage", ())] + self._segment_notes(seq, meta, no_creation)
        self.stats["segments"] += 1
        if self.stats["segments"] % 100 == 0 and out:  # bounded memory in a long live run
            self._prune(out[-1].slot, h24)
        return out

    def _prune(self, last, h24):
        """Drop what the farmer window [last - h24, last] no longer needs: farmer history, curve legs of
        mints quiet for a day or complete for CURVE_DONE_SLOTS, and the backfill dedup keys of older
        slots. A pool without keys left is treated as before its first backfill again (its recorded
        rows are no longer tracked). Tokens older than TOKEN_KEEP_DAYS that never completed go too
        (a later graduation then takes the pool's coin creator as the creator, as without the create)."""
        self.farmers.prune(last, h24)

        def done(m, legs):
            tok = self.tokens.get(m)
            return legs[-1].slot < last - h24 or (tok is not None and tok.complete_slot is not None
                                                  and tok.complete_slot < last - CURVE_DONE_SLOTS)

        for m in [m for m, legs in self.curve_hist.items() if done(m, legs)]:
            del self.curve_hist[m]
        old = [m for m, t in self.tokens.items() if t.complete_slot is None and t.pool is None
               and t.create_slot is not None and t.create_slot < last - TOKEN_KEEP_DAYS * h24]
        if old:
            for m in old:
                del self.tokens[m]
            self.con.register("old_mints", pa.table({"mint": old}))
            self.con.execute("DELETE FROM live_mints WHERE mint IN (SELECT mint FROM old_mints)")
            self.con.unregister("old_mints")
            self.stats["tokens_dropped"] += len(old)
        for pool in list(self.farm_keys):
            keys = self.farm_keys[pool]
            keys.drop_before(last - h24)
            if not len(keys):
                del self.farm_keys[pool]

    def _arrival_of(self, c, meta):
        """Event.arrival for the segment's transactions: (received_unix_ns, created_unix_ns, the
        segment's written_unix); a transaction without an arrival row gets (None, None, written)."""
        arrived, written = self._arrivals(c), meta.get("written_unix")
        return lambda slot, tx: arrived.get((slot, tx), (None, None)) + (written,)

    def _arrivals(self, c):
        """{(slot, tx_index): (received_unix_ns, created_unix_ns)} of one segment, the earliest per
        transaction (it can arrive on more than one stream)."""
        src = c.src("arrivals")
        if not src:
            return {}
        return {(slot, tx): (received, created) for slot, tx, received, created in self.con.execute(
            f"""SELECT slot, tx_index, min(received_unix_ns), min({c.col('arrivals', 'created_unix_ns', 'BIGINT')})
                FROM {src} GROUP BY slot, tx_index""").fetchall()}

    def _segments(self):
        """Complete segments from next_seq on (the shadow recorder writes them one by one instead)."""
        return [s for s in segment_dirs(self.root) if s[0] >= self.next_seq]

    def events(self):
        """Events of all complete segments in chain order; with follow, keep polling for new ones.
        A slot is released once a later slot has been seen (lag_slots), the rest at the end."""
        heap, last_out, top = [], None, None
        tie, queued = itertools.count(), set()  # equal (order, venue, key) never compare the Events
        out_keys = set()  # keys released at slot last_out: one coming again there is a duplicate too
        while True:
            n = 0
            for seq, seg, meta in self._segments():
                n += 1
                for e in self.segment_events(seq, seg, meta):
                    if last_out is not None and e.slot < last_out:
                        self.stats["late_rows"] += 1  # arrived after its slot was released
                        continue
                    k = (e.order, e.venue, e.key)
                    if k in queued or (e.slot == last_out and k in out_keys):
                        self.stats["duplicate_rows"] += 1
                        continue
                    queued.add(k)
                    heapq.heappush(heap, (e.order, e.venue, e.key, next(tie), e))
                    top = e.slot if top is None else max(top, e.slot)
                self.next_seq = seq + 1
                while heap and heap[0][0][0] <= top - self.lag:
                    e = heapq.heappop(heap)[4]
                    k = (e.order, e.venue, e.key)
                    queued.discard(k)
                    if e.slot != last_out:
                        out_keys.clear()
                    out_keys.add(k)
                    last_out = e.slot
                    yield e
            if not self.follow:
                break
            if not n:
                time.sleep(self.poll_s)
        while heap:
            yield heapq.heappop(heap)[4]
