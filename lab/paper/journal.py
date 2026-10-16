"""Paper journal: SQLite (WAL) while running, one Parquet file per table per UTC day.

Every row has a natural key (an event position or a signal), and writes are INSERT OR REPLACE, so
processing the same events again (a restart) gives exactly the same journal. Open positions are
kept in their own table for inspection.
"""

import os
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq

TABLES = {
    "signals": ("family TEXT, variant TEXT, key TEXT, t INTEGER, day TEXT, venue TEXT, orientation TEXT, depth INTEGER, "
                "trigger_trader TEXT, status TEXT, reason TEXT, farm TEXT, "
                # Live only (replay: NULL): measured latency of the trigger transaction next to the
                # simulated d (engine._latency): block time of t, receive time, the server's created_at,
                # receive - block time, the segment's close and close - block time, d in ms.
                "block_time INTEGER, received_unix_ns INTEGER, created_unix_ns INTEGER, latency_ms INTEGER, "
                "segment_written_unix INTEGER, segment_lag_ms INTEGER, d_ms INTEGER, "
                # Live only: 1 when the farmer history behind the farm label was complete at t (LiveFeed's
                # farm_seed complete_from), 0 before; NULL in replay (the store's labels).
                "farm_complete INTEGER, "
                # Pools: the first slot the engine saw the pool (backfill included), and whether the farm label's
                # hour [t - 1 h, t) was covered (seen for an hour, or followed since the pool's creation); a
                # group with require_farm_complete opens nothing on 0 (week4-spec-paper-h4h5 §1).
                "pool_seen_slot INTEGER, farm_window_complete INTEGER", "family, variant, key, t"),
    "orders": ("family TEXT, variant TEXT, key TEXT, t INTEGER, day TEXT, entry_slot INTEGER, size INTEGER, status TEXT, "
               "reason TEXT", "family, variant, key, t"),
    "fills": ("family TEXT, variant TEXT, key TEXT, t INTEGER, side TEXT, day TEXT, slot INTEGER, tokens INTEGER, "
              "quote INTEGER", "family, variant, key, t, side"),
    "exits": ("family TEXT, variant TEXT, key TEXT, t INTEGER, day TEXT, entry_slot INTEGER, exit_decision_slot INTEGER, "
              "exit_slot INTEGER, exit_reason TEXT, tokens INTEGER, cost INTEGER, proceeds INTEGER, pnl INTEGER, ret REAL, "
              "exit_attempts INTEGER, cf_graduation INTEGER, seed_pool INTEGER, vault_capped INTEGER, "
              "reverted_direct INTEGER, reverted_router INTEGER, sells_dropped INTEGER, sells_scaled INTEGER, "
              # The highest mark since the entry (gross, as proceeds) and its first slot (the tournament's
              # peak_mark / peak_slot); the exit rule's windows in slots: the flow window and the time cap.
              "peak_mark INTEGER, peak_slot INTEGER, n_flow_slots INTEGER, cap_slots INTEGER",
              "family, variant, key, t"),
    "heartbeat": ("minute INTEGER, day TEXT, last_slot INTEGER, last_event TEXT, events INTEGER, open_positions INTEGER, "
                  "gap_slots INTEGER, note TEXT", "minute, note"),
    # Live data usage per minute and recorder stream (bytes received from Triton), from the recorder.
    "usage": ("minute INTEGER, day TEXT, stream TEXT, bytes INTEGER, messages INTEGER, seq INTEGER, note TEXT",
              "minute, stream"),
    # Open positions; mark_*: a sale of the whole position now (the exit's fill math, after costs) at mark_slot.
    "positions": ("family TEXT, variant TEXT, key TEXT, t INTEGER, entry_slot INTEGER, cost INTEGER, tokens INTEGER, "
                  "status TEXT, mark_proceeds INTEGER, mark_slot INTEGER, mark_pnl INTEGER", "family, variant, key, t"),
    "state": ("k TEXT, v TEXT", "k"),
    # Live: pools, graduated tokens and epoch clock spans LiveFeed learned from the recorder's segments (seq: the
    # segment it learned them from; info: JSON). A restart loads them, so what was learned before the retained
    # segments stays known (paper/restart.py). A cache, not a record: rewritten per key.
    "learned": ("kind TEXT, key TEXT, seq INTEGER, slot INTEGER, info TEXT", "kind, key"),
}
DAILY = ("signals", "orders", "fills", "exits", "heartbeat", "usage")


class Journal:
    def __init__(self, directory):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(directory, "journal.sqlite"))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        for name, (cols, key) in TABLES.items():
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {name} ({cols}, PRIMARY KEY ({key}))")
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({name})")}
            for c in cols.split(", "):  # a journal from before a column was added
                if c.split()[0] not in have:
                    self.db.execute(f"ALTER TABLE {name} ADD COLUMN {c}")
        self.db.commit()
        self.cols = {name: [c.split()[0] for c in cols.split(", ")] for name, (cols, _) in TABLES.items()}

    def put(self, table, **row):
        cols = self.cols[table]
        self.db.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row.get(c) for c in cols])

    def delete(self, table, **key):
        self.db.execute(f"DELETE FROM {table} WHERE " + " AND ".join(f"{k} = ?" for k in key), list(key.values()))

    def state(self, k, v=None):
        if v is None:
            r = self.db.execute("SELECT v FROM state WHERE k = ?", [k]).fetchone()
            return r[0] if r else None
        self.put("state", k=k, v=str(v))

    def commit(self):
        self.db.commit()

    def rows(self, table, where="1=1", params=()):
        cur = self.db.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY 1, 2, 3, 4", params)
        names = [c[0] for c in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def export_day(self, day):
        """Write the day's rows of every daily table as Parquet, sorted by key (deterministic). No commit: this
        connection reads its own rows, and the engine commits at its minutes only, each with its high-water mark
        (paper/restart.py)."""
        out = os.path.join(self.dir, day)
        os.makedirs(out, exist_ok=True)
        for t in DAILY:
            key = TABLES[t][1]
            cur = self.db.execute(f"SELECT * FROM {t} WHERE day = ? ORDER BY {key}", [day])
            names = [c[0] for c in cur.description]
            rows = cur.fetchall()
            table = pa.table({n: [r[i] for r in rows] for i, n in enumerate(names)}) if rows else pa.table({n: [] for n in names})
            pq.write_table(table, os.path.join(out, f"{t}.parquet.tmp"))
            os.replace(os.path.join(out, f"{t}.parquet.tmp"), os.path.join(out, f"{t}.parquet"))
        return out

    def days(self):
        return [r[0] for r in self.db.execute("SELECT DISTINCT day FROM signals UNION SELECT DISTINCT day FROM heartbeat "
                                              "ORDER BY 1").fetchall() if r[0]]

    def close(self):
        self.db.commit()
        self.db.close()
