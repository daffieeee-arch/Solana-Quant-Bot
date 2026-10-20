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


def _visible(d):
    return sorted(n for n in os.listdir(d) if not n.startswith("."))  # without put's lock file


def _queue(root, command, stdin=b""):
    env = dict(os.environ, UI_JOBS_DIR=root, SSH_ORIGINAL_COMMAND=command)
    return subprocess.run([sys.executable, "-I", os.path.join(UIJOBS, "ui_queue.py")], input=stdin, env=env, capture_output=True)


def test_forced_command_puts_and_reads_only():
    with tempfile.TemporaryDirectory() as root:
        r = _queue(root, "put", json.dumps(job()).encode())
        assert r.returncode == 0 and r.stdout.decode().strip() == JID
        assert _visible(os.path.join(root, "queue")) == [f"ui-{JID}.json"]
        assert _queue(root, "put", json.dumps(job(farm="all")).encode()).returncode == 2  # the id is used by another job
        assert json.loads(_queue(root, f"status {JID}").stdout)["state"] == "queued"
        assert _queue(root, f"get {JID}").returncode == 2  # no result yet
        for cmd in ("", "ls", "put x", "get ../../etc/passwd", "get " + JID + " x", "status /lab", "rm -rf /", "cat /etc/passwd"):
            r = _queue(root, cmd)
            assert r.returncode == 2 and r.stdout == b"", cmd
        assert _queue(root, "put", json.dumps(job(epoch_to=1047)).encode()).returncode == 2
        os.makedirs(os.path.join(root, "results", JID))
        with open(os.path.join(root, "results", JID, "result.json"), "w") as f:
            f.write('{"ok": 1}')
        assert _queue(root, f"get {JID}").returncode == 2  # a result without the runner's 'done' is never served
        with open(os.path.join(root, "results", JID, "status.json"), "w") as f:
            json.dump({"id": JID, "state": "done"}, f)
        assert json.loads(_queue(root, f"get {JID}").stdout) == {"ok": 1}
        assert _visible(root) == ["queue", "results"]


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
        assert names <= set(schema.env(schema.validate(job()))) | {"UI_JOB_ID", "UI_OUT", "UI_LIB", "HOME", "LANG", "PATH", "PWD", "SHLVL", "_", "OLDPWD"} | set(runner.PASS)

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
        assert len(json.dumps(got).encode()) < 8_192  # 14 epochs, both variants: a few KB, far under get's 64 KB
        w = {(r[0], r[1], r[2]): r[3:] for r in want}
        for r in got:
            assert tuple(r[k] for k in keys) == w[(r["variant"], r["d"], r["regime"])], (farm, r["variant"], r["d"], r["regime"])
    k3 = next(r for r in result.rows(LONG_DEV, ["tp4_T600"], "pool_reversed", "all", 0.5) if r["d"] == 2 and r["regime"] == "270ms")
    assert round(k3["mean_pnl_sol"], 4) == 0.0029  # the report's d 2 270 ms


def test_a_result_stays_well_under_the_get_cap(monkeypatch, capsys):
    """ui_queue's get refuses a result over MAX_RESULT (64 KB); the dispatcher then fails the job (#470 review). A
    7-epoch job never comes near it: with the job's one --delay-ms each epoch gives one d, so at most 7 (d, regime)
    rows per variant. Pessimistic here: 2 variants x 7 regimes x 3 d = 42 rows, the longest variant name, every
    number at full float precision: still under half the cap."""
    result, q = _load("result"), _load("ui_queue")
    longest = max(schema.GRID_OF, key=len)
    rows = [{"variant": v, "d": d, "regime": f"{270 + 10 * i}ms", "n": 123_456, "filled": 123_000, "days": 7,
             "mean_pnl_sol": -0.012345678901234567, "total_pnl_sol": -1234.5678901234567, "pnl_ci_lo": -0.023456789012345678,
             "pnl_ci_hi": 0.0012345678901234567, "mean_ret": -0.024691357802469135, "win_rate": 0.41234567890123456,
             "verdict": "insufficient_evidence", "failed_entries": 456, "sum_sol_per_day": -176.36684144620810}
            for v in (longest, schema.K3) for i in range(7) for d in (1, 2, 3)]
    monkeypatch.setattr(result, "rows", lambda *a, **k: rows)
    monkeypatch.setattr(sys, "argv", ["result.py", "--run", "r", "--variant", longest, "--k3", schema.K3, "--segment", "pool_reversed",
                                      "--farm", "organic", "--size", "25", "--id", JID])
    result.main()
    out = capsys.readouterr().out.encode()
    assert len(json.loads(out)["rows"]) == 42
    assert len(out) < q.MAX_RESULT // 2, len(out)  # about 18 KB


def test_sums_and_deep_json():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tpl:
        with open(os.path.join(tpl, "SHA256SUMS"), "w") as f:
            f.write(f"{'a' * 64}  backtest-f7.sh\n{'b' * 64}  other.sh\nnot a line\n")
        env = dict(os.environ, UI_JOBS_DIR=root, UI_TEMPLATES_DIR=tpl, SSH_ORIGINAL_COMMAND="sums")
        r = subprocess.run([sys.executable, "-I", os.path.join(UIJOBS, "ui_queue.py")], env=env, capture_output=True)
        assert r.returncode == 0 and json.loads(r.stdout) == {"backtest-f7": "a" * 64}  # only known templates
        deep = b"[" * 100_000 + b"]" * 100_000
        r = _queue(root, "put", deep[: schema.MAX_BYTES])
        assert r.returncode == 2 and b"Traceback" not in r.stderr
        with pytest.raises(ValueError):
            schema.validate("[" * 50_000 + "]" * 50_000)


TIMEOUT_STUB = """#!/bin/bash
sleep 300 &
echo $! > "$UI_OUT/child.pid"
wait
"""


def test_timeout_kills_the_whole_job():
    import time
    runner = _load("nas_runner")
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tpl:
        with open(os.path.join(tpl, "backtest-f7.sh"), "w") as f:
            f.write(TIMEOUT_STUB)
        sha = hashlib.sha256(TIMEOUT_STUB.encode()).hexdigest()
        with open(os.path.join(tpl, "SHA256SUMS"), "w") as f:
            f.write(f"{sha}  backtest-f7.sh\n")
        runner.ROOT, runner.TEMPLATES, runner.TIMEOUT = root, tpl, 1
        q = _load("ui_queue")
        q.ROOT = root
        q.run("put", io.BytesIO(json.dumps(dict(job(), template_sha256=sha)).encode()), io.StringIO())
        assert runner.once() == JID
        with open(os.path.join(root, "results", JID, "status.json")) as f:
            st = json.load(f)
        assert (st["state"], st["rc"]) == ("failed", "timeout")
        with open(os.path.join(root, "results", JID, "child.pid")) as f:
            pid = int(f.read())
        for _ in range(50):  # the worker of the job's group is gone too, not only bash
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("the job's child survived the timeout")


# --- A lost answer and an interrupted runner (Codex review on main 383a24d, findings 4 and 5) ---------------------

def test_put_is_idempotent_and_never_two_jobs_under_one_id():
    """The same job again under its id (a retry after a lost answer; any spacing or key order) is accepted and stored
    once, wherever the runner has moved it; another job under a used id is refused; status gives the stored job's
    sha256, the one the dispatcher computes."""
    with tempfile.TemporaryDirectory() as root:
        j = job()
        assert _queue(root, "put", json.dumps(j).encode()).returncode == 0
        again = json.dumps(dict(reversed(list(j.items()))), indent=2).encode()
        for where in ("queue", "running", "done"):
            if where != "queue":
                os.makedirs(os.path.join(root, where), exist_ok=True)
                src = next(os.path.join(root, d, f"ui-{JID}.json") for d in ("queue", "running") if os.path.exists(os.path.join(root, d, f"ui-{JID}.json")))
                os.replace(src, os.path.join(root, where, f"ui-{JID}.json"))
            r = _queue(root, "put", again)
            assert r.returncode == 0 and r.stdout.decode().strip() == JID, where
            st = json.loads(_queue(root, f"status {JID}").stdout)
            assert st["job_sha256"] == schema.job_sha256(j), where
            other = _queue(root, "put", json.dumps(job(farm="all")).encode())
            assert other.returncode == 2 and b"another job" in other.stderr, where
            stored = [n for d in ("queue", "running", "done") if os.path.isdir(os.path.join(root, d))
                      for n in _visible(os.path.join(root, d))]
            assert stored == [f"ui-{JID}.json"], where  # once, never a second copy in queue/


STUB_RUNS = """#!/bin/bash
set -eu
echo run >> "$UI_OUT/runs"
printf '{"variant": "%s"}' "$UI_VARIANT" > "$UI_OUT/result.json"
"""
JID2 = "01JABCDEFGHJKMNPQRSTVWXYZ9"


class Stop(BaseException):
    """The runner process stopping at this point (a crash, a NAS restart): nothing after it runs."""


def _runner(root, tpl, stub):
    runner = _load("nas_runner")
    with open(os.path.join(tpl, "backtest-f7.sh"), "w") as f:
        f.write(stub)
    sha = hashlib.sha256(stub.encode()).hexdigest()
    with open(os.path.join(tpl, "SHA256SUMS"), "w") as f:
        f.write(f"{sha}  backtest-f7.sh\n")
    runner.ROOT, runner.TEMPLATES = root, tpl
    q = _load("ui_queue")
    q.ROOT = root
    for jid in (JID, JID2):
        q.run("put", io.BytesIO(json.dumps(dict(job(), id=jid, template_sha256=sha)).encode()), io.StringIO())
    return runner, q


def _st(root, jid):
    with open(os.path.join(root, "results", jid, "status.json")) as f:
        return json.load(f)


def _runs(root, jid):
    p = os.path.join(root, "results", jid, "runs")
    return sum(1 for _ in open(p)) if os.path.exists(p) else 0


def _get(q, jid):
    out = io.StringIO()
    try:
        q.run(f"get {jid}", io.BytesIO(b""), out)
    except ValueError:
        return None
    return json.loads(out.getvalue())


def _wait_gone(runner, jid, secs=15):
    import time
    for _ in range(secs * 10):
        if not runner.job_pids(jid):
            return
        time.sleep(0.1)
    raise AssertionError("the job's processes did not end")


@pytest.mark.parametrize("point", ["after_move_to_running", "after_status_running", "after_result_before_final_status",
                                   "after_final_status_before_move"])
def test_a_runner_stopped_at_each_step_is_recovered_never_rerun(point, monkeypatch):
    """The runner stops at each step of a job. The next runner (holding the lock) gives that job a final status
    without running it again, and the queue goes on: the status already final stays (stopped between status and
    move); otherwise failed 'interrupted', and its result.json, if any, is never served."""
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tpl:
        runner, q = _runner(root, tpl, STUB_RUNS)
        real_move, real_write = runner._move, runner._write

        def move(src, dst):
            if point == "after_final_status_before_move" and os.path.dirname(dst).endswith("done"):
                raise Stop
            real_move(src, dst)
            if point == "after_move_to_running":
                raise Stop

        def write(path, obj):
            if point == "after_result_before_final_status" and obj.get("state") in ("done", "failed"):
                assert os.path.exists(os.path.join(os.path.dirname(path), "result.json"))  # the result is written
                raise Stop
            real_write(path, obj)
            if point == "after_status_running" and obj.get("state") == "running":
                raise Stop

        monkeypatch.setattr(runner, "_move", move)
        monkeypatch.setattr(runner, "_write", write)
        with pytest.raises(Stop):
            runner.once()
        monkeypatch.undo()
        assert os.listdir(os.path.join(root, "running")) == [f"ui-{JID}.json"]  # left behind at every point
        _wait_gone(runner, JID)
        assert runner.once() == JID2  # JID recovered first, then the queue goes on
        assert runner.once() is None  # and JID is never run again
        st = _st(root, JID)
        assert os.listdir(os.path.join(root, "running")) == []
        assert f"ui-{JID}.json" in os.listdir(os.path.join(root, "done"))
        started = point in ("after_result_before_final_status", "after_final_status_before_move")
        assert _runs(root, JID) == (1 if started else 0)
        if point == "after_final_status_before_move":
            assert (st["state"], st["rc"]) == ("done", 0)
            assert _get(q, JID) == {"variant": "flow300_H6"}
        else:
            assert st["state"] == "failed" and st["reason"].startswith("interrupted")
            assert _get(q, JID) is None
        assert _st(root, JID2)["state"] == "done" and _runs(root, JID2) == 1


def test_a_job_in_done_without_a_final_status_becomes_interrupted():
    """Defensive (an older runner moved before writing the status): a job in done/ whose status is not final."""
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tpl:
        runner, q = _runner(root, tpl, STUB_RUNS)
        assert runner.once() == JID
        with open(os.path.join(root, "results", JID, "status.json"), "w") as f:
            json.dump({"id": JID, "state": "running", "started_unix": 1}, f)
        assert runner.once() == JID2
        assert _st(root, JID)["state"] == "failed" and _get(q, JID) is None and _runs(root, JID) == 1


STUB_SLOW = """#!/bin/bash
set -eu
echo run >> "$UI_OUT/runs"
sleep SECS &
wait
printf '{"variant": "%s"}' "$UI_VARIANT" > "$UI_OUT/result.json"
"""


@pytest.fixture
def no_orphans():
    """Kills whatever a test's orphaned job left, so a failing test never leaves a process carrying a test id."""
    yield
    runner = _load("nas_runner")
    for jid in (JID, JID2):
        for pid in runner.job_pids(jid):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


def _stop_after_start(runner, monkeypatch):
    real = runner.subprocess.Popen

    def popen(*a, **k):
        real(*a, **k)  # the job starts in its own session and outlives the runner
        raise Stop
    monkeypatch.setattr(runner.subprocess, "Popen", popen)


def test_a_live_job_without_its_runner_is_waited_for_never_doubled(monkeypatch, no_orphans):
    """The runner stops right after starting a job, whose processes run on: the next runner neither starts it again
    nor starts the next job beside it; once they end, the job becomes failed 'interrupted' and the queue goes on.
    A second runner while one holds the lock does nothing."""
    import fcntl
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tpl:
        runner, q = _runner(root, tpl, STUB_SLOW.replace("SECS", "2"))
        _stop_after_start(runner, monkeypatch)
        with pytest.raises(Stop):
            runner.once()
        monkeypatch.undo()
        assert runner.job_pids(JID)  # found by its marker, with no pid file
        assert runner.once() is None  # alive: wait
        assert _st(root, JID)["state"] == "running" and _visible(os.path.join(root, "queue")) == [f"ui-{JID2}.json"]
        with open(os.path.join(root, "runner.lock"), "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            _wait_gone(runner, JID)
            assert runner.once() is None  # another runner holds the lock
        assert runner.once() == JID2
        st = _st(root, JID)
        assert st["state"] == "failed" and st["reason"].startswith("interrupted") and _runs(root, JID) == 1
        assert _get(q, JID) is None  # its result.json was written, but no runner saw it end


def test_an_orphaned_job_past_the_timeout_is_killed_then_interrupted(monkeypatch, no_orphans):
    import time
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as tpl:
        runner, _ = _runner(root, tpl, STUB_SLOW.replace("SECS", "300"))
        _stop_after_start(runner, monkeypatch)
        with pytest.raises(Stop):
            runner.once()
        monkeypatch.undo()
        for _ in range(50):  # bash and, once it has forked, its sleep
            if len(runner.job_pids(JID)) >= 2:
                break
            time.sleep(0.1)
        assert len(runner.job_pids(JID)) >= 2
        runner.TIMEOUT = 1
        time.sleep(1.5)
        assert runner.once() == JID2
        assert not runner.job_pids(JID)
        assert "killed at the timeout" in _st(root, JID)["reason"]
