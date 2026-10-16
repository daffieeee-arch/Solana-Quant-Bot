"""The generic clean-data job (backtest.clean_hx) and H5's Spec (backtest.clean_h5): the Spec's grid and variants, the
pin's replay hashes, the guards, the control on the common columns, the shell settings. The jobs run on the NAS
(backtest/nas_clean_hx.sh)."""

import dataclasses
import hashlib
import json
import os
import subprocess
from types import SimpleNamespace

import duckdb
import pytest

from backtest import clean_hx as hx
from backtest import clean_test as ct
from backtest.clean_h5 import SPEC

HERE = os.path.dirname(os.path.dirname(os.path.abspath(hx.__file__)))
BIG_TP = "4dbd34b"  # #293's merge: the engine of job 87, whose files H5's pin keeps


def test_h5s_spec():
    SPEC.check()
    assert len(SPEC.variants) == len(set(SPEC.variants)) == 9 + 18 + 6 + 3
    assert SPEC.variants[-3:] == ["tp50_H6", "tp100_H6", "flow300_tp100_H6"] and SPEC.variant in SPEC.variants
    assert hx.grid_variants("v1,exits,long") == SPEC.variants[:33]  # H4's grid, in the same order
    assert SPEC.run["f7_grid"] == "v1,exits,long,big" and SPEC.run["delay_ms"] == 600
    with pytest.raises(AssertionError):
        dataclasses.replace(SPEC, variant="tp200_H6").check()


def test_the_replay_hashes_are_the_big_tp_engines_files():
    try:
        blobs = {f: subprocess.run(["git", "-C", HERE, "show", f"{BIG_TP}:lab/backtest/{f}"], capture_output=True, check=True).stdout
                 for f in SPEC.replay_sha256}
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("no git history with 4dbd34b")
    assert {f: hashlib.sha256(b).hexdigest() for f, b in blobs.items()} == SPEC.replay_sha256


def test_another_replay_code_is_refused(tmp_path):
    spec = dataclasses.replace(SPEC, replay_sha256={f: hashlib.sha256(f"# {f}\n".encode()).hexdigest() for f in SPEC.replay_sha256})
    for f in spec.replay_sha256:
        (tmp_path / f).write_text(f"# {f}\n")
    assert hx.check_replay(str(tmp_path), spec) == sorted(spec.replay_sha256)
    with open(tmp_path / "strategies.py", "a") as f:
        f.write("# a later exit\n")
    with pytest.raises(ct.Refused, match="H5 pin"):
        hx.check_replay(str(tmp_path), spec)


def test_the_lock_and_the_run(tmp_path):
    store = tmp_path / "clean-1055-1060.duckdb"
    store.write_bytes(b"store")
    with pytest.raises(ct.Refused, match="H5 waits"):
        hx.check_lock(str(store), "H5")
    (tmp_path / "clean.lock").write_text(json.dumps({"store_sha256": hashlib.sha256(b"store").hexdigest(), "test_run": "/runs/e20"}))
    assert hx.check_lock(str(store), "H5")[0]["test_run"] == "/runs/e20"
    meta = {"period": "clean", "window": [455_760_000, 458_352_000]}
    cfg = dict(SPEC.run, decide_range=[455_760_000, 458_352_000], engine_commit="abc1234ff")
    assert hx.check_run(cfg, meta, "abc1234", SPEC) == (455_760_000, 458_352_000)
    for over in ({"f7_grid": "v1,exits,long"}, {"f7_grid": "v1,big"}, {"variants": ["flow300_tp100_H6"]}, {"delay_ms": None},
                 {"families": ["F7", "N2"]}, {"engine_commit": "4dbd34b"}):
        with pytest.raises(ct.Refused, match="not H5's"):
            hx.check_run(dict(cfg, **over), meta, "abc1234", SPEC)


def _parts(tmp_path, name, rows):
    con = duckdb.connect()
    cols = list(rows[0])
    typ = lambda v: "BOOLEAN" if isinstance(v, bool) else "BIGINT" if isinstance(v, int) else "DOUBLE" if isinstance(v, float) else "VARCHAR"  # noqa: E731
    con.execute("CREATE TABLE r (" + ", ".join(f"{c} {typ(rows[0][c])}" for c in cols) + ")")
    con.executemany(f"INSERT INTO r VALUES ({', '.join('?' * len(cols))})", [[r[c] for c in cols] for r in rows])
    os.makedirs(tmp_path / name, exist_ok=True)
    con.execute(f"COPY r TO '{tmp_path / name / 'x.parquet'}' (FORMAT parquet)")
    return str(tmp_path / name / "*.parquet")


def test_the_control_compares_the_common_columns(tmp_path):
    k3 = {"family": "F7", "segment": "pool_reversed", "size_sol": 0.5, "q": 0.5, "overlay": "none", "skipped": False, "d": 3,
          "variant": "tp4_T600", "mint": "M", "pool": "P", "t": 455_760_010, "proceeds": 5}
    a = _parts(tmp_path, "run", [dict(k3, peak_mark=9, mayhem_reverted=0), dict(k3, variant="tp50_H6", peak_mark=12, mayhem_reverted=0)])
    b = _parts(tmp_path, "e20", [k3])
    ok, out = hx.equality(duckdb.connect(), a, b, "true", 455_760_000, 455_770_000)
    assert ok and out == {455_760_000 // 432_000: {"run_rows": 1, "e20_rows": 1, "only_run": 0, "only_e20": 0}}
    ok, _ = hx.equality(duckdb.connect(), a, _parts(tmp_path, "e20b", [dict(k3, proceeds=6)]), "true", 455_760_000, 455_770_000)
    assert not ok


def test_the_shell_settings():
    env = dict(line.split("=", 1) for line in hx.spec_env(SPEC).splitlines())
    assert env == {"HYP_FILE": SPEC.hypothesis, "HYP_SHA": SPEC.sha, "F7_GRID": SPEC.f7_grid, "TRIALS_RUN": SPEC.trials_run, "NAME": "h5"}


def test_a_dry_run_never_reads_the_clean_data(tmp_path, monkeypatch):
    store = str(tmp_path / "s.duckdb")
    c = duckdb.connect(store)
    c.execute("CREATE TABLE meta AS SELECT ? AS json", [json.dumps({"period": "clean", "slot_end_exclusive": 456_192_000})])
    c.close()
    monkeypatch.setattr(ct, "check_document", lambda *a, **k: "0" * 64)
    a = SimpleNamespace(dry_run=True, hypothesis="h", exclusions="e", exclusions_sha256="0" * 64, store=store, e20_run="x",
                        engine_dir=HERE, out=str(tmp_path / "o"))
    with pytest.raises(ct.Refused, match="never reads the clean data"):
        hx.run_hx(a, SPEC)


def test_the_h5_document():
    path = "/home/chupa/Solana-project/data-old-faithful-one/lab/research/" + SPEC.hypothesis
    if not os.path.exists(path):
        pytest.skip("the H5 file is not on this machine")
    assert ct.check_document(path, SPEC.sha, "H5", sidecar=True) == SPEC.sha
    assert (SPEC.variant, SPEC.farm, SPEC.trials, SPEC.trials_columns, SPEC.common_with) == ("flow300_tp100_H6", "organic", 258, 48, "flow300_H6")


def test_the_take_profit_exits_and_the_common_triggers():
    con = duckdb.connect()
    con.execute("""CREATE TABLE h (variant VARCHAR, farm VARCHAR, pool VARCHAR, t BIGINT, d BIGINT, exit_reason VARCHAR,
                                   entry_failed BOOLEAN, proceeds BIGINT, cost BIGINT, pnl DOUBLE)""")
    rows = [("flow300_tp100_H6", "organic", "P1", 1, 3, "take_profit", False, 210, 100, 1.0),
            ("flow300_tp100_H6", "organic", "P2", 2, 3, "take_profit", False, 230, 100, 1.2),
            ("flow300_tp100_H6", "organic", "P3", 3, 3, "outflow", False, 50, 100, -0.5),
            ("flow300_tp100_H6", "flagged", "P4", 4, 3, "take_profit", False, 300, 100, 2.0),
            ("flow300_H6", "organic", "P1", 1, 3, "outflow", False, 500, 100, 4.0),
            ("flow300_H6", "organic", "P3", 3, 3, "outflow", False, 50, 100, -0.5),
            ("flow300_H6", "organic", "P5", 5, 3, "outflow", False, 90, 100, -0.1)]
    con.executemany("INSERT INTO h VALUES (" + ", ".join("?" * 10) + ")", rows)
    tp = hx.tp_exits(con, "flow300_tp100_H6", "organic")
    assert tp["n"] == 2 and tp["median"] == pytest.approx(2.2) and tp["p5"] == pytest.approx(2.11) and tp["p95"] == pytest.approx(2.29)
    c = hx.common_triggers(con, "flow300_tp100_H6", "flow300_H6", "organic")
    assert c == {"other": "flow300_H6", "farm": "organic", "common": 2, "flow300_tp100_H6_sol": 0.5, "flow300_H6_sol": 3.5,
                 "only_flow300_tp100_H6": 1, "only_flow300_H6": 1}
