"""clean_k3_rows (E20's K3 rows from its locked output) on a dev stand-in: a 2,000-slot window of dev epoch 1043 as
its own store, the pre-registered test run (F7 + N2, pool_reversed, 0.5 SOL, --delay-ms 600) and matrix run (--delays
1, 2, 3 and the Clock's d) from an engine snapshot of this checkout, a clean.lock and an exclusion list of one pool.
The script runs as the NAS job does (python -P, the snapshot's backtest) in --dry-run (a dev store). Its rows equal
the test run's K3 rows minus the excluded pool, its flags equal backtest.wash on the test run's triggers, and every
check refuses (store sha256, config, exclusion list, result.json, expected counts, an existing output). No clean data."""

import glob
import json
import os
import shutil
import subprocess
import sys

import duckdb
import pandas as pd
import pytest

from backtest import clean_test, data, store, strategies, tournament, wash
from backtest.tests.test_store_u64 import HI, LO, mini_chunk

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clean_k3_rows.py")


def sha(path):
    return clean_test.sha256(path)


@pytest.fixture(scope="module")
def stand_in(tmp_path_factory):
    src = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]]
    if not src:
        pytest.skip("epoch-1043 chunk not available")
    root = tmp_path_factory.mktemp("k3rows")
    mp = pytest.MonkeyPatch()
    mp.setattr(store.health, "wait_ok", lambda *a, **k: 0)
    st = str(root / "store" / "dev-mini.duckdb")
    os.makedirs(os.path.dirname(st))
    store.build("dev", st, store.load_defaults(), [mini_chunk(src[0], str(root / "chunk"))], log=lambda m: None)
    runs, short = str(root / "runs"), strategies.ms(50)
    for k, v in (("LAB_STORE", st), ("LAB_BACKTESTS", runs), ("LAB_ENGINE_COMMIT", "e20test")):
        mp.setenv(k, v)
    mp.setattr(tournament, "STORE", st)
    mp.setattr(tournament, "OUT", runs)
    mp.setattr(strategies, "BURN_IN_MS", short)
    mp.setattr(strategies, "END_GUARD_MS", short)
    mp.setattr(strategies, "decision_bounds", lambda cfg, lo, hi, clock=None, **kw: (
        lo + (clock or strategies.Clock()).slots(short, lo), hi - (clock or strategies.Clock()).slots(short, hi - 1)))
    con = duckdb.connect()
    con.execute(f"ATTACH '{st}' AS s (READ_ONLY)")
    need = sorted(set(clean_test.epoch_delays(con, None, None).values()) | {1, 2, 3})
    con.close()
    args = (["F7", "N2"], [0.5])
    rest = (["empirical"], [0.5], 0.05, 1, 0, 0)
    tournament.run(*args, [1], *rest, "test", segments=["pool_reversed"], delay_ms=600, report=False)
    tournament.run(*args, need, *rest, "test-matrix", segments=["pool_reversed"], report=False)
    mp.undo()
    test_run, matrix_run = os.path.join(runs, "test"), os.path.join(runs, "test-matrix")
    eng = os.path.join(test_run, "engine")
    shutil.copytree(os.path.dirname(clean_test.__file__), os.path.join(eng, "backtest"),
                    ignore=shutil.ignore_patterns("tests", "__pycache__"))
    with open(os.path.join(eng, "COMMIT"), "w") as f:
        f.write("e20test\n")
    lock = {"store": st, "store_sha256": sha(st), "test_run": test_run, "matrix_run": matrix_run,
            "test_config_sha256": sha(os.path.join(test_run, "config.json")),
            "matrix_config_sha256": sha(os.path.join(matrix_run, "config.json")), "engine_commit": "e20test", "trading_days": 1}
    with open(os.path.join(root, "store", "clean.lock"), "w") as f:
        json.dump(lock, f)
    tg = os.path.join(test_run, "parts", "*.parquet")
    k3 = duckdb.connect().execute(f"SELECT pool, count(*) FROM read_parquet('{tg}', union_by_name = true) WHERE {clean_test.K3} "
                                  "GROUP BY pool ORDER BY 2 DESC, 1").fetchall()
    assert len(k3) >= 2, k3
    excl = root / "exclusions.md"
    excl.write_text(f"| datum | adres | label |\n|---|---|---|\n| 2026-10-24 | `{k3[0][0]}` | test |\n")
    return {"root": root, "lock": os.path.join(root, "store", "clean.lock"), "excl": str(excl), "store": st, "tg": tg,
            "test_run": test_run, "excluded_pool": k3[0][0], "k3": sum(n for _, n in k3), "k3_excluded": k3[0][1]}


def script(s, out, *extra, ok=True):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    p = subprocess.run([sys.executable, "-P", SCRIPT, "--lock", s["lock"], "--exclusions", s["excl"],
                        "--exclusions-sha256", sha(s["excl"]), "--out", out, "--dry-run", *extra],
                       capture_output=True, text=True, env=env, cwd="/")
    assert (p.returncode == 0) == ok, p.stdout + p.stderr
    return p.stdout + p.stderr


def test_the_extract_reproduces_the_k3_rows_and_flags(stand_in):
    s = stand_in
    out = str(s["root"] / "k3-rows")
    log = script(s, out)
    assert "snapshot" in log and os.path.join(s["test_run"], "engine") in log
    assert sorted(os.listdir(out)) == ["SHA256SUMS", "counts.json", "k3-rows.parquet"] and not os.path.exists(out + ".tmp")
    for line in open(os.path.join(out, "SHA256SUMS")):
        h, name = line.split()
        assert sha(os.path.join(out, name)) == h
    info = json.load(open(os.path.join(out, "counts.json")))
    con = duckdb.connect()
    rows = con.execute(f"SELECT * FROM '{out}/k3-rows.parquet'").fetchdf()
    c = info["counts"]
    assert (c["k3"], c["excluded_positions"], len(rows)) == (s["k3"], s["k3_excluded"], s["k3"] - s["k3_excluded"]) and len(rows) > 0
    assert s["excluded_pool"] not in set(rows["pool"])
    assert list(rows.columns) == ["pool", "t", "mint", "day", "entry_failed", "filled", "cost", "proceeds", "entry_slot",
                                  "exit_slot", "trigger_trader", "ret", "net_sol", "rt_tx", "wash_1h"]
    assert c["filled"] == int(rows["filled"].sum()) > 0 and c["failed_entries"] == int(rows["entry_failed"].sum())
    assert c["filled"] == c["rt_tx"] + c["no_rt_tx"] + c["no_trigger_leg"]
    # The flags, independently: backtest.wash on the test run's own triggers (the script adds the matrix run's).
    con.execute(f"ATTACH '{s['store']}' AS s (READ_ONLY)")
    con.execute(f"CREATE TEMP TABLE trig AS SELECT DISTINCT pool, t, trigger_trader FROM read_parquet('{s['tg']}', union_by_name = true) "
                f"WHERE {clean_test.F7REV}")
    wash.flags(con, "trig")
    want = dict(((p, t, tr), (rt, (ws or 0) >= wash.WASH_SHARE)) for p, t, tr, rt, ws in con.execute(
        "SELECT pool, t, trigger_trader, f_rt.rt_tx, f_w.wash_share FROM trig LEFT JOIN f_rt USING (pool, t, trigger_trader) "
        "LEFT JOIN f_w USING (pool, t)").fetchall())
    got = {(r.pool, r.t, r.trigger_trader): (None if pd.isna(r.rt_tx) else bool(r.rt_tx), bool(r.wash_1h)) for r in rows.itertuples()}
    assert got == {k: want[k] for k in got} and any(v[0] is not None for v in got.values())

    # A result.json and the expected counts that agree pass; one count off is refused, nothing written.
    res = s["root"] / "result.json"
    m = lambda cond: con.execute(f"SELECT sum(ret) / count(*), count(*) FROM '{out}/k3-rows.parquet' WHERE filled AND {cond}").fetchone()  # noqa: E731
    good = {"positions": {k: c[k] for k in c} | {"failed_entry_pnl_sol": float(rows.loc[rows.entry_failed, "net_sol"].sum())},
            "H1": {"mean": m("NOT rt_tx")[0]}, "H2": {"with_rt_tx": {"mean": m("rt_tx")[0]}},
            "wash_1h": {n: {"mean": m(q)[0], "n": m(q)[1]} for n, q in (("wash_1h", "wash_1h"), ("geen wash_1h", "NOT wash_1h"),
                                                                        ("geen rt_tx en geen wash_1h", "NOT rt_tx AND NOT wash_1h"))}}
    res.write_text(json.dumps(good))
    exp = f"{c['filled']},{c['rt_tx']},{c['no_rt_tx']}"
    script(s, out + "-2", "--result", str(res), "--expect", exp, "--expect-failed", str(c["failed_entries"]))
    assert json.load(open(out + "-2/counts.json"))["counts"] == c
    bad = dict(good, positions=dict(good["positions"], rt_tx=c["rt_tx"] + 1))
    res.write_text(json.dumps(bad))
    assert "rt_tx: " in script(s, out + "-3", "--result", str(res), ok=False) and not glob.glob(out + "-3*")
    assert "expected" in script(s, out + "-4", "--expect", f"{c['filled'] + 1},0,0", ok=False)


def test_every_check_refuses(stand_in, tmp_path):
    s = stand_in
    out = str(tmp_path / "x")
    os.makedirs(out)
    assert "exists" in script(s, out, ok=False)
    lock = json.load(open(s["lock"]))
    for change, msg in (({"store_sha256": "0" * 64}, "store's sha256"), ({"test_config_sha256": "0" * 64}, "config.json"),
                        ({"engine_commit": "zzzzzzz"}, "engine snapshot")):
        p = tmp_path / f"lock-{msg[:5]}.json"
        p.write_text(json.dumps(lock | change))
        assert msg in script(dict(s, lock=str(p)), str(tmp_path / "z"), ok=False)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    p = subprocess.run([sys.executable, "-P", SCRIPT, "--lock", s["lock"], "--exclusions", s["excl"], "--exclusions-sha256", "0" * 64,
                        "--out", str(tmp_path / "w"), "--dry-run"], capture_output=True, text=True, env=env, cwd="/")
    assert p.returncode != 0 and "exclusion list" in p.stderr
    p = subprocess.run([sys.executable, "-P", SCRIPT, "--lock", s["lock"], "--exclusions", s["excl"], "--exclusions-sha256", sha(s["excl"]),
                        "--out", str(tmp_path / "v")], capture_output=True, text=True, env=env, cwd="/")
    assert p.returncode != 0 and "pass --result" in p.stderr  # a real run needs every check
