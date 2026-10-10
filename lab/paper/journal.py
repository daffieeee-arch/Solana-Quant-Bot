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
                "trigger_trader TEXT, status TEXT, reason TEXT", "family, variant, key, t"),
    "orders": ("family TEXT, variant TEXT, key TEXT, t INTEGER, day TEXT, entry_slot INTEGER, size INTEGER, status TEXT, "
               "reason TEXT", "family, variant, key, t"),
    "fills": ("family TEXT, variant TEXT, key TEXT, t INTEGER, side TEXT, day TEXT, slot INTEGER, tokens INTEGER, "
              "quote INTEGER", "family, variant, key, t, side"),
    "exits": ("family TEXT, variant TEXT, key TEXT, t INTEGER, day TEXT, entry_slot INTEGER, exit_decision_slot INTEGER, "
              "exit_slot INTEGER, exit_reason TEXT, tokens INTEGER, cost INTEGER, proceeds INTEGER, pnl INTEGER, ret REAL, "
              "exit_attempts INTEGER, cf_graduation INTEGER, seed_pool INTEGER, vault_capped INTEGER, "
              "reverted_direct INTEGER, reverted_router INTEGER, sells_dropped INTEGER, sells_scaled INTEGER",
              "family, variant, key, t"),
    "heartbeat": ("minute INTEGER, day TEXT, last_slot INTEGER, last_event TEXT, events INTEGER, open_positions INTEGER, "
                  "gap_slots INTEGER, note TEXT", "minute, note"),
    "positions": ("family TEXT, variant TEXT, key TEXT, t INTEGER, entry_slot INTEGER, cost INTEGER, tokens INTEGER, "
                  "status TEXT", "family, variant, key, t"),
    "state": ("k TEXT, v TEXT", "k"),
}
DAILY = ("signals", "orders", "fills", "exits", "heartbeat")


class Journal:
    def __init__(self, directory):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(directory, "journal.sqlite"))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        for name, (cols, key) in TABLES.items():
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {name} ({cols}, PRIMARY KEY ({key}))")
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
        """Write the day's rows of every daily table as Parquet, sorted by key (deterministic)."""
        self.commit()
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
