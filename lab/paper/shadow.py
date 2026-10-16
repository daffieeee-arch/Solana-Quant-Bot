"""Shadow live on an archived epoch: the live recorder simulated from OF1 chunks, LiveFeed and the
paper engine on its segments, compared with the engine's replay of the store over the same slots
(coordinator, 2026-10-12: end-to-end test on epoch 1052; forward data, illustration only).

  python -m paper.shadow --store <fwd1052.duckdb> --out DIR [--from A --to B] [--gaps 3]
                         [--reconnects 6] [--seed 1052] [--no-probes] [--variants 0.04:600,...]

Under DIR:
  1. The #180 watchlist (wallets.watchlist: F7-reversed in at >= 120 SOL real depth and organic, out
     below 100 SOL; M1 for 6 h) stepped per UTC minute over the store's per-(pool, minute)
     aggregates: the list valid for minute m was decided after m - 1. The recorder's probes: a
     reversed pool created inside the data whose WSOL vault reaches 120 SOL within 60 min of its
     creation (new_vaults) is followed from that slot plus PROBE_LATENCY_MS for 180 min, and its
     earlier transactions (from its creation) arrive as a backfill/ BACKFILL_WAIT_MS later.
  2. replay/: the engine on ReplayFeed(store) (no risk limits, d, q, the store's clock).
  3. rec/segments/ and live/: the engine on ShadowFeed, a LiveFeed whose segments the simulated
     recorder writes one UTC minute at a time just before LiveFeed reads them, with the engine's
     open-pools.json of that moment. A segment holds every row of the transactions the recorder
     would have received in its slots:
       tx_watchlist  transactions with an event of a pool on the minute's list or with an open
                     position (open-pools.json);
       tx_probes     transactions with an event of a probed pool;
       tx_new_pools  graduations (CompletePumpAmmMigrationEvent);
       tx_new_reversed  reversed-pool creations (CreatePoolEvent with WSOL as base);
       tx_pump_creates  with --pump-creates: every pump.fun create transaction (its CreateEvent gives
                     LiveFeed the creator of a token created after the live start);
       pool_creations every CreatePoolEvent as a pool account row; blocks: every slot.
     The real filter matches any account of a transaction; one that names a pool without an event
     of it is not modelled. Lossless reconnects move a segment's last slots into the next one
     (from_slot replay, filtered with the next minute's list); gaps drop every slot of a range
     (resumed without from_slot). Usage is estimated at 3 KB per transaction (#180).
  4. compare.json / compare.md: signals, orders, fills and exits of F7 and N2_F7, live against
     replay, with what live received read from the segments. Every difference gets a cause: curve
     (the recorder does not take curve transactions), follows_f7 (the N2 of an F7 trigger that
     differs), n2_pick (the N2 draw came from a different hour set: live knows only received
     pools), busy_follows (busy in one run because of an earlier different position), gap,
     not_seen (no event of the trigger's slot was received: the watchlist did not see it),
     state_stale (the pool's last event before the entry was not received, e.g. an N2 pick seen
     only as the other pool of a received transaction), window_incomplete (an event in
     [t - 400, t + d] was not received), position_events_unseen (during the position),
     creator_unknown (an F7 trigger live took while the replay's creator rule vetoed it: the token
     was created after the live start, so live never got its CreateEvent, a curve transaction, and
     took the pool's coin creator as the creator; the real creator, who had moved the creator fee
     to a sharing config, sold in F7's creator window [t - 200 v1 slots, t]), or unexplained,
     which must be 0.
  5. live-risk/: `engine --live` as it runs live (risk.yaml limits, the clock measured from the
     segments' block times, decisions from the first segment) on the same segments, with its daily
     reports; reconcile.json: paper.reconcile against the chunks on every segment with a simulated
     reconnect or gap and every --reconcile-every-th other one.
"""

import argparse
import bisect
import ctypes
import gc
import glob
import itertools
import json
import os
import shutil
import sqlite3
import sys
import time
import types
from collections import Counter, defaultdict, deque

import duckdb

import numpy as np
import pyarrow as pa

from backtest import data, streams
from backtest.store import DAY_MS
from backtest.strategies import F7_CREATOR_MS, F7_GRID, HOUR_MS, Clock, decision_bounds
from wallets import watchlist as W

from . import reconcile
from .engine import HISTORY_SLOTS, Engine
from .feed import HOLDOUT_END, ReplayFeed
from .journal import Journal
from .live import WSOL, LiveFeed

SOL = 1_000_000_000
PROBE_SOL = 120.0                # recorder --probe-depth-sol
FRESH_VAULT_MS = 60 * 60_000     # recorder --fresh-vault-minutes: a new pool's WSOL vault is followed this long
PROBE_MS = 180 * 60_000          # recorder --probe-minutes
PROBE_LATENCY_MS = 2_000         # the vault update that crosses 120 SOL to the probe on the filter (urgent update)
BACKFILL_WAIT_MS = 20_000        # recorder --backfill-wait-secs: the backfill comes with the segment after this
BACKFILL_AFTER = 150             # the backfill's to_slot: the last closed slot + 150
TX_BYTES = 3072                  # a full transaction with logs (#180, triton-edge §6)
GAP_SLOTS = (150, 600, 2400)     # simulated gaps: ~40 s, ~2.5 min, ~10 min at 250 ms
RECONNECT_SLOTS = (40, 200)      # lossless reconnects: a segment's last slots come with the next one
STREAMS = {1: "tx_watchlist", 2: "tx_probes", 3: "tx_new_pools", 4: "tx_new_reversed", 5: "tx_pump_creates"}
FAMILIES = ("F7", "N2_F7")
# Work tables are written one slot window at a time, each window sorted: the tables end up in slot
# order (a segment's copy reads only its own slots via zonemaps) while no sort holds more than one
# window. One sort over a whole epoch's txu ran out of a 1 GB connection (shadow-1052-db9a9c9).
WINDOW_SLOTS = 20_000


# ----------------------------------------------------------------------------- inputs from the store


def minute_starts(scon, lo, hi):
    """[(minute, first slot)] of the store's blocks in [lo, hi)."""
    return scon.execute(f"""SELECT block_time // 60 AS m, min(slot) FROM blocks WHERE slot >= {lo} AND slot < {hi}
                            GROUP BY 1 ORDER BY 1""").fetchall()


def watchlists(scon, lo, hi, warm_lo, rules=None):
    """{minute: {pool: tags}}: the #180 list valid for each minute of [lo, hi)."""
    pools = scon.execute(W.pools_sql(warm_lo, hi)).fetchall()
    create_min = W.minute_of_slot(scon, {r[5] for r in pools if r[5] is not None and r[5] >= warm_lo})
    wl = W.Watchlist(rules or W.Rules())
    for pool, mint, qc, orient, mayhem, cslot, gic in pools:
        wl.add_pool(W.PoolInfo(pool, mint, qc, orient, bool(mayhem), create_min.get(cslot), bool(gic)))
    agg = scon.execute(W.aggregates_sql(warm_lo, hi)).to_arrow_table()
    cols = {k: agg.column(k).to_pylist() for k in ("pool", "minute", "trades", "sol", "farmer_sol", "depth_last")}
    last = scon.execute(f"SELECT max(block_time) // 60 FROM blocks WHERE slot < {hi}").fetchone()[0]
    out, i, n = {}, 0, agg.num_rows
    for m in range(min(cols["minute"]) if n else last, last + 1):
        rows = []
        while i < n and cols["minute"][i] == m:
            rows.append((cols["pool"][i], cols["trades"][i], cols["sol"][i], cols["farmer_sol"][i], cols["depth_last"][i]))
            i += 1
        out[m + 1] = dict(wl.step(m, rows))
    return out


def probes(scon, lo, hi, clock):
    """The recorder's probes: (pool, create_slot, start, end, to_slot, arrive_slot) for every reversed,
    non-mayhem pool created in [lo, hi) whose WSOL vault reaches PROBE_SOL within FRESH_VAULT_MS of its
    creation. The vault crosses at the slot of the event before the first trade that sees the depth
    (or at creation, when the pool was created that deep)."""
    rows = scon.execute(f"""
      WITH p AS (
        SELECT p.pool, p.create_slot, CASE WHEN p.orientation = 'reversed' THEN p.b0 ELSE p.q0 END AS sol0
        FROM pools p LEFT JOIN mints m ON m.mint = p.mint
        WHERE p.quote_class = 'reversed' AND NOT (COALESCE(p.mayhem, false) OR COALESCE(m.mayhem, false))
          AND p.create_slot >= {lo} AND p.create_slot < {hi}),
      e AS (
        SELECT e.pool, e.kind, e.sol_depth, e.slot,
               lag(e.slot) OVER (PARTITION BY e.pool ORDER BY e.slot, e.tx_index, e.outer_ix, e.inner_ix) AS prev_slot
        FROM pool e WHERE e.slot >= {lo} AND e.slot < {hi} AND e.pool IN (SELECT pool FROM p)),
      c AS (
        SELECT pool, min(COALESCE(prev_slot, slot)) AS cross_slot FROM e
        WHERE kind IN ('buy', 'sell') AND sol_depth >= {int(PROBE_SOL * SOL)} GROUP BY pool)
      SELECT p.pool, p.create_slot, CASE WHEN p.sol0 >= {int(PROBE_SOL * SOL)} THEN p.create_slot ELSE c.cross_slot END
      FROM p LEFT JOIN c USING (pool) ORDER BY 3""").fetchall()
    out = []
    for pool, created, cross in rows:
        if cross is None or cross > created + clock.slots(FRESH_VAULT_MS, created):
            continue
        cross = max(cross, created)
        start = cross + clock.slots(PROBE_LATENCY_MS, cross)
        end = start + clock.slots(PROBE_MS, start)
        out.append((pool, created, start, end, start + BACKFILL_AFTER, start + clock.slots(BACKFILL_WAIT_MS, start)))
    return out


# ----------------------------------------------------------------------------- the simulated recorder


class ShadowRecorder:
    """Writes the recorder's segments for [lo, hi) from the chunks, one at a time (write(seq))."""

    def __init__(self, scon, chunks, root, lo, hi, clock, window, probes_on=True, gaps=3, reconnects=6, seed=1052,
                 watch_warmup_minutes=0, work=None, memory="1GB", log=print, reuse=False, pump_creates=False, prepare=True):
        self.root, self.lo, self.hi, self.clock, self.log = root, lo, hi, clock, log
        self.pump_creates = pump_creates
        os.makedirs(os.path.join(root, "segments"), exist_ok=True)
        t0 = time.time()
        warm_lo = max(lo - clock.slots(watch_warmup_minutes * 60_000, lo), 0)
        self.watch = watchlists(scon, lo, hi, warm_lo)
        self.probes = probes(scon, lo, hi, clock) if probes_on else []
        log(f"watchlist {len(self.watch)} minutes (median {np.median([len(w) for w in self.watch.values()] or [0]):.0f} "
            f"pools), {len(self.probes)} probes ({time.time() - t0:.0f}s)")
        # Segments: one per UTC minute; lossless reconnects move the last slots of a segment into the next.
        starts = minute_starts(scon, lo, hi)
        self.ranges = [[m, a, starts[i + 1][1] if i + 1 < len(starts) else hi] for i, (m, a) in enumerate(starts)]
        rng = np.random.default_rng(seed)
        inside = [i for i, (_, a, b) in enumerate(self.ranges[:-1]) if window[0] <= a and b <= window[1]]
        picks = rng.choice(inside, size=min(len(inside), gaps + reconnects), replace=False) if inside else []
        self.gaps, self.reconnects = [], {}
        for j, i in enumerate(sorted(int(x) for x in picks)):
            m, a, b = self.ranges[i]
            if j % 2 == 1 and len(self.reconnects) < reconnects or len(self.gaps) >= gaps:
                k = min(RECONNECT_SLOTS[len(self.reconnects) % len(RECONNECT_SLOTS)], b - a - 1)
                self.ranges[i][2] -= k
                self.ranges[i + 1][1] -= k
                self.reconnects[i + 1] = {"after_slot": b - k - 1, "dropped_pending_slots": k,
                                          "reason": "shadow: simulated disconnect, resumed with from_slot"}
            else:
                ga = a + int(rng.integers(0, b - a))
                self.gaps.append((ga, ga + GAP_SLOTS[len(self.gaps) % len(GAP_SLOTS)]))
        self.gaps.sort()
        self.recv = []  # (pool, lo, hi, source): what was received, for the comparison
        self.totals = Counter()
        self.t_start = time.time()
        self.sizes = None  # set by run(): sizes of the engine's and LiveFeed's long-lived state, for the progress lines
        self.state_log = None  # set by run(): <out>/state.jsonl, one line per progress line with every structure
        self.signature = {"from_slot": lo, "to_slot": hi, "probes": probes_on, "watch_warmup_minutes": watch_warmup_minutes,
                          "chunks": [c[2] for c in chunks], "pools": len({p for w in self.watch.values() for p in w}),
                          "probe_pools": len(self.probes), "version": 2}
        if pump_creates:
            self.signature["pump_creates"] = True
        if not prepare:  # --from-compare: the segments are written; ranges, gaps and the lists are what counts
            return
        self._prepare(chunks, work or os.path.join(os.path.dirname(root.rstrip("/")), "work"), memory, reuse)
        log(f"recorder input ready ({time.time() - t0:.0f}s): {len(self.ranges)} segments, gaps {self.gaps}, "
            f"reconnects after {[r['after_slot'] for r in self.reconnects.values()]}")

    def _prepare(self, chunks, work, memory, reuse=False):
        """The pools of every transaction (txu), and every row of the transactions a segment will
        most likely take (pools ever on the list or probed, the other pools of their transactions,
        graduations, reversed creations) in a work database, so a segment is a few small copies.
        A segment that needs other transactions (an open position in a pool outside that set)
        loads them from the chunks for its own slots (_load)."""
        os.makedirs(work, exist_ok=True)
        path = os.path.join(work, "shadow-rows.duckdb")
        ready = os.path.join(work, "READY.json")
        self.chunks = chunks
        self.loads = 0
        if reuse and os.path.exists(ready) and os.path.exists(path):
            with open(ready) as f:
                done = json.load(f)
            if done.get("signature") == self.signature:
                con = self.con = data.connect(threads=2, memory=memory, temp=os.path.join(work, "spill"))
                con.execute(f"ATTACH '{path}' AS w")
                con.execute("USE w")
                self.tables = done["tables"]
                self.all_tables = list(self.tables)
                con.execute("CREATE OR REPLACE TABLE segr (seq INTEGER, a UBIGINT, b UBIGINT)")
                con.executemany("INSERT INTO segr VALUES (?, ?, ?)", [(i, a, b) for i, (_, a, b) in enumerate(self.ranges)])
                self._index_rows()
                self.log(f"reusing the work database {path} (built {done.get('built')})")
                return
            self.log("work database signature differs: rebuilding")
        for p in (path, path + ".wal", ready):
            if os.path.exists(p):
                os.remove(p)
        con = self.con = data.connect(threads=2, memory=memory, temp=os.path.join(work, "spill"))
        con.execute(f"ATTACH '{path}' AS w")
        con.execute("USE w")
        lo, hi = self.lo, self.hi
        span = f"slot >= {lo} AND slot < {hi}"
        u = sorted({p for w in self.watch.values() for p in w} | {p[0] for p in self.probes})
        con.register("u_arrow", pa.table({"pool": u}))
        con.execute("CREATE TABLE u AS SELECT pool FROM u_arrow")
        con.unregister("u_arrow")
        tables = sorted({os.path.relpath(os.path.dirname(f), c[2]) for c in chunks
                         for f in glob.glob(os.path.join(c[2], "pump*", "*", "*.parquet"))})
        self.tables = {}
        self.all_tables = tables
        # Every table is written in slot order: a segment's copy reads only its own slots (zonemaps);
        # unordered (preserve_insertion_order = false) each copy scanned whole tables.
        with_pool = [t for t in tables if "pool" in data.columns(t, chunks)]
        if with_pool:
            self._ordered("txu", lambda a, b: " UNION ALL ".join(
                f"SELECT DISTINCT slot::UBIGINT AS slot, tx_index::BIGINT AS tx_index, pool FROM {data.parquet(t, chunks)} "
                f"WHERE slot >= {a} AND slot < {b} AND pool IS NOT NULL" for t in with_pool))
        else:
            con.execute("CREATE TABLE txu (slot UBIGINT, tx_index BIGINT, pool VARCHAR)")
        mig = data.parquet("pump/CompletePumpAmmMigrationEvent", chunks, missing_ok=True)
        con.execute(f"CREATE TABLE grad AS SELECT DISTINCT slot, tx_index FROM {mig} WHERE {span}" if mig else
                    "CREATE TABLE grad (slot UBIGINT, tx_index BIGINT)")
        cp = data.parquet("pump_amm/CreatePoolEvent", chunks, missing_ok=True)
        if cp:
            mayhem = "COALESCE(is_mayhem_mode, false)" if "is_mayhem_mode" in data.columns("pump_amm/CreatePoolEvent", chunks) \
                else "false"
            self._ordered("pc", lambda a, b: f"""SELECT slot, tx_index, pool, base_mint, quote_mint, coin_creator,
                                   {mayhem} AS is_mayhem_mode FROM {cp} WHERE slot >= {a} AND slot < {b}""")
        else:
            con.execute("""CREATE TABLE pc (slot UBIGINT, tx_index BIGINT, pool VARCHAR, base_mint VARCHAR,
                                            quote_mint VARCHAR, coin_creator VARCHAR, is_mayhem_mode BOOLEAN)""")
        con.execute(f"CREATE TABLE crev AS SELECT DISTINCT slot, tx_index FROM pc WHERE base_mint = '{WSOL}'")
        # --pump-creates (the recorder's account_required [pump.fun, its mint authority]): every create transaction.
        cre = data.parquet("pump/CreateEvent", chunks, missing_ok=True) if self.pump_creates else None
        con.execute(f"CREATE TABLE cre AS SELECT DISTINCT slot, tx_index FROM {cre} WHERE {span}" if cre else
                    "CREATE TABLE cre (slot UBIGINT, tx_index BIGINT)")
        # The other pools of those transactions can trade live too (an N2 pick, a trigger): take them along.
        con.execute("""CREATE TEMP TABLE k0 AS SELECT DISTINCT slot, tx_index FROM
                       (SELECT slot, tx_index FROM txu WHERE pool IN (SELECT pool FROM u)
                        UNION ALL SELECT * FROM grad UNION ALL SELECT * FROM crev)""")
        con.execute("INSERT INTO u SELECT DISTINCT pool FROM txu SEMI JOIN k0 USING (slot, tx_index) "
                    "WHERE pool NOT IN (SELECT pool FROM u)")
        con.execute("""CREATE TABLE allkeys AS SELECT DISTINCT slot, tx_index FROM
                       (SELECT slot, tx_index FROM txu WHERE pool IN (SELECT pool FROM u)
                        UNION ALL SELECT * FROM grad UNION ALL SELECT * FROM crev UNION ALL SELECT * FROM cre)""")
        self.loads = 0
        con.execute(f"CREATE TABLE blocks_t AS SELECT * FROM {data.parquet('blocks', chunks)} WHERE {span} ORDER BY slot")
        con.execute("CREATE TABLE segr (seq INTEGER, a UBIGINT, b UBIGINT)")
        con.executemany("INSERT INTO segr VALUES (?, ?, ?)", [(i, a, b) for i, (_, a, b) in enumerate(self.ranges)])
        for i, t in enumerate(tables):
            name = f"t{i}"
            src = data.parquet(t, chunks)
            self._ordered(name, lambda a, b: f"""SELECT x.* FROM {src} x
                            SEMI JOIN (SELECT slot, tx_index FROM allkeys WHERE slot >= {a} AND slot < {b}) k
                              ON x.slot = k.slot AND x.tx_index = k.tx_index
                            WHERE x.slot >= {a} AND x.slot < {b}""")
            self.tables[t] = name
        self._index_rows()
        con.execute("CHECKPOINT")
        with open(ready + ".tmp", "w") as f:
            json.dump({"signature": self.signature, "tables": self.tables, "built": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
        os.replace(ready + ".tmp", ready)

    def _ordered(self, name, select):
        """CREATE TABLE name from select(a, b), the rows of slots [a, b), one sorted window at a time."""
        for i, a in enumerate(range(self.lo, self.hi, WINDOW_SLOTS) or [self.lo]):
            sql = f"SELECT * FROM ({select(a, min(a + WINDOW_SLOTS, self.hi))}) ORDER BY slot, tx_index"
            self.con.execute(f"CREATE TABLE {name} AS {sql}" if i == 0 else f"INSERT INTO {name} {sql}")

    def _index_rows(self):
        """Per table, the segments that have rows (a segment copies only those tables)."""
        self.with_rows = {t: {r[0] for r in self.con.execute(f"""SELECT DISTINCT s.seq FROM (SELECT DISTINCT slot FROM {name}) x
                                                                JOIN segr s ON x.slot >= s.a AND x.slot < s.b""").fetchall()}
                          for t, name in self.tables.items()}

    def _load(self, seq, a, b):
        """Rows of this segment's transactions that are not in the work database yet, from the
        chunks for [a, b) only."""
        con = self.con
        con.execute("""CREATE OR REPLACE TEMP TABLE need AS SELECT slot, tx_index FROM seg_keys
                           EXCEPT SELECT slot, tx_index FROM allkeys""")
        if not con.execute("SELECT count(*) FROM need").fetchone()[0]:
            return
        self.loads += 1
        for t, name in self.tables.items():
            got = con.execute(f"""INSERT INTO {name} BY NAME SELECT x.* FROM {data.parquet(t, self.chunks)} x
                                  SEMI JOIN need k ON x.slot = k.slot AND x.tx_index = k.tx_index
                                  WHERE x.slot >= {a} AND x.slot < {b}""").fetchone()[0]
            if got:
                self.with_rows[t].add(seq)
        con.execute("INSERT INTO allkeys SELECT * FROM need")

    def _pred(self, a, b, col="slot"):
        cut = "".join(f" AND NOT ({col} >= {ga} AND {col} < {gb})" for ga, gb in self.gaps if ga < b and a < gb)
        return f"{col} >= {a} AND {col} < {b}{cut}"

    def _copy(self, sql, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        n = self.con.execute(f"COPY ({sql}) TO '{path}' (FORMAT parquet)").fetchone()[0]
        if not n:
            os.remove(path)
            if not os.listdir(os.path.dirname(path)):
                os.rmdir(os.path.dirname(path))
        return n

    def open_pools(self):
        path = os.path.join(self.root, "open-pools.json")
        if not os.path.exists(path):
            return set()
        with open(path) as f:
            return set(json.load(f).get("pools", ()))

    def write(self, seq):
        """Write segment seq (its watchlist from the minute's list and the engine's open pools now)."""
        m, a, b = self.ranges[seq]
        d = os.path.join(self.root, "segments", f"{seq:09d}")
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d)
        watch, opened = self.watch.get(m, {}), self.open_pools()
        listed = set(watch) | opened
        recv = [(p, a, b, "tx_watchlist") for p in sorted(listed)]
        recv += [(p, max(a, s), min(b, e), "tx_probes") for p, _, s, e, _, _ in self.probes
                 if s < b and a < e and p not in listed]
        self.recv += recv
        con = self.con
        con.register("recv_arrow", pa.table({"pool": [r[0] for r in recv], "lo": [r[1] for r in recv],
                                             "hi": [r[2] for r in recv], "src": [1 if r[3] == "tx_watchlist" else 2 for r in recv]},
                                            schema=pa.schema([("pool", pa.string()), ("lo", pa.uint64()), ("hi", pa.uint64()),
                                                              ("src", pa.int32())])))
        con.execute(f"""CREATE OR REPLACE TEMP TABLE seg_keys AS SELECT slot, tx_index, min(src) AS src FROM (
                          SELECT t.slot, t.tx_index, r.src FROM txu t JOIN recv_arrow r
                            ON t.pool = r.pool AND t.slot >= r.lo AND t.slot < r.hi WHERE {self._pred(a, b, 't.slot')}
                          UNION ALL SELECT slot, tx_index, 3 FROM grad WHERE {self._pred(a, b)}
                          UNION ALL SELECT slot, tx_index, 4 FROM crev WHERE {self._pred(a, b)}
                          UNION ALL SELECT slot, tx_index, 5 FROM cre WHERE {self._pred(a, b)}) GROUP BY 1, 2""")
        con.unregister("recv_arrow")
        self._load(seq, a, b)
        rows = 0
        for t, name in self.tables.items():
            if seq in self.with_rows[t]:
                rows += self._copy(f"""SELECT x.* FROM {name} x SEMI JOIN seg_keys k ON x.slot = k.slot AND x.tx_index = k.tx_index
                                       WHERE x.slot >= {a} AND x.slot < {b}""", os.path.join(d, t, "part-00000.parquet"))
        n_slots = self._copy(f"SELECT * FROM blocks_t WHERE {self._pred(a, b)}", os.path.join(d, "blocks", "part-00000.parquet"))
        n_pc = self._copy(f"""SELECT slot, pool, base_mint, quote_mint, coin_creator, is_mayhem_mode FROM pc
                              WHERE {self._pred(a, b)}""", os.path.join(d, "pool_creations", "part-00000.parquet"))
        per = dict(con.execute("SELECT src, count(*) FROM seg_keys GROUP BY 1").fetchall())
        usage = [{"minute": m, "stream": STREAMS[s], "bytes": n * TX_BYTES, "messages": n, "note": "estimated: 3 KB per transaction"}
                 for s, n in sorted(per.items())]
        usage.append({"minute": m, "stream": "blocks_meta", "bytes": n_slots * 200, "messages": n_slots, "note": "estimated"})
        if n_pc:
            usage.append({"minute": m, "stream": "pool_creations", "bytes": n_pc * 400, "messages": n_pc, "note": "estimated"})
        # Backfills that arrive in this segment: the probe pool's transactions from its creation.
        backfills, n_back = [], 0
        for i, (p, created, start, _, to_slot, arrive) in enumerate(self.probes):
            if not a <= arrive < b:
                continue
            n = 0
            for t, name in self.tables.items():
                n += self._copy(f"""SELECT x.* FROM {name} x SEMI JOIN (SELECT DISTINCT slot, tx_index FROM txu
                                      WHERE pool = '{p}' AND slot >= {created} AND slot <= {to_slot}) k
                                      ON x.slot = k.slot AND x.tx_index = k.tx_index
                                      WHERE x.slot >= {created} AND x.slot <= {to_slot}""",
                                os.path.join(d, "backfill", t, f"part-{i:05d}.parquet"))
            txs = con.execute(f"SELECT count(DISTINCT (slot, tx_index)) FROM txu WHERE pool = '{p}' AND slot >= {created} "
                              f"AND slot <= {to_slot}").fetchone()[0]
            n_back += txs
            backfills.append({"id": f"{p}-{created}", "pool": p, "from_slot": created, "to_slot": to_slot, "rows": n,
                              "status": "complete", "error": None})
        if n_back:
            usage.append({"minute": m, "stream": "backfill", "bytes": n_back * TX_BYTES, "messages": n_back,
                          "note": "estimated: 3 KB per transaction"})
        reconnects = [dict(self.reconnects[seq], unix=m * 60)] if seq in self.reconnects else []
        for ga, gb in self.gaps:
            if a <= gb < b or (seq == len(self.ranges) - 1 and a <= ga):
                reconnects.append({"unix": m * 60, "after_slot": ga - 1, "dropped_pending_slots": 0,
                                   "reason": "shadow: simulated disconnect (resumed without from_slot)", "shadow_gap": [ga, gb]})
        wl = {"pools": [{"pool": p} for p in sorted(listed)], "wallets": [], "pools_partial": [], "wallets_partial": [],
              "vaults": [], "new_pools": True, "new_reversed": True,
              "filter": {"tx_pools": len(listed), "tx_probes": sum(1 for r in recv if r[3] == "tx_probes")},
              "source": {"valid_for_minute": m, "counts": {"pools": len(watch), "open_pools": len(opened)}}}
        meta = {"seq": seq, "first_slot": a, "last_slot": b - 1, "complete": True, "usage": usage, "budget": {},
                "filter": wl["filter"], "reconnects": reconnects, "totals": {"rows": rows},
                "shadow": {"minute": m, "watchlist": len(watch), "open_pools": len(opened)}}
        if backfills:
            meta["backfills"] = backfills
        with open(os.path.join(d, "watchlist.json"), "w") as f:
            json.dump(wl, f)
        with open(os.path.join(d, "_segment.json"), "w") as f:
            json.dump(meta, f)
        self.totals["segments_with_chunk_loads"] = self.loads
        self.totals.update({"segments": 1, "rows": rows, "slots": n_slots, "pool_creations": n_pc, "backfills": len(backfills),
                            "backfill_txs": n_back, **{STREAMS[s]: n for s, n in per.items()}})
        if seq % 100 == 0:
            rss, st = _rss_mb(), self.sizes() if self.sizes else None
            top = dict(sorted(st["est_mb"].items(), key=lambda kv: -kv[1])[:STATE_TOP]) if st else None
            self.log(f"segment {seq}/{len(self.ranges)}: {len(watch)} on the list, {len(opened)} open, {rows} rows, "
                     f"rss {rss} MB, {time.time() - self.t_start:.0f}s"
                     + (f", state {st['n']}, est_mb {top}, mem {st['mem']}, measured in {st['measure_s']}s" if st else ""))
            if st and self.state_log:
                with open(self.state_log, "a") as f:
                    f.write(json.dumps({"seq": seq, "rss_mb": rss, "s": round(time.time() - self.t_start), **st}) + "\n")
        return seq, d, meta

    def coverage(self):
        return Coverage(self.recv, self.gaps)

    def from_segments(self):
        """--from-compare: what write() recorded, rebuilt from the written segments: recv from each segment's
        slots (_segment.json) and listed pools (watchlist.json: the minute's list and the open pools then)
        plus the probes (from the store, as in write()), and the totals from the usage rows. A segment whose
        slots are not this recorder's range (another seed, gaps or window) is refused."""
        self.recv, self.totals = [], Counter()
        for seq, (m, a, b) in enumerate(self.ranges):
            d = os.path.join(self.root, "segments", f"{seq:09d}")
            with open(os.path.join(d, "_segment.json")) as f:
                meta = json.load(f)
            if (meta["first_slot"], meta["last_slot"] + 1) != (a, b):
                raise SystemExit(f"segment {seq}: slots {meta['first_slot']}..{meta['last_slot']} are not the range {a}..{b - 1}")
            with open(os.path.join(d, "watchlist.json")) as f:
                listed = {x["pool"] for x in json.load(f)["pools"]}
            self.recv += [(p, a, b, "tx_watchlist") for p in sorted(listed)]
            self.recv += [(p, max(a, s), min(b, e), "tx_probes") for p, _, s, e, _, _ in self.probes
                          if s < b and a < e and p not in listed]
            self.totals.update({"segments": 1, "rows": meta["totals"]["rows"], "backfills": len(meta.get("backfills", []))})
            for u in meta["usage"]:
                key = "backfill_txs" if u["stream"] == "backfill" else u["stream"]
                self.totals[key] += u["messages"]
        return self

    def gap_segments(self):
        return {i for i, (_, a, b) in enumerate(self.ranges) if any(ga < b and a < gb for ga, gb in self.gaps)}


def _state_sizes(feed, eng):
    """Sizes of the long-lived structures of a live run: what grows, if anything, shows here."""
    return {"recent_keys": len(eng.recent), "recent_legs": sum(len(v) for v in eng.recent.values()),
            "last_state": len(eng.last_state), "curve_hist_legs": sum(len(v) for v in feed.curve_hist.values()),
            "farmer_entries": feed.farmers.entries(), "seen_keys": len(feed.seen_keys),
            "tokens": len(feed.tokens), "pools": len(feed.pools), "busy": len(eng.busy),
            "watch_pools": len(eng.watch.wl.pools) if eng.watch is not None else 0}


STATE_SAMPLE = 200  # entries sampled per structure for its byte estimate
STATE_INNER = 32  # elements sampled inside a nested container larger than this
STATE_DEPTH = 6
STATE_TOP = 8  # structures in the progress line (state.jsonl has all)


def _structures(feed, eng):
    """The long-lived containers of a live run by name: LiveFeed's, the engine's and the live watchlist's."""
    out = {f"feed.{k}": getattr(feed, k, None)
           for k in ("curve_hist", "seen_keys", "seen_order", "farm_keys", "ctx", "tokens", "pools", "pool_class", "unknown_seen",
                     "seeded", "epoch_span", "history", "backfill_pending", "new_pools", "usage")}
    out["feed.farmers.hist"] = feed.farmers.hist
    out.update({f"eng.{k}": getattr(eng, k, None)
                for k in ("recent", "last_state", "last_seen", "open", "pending", "busy", "farm_win", "hour_events", "grad_wait",
                          "cool", "slot_arrivals")})
    w = getattr(eng, "watch", None)
    if w is not None:
        out.update({f"watch.{k}": getattr(w, k, None) for k in ("rows", "slots", "minutes", "added", "_pending")})
        out.update({f"watch.wl.{k}": getattr(w.wl, k, None) for k in ("pools", "state", "archive", "members")})
    return {k: v for k, v in out.items() if v is not None}


_LEAF = (str, bytes, int, float, bool, type(None))
_SKIP = (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.MethodType)


def _deep(o, seen, depth=STATE_DEPTH):
    """Bytes of o and of what it holds (an object reached twice counts once per `seen`). Containers
    larger than STATE_INNER are extrapolated from STATE_INNER evenly spaced elements."""
    if id(o) in seen or isinstance(o, _SKIP):
        return 0
    seen.add(id(o))
    n = sys.getsizeof(o)
    if depth == 0 or isinstance(o, _LEAF):
        return n
    if not isinstance(o, (dict, list, tuple, set, frozenset, deque)):
        d = getattr(o, "__dict__", None)
        if d is not None:
            n += _deep(d, seen, depth - 1)
        for cls in type(o).__mro__:
            slots = cls.__dict__.get("__slots__", ())
            for name in (slots,) if isinstance(slots, str) else slots:
                if name not in ("__dict__", "__weakref__") and hasattr(o, name):
                    n += _deep(getattr(o, name), seen, depth - 1)
        return n
    k = len(o)
    if k == 0:
        return n
    sample = list(itertools.islice(o.items() if isinstance(o, dict) else o, 0, None, max(1, k // STATE_INNER)))
    if isinstance(o, dict):
        got = sum(_deep(a, seen, depth - 1) + _deep(b, seen, depth - 1) for a, b in sample)
    else:
        got = sum(_deep(x, seen, depth - 1) for x in sample)
    return n + round(got * k / len(sample))


def _est_mb(c):
    """Estimated MB of a long-lived container: its own table plus its entries, from STATE_SAMPLE of them.
    An object shared by many entries (an interned string, a state two legs share) counts once per
    sample, then scales with the entries: an estimate, not an exact size."""
    n, seen = len(c), set()
    if n == 0:
        return 0.0
    step = max(1, n // STATE_SAMPLE)
    if isinstance(c, dict):
        sample = list(itertools.islice(c.items(), 0, None, step))
        per = sum(_deep(k, seen) + _deep(v, seen) for k, v in sample) / len(sample)
    else:
        sample = list(itertools.islice(c, 0, None, step))
        per = sum(_deep(x, seen) for x in sample) / len(sample)
    return round((sys.getsizeof(c) + per * n) / 2**20, 1)


class _MallInfo2(ctypes.Structure):
    _fields_ = [(n, ctypes.c_size_t) for n in ("arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks",
                                                 "uordblks", "fordblks", "keepcost")]


def _mem_parts(cons):
    """Where the process's memory sits: Python's small-object blocks, glibc malloc (in use, and free
    inside its arenas: fragmentation), Arrow's pool and each DuckDB database (duckdb_memory()).
    RSS minus these is memory none of them reports (DuckDB's or Arrow's allocator overhead, code)."""
    out = {"py_blocks_k": sys.getallocatedblocks() // 1000, "arrow_mb": pa.total_allocated_bytes() >> 20}
    try:
        f = ctypes.CDLL(None).mallinfo2  # glibc >= 2.33
        f.restype = _MallInfo2
        m = f()
        out["malloc_used_mb"], out["malloc_free_mb"] = (m.uordblks + m.hblkhd) >> 20, m.fordblks >> 20
    except (OSError, AttributeError):
        pass
    for name, con in cons.items():
        if con is None:
            continue
        try:
            cur = con.cursor()  # its own result: a result the caller still reads stays intact
            try:
                out[f"duckdb_{name}_mb"] = int(cur.execute("SELECT COALESCE(sum(memory_usage_bytes), 0) FROM duckdb_memory()")
                                               .fetchone()[0]) >> 20
            finally:
                cur.close()
        except duckdb.Error:
            pass
    return out


def _state_report(feed, eng, cons):
    """The progress lines' state: counts (_state_sizes), estimated MB per structure and the memory parts."""
    t0 = time.time()
    est = {k: _est_mb(v) for k, v in _structures(feed, eng).items()}
    return {"n": _state_sizes(feed, eng), "est_mb": est, "mem": _mem_parts(cons), "measure_s": round(time.time() - t0, 2)}


def _rss_mb():
    """This process's resident memory in MB (Linux), for the progress lines."""
    try:
        with open("/proc/self/status") as f:
            return next(int(line.split()[1]) // 1024 for line in f if line.startswith("VmRSS:"))
    except (OSError, StopIteration, ValueError):
        return -1


class ShadowFeed(LiveFeed):
    """LiveFeed whose segments the shadow recorder writes just before they are read."""

    def __init__(self, rec, **kw):
        super().__init__(rec.root, follow=False, **kw)
        self.rec = rec

    def _segments(self):
        for seq in range(self.next_seq, len(self.rec.ranges)):
            yield self.rec.write(seq)


# ----------------------------------------------------------------------------- comparison


class Coverage:
    """Which (pool, slot) the recorder received: per pool, merged [lo, hi) intervals, minus the gaps."""

    def __init__(self, recv, gaps):
        iv = defaultdict(list)
        for p, a, b, _ in recv:
            iv[p].append((a, b))
        self.iv = {}
        for p, xs in iv.items():
            xs.sort()
            merged = [list(xs[0])]
            for a, b in xs[1:]:
                if a <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            self.iv[p] = merged
        self.gaps = sorted(gaps)

    def in_gap(self, slot):
        return any(a <= slot < b for a, b in self.gaps)

    def gap_overlaps(self, a, b):
        return any(ga <= b and a < gb for ga, gb in self.gaps)

    def seen(self, pool, slot):
        return not self.in_gap(slot) and any(a <= slot < b for a, b in self.iv.get(pool, ()))


def _journal(path):
    db = sqlite3.connect(os.path.join(path, "journal.sqlite"))
    db.row_factory = sqlite3.Row
    out = {}
    for t in ("signals", "orders", "fills", "exits"):
        rows = db.execute(f"SELECT * FROM {t} WHERE family IN ({','.join('?' * len(FAMILIES))})", FAMILIES).fetchall()
        out[t] = {(r["family"], r["variant"], r["key"], r["t"]) + ((r["side"],) if t == "fills" else ()): dict(r) for r in rows}
    db.close()
    return out


SIG_COLS = ("status", "reason", "depth", "trigger_trader")
ORD_COLS = ("entry_slot", "size", "status", "reason")
FILL_COLS = ("slot", "tokens", "quote")
EXIT_COLS = ("entry_slot", "exit_slot", "exit_reason", "tokens", "cost", "proceeds", "pnl")


def _same(a, b, cols):
    if a is None or b is None:
        return a is None and b is None
    return all(a.get(c) == b.get(c) for c in cols)


def creator_sells_unknown_live(scon, pools, snapshot_before):
    """Pool -> sorted slots of sells by its token's creator, for the pools whose creator live cannot
    know: the token was created at or after the live start (the recorder takes no curve
    transactions, so its CreateEvent never arrives and live takes the pool's coin creator as the
    creator) and its creator is not the coin creator (the creator moved the curve's creator fee to a
    sharing config: pfee migrate_bonding_curve_creator). The replay's F7 creator rule sees these
    sells; live does not."""
    if not pools:
        return {}
    scon.register("cr_pools", pa.table({"pool": sorted(pools)}))
    out = defaultdict(list)
    for pool, slot in scon.execute("""
          SELECT e.pool, e.slot FROM pool e JOIN pools p USING (pool) JOIN mints m ON m.mint = p.mint
          WHERE e.pool IN (SELECT pool FROM cr_pools) AND m.create_slot >= ? AND m.creator <> p.coin_creator
            AND e.trader = m.creator AND e.kind IN ('buy', 'sell') AND NOT e.token_buy
          ORDER BY 1, 2""", [snapshot_before]).fetchall():
        out[pool].append(slot)
    scon.unregister("cr_pools")
    return out


def compare(replay_dir, live_dir, cov, scon, root, d=1, hour_slots=13_500, snapshot_before=None, creator_slots=0):
    """Per signal key: same, or a cause (module docstring). What live received is read from the
    segments themselves: (pool, slot, tx_index) of their pump_amm rows (a backfill is history, not
    events, so it does not count). snapshot_before: the live start (the LiveFeed snapshot's bound);
    creator_slots: F7's creator window in slots."""
    R, L = _journal(replay_dir), _journal(live_dir)
    # The pools of every pool signal, from two hours before it (N2 picks a pool that traded this
    # hour; its state is its last event) to its exit: the store's events and the received ones.
    want = {}
    for k in set(R["signals"]) | set(L["signals"]):
        if (R["signals"].get(k) or L["signals"].get(k))["venue"] != "pool":
            continue
        lo_ = k[3] - 2 * hour_slots
        hi_ = max([x["exit_slot"] for J in (R, L) for x in [J["exits"].get(k)] if x and x.get("exit_slot")] or [k[3] + d])
        w = want.get(k[2])
        want[k[2]] = (lo_, hi_) if w is None else (min(w[0], lo_), max(w[1], hi_))
    events, got = defaultdict(list), set()
    if want:
        scon.register("want_arrow", pa.table({"pool": list(want), "a": [max(0, v[0]) for v in want.values()],
                                              "b": [v[1] for v in want.values()]}))
        for pool, slot, tx in scon.execute("""SELECT DISTINCT e.pool, e.slot, e.tx_index FROM pool e JOIN want_arrow w
                                              ON e.pool = w.pool AND e.slot >= w.a AND e.slot <= w.b ORDER BY 1, 2, 3""").fetchall():
            events[pool].append((slot, tx))
        files = glob.glob(os.path.join(root, "segments", "*", "pump_amm", "*", "*.parquet"))
        if files:
            got = set(scon.execute(f"""SELECT DISTINCT pool, slot, tx_index FROM read_parquet({files!r}, union_by_name = true)
                                       WHERE pool IN (SELECT pool FROM want_arrow)""").fetchall())
        scon.unregister("want_arrow")

    live_only_f7 = {k[2] for k in L["signals"] if k[0] == "F7" and k not in R["signals"] and L["signals"][k]["venue"] == "pool"}
    creator_sells = creator_sells_unknown_live(scon, live_only_f7, snapshot_before) if snapshot_before is not None else {}

    def creator_sold(pool, t):
        xs = creator_sells.get(pool, ())
        i = bisect.bisect_left(xs, t - creator_slots)
        return i < len(xs) and xs[i] <= t

    def unseen(pool, a, b):
        xs = events.get(pool, [])
        i = bisect.bisect_left(xs, (a, -1))
        n = 0
        while i < len(xs) and xs[i][0] <= b:
            n += (pool,) + xs[i] not in got
            i += 1
        return n

    def stale(pool, s):
        """The pool's last event before slot s was not received: live holds an older state."""
        xs = events.get(pool, [])
        i = bisect.bisect_left(xs, (s, -1))
        return i > 0 and (pool,) + xs[i - 1] not in got

    def exit_slot(k):
        xs = [J["exits"].get(k) for J in (R, L)]
        return max([x["exit_slot"] for x in xs if x and x.get("exit_slot")] or [k[3] + d])

    keys = set(R["signals"]) | set(L["signals"])
    # The F7 trigger behind each N2 signal: same slot t (n2_k = 1 pick per trigger and variant).
    f7_at = defaultdict(set)
    for J in (R, L):
        for (fam, var, key, t), s in J["signals"].items():
            if fam == "F7":
                f7_at[(var, t)].add(key)
    n2_picks = {side: defaultdict(set) for side in ("r", "l")}
    for side, J in (("r", R), ("l", L)):
        for (fam, var, key, t) in J["signals"]:
            if fam == "N2_F7":
                n2_picks[side][(var, t)].add(key)
    out = []
    for k in sorted(keys):
        fam, var, key, t = k
        r, l = R["signals"].get(k), L["signals"].get(k)
        venue = (r or l)["venue"]
        orient = (r or l)["orientation"]
        same = (_same(r, l, SIG_COLS) and _same(R["orders"].get(k), L["orders"].get(k), ORD_COLS)
                and _same(R["fills"].get(k + ("buy",)), L["fills"].get(k + ("buy",)), FILL_COLS)
                and _same(R["exits"].get(k), L["exits"].get(k), EXIT_COLS))
        cause = None
        if not same:
            end = exit_slot(k)
            if venue == "curve":
                cause = "curve"
            elif fam == "F7" and r is None and l is not None and creator_sold(key, t):
                cause = "creator_unknown"
            elif fam == "N2_F7" and n2_picks["r"][(var, t)] != n2_picks["l"][(var, t)]:
                trig_r = {x for x in f7_at[(var, t)] if R["signals"].get(("F7", var, x, t))}
                trig_l = {x for x in f7_at[(var, t)] if L["signals"].get(("F7", var, x, t))}
                cause = "follows_f7" if trig_r != trig_l else "n2_pick"
            elif r and l and r["status"] != l["status"] and "busy" in (r["reason"], l["reason"]):
                cause = "busy_follows"
            elif cov.gap_overlaps(t - HISTORY_SLOTS, end):
                cause = "gap"
            elif fam == "F7" and unseen(key, t, t) == sum(1 for x in events.get(key, ()) if x[0] == t):
                cause = "not_seen"
            elif stale(key, t + d):
                cause = "state_stale"
            elif unseen(key, t - HISTORY_SLOTS, t + d):
                cause = "window_incomplete"
            elif unseen(key, t + d, end):
                cause = "position_events_unseen"
            else:
                cause = "unexplained"
        out.append({"family": fam, "variant": var, "key": key, "t": t, "venue": venue, "orientation": orient,
                    "in_replay": r is not None, "in_live": l is not None, "same": same, "cause": cause,
                    "replay_status": r and r["status"], "live_status": l and l["status"],
                    "replay_pnl": (R["exits"].get(k) or {}).get("pnl"), "live_pnl": (L["exits"].get(k) or {}).get("pnl")})
    return out


def summarize(rows):
    """Counts per family, venue/orientation: signals, distinct triggers, same, causes."""
    s = defaultdict(lambda: {"replay": 0, "live": 0, "same": 0, "causes": Counter(), "triggers_replay": set(),
                             "triggers_live": set()})
    for r in rows:
        g = s[(r["family"], r["venue"] if r["venue"] == "curve" else f"pool_{r['orientation']}")]
        g["replay"] += r["in_replay"]
        g["live"] += r["in_live"]
        g["same"] += r["same"]
        if r["cause"]:
            g["causes"][r["cause"]] += 1
        if r["in_replay"]:
            g["triggers_replay"].add((r["key"], r["t"]))
        if r["in_live"]:
            g["triggers_live"].add((r["key"], r["t"]))
    return {f"{fam} {seg}": {"signals_replay": g["replay"], "signals_live": g["live"], "same": g["same"],
                             "triggers_replay": len(g["triggers_replay"]), "triggers_live": len(g["triggers_live"]),
                             "triggers_both": len(g["triggers_replay"] & g["triggers_live"]), "causes": dict(g["causes"])}
            for (fam, seg), g in sorted(s.items())}


# ----------------------------------------------------------------------------- run


def _status(out, step, **kw):
    """<out>/STATUS.json: the last finished step (a run that stops early shows where)."""
    path = os.path.join(out, "STATUS.json")
    with open(path + ".tmp", "w") as f:
        json.dump({"step": step, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}, f)
    os.replace(path + ".tmp", path)


def _record_and_live(a, scon, out, root, lo, hi, clock, window, blo, bhi, h24, variants, resume, info, log):
    """Step 2: the shadow recorder writes the segments one minute at a time while LiveFeed and the engine
    read them. Returns (recorder, the live journal's directory); closes the store connection."""
    shutil.rmtree(root, ignore_errors=True)
    rec = ShadowRecorder(scon, data.complete_chunks(a.chunks), root, lo, hi, clock, window, probes_on=not a.no_probes,
                         gaps=a.gaps, reconnects=a.reconnects, seed=a.seed, watch_warmup_minutes=a.watch_warmup,
                         work=os.path.join(out, "work"), memory=a.memory, log=log, reuse=resume,
                         pump_creates=getattr(a, "pump_creates", False))
    _status(out, "recorder input")
    scon.close()  # not needed while live runs (its cache would hold memory); reopened for the comparison
    gc.collect()
    ldir = os.path.join(out, "live")
    shutil.rmtree(ldir, ignore_errors=True)
    feed = ShadowFeed(rec, store=a.store, snapshot_before=lo, h24=h24, meta_bounds=(blo, bhi), memory=a.memory,
                      seed_farmers=True)
    # The live watchlist runs beside it on what was received (the simulation keeps using the batch list).
    from .watchlist_live import LiveWatchlist

    watch = LiveWatchlist(feed, root)
    eng = Engine(feed, Journal(ldir), None, variants=variants, d=a.d, q=a.q, ignore_risk=True, clock=clock, backup=False,
                 dev_bounds=(blo, bhi), watchlist=watch)
    rec.sizes = lambda: _state_report(feed, eng, {"feed": feed.con, "seed": feed.seed_con, "rec": rec.con})
    rec.state_log = os.path.join(out, "state.jsonl")
    if os.path.exists(rec.state_log):
        os.remove(rec.state_log)
    eng.run()
    rec.sizes = rec.state_log = None
    info["live_watchlist"] = compare_watchlists(os.path.join(root, "watchlist-log.jsonl"), rec.watch)
    info["livefeed"] = dict(feed.stats)
    info["recorder"] = dict(rec.totals)
    feed.close()  # the recorder's and LiveFeed's databases are done: free their memory
    rec.con.close()
    del feed
    gc.collect()
    return rec, ldir


def run(a, log=print):
    """The five steps. With a.resume, a finished replay (replay/DONE.json with the same settings) and
    a finished work database (work/READY.json with the same signature) are reused; the live run,
    engine --live, the comparison and the reconciliation always run again. With a.from_compare the
    finished replay and live run (STATUS.json at least "live", their journals and the segments) are
    reused and only steps 3-5 run: the recorder's coverage is rebuilt from the segments."""
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    resume = getattr(a, "resume", False)
    from_compare = getattr(a, "from_compare", False)
    if from_compare:
        status = json.load(open(os.path.join(out, "STATUS.json"))).get("step") if os.path.exists(os.path.join(out, "STATUS.json")) else None
        if status not in ("live", "compare", "engine --live", "done") or not os.path.exists(os.path.join(out, "live", "journal.sqlite")):
            raise SystemExit(f"--from-compare needs a finished live step (STATUS.json: {status!r})")
    scon = streams.open_store(a.store, threads=2, memory=a.memory)
    clock = Clock.from_store(scon)
    blo, bhi = scon.execute("SELECT min(slot), max(slot) + 1 FROM blocks").fetchone()
    lo, hi = a.lo or blo, a.hi or bhi
    meta = json.loads(scon.execute("SELECT json FROM meta").fetchone()[0])
    variants = [tuple(float(x) if i == 0 else int(x) for i, x in enumerate(v.split(":"))) for v in a.variants.split(",")
                if v] or F7_GRID
    from backtest.store import load_defaults

    cfg = load_defaults()
    window = decision_bounds(cfg, blo, bhi, clock)
    ms = clock.ms_per_slot(lo)
    h24 = round(DAY_MS / ms)
    info = {"store": a.store, "store_purpose": meta.get("purpose"), "from_slot": lo, "to_slot": hi, "decisions": window,
            "variants": [f"tp{int(tp * 100)}_T{T}" for tp, T in variants], "d": a.d, "q": a.q, "ms_per_slot": ms,
            "probes": not a.no_probes, "pump_creates": getattr(a, "pump_creates", False), "seed": a.seed,
            "commit": W.git_commit()}
    log(f"shadow {lo}..{hi}, decisions {window[0]}..{window[1]} ({ms:.1f} ms/slot)")

    # 1. replay
    t0 = time.time()
    rdir = os.path.join(out, "replay")
    replay_key = {"store": os.path.abspath(a.store), "from_slot": lo, "to_slot": hi, "variants": info["variants"],
                  "d": a.d, "q": a.q}
    done_path = os.path.join(rdir, "DONE.json")
    if (resume or from_compare) and os.path.exists(done_path) and json.load(open(done_path)).get("key") == replay_key:
        log(f"replay reused ({done_path})")
    elif from_compare:
        raise SystemExit(f"--from-compare needs the finished replay of the same settings ({done_path})")
    else:
        shutil.rmtree(rdir, ignore_errors=True)
        rfeed = ReplayFeed(a.store, lo, hi, con=scon)
        Engine(rfeed, Journal(rdir), None, variants=variants, d=a.d, q=a.q, ignore_risk=True, clock=clock, backup=False,
               dev_bounds=(blo, bhi)).run()
        del rfeed
        with open(done_path, "w") as f:
            json.dump({"key": replay_key, "seconds": round(time.time() - t0)}, f)
        log(f"replay done ({time.time() - t0:.0f}s)")
    _status(out, "replay")

    # 2. recorder + live
    t0 = time.time()
    root = os.path.join(out, "rec")
    if from_compare:
        rec = ShadowRecorder(scon, data.complete_chunks(a.chunks), root, lo, hi, clock, window, probes_on=not a.no_probes,
                             gaps=a.gaps, reconnects=a.reconnects, seed=a.seed, watch_warmup_minutes=a.watch_warmup,
                             memory=a.memory, log=log, prepare=False, pump_creates=getattr(a, "pump_creates", False)).from_segments()
        scon.close()
        ldir = os.path.join(out, "live")
        info["live_watchlist"] = compare_watchlists(os.path.join(root, "watchlist-log.jsonl"), rec.watch)
        info["livefeed"] = "see the live run's log (--from-compare reuses the live step)"
        info["recorder"] = dict(rec.totals)
        log(f"live reused: {len(rec.ranges)} segments, {len(rec.recv)} received ranges rebuilt ({time.time() - t0:.0f}s)")
    else:
        rec, ldir = _record_and_live(a, scon, out, root, lo, hi, clock, window, blo, bhi, h24, variants, resume, info, log)
    info["gaps"], info["reconnects"] = rec.gaps, list(rec.reconnects.values())
    days = max(1e-9, (hi - lo) * ms / DAY_MS)
    info["estimated_gb_per_day"] = round(sum(rec.totals[s] for s in list(STREAMS.values()) + ["backfill_txs"])
                                         * TX_BYTES / 1e9 / days, 2)
    log(f"live done ({time.time() - t0:.0f}s): {dict(rec.totals)}")
    if not from_compare:
        _status(out, "live", segments=rec.totals.get("segments", 0))

    # 3. compare
    log(f"compare: rss {_rss_mb()} MB before")
    scon = streams.open_store(a.store, threads=2, memory=a.memory)
    rows = compare(rdir, ldir, rec.coverage(), scon, root, d=a.d, hour_slots=clock.slots(HOUR_MS, lo), snapshot_before=lo,
                   creator_slots=clock.slots(F7_CREATOR_MS, lo))
    log(f"compare: {len(rows)} keys, rss {_rss_mb()} MB after")
    info["summary"] = summarize(rows)
    info["unexplained"] = sum(1 for r in rows if r["cause"] == "unexplained")
    pa_rows = pa.Table.from_pylist(rows)
    import pyarrow.parquet as pq

    pq.write_table(pa_rows, os.path.join(out, "compare.parquet"))
    scon.close()
    _status(out, "compare")

    # 4. engine --live as it runs live (risk limits, live clock), with its daily reports
    t0 = time.time()
    rdir2 = os.path.join(out, "live-risk")
    shutil.rmtree(rdir2, ignore_errors=True)
    lfeed = LiveFeed(root, store=a.store, follow=False, snapshot_before=lo, h24=h24, memory=a.memory, seed_farmers=True)
    Engine(lfeed, Journal(rdir2), a.risk, variants=variants, d=a.d, q=a.q, clock=lfeed.clock, backup=False).run()
    lfeed.close()
    del lfeed
    gc.collect()
    info["live_risk_reports"] = sorted(os.path.relpath(os.path.join(dp, f), out) for dp, _, fs in os.walk(rdir2)
                                       for f in fs if f == "report.md")
    log(f"engine --live with risk limits done ({time.time() - t0:.0f}s)")
    _status(out, "engine --live")

    # 5. reconcile a sample of segments (every one with a reconnect or gap)
    t0 = time.time()
    gap_segs = rec.gap_segments()
    special = {s for s, _, m in _segments(root) if m.get("reconnects")} | gap_segs
    picks = {s for s, _, _ in _segments(root) if s % a.reconcile_every == 0} | special
    rep = reconcile.run(root, a.chunks, seqs=picks)
    clean = [s for s in rep["segments"] if s["seq"] not in gap_segs]
    info["reconcile"] = {"segments_checked": rep["totals"]["segments_checked"], "with_simulated_gap": len(gap_segs & picks),
                         "others_clean": all(not any(t[k] for t in s["tables"].values() for k in ("missing", "extra", "mismatched"))
                                             for s in clean),
                         "gap_segments": {s["seq"]: {k: sum(t[k] for t in s["tables"].values()) for k in ("missing", "extra", "mismatched")}
                                          for s in rep["segments"] if s["seq"] in gap_segs}}
    with open(os.path.join(out, "reconcile.json"), "w") as f:
        json.dump(rep, f, indent=1, default=str)
    log(f"reconcile done ({time.time() - t0:.0f}s): {info['reconcile']['segments_checked']} segments")

    with open(os.path.join(out, "compare.json"), "w") as f:
        json.dump(info, f, indent=1, default=str)
    with open(os.path.join(out, "compare.md"), "w") as f:
        f.write(render(info, rows))
    _status(out, "done", unexplained=info["unexplained"])
    return info


def compare_watchlists(log_path, batch):
    """The live watchlist (received pools only) against the batch list per minute: of the batch's
    F7-reversed and M1 members, the share the live list also had; and the live-only members."""
    out = {"minutes": 0, "f7_batch": 0, "f7_live_too": 0, "m1_batch": 0, "m1_live_too": 0, "live_only": 0}
    if not os.path.exists(log_path):
        return out
    with open(log_path) as f:
        for line in f:
            x = json.loads(line)
            b = batch.get(x["minute"])
            if b is None:
                continue
            live = set(x["pools"])
            out["minutes"] += 1
            for tag in ("f7_rev", "m1"):
                members = {p for p, t in b.items() if tag in t}
                key = "f7" if tag == "f7_rev" else "m1"
                out[f"{key}_batch"] += len(members)
                out[f"{key}_live_too"] += len(members & live)
            out["live_only"] += len(live - set(b))
    for key in ("f7", "m1"):
        out[f"{key}_share"] = round(out[f"{key}_live_too"] / out[f"{key}_batch"], 4) if out[f"{key}_batch"] else None
    return out


def _segments(root):
    from .live import segment_dirs

    return segment_dirs(root)


def render(info, rows):
    L = [f"# Shadow live: {info['from_slot']}–{info['to_slot']}", "",
         f"Store {os.path.basename(info['store'])}"
         + (" — **data after the hold-out (forward): illustration only**" if info["from_slot"] >= HOLDOUT_END else " (development data)")
         + ". A mechanical test of recorder → LiveFeed → engine, not a strategy result; no P&L is reported here.", "",
         f"Engine: F7 {', '.join(info['variants'])} and N2_F7, 0.5 SOL, d={info['d']}, q={info['q']}, no risk limits, the "
         f"store's clock ({info['ms_per_slot']:.1f} ms/slot); decisions in slots {info['decisions'][0]}–{info['decisions'][1]} "
         "(the 24 h burn-in of the store start). Live = the same engine on the simulated recorder's segments.", "",
         "## Live against replay", "",
         "| family / segment | signals replay | signals live | identical | triggers replay | live | both | differences by cause |",
         "|---|---|---|---|---|---|---|---|"]
    for k, g in info["summary"].items():
        causes = ", ".join(f"{c} {n}" for c, n in sorted(g["causes"].items())) or "—"
        L.append(f"| {k} | {g['signals_replay']} | {g['signals_live']} | {g['same']} | {g['triggers_replay']} | "
                 f"{g['triggers_live']} | {g['triggers_both']} | {causes} |")
    L += ["", f"**Unexplained differences: {info['unexplained']}** (must be 0).", "",
          "Causes: not_seen = no event of the trigger's slot was received (the watchlist did not see it); state_stale = "
          "the pool's last event before the entry was not received (e.g. an N2 pick seen only as the other pool of a received "
          "transaction); window_incomplete = an event of the pool in [t − 400, t + d] was not received (the list or probe "
          "took it on shortly before); position_events_unseen = during the position; gap = a simulated gap; busy_follows = "
          "busy in one run because of an earlier different position; n2_pick = the N2 draw came from a different hour set "
          "(live only knows received pools); follows_f7 = the N2 of an F7 trigger that differs; curve = curve transactions "
          "are not recorded; creator_unknown = live took an F7 trigger that the creator rule vetoes in the replay: the token "
          "was created after the live start, so live never got its CreateEvent and took the pool's coin creator as the "
          "creator, while the real creator (who moved the creator fee to a sharing config) sold in F7's creator window "
          "[t − 200 v1 slots, t].", ""]
    un = [r for r in rows if r["cause"] == "unexplained"][:10]
    if un:
        L += ["### Unexplained (first 10)", "", "| family | variant | key | t | replay | live |", "|---|---|---|---|---|---|"]
        L += [f"| {r['family']} | {r['variant']} | {r['key']} | {r['t']} | {r['replay_status']} | {r['live_status']} |" for r in un]
        L.append("")
    rt = info["recorder"]
    L += ["## Simulated recorder", "",
          f"- {rt.get('segments', 0)} segments (one per UTC minute), {rt.get('rows', 0):,} rows, {rt.get('slots', 0):,} slots.",
          "- Transactions: " + ", ".join(f"{s} {rt.get(s, 0):,}" for s in STREAMS.values())
          + f"; backfills {rt.get('backfills', 0)} ({rt.get('backfill_txs', 0):,} transactions); pool creations {rt.get('pool_creations', 0):,}.",
          f"- Estimated volume: **{info['estimated_gb_per_day']} GB/day** at 3 KB per transaction (the #180 convention).",
          f"- Simulated gaps (slots): {info['gaps']}; lossless reconnects: {len(info['reconnects'])}.",
          f"- LiveFeed: {info['livefeed']}.",
          f"- Live watchlist (received pools only) against the batch list: {info.get('live_watchlist')}.", "",
          "## Reconciliation against OF1", "",
          f"- {info['reconcile']['segments_checked']} segments checked (every one with a simulated reconnect or gap and a "
          f"regular sample); segments without a simulated gap clean: **{info['reconcile']['others_clean']}**.",
          f"- Segments with a simulated gap (missing rows expected there): {info['reconcile']['gap_segments']}.", "",
          "## engine --live (risk limits, live clock)", "",
          "Daily reports: " + (", ".join(info["live_risk_reports"]) or "none") + " (illustration only).", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--from", dest="lo", type=int, help="first slot (default: the store's)")
    ap.add_argument("--to", dest="hi", type=int, help="end slot, exclusive (default: the store's)")
    ap.add_argument("--chunks", default=None, help="OF1 chunks root (default LAB_CHUNKS)")
    ap.add_argument("--variants", default="", help="e.g. 0.04:600,0.06:600 (default: the F7 grid)")
    ap.add_argument("--d", type=int, default=1)
    ap.add_argument("--q", type=float, default=0.5)
    ap.add_argument("--gaps", type=int, default=3)
    ap.add_argument("--reconnects", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1052)
    ap.add_argument("--no-probes", action="store_true")
    ap.add_argument("--pump-creates", action="store_true",
                    help="the recorder's --pump-creates: every pump.fun create transaction (live learns new tokens' creators)")
    ap.add_argument("--watch-warmup", type=int, default=0, help="watchlist warm-up minutes before --from (inside the store)")
    ap.add_argument("--risk", default=os.path.join(os.path.dirname(__file__), "risk.yaml"))
    ap.add_argument("--reconcile-every", type=int, default=30)
    ap.add_argument("--memory", default=os.environ.get("LAB_DUCKDB_MEM", "1GB"), help="per DuckDB connection")
    ap.add_argument("--resume", action="store_true",
                    help="reuse a finished replay and work database of the same settings (the rest runs again)")
    ap.add_argument("--from-compare", action="store_true",
                    help="reuse the finished replay and live step (journals, segments); run compare, engine --live and reconcile")
    a = ap.parse_args()
    info = run(a, log=lambda m: print(time.strftime("%H:%M:%S"), m, flush=True))
    if not os.path.exists(os.path.join(os.path.abspath(a.out), "compare.json")):
        raise SystemExit("shadow: compare.json was not written")  # never a silent success
    print(json.dumps({k: info[k] for k in ("summary", "unexplained", "estimated_gb_per_day", "reconcile")}, indent=1, default=str))


if __name__ == "__main__":
    main()
