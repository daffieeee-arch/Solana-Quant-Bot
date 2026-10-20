#!/usr/bin/env python3
"""The NAS runner for UI jobs (spec v0.3 §5, §5.1): one job at a time, the lowest priority, held while
<UI_JOBS_DIR>/ui-hold exists. A job is parameters only; this runner renders it from a template pinned on the NAS:
the job's template_sha256 must be the one SHA256SUMS lists for that template, and the file must still hash to
it. The job's values reach the template as UI_* environment values only (schema.env), never as shell text.

  python3 -I /lab/bin/uijobs/nas_runner.py            # loop (a service of the NAS app, ≤ 3 CPU / 8 GB)
  python3 -I /lab/bin/uijobs/nas_runner.py --once     # at most one job, then exit

Directories under UI_JOBS_DIR (default /lab/jobs-ui): queue/ (ui_queue.py writes), running/, done/,
results/<id>/ (status.json, result.json, job.log). Templates: UI_TEMPLATES_DIR (default /lab/templates) with
SHA256SUMS ("<sha256>  <template>.sh" lines, written by the coordinator after review).
The template's fixed environment comes from this runner's own: LAB_PY, LAB_CODE, LAB_STORE_DIR, PATH.

One runner at a time (an flock on <UI_JOBS_DIR>/runner.lock). A job's steps: queue/ -> running/, status
'running', the template, then the final status (done, failed or refused) and only then running/ -> done/, so a job
in done/ always has its final status. A runner that stops anywhere in between (a crash, a NAS restart) leaves the
job in running/; the next runner, holding the lock, recovers it before anything else and never runs it again:
  - the job's processes still alive (any process whose environment carries UI_JOB_ID=<id>, which the runner sets
    and the template's workers inherit; no pid file, so nothing races the start): it waits, one job at a time,
    and past the timeout it kills them;
  - then the final status already written stays (stopped between status and move); otherwise failed, with the
    reason 'interrupted'. A result.json of an interrupted job is never served (ui_queue get wants 'done').
"""

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import schema  # noqa: E402

ROOT = os.environ.get("UI_JOBS_DIR", "/lab/jobs-ui")
TEMPLATES = os.environ.get("UI_TEMPLATES_DIR", "/lab/templates")
TIMEOUT = int(os.environ.get("UI_JOB_TIMEOUT", 6 * 3600))
PASS = ("LAB_PY", "LAB_CODE", "LAB_STORE_DIR", "PATH")  # the runner's own settings a template may see
FINAL = ("done", "failed", "refused")


def _sums():
    out = {}
    with open(os.path.join(TEMPLATES, "SHA256SUMS")) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 2 and schema.SHA_RE.match(parts[0]):
                out[parts[1]] = parts[0]
    return out


def _kill_group(proc, grace=30):
    """TERM to the job's process group, then KILL after `grace` seconds; waits for the leader."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 10)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            if sig == signal.SIGTERM:
                try:  # the leader is gone; make sure no worker of its group is left
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            return
        except subprocess.TimeoutExpired:
            continue


def _write(path, obj):
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f)
    os.replace(path + ".tmp", path)


def _move(src, dst):
    os.replace(src, dst)  # one place for a job's moves (the tests stop the runner at each step)


def _res(jid):
    return os.path.join(ROOT, "results", jid if schema.ID_RE.match(jid) else "invalid")


def _status(jid):
    try:
        with open(os.path.join(_res(jid), "status.json")) as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def job_pids(jid):
    """The live processes of a job: those whose environment carries UI_JOB_ID=<id> (the runner sets it, the
    template's workers inherit it). A zombie's environment reads empty, so it does not count."""
    want = f"UI_JOB_ID={jid}".encode()
    pids = []
    for n in os.listdir("/proc"):
        if n.isdigit():
            try:
                with open(f"/proc/{n}/environ", "rb") as f:
                    if want in f.read().split(b"\0"):
                        pids.append(int(n))
            except OSError:
                continue
    return pids


def _recover():
    """With the runner lock held, the jobs a stopped runner left behind (see the module's docstring). True while
    such a job's processes still run: no new job may start beside them."""
    run_dir, done_dir = os.path.join(ROOT, "running"), os.path.join(ROOT, "done")
    left = [(os.path.join(run_dir, n), True) for n in sorted(os.listdir(run_dir)) if n.startswith("ui-") and n.endswith(".json")]
    # Defensive: a job in done/ without a final status (an older runner moved before it wrote the status).
    left += [(os.path.join(done_dir, n), False) for n in sorted(os.listdir(done_dir)) if n.startswith("ui-") and n.endswith(".json")
             and _status(n[3:-5]).get("state") not in FINAL]
    for path, in_running in left:
        jid = os.path.basename(path)[3:-5]
        st = _status(jid)
        reason = "interrupted: the runner stopped before the job ended; never run again"
        pids = job_pids(jid)
        if pids:
            started = st.get("started_unix") or os.stat(path).st_mtime
            if time.time() - started < TIMEOUT:
                return True  # still running without its runner: wait (one at a time), never a second copy
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for _ in range(50):
                if not job_pids(jid):
                    break
                time.sleep(0.1)
            else:
                return True
            reason = "interrupted: the runner stopped; the job's processes were killed at the timeout; never run again"
        if st.get("state") not in FINAL:
            os.makedirs(_res(jid), exist_ok=True)
            _write(os.path.join(_res(jid), "status.json"), dict(st, id=jid, state="failed", rc=None, reason=reason, ended_unix=int(time.time())))
        if in_running:
            _move(path, os.path.join(done_dir, os.path.basename(path)))
    return False


def once():
    """Run at most one queued job; its id, or None (nothing queued, held, another runner at work, or a job left
    by a stopped runner still running)."""
    if os.path.exists(os.path.join(ROOT, "ui-hold")):
        return None
    for d in ("queue", "running", "done", "results"):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    fd = os.open(os.path.join(ROOT, "runner.lock"), os.O_RDWR | os.O_CREAT, 0o600)  # not inherited by the job
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        if _recover():
            return None
        return _next()
    finally:
        os.close(fd)


def _next():
    queued = sorted(n for n in os.listdir(os.path.join(ROOT, "queue")) if n.startswith("ui-") and n.endswith(".json"))
    if not queued:
        return None
    name = queued[0]  # ULIDs sort by time: the oldest first
    run_path = os.path.join(ROOT, "running", name)
    _move(os.path.join(ROOT, "queue", name), run_path)
    jid = name[3:-5]
    res = _res(jid)
    os.makedirs(res, exist_ok=True)
    status = {"id": jid, "state": "refused", "started_unix": int(time.time())}
    try:
        with open(run_path, "rb") as f:
            job = schema.validate(f.read(schema.MAX_BYTES + 1))
        if job["id"] != jid:
            raise ValueError("file name and id differ")
        tpl = job["template"] + ".sh"
        pinned = _sums().get(tpl)
        if pinned != job["template_sha256"]:
            raise ValueError(f"template {tpl}: sha256 not pinned in SHA256SUMS")
        path = os.path.join(TEMPLATES, tpl)
        with open(path, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != pinned:
                raise ValueError(f"template {tpl}: the file no longer matches SHA256SUMS")
    except (ValueError, OSError, RecursionError) as e:
        _write(os.path.join(res, "status.json"), dict(status, reason=str(e)[:300], ended_unix=int(time.time())))
        _move(run_path, os.path.join(ROOT, "done", name))
        return jid
    env = {k: os.environ[k] for k in PASS if k in os.environ}
    env.update(schema.env(job), UI_JOB_ID=jid, UI_OUT=res, UI_LIB=HERE, HOME=res, LANG="C.UTF-8")
    _write(os.path.join(res, "status.json"), dict(status, state="running"))
    rc = None
    with open(os.path.join(res, "job.log"), "w") as log:
        # Its own process group: at the timeout the whole job goes (the tournament and its workers, not only bash),
        # so the next job never starts beside a leftover (LAB review on #440).
        proc = subprocess.Popen(["nice", "-n", "19", "bash", path], env=env, cwd=res, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            rc = proc.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            rc = "timeout"
            _kill_group(proc)
    ok = rc == 0 and os.path.isfile(os.path.join(res, "result.json"))
    # The final status first, then the move: a job in done/ always has its final status.
    _write(os.path.join(res, "status.json"), dict(status, state="done" if ok else "failed", rc=rc, ended_unix=int(time.time())))
    _move(run_path, os.path.join(ROOT, "done", name))
    return jid


def main():
    if "--once" in sys.argv[1:]:
        once()
        return
    while True:
        if once() is None:
            time.sleep(30)


if __name__ == "__main__":
    main()
