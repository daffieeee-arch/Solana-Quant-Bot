"""lab/ops/uijobs: the UI job schema, the NAS's forced command (ui_queue.py) and runner (nas_runner.py), spec v0.3
§5.1. A job is parameters only: every field from an enum, a bounded number or a ULID; the hold-out and the clean
test period are never selectable; the forced command writes only queue files and reads only results; the runner
runs only a template pinned in SHA256SUMS, with the job's values as UI_* environment values and nothing else."""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

import pytest

OPS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UIJOBS = os.path.join(OPS, "uijobs")
LAB = os.path.dirname(OPS)


def _load(name):
    sys.path.insert(0, UIJOBS)
    spec = importlib.util.spec_from_file_location(f"uijobs_{name}", os.path.join(UIJOBS, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


schema = _load("schema")
JID = "01JABCDEFGHJKMNPQRSTVWXYZ0"
SHA = "0" * 64


def job(**params):
    p = {"variant": "flow300_H6", "segment": "pool_reversed", "farm": "organic", "size_sol": 0.5, "d_ms": 600,
         "store": "dev2", "epoch_from": 1040, "epoch_to": 1046}
    p.update(params)
    return {"id": JID, "template": "backtest-f7", "template_sha256": SHA, "params": p}


def test_a_valid_job_and_its_environment():
    j = schema.validate(json.dumps(job()))
    env = schema.env(j)
    assert env["UI_GRID"] == "long" and env["UI_K3"] == "tp4_T600" and env["UI_K3_GRID"] == "v1"
    assert (env["UI_FROM_SLOT"], env["UI_TO_SLOT"]) == (str(1040 * 432_000), str(1047 * 432_000))
    assert env["UI_SIZE"] == "0.5" and env["UI_STORE_FILE"] == "dev2.duckdb"
    assert all(k.startswith("UI_") for k in env)


@pytest.mark.parametrize("bad", [
    dict(variant="tp4_T600; rm -rf /"), dict(variant="../x"), dict(segment="pool"), dict(farm="flagged"),
    dict(size_sol=1.0), dict(size_sol="0.5"), dict(size_sol=True), dict(d_ms=600.0), dict(d_ms=True), dict(d_ms=450),
    dict(store="holdout"), dict(store="../../etc/passwd"), dict(epoch_from=1032), dict(epoch_to=1047),
    dict(epoch_from=1046, epoch_to=1045), dict(epoch_from="1040"), dict(store="fwd1052", epoch_from=1051, epoch_to=1052),
    dict(store="fwd1053-1054", epoch_from=1053, epoch_to=1055), dict(epoch_from=1033, epoch_to=1046),
])
def test_bad_params_are_refused(bad):
    with pytest.raises(ValueError):
        schema.validate(job(**bad))


def test_bad_jobs_are_refused():
    for bad in (dict(job(), extra=1), dict(job(), id="../../x"), dict(job(), id=JID.lower()), dict(job(), template="sh"),
                dict(job(), template_sha256="x" * 64), dict(job(), params=dict(job()["params"], cmd="ls"))):
        with pytest.raises(ValueError):
            schema.validate(bad)
    with pytest.raises(ValueError):
        schema.validate(b"{" + b" " * schema.MAX_BYTES + b"}")  # oversize
    with pytest.raises(ValueError):
        schema.validate(b"not json")


def test_holdout_and_clean_never_selectable():
    for _, lo, hi in schema.STORES.values():
        assert not any(e in schema.HOLDOUT or e in schema.CLEAN for e in range(lo, hi + 1))
    stores, schema.STORES = schema.STORES, dict(schema.STORES, wide=("x.duckdb", 1040, 1064))
    try:
        for lo, hi in ((1046, 1047), (1051, 1052), (1054, 1055), (1063, 1064)):
            with pytest.raises(ValueError, match="not selectable"):
                schema.validate(job(store="wide", epoch_from=lo, epoch_to=hi))
    finally:
        schema.STORES = stores


def test_grids_are_the_engines():
    pytest.importorskip("numpy")
    sys.path.insert(0, LAB)
    from backtest.strategies import F7_BIG_GRID, F7_EXIT_GRID, F7_GRID, F7_LONG_GRID, f7_exit_variant

    assert schema.GRIDS["v1"] == [f"tp{int(tp * 100)}_T{T}" for tp, T in F7_GRID]
    assert schema.GRIDS["exits"] == [f7_exit_variant(*p) for p in F7_EXIT_GRID]
    assert schema.GRIDS["long"] == [f7_exit_variant(*p) for p in F7_LONG_GRID]
    assert schema.GRIDS["big"] == [f7_exit_variant(*p) for p in F7_BIG_GRID]


def _queue(root, command, stdin=b""):
    env = dict(os.environ, UI_JOBS_DIR=root, SSH_ORIGINAL_COMMAND=command)
    return subprocess.run([sys.executable, "-I", os.path.join(UIJOBS, "ui_queue.py")], input=stdin, env=env, capture_output=True)


def test_forced_command_puts_and_reads_only():
    with tempfile.TemporaryDirectory() as root:
        r = _queue(root, "put", json.dumps(job()).encode())
        assert r.returncode == 0 and r.stdout.decode().strip() == JID
        assert os.listdir(os.path.join(root, "queue")) == [f"ui-{JID}.json"]
        assert _queue(root, "put", json.dumps(job()).encode()).returncode == 2  # the id is used
        assert json.loads(_queue(root, f"status {JID}").stdout)["state"] == "queued"
        assert _queue(root, f"get {JID}").returncode == 2  # no result yet
        for cmd in ("", "ls", "put x", "get ../../etc/passwd", "get " + JID + " x", "status /lab", "rm -rf /", "cat /etc/passwd"):
            r = _queue(root, cmd)
            assert r.returncode == 2 and r.stdout == b"", cmd
        assert _queue(root, "put", json.dumps(job(epoch_to=1047)).encode()).returncode == 2
        os.makedirs(os.path.join(root, "results", JID))
        with open(os.path.join(root, "results", JID, "result.json"), "w") as f:
            f.write('{"ok": 1}')
        assert json.loads(_queue(root, f"get {JID}").stdout) == {"ok": 1}
        assert sorted(os.listdir(root)) == ["queue", "results"]


STUB = """#!/bin/bash
set -eu
env | sort > "$UI_OUT/env.txt"
printf '{"variant": "%s"}' "$UI_VARIANT" > "$UI_OUT/result.json"
"""


def test_runner_runs_only_pinned_templates_with_the_jobs_values():
    runner = _load("nas_runner")
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tpl:
        with open(os.path.join(tpl, "backtest-f7.sh"), "w") as f:
            f.write(STUB)
        sha = hashlib.sha256(STUB.encode()).hexdigest()
        with open(os.path.join(tpl, "SHA256SUMS"), "w") as f:
            f.write(f"{sha}  backtest-f7.sh\n")
        runner.ROOT, runner.TEMPLATES = root, tpl

        def put(j):
            sys_out = io.StringIO()
            q = _load("ui_queue")
            q.ROOT = root
            q.run("put", io.BytesIO(json.dumps(j).encode()), sys_out)

        os.environ["SECRET_TOKEN"] = "do-not-pass"
        try:
            put(dict(job(), template_sha256=sha))
            open(os.path.join(root, "ui-hold"), "w").close()
            assert runner.once() is None  # held
            os.remove(os.path.join(root, "ui-hold"))
            assert runner.once() == JID
        finally:
            del os.environ["SECRET_TOKEN"]
        res = os.path.join(root, "results", JID)
        with open(os.path.join(res, "status.json")) as f:
            assert json.load(f)["state"] == "done"
        with open(os.path.join(res, "result.json")) as f:
            assert json.load(f) == {"variant": "flow300_H6"}
        with open(os.path.join(res, "env.txt")) as f:
            names = {ln.split("=", 1)[0] for ln in f}
        assert "SECRET_TOKEN" not in names and "SSH_ORIGINAL_COMMAND" not in names
        assert names <= set(schema.env(schema.validate(job()))) | {"UI_OUT", "UI_LIB", "HOME", "LANG", "PATH", "PWD", "SHLVL", "_", "OLDPWD"} | set(runner.PASS)

        other = "01JABCDEFGHJKMNPQRSTVWXYZ1"
        put(dict(job(), id=other, template_sha256="1" * 64))  # a sha the NAS did not pin
        assert runner.once() == other
        with open(os.path.join(root, "results", other, "status.json")) as f:
            st = json.load(f)
        assert st["state"] == "refused" and "not pinned" in st["reason"]

        third = "01JABCDEFGHJKMNPQRSTVWXYZ2"
        put(dict(job(), id=third, template_sha256=sha))
        with open(os.path.join(tpl, "backtest-f7.sh"), "a") as f:
            f.write("echo changed\n")  # the file no longer matches SHA256SUMS
        assert runner.once() == third
        with open(os.path.join(root, "results", third, "status.json")) as f:
            assert "no longer matches" in json.load(f)["reason"]
        assert not os.path.exists(os.path.join(root, "results", third, "env.txt"))


LONG_DEV = "/home/chupa/Solana-project/data-old-faithful-one/lab/backtests/long-dev-1033-1046-fc4173c"


def test_result_rows_are_the_tournament_reports():
    """A UI job's numbers are the canonical report's: every (variant, d, regime) row of summary.parquet for K3 and
    flow300_H6 (pool_reversed, farm all and organic, 0.5 SOL, base) comes back exactly (LAB review: the first
    result.py pooled d and regimes, K3 +0.0058 SOL/trade, a number in no report)."""
    if not os.path.exists(os.path.join(LONG_DEV, "summary.parquet")):
        pytest.skip("no long-dev run here")
    pytest.importorskip("numpy")
    import duckdb
    sys.path.insert(0, LAB)
    result = _load("result")
    keys = ("n", "filled", "days", "mean_pnl_sol", "total_pnl_sol", "pnl_ci_lo", "pnl_ci_hi")
    for farm in ("all", "organic"):
        got = result.rows(LONG_DEV, ["flow300_H6", "tp4_T600"], "pool_reversed", farm, 0.5)
        want = duckdb.sql(f"""SELECT variant, d, regime, {', '.join(keys)} FROM read_parquet('{LONG_DEV}/summary.parquet')
                              WHERE scenario = 'base' AND family = 'F7' AND variant IN ('flow300_H6', 'tp4_T600')
                                    AND segment = 'pool_reversed' AND farm = '{farm}' AND size_sol = 0.5""").fetchall()
        assert len(got) == len(want) == 8
        w = {(r[0], r[1], r[2]): r[3:] for r in want}
        for r in got:
            assert tuple(r[k] for k in keys) == w[(r["variant"], r["d"], r["regime"])], (farm, r["variant"], r["d"], r["regime"])
    k3 = next(r for r in result.rows(LONG_DEV, ["tp4_T600"], "pool_reversed", "all", 0.5) if r["d"] == 2 and r["regime"] == "270ms")
    assert round(k3["mean_pnl_sol"], 4) == 0.0029  # the report's d 2 270 ms
