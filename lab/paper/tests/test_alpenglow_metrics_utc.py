"""alpenglow_metrics buckets hours and days in UTC whatever the host zone: a block at 23:30 UTC on 14 Oct lands in the
'2026-10-14 23' hour and the 2026-10-14 day even when every new DuckDB connection starts in Europe/Amsterdam (the VPS)."""
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from paper import alpenglow_metrics as am

T = 1792020600  # 2026-10-14 23:30:00 UTC (01:30 CEST on 15 Oct)


def test_hour_and_day_buckets_are_utc_under_a_cest_session(tmp_path, monkeypatch):
    real = duckdb.connect

    def connect(*a, **kw):
        con = real(*a, **kw)
        con.execute("SET TimeZone = 'Europe/Amsterdam'")
        return con
    monkeypatch.setattr(duckdb, "connect", connect)
    kept = str(tmp_path / "kept")
    os.makedirs(os.path.join(kept, "000000001"))
    rows = [{"slot": s, "parent_slot": s - 1, "block_time": T + s // 4, "block_height": s, "executed_tx_count": 10, "entry_count": 1, "skipped": False}
            for s in range(40)]
    pq.write_table(pa.table({k: [r[k] for r in rows] for k in rows[0]}), os.path.join(kept, "000000001", "blocks.parquet"))
    m = am.compute(None, [kept], None)
    assert [r["day"] for r in m["m5_blocks"]] == ["2026-10-14"] and [r["day"] for r in m["m4_day"]] == ["2026-10-14"]
    assert [r["hour"] for r in m["m4_hour"]] == ["2026-10-14 23"]
