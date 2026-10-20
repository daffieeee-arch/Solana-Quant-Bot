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
"""

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


def once():
    """Run at most one queued job; its id, or None (nothing queued, or held)."""
    if os.path.exists(os.path.join(ROOT, "ui-hold")):
        return None
    for d in ("queue", "running", "done", "results"):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    queued = sorted(n for n in os.listdir(os.path.join(ROOT, "queue")) if n.startswith("ui-") and n.endswith(".json"))
    if not queued:
        return None
    name = queued[0]  # ULIDs sort by time: the oldest first
    run_path = os.path.join(ROOT, "running", name)
    os.replace(os.path.join(ROOT, "queue", name), run_path)
    jid = name[3:-5]
    res = os.path.join(ROOT, "results", jid if schema.ID_RE.match(jid) else "invalid")
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
        os.replace(run_path, os.path.join(ROOT, "done", name))
        return jid
    env = {k: os.environ[k] for k in PASS if k in os.environ}
    env.update(schema.env(job), UI_OUT=res, UI_LIB=HERE, HOME=res, LANG="C.UTF-8")
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
    _write(os.path.join(res, "status.json"), dict(status, state="done" if ok else "failed", rc=rc, ended_unix=int(time.time())))
    os.replace(run_path, os.path.join(ROOT, "done", name))
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
