"""summary prints block times in UTC, also on a host whose time zone is not UTC (the VPS: Europe/Amsterdam)."""

import datetime as dt
import json

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from checks import summary


def test_block_times_are_utc_on_a_host_in_amsterdam(tmp_path, monkeypatch):
    d = tmp_path / "c0" / "blocks"
    d.mkdir(parents=True)
    (tmp_path / "c0" / "_manifest.json").write_text(json.dumps({"status": "complete", "slot_start": 0, "slot_end_exclusive": 2,
                                                                "elapsed_seconds": 1, "counters": {"txs_seen": 0}}))
    late = int(dt.datetime(2026, 9, 25, 23, 30, tzinfo=dt.timezone.utc).timestamp())
    pq.write_table(pa.table({"slot": pa.array([0, 1], pa.uint64()), "block_time": [late, late + 3600], "skipped": [False, False]}),
                   d / "part-00000.parquet")
    real = duckdb.connect

    def amsterdam(*a, **k):
        con = real(*a, **k)
        con.execute("SET TimeZone = 'Europe/Amsterdam'")
        return con

    monkeypatch.setattr(summary.duckdb, "connect", amsterdam)
    out = tmp_path / "s.json"
    summary.main([str(tmp_path), "--json", str(out)])
    t = json.loads(out.read_text())["time"]
    assert (t["first_block"], t["last_block"]) == ("2026-09-25 23:30:00 UTC", "2026-09-26 00:30:00 UTC")
