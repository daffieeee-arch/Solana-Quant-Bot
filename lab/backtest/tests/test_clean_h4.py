"""H4's job (backtest.clean_h4): its guards (the pin's replay code, E20's lock and store, the run), the control on
the common columns, the entry day and farm, the minimum, the concentration and the absolute day bootstrap. The job
itself runs on the NAS (backtest/nas_clean_h4.sh)."""

import hashlib
import json
import os
from types import SimpleNamespace

import duckdb
import numpy as np
import pytest

from backtest import clean_h4 as h4
from backtest import clean_test as ct

HERE = os.path.dirname(os.path.dirname(os.path.abspath(h4.__file__)))


def test_the_replay_code_is_this_checkouts():
    """The pin's hashes are this checkout's files (the PR of the job merges them unchanged into main)."""
    assert set(h4.REPLAY_SHA256_H4) == set(ct.REPLAY_SHA256)
    assert h4.check_replay(os.path.join(HERE, "backtest")) == sorted(h4.REPLAY_SHA256_H4)


def test_another_replay_code_is_refused(tmp_path, monkeypatch):
    for f in h4.REPLAY_SHA256_H4:
        (tmp_path / f).write_text(f"# {f}\n")
    monkeypatch.setattr(h4, "REPLAY_SHA256_H4", {f: hashlib.sha256(f"# {f}\n".encode()).hexdigest() for f in h4.REPLAY_SHA256_H4})
    assert h4.check_replay(str(tmp_path)) == sorted(h4.REPLAY_SHA256_H4)
    with open(tmp_path / "replay.py", "a") as f:
        f.write("# a later change\n")
    with pytest.raises(ct.Refused):
        h4.check_replay(str(tmp_path))


def test_the_h4_document_and_grid():
    path = "/home/chupa/Solana-project/data-old-faithful-one/lab/research/hypothesis-h4-flow300-organic-2026-10-16.md"
    if os.path.exists(path):
        assert ct.check_document(path, h4.H4_SHA, "H4", sidecar=True) == h4.H4_SHA
    assert len(h4.VARIANTS) == len(set(h4.VARIANTS)) == 33  # the PBO matrix: v1 + spec E + long
    assert {h4.H4_VARIANT, h4.HOLD, h4.BASELINE} <= set(h4.VARIANTS)
    assert h4.TRIALS == 42 + 108 + 60 and h4.TRIALS_COLUMNS == 15 * 2 * 2


def test_h4_waits_for_e20s_lock_and_reads_its_store(tmp_path):
    store = tmp_path / "clean-1055-1060.duckdb"
    store.write_bytes(b"store")
    with pytest.raises(ct.Refused, match="H4 waits"):
        h4.check_lock(str(store))
    sha = hashlib.sha256(b"store").hexdigest()
    (tmp_path / "clean.lock").write_text(json.dumps({"store_sha256": sha, "test_run": "/runs/e20"}))
    lock, got = h4.check_lock(str(store))
    assert got == sha and lock["test_run"] == "/runs/e20"
    (tmp_path / "clean.lock").write_text(json.dumps({"store_sha256": "0" * 64}))
    with pytest.raises(ct.Refused):
        h4.check_lock(str(store))


def test_the_run_must_be_h4s():
    meta = {"period": "clean", "window": [455_760_000, 458_352_000]}
    cfg = dict(h4.RUN, decide_range=[455_760_000, 458_352_000], engine_commit="abc1234ff")
    assert h4.check_run(cfg, meta, "abc1234") == (455_760_000, 458_352_000)
    for over in ({"f7_grid": "all"}, {"f7_grid": "v1,long"}, {"variants": ["flow300_H6"]}, {"families": ["F7", "N2"]},
                 {"delay_ms": None}, {"decide_range": [455_760_000, 457_000_000]}, {"engine_commit": "fc4173c"}):
        with pytest.raises(ct.Refused):
            h4.check_run(dict(cfg, **over), meta, "abc1234")
    with pytest.raises(ct.Refused):
        h4.check_run(cfg, dict(meta, period="forward"), "abc1234")


BASE = {"family": "F7", "segment": "pool_reversed", "size_sol": 0.5, "q": 0.5, "overlay": "none", "skipped": False, "d": 3}


def _parts(tmp_path, name, rows, extra=()):
    con = duckdb.connect()
    cols = list(rows[0])
    con.execute("CREATE TABLE r (" + ", ".join(f"{c} {_type(rows[0][c])}" for c in cols) + ")")
    con.executemany(f"INSERT INTO r VALUES ({', '.join('?' * len(cols))})", [[r[c] for c in cols] for r in rows])
    os.makedirs(tmp_path / name, exist_ok=True)
    con.execute(f"COPY r TO '{tmp_path / name / 'x.parquet'}' (FORMAT parquet)")
    return str(tmp_path / name / "*.parquet")


def _type(v):
    return "BOOLEAN" if isinstance(v, bool) else "BIGINT" if isinstance(v, int) else "DOUBLE" if isinstance(v, float) else "VARCHAR"


def test_the_control_compares_the_common_columns(tmp_path):
    """E20's engine (46a0236) writes no peak_mark / mayhem_reverted: the control compares the columns both carry."""
    k3 = dict(BASE, variant="tp4_T600", mint="M", pool="P", t=455_760_010, proceeds=5)
    h4_rows = [dict(k3, peak_mark=9, mayhem_reverted=0), dict(k3, variant="flow300_H6", peak_mark=12, mayhem_reverted=0)]
    a = _parts(tmp_path, "h4", h4_rows)
    b = _parts(tmp_path, "e20", [k3])
    con = duckdb.connect()
    assert h4.common_columns(con, a, b) == list(k3)
    ok, out = h4.equality(con, a, b, "true", 455_760_000, 455_770_000)
    assert ok and out == {455_760_000 // 432_000: {"h4_rows": 1, "e20_rows": 1, "only_h4": 0, "only_e20": 0}}
    c = _parts(tmp_path, "e20b", [dict(k3, proceeds=6)])
    ok, out = h4.equality(con, a, c, "true", 455_760_000, 455_770_000)
    assert not ok and out[455_760_000 // 432_000]["only_h4"] == 1


def _h_table(con, rows):
    con.execute("CREATE OR REPLACE TABLE h (variant VARCHAR, farm VARCHAR, pool VARCHAR, eday VARCHAR, pnl DOUBLE)")
    con.executemany("INSERT INTO h VALUES (?, ?, ?, ?, ?)", rows)


def test_the_concentration_shares():
    con = duckdb.connect()
    pos = [("flow300_H6", "organic", f"P{i % 5}", "2026-10-20", float(x)) for i, x in enumerate([10, 5, 1, 1, 1, -3] + [0.5] * 194)]
    _h_table(con, pos + [("flow300_H6", "flagged", "Q", "2026-10-20", 100.0)])
    c = h4.concentration(con, "flow300_H6", "organic", k=2)
    total = 10 + 5 + 1 + 1 + 1 - 3 + 97
    assert c["positions"] == 200 and c["net_sol"] == pytest.approx(total) and c["top1pct_n"] == 2
    assert c["top2_positions_sol"] == 15 and c["top2_positions_share"] == pytest.approx(15 / total)
    assert c["largest_position_sol"] == 10 and c["top1pct_positions_sol"] == 15
    by_pool = {}
    for _, _, p, _, x in pos:
        by_pool[p] = by_pool.get(p, 0) + x
    assert c["top2_pools_sol"] == pytest.approx(sum(sorted(by_pool.values())[-2:]))
    assert h4.concentration(con, "flow300_H6", "all", k=2)["largest_position_sol"] == 100
    _h_table(con, [("flow300_H6", "organic", "P", "2026-10-20", -1.0)])
    assert h4.concentration(con, "flow300_H6", "organic")["top10_positions_share"] is None  # no share of a loss


def test_the_absolute_day_bootstrap():
    m, lo, hi = h4.boot_mean_days([2.0, 2.0, 2.0], draws=2_000)
    assert (m, lo, hi) == (2.0, 2.0, 2.0)
    rng = np.random.default_rng(1)
    m, lo, hi = h4.boot_mean_days(rng.normal(0, 1, 30), draws=2_000)
    assert lo < m < hi
    assert h4.boot_mean_days([1.0])[1:] == (None, None)


def test_the_day_and_farm_of_a_row(tmp_path):
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS s")
    midnight = 1_791_849_600  # 2026-10-13 00:00 UTC
    con.execute("CREATE TABLE s.blocks AS SELECT * FROM (VALUES (100, ?), (101, NULL), (102, ?), (200, ?)) v(slot, block_time)",
                [midnight - 1, midnight, midnight + 3_600])
    base = dict(BASE, mint="M", pool="P", regime="218ms", exit_reason="outflow", pnl_x=7)
    rows = [dict(base, variant="flow300_H6", farm="organic", t=99, entry_slot=102, entry_failed=False),
            dict(base, variant="flow300_H6", farm="flagged", t=100, entry_slot=None, entry_failed=True)]
    con.execute("CREATE TABLE r (family VARCHAR, segment VARCHAR, size_sol DOUBLE, q DOUBLE, overlay VARCHAR, skipped BOOLEAN, d BIGINT, "
                "mint VARCHAR, pool VARCHAR, regime VARCHAR, exit_reason VARCHAR, pnl_x BIGINT, variant VARCHAR, farm VARCHAR, t BIGINT, "
                "entry_slot BIGINT, entry_failed BOOLEAN)")
    con.executemany("INSERT INTO r VALUES (" + ", ".join("?" * 17) + ")", [list(r.values()) for r in rows])
    os.makedirs(tmp_path / "p")
    con.execute(f"COPY r TO '{tmp_path / 'p' / 'x.parquet'}' (FORMAT parquet)")
    h4.entry_days(con, str(tmp_path / "p" / "*.parquet"), "true", "true", "pnl_x")
    got = {t: (d, f) for t, d, f in con.execute("SELECT t, eday, farm FROM h").fetchall()}
    assert got == {99: ("2026-10-13", "organic"), 100: ("2026-10-12", "flagged")}
    assert h4.daily(con, "flow300_H6", "organic") == {"2026-10-13": 7.0 / 1e9}
    assert h4.daily(con, "flow300_H6", "all") == {"2026-10-13": 7.0 / 1e9, "2026-10-12": 7.0 / 1e9}


def test_a_dry_run_never_reads_the_clean_data(tmp_path, monkeypatch):
    store = str(tmp_path / "s.duckdb")
    c = duckdb.connect(store)
    c.execute("CREATE TABLE meta AS SELECT ? AS json", [json.dumps({"period": "clean", "slot_end_exclusive": 456_192_000})])
    c.close()
    monkeypatch.setattr(ct, "check_document", lambda *a, **k: "0" * 64)
    a = SimpleNamespace(dry_run=True, hypothesis="h", exclusions="e", exclusions_sha256="0" * 64, store=store, e20_run="x",
                        engine_dir=HERE, out=str(tmp_path / "o"))
    with pytest.raises(ct.Refused, match="never reads the clean data"):
        h4.run_h4(a)
