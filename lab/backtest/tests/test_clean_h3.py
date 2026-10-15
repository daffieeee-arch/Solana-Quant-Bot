"""H3's job (backtest.clean_h3): its guards (replay = befacff, E20's lock and store, the run), the entry day and
the day bootstrap. The job itself runs on the NAS (backtest/nas_clean_h3.sh)."""

import hashlib
import json
import os
from types import SimpleNamespace

import duckdb
import numpy as np
import pytest

from backtest import clean_h3 as h3
from backtest import clean_test as ct

HERE = os.path.dirname(os.path.dirname(os.path.abspath(h3.__file__)))


def test_the_replay_code_must_be_befacffs(tmp_path, monkeypatch):
    """The guard on a copy with known hashes (main moves on; H3 runs from its pin)."""
    assert set(h3.REPLAY_SHA256_BEFACFF) == set(ct.REPLAY_SHA256)
    assert h3.REPLAY_SHA256_BEFACFF["replay.py"] == ct.REPLAY_SHA256["replay.py"]  # #261 changed tournament and strategies only
    for f in h3.REPLAY_SHA256_BEFACFF:
        (tmp_path / f).write_text(f"# {f}\n")
    monkeypatch.setattr(h3, "REPLAY_SHA256_BEFACFF", {f: hashlib.sha256(f"# {f}\n".encode()).hexdigest() for f in h3.REPLAY_SHA256_BEFACFF})
    assert h3.check_replay(str(tmp_path)) == sorted(h3.REPLAY_SHA256_BEFACFF)
    with open(tmp_path / "strategies.py", "a") as f:
        f.write("# a later exit\n")
    with pytest.raises(ct.Refused):
        h3.check_replay(str(tmp_path))


def test_h3_waits_for_e20s_lock_and_reads_its_store(tmp_path):
    store = tmp_path / "clean-1055-1060.duckdb"
    store.write_bytes(b"store")
    with pytest.raises(ct.Refused, match="no clean.lock"):
        h3.check_lock(str(store))
    sha = hashlib.sha256(b"store").hexdigest()
    (tmp_path / "clean.lock").write_text(json.dumps({"store_sha256": sha, "test_run": "/runs/e20"}))
    lock, got = h3.check_lock(str(store))
    assert got == sha and lock["test_run"] == "/runs/e20"
    (tmp_path / "clean.lock").write_text(json.dumps({"store_sha256": "0" * 64}))
    with pytest.raises(ct.Refused):  # a rebuilt store is not the one E20 judged
        h3.check_lock(str(store))


def test_the_run_must_be_h3s():
    meta = {"period": "clean", "window": [455_760_000, 458_352_000]}
    cfg = dict(h3.RUN, decide_range=[455_760_000, 458_352_000], engine_commit="abc1234ff")
    assert h3.check_run(cfg, meta, "abc1234") == (455_760_000, 458_352_000)
    for over in ({"f7_grid": "v1"}, {"variants": ["tp4_T600", "hold180"]}, {"families": ["F7", "N2"]}, {"delay_ms": None},
                 {"decide_range": [455_760_000, 457_000_000]}, {"engine_commit": "befacff"}):
        with pytest.raises(ct.Refused):
            h3.check_run(dict(cfg, **over), meta, "abc1234")
    with pytest.raises(ct.Refused):
        h3.check_run(cfg, dict(meta, period="forward"), "abc1234")


def test_the_day_is_the_entry_slots_utc_day(tmp_path):
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS s")
    midnight = 1_791_849_600  # 2026-10-13 00:00 UTC
    con.execute("CREATE TABLE s.blocks AS SELECT * FROM (VALUES (100, ?), (101, NULL), (102, ?), (200, ?)) v(slot, block_time)",
                [midnight - 1, midnight, midnight + 3_600])
    base = {"family": "F7", "segment": "pool_reversed", "size_sol": 0.5, "q": 0.5, "overlay": "none", "skipped": False, "mint": "M", "pool": "P", "d": 3}
    rows = [dict(base, variant="hold180", t=99, entry_slot=102, entry_failed=False, pnl_x=7),   # decided before, entered after midnight
            dict(base, variant="hold180", t=100, entry_slot=None, entry_failed=True, pnl_x=-1)]  # a failed entry: its trigger slot
    os.makedirs(tmp_path / "p")
    con.execute("CREATE TABLE r (family VARCHAR, segment VARCHAR, size_sol DOUBLE, q DOUBLE, overlay VARCHAR, skipped BOOLEAN, mint VARCHAR, "
                "pool VARCHAR, d BIGINT, variant VARCHAR, t BIGINT, entry_slot BIGINT, entry_failed BOOLEAN, pnl_x BIGINT)")
    con.executemany("INSERT INTO r VALUES (" + ", ".join("?" * 14) + ")", [list(r.values()) for r in rows])
    con.execute(f"COPY r TO '{tmp_path / 'p' / 'x.parquet'}' (FORMAT parquet)")
    h3.entry_days(con, str(tmp_path / "p" / "*.parquet"), "true", "true", "pnl_x")
    got = dict(con.execute("SELECT t, eday FROM h").fetchall())
    assert got == {99: "2026-10-13", 100: "2026-10-12"}


def test_the_day_bootstrap_of_the_difference():
    m, lo, hi = h3.boot_diff_days([3.0, 3.0, 3.0], [1.0, 1.0, 1.0], draws=2_000)
    assert (m, lo, hi) == (2.0, 2.0, 2.0)
    rng = np.random.default_rng(1)
    m, lo, hi = h3.boot_diff_days(rng.normal(0, 1, 30), rng.normal(0, 1, 30), draws=2_000)
    assert lo < m < hi
    assert h3.boot_diff_days([1.0], [0.0])[1:] == (None, None)


def test_a_dry_run_never_reads_the_clean_data(tmp_path, monkeypatch):
    store = str(tmp_path / "s.duckdb")
    c = duckdb.connect(store)
    c.execute("CREATE TABLE meta AS SELECT ? AS json", [json.dumps({"period": "clean", "slot_end_exclusive": 456_192_000})])
    c.close()
    monkeypatch.setattr(ct, "check_document", lambda *a, **k: "0" * 64)
    a = SimpleNamespace(dry_run=True, hypothesis="h", exclusions="e", exclusions_sha256="0" * 64, store=store, e20_run="x",
                        engine_dir=HERE, out=str(tmp_path / "o"))
    with pytest.raises(ct.Refused, match="never reads the clean data"):
        h3.run_h3(a)
