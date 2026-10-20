#!/usr/bin/env python3
"""The NAS's forced command for the UI's job key (spec v0.3 §5.1). authorized_keys on the NAS:

  restrict,command="/usr/bin/python3 -I /lab/bin/uijobs/ui_queue.py" ssh-ed25519 AAAA… lab-ui-jobs

The client's command line arrives in SSH_ORIGINAL_COMMAND and may only be:
  sums           the pinned templates' sha256 ({"backtest-f7": "<hex>"}), read-only
  put            a job (JSON on stdin, at most schema.MAX_BYTES), checked against schema.py, written atomically in
                 its canonical form to <UI_JOBS_DIR>/queue/ui-<id>.json; prints the id. Idempotent: the same job
                 again under its id (a put whose answer was lost) prints the id and stores nothing; a different job
                 under a used id is refused.
  status <id>    the job's state: queued, running, done, failed or refused (status.json), or unknown; with the
                 stored job's job_sha256, so the dispatcher never takes another job under that id for its own
  get <id>       the job's result.json (at most MAX_RESULT bytes), only once the job is done
Nothing else: no paths, no listing, no other files. Exit 0 on success, 2 when refused (reason on stderr).
"""

import fcntl
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # schema.py beside this file (/lab/bin/uijobs, installed by the coordinator)
import schema  # noqa: E402

ROOT = os.environ.get("UI_JOBS_DIR", "/lab/jobs-ui")
MAX_RESULT = 65_536


def _job_file(jid):
    """The stored job, wherever the runner has moved it. Checked in the order the runner moves a job (queue, running,
    done), so a move during the look-up is never missed."""
    for d in ("queue", "running", "done"):
        p = os.path.join(ROOT, d, f"ui-{jid}.json")
        if os.path.isfile(p):
            return p
    return None


def _state(jid):
    """status.json of a started job, else where the job file is; with the stored job's sha256."""
    out = {"id": jid, "state": "unknown"}
    st = os.path.join(ROOT, "results", jid, "status.json")
    if os.path.isfile(st):
        with open(st) as f:
            out = json.load(f)
    else:
        # done/ without a status: not final until the runner's recovery writes one (nas_runner._recover), but known
        for d, state in (("queue", "queued"), ("running", "running"), ("done", "running")):
            if os.path.isfile(os.path.join(ROOT, d, f"ui-{jid}.json")):
                out = {"id": jid, "state": state}
    path = _job_file(jid)
    if path:  # put stores the canonical bytes, so their sha256 is the job's (schema.job_sha256), with no re-validation
        with open(path, "rb") as f:
            out = dict(out, job_sha256=hashlib.sha256(f.read(schema.MAX_BYTES + 1)).hexdigest())
    return out


def run(command, stdin, out):
    parts = (command or "").split()
    if parts == ["put"]:
        job = schema.validate(stdin.read(schema.MAX_BYTES + 1))
        data = schema.canonical(job)
        q = os.path.join(ROOT, "queue")
        os.makedirs(q, exist_ok=True)
        # One put at a time (the check and the write together): a retry that overlaps a slow first put of the same
        # job can never write it a second time after the runner took the first (LAB review: a lost answer).
        with open(os.path.join(q, ".put.lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            st = _state(job["id"])
            if st["state"] != "unknown":
                if st.get("job_sha256") == hashlib.sha256(data).hexdigest():
                    out.write(job["id"] + "\n")  # this very job is already here: the answer to an earlier put was lost
                    return
                raise ValueError("this id is already used by another job")
            fd, tmp = tempfile.mkstemp(dir=q, prefix=".put-", suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, os.path.join(q, f"ui-{job['id']}.json"))
        out.write(job["id"] + "\n")
        return
    if parts == ["sums"]:
        # The templates the NAS has pinned (SHA256SUMS), read-only: the VPS puts the matching template_sha256 in a job,
        # so a reviewed template change on the NAS never makes every job fail (LAB review on #440).
        path = os.path.join(os.environ.get("UI_TEMPLATES_DIR", "/lab/templates"), "SHA256SUMS")
        with open(path) as f:
            pinned = {}
            for line in f.read(16_384).splitlines():
                p = line.split()
                if len(p) == 2 and schema.SHA_RE.match(p[0]) and p[1].endswith(".sh") and p[1][:-3] in schema.TEMPLATES:
                    pinned[p[1][:-3]] = p[0]
        out.write(json.dumps(pinned, sort_keys=True) + "\n")
        return
    if len(parts) == 2 and parts[0] in ("status", "get") and schema.ID_RE.match(parts[1]):
        jid = parts[1]
        if parts[0] == "status":
            out.write(json.dumps(_state(jid)) + "\n")
            return
        path = os.path.join(ROOT, "results", jid, "result.json")
        if _state(jid)["state"] != "done" or not os.path.isfile(path):
            # A result.json of an interrupted job is never served: only the runner's 'done' (rc 0) vouches for it.
            raise ValueError("no result (yet)")
        with open(path, "rb") as f:
            data = f.read(MAX_RESULT + 1)
        if len(data) > MAX_RESULT:
            raise ValueError("result too large")
        out.write(data.decode("utf-8"))
        return
    raise ValueError("commands: sums | put | status <id> | get <id>")


def main():
    try:
        run(os.environ.get("SSH_ORIGINAL_COMMAND", ""), sys.stdin.buffer, sys.stdout)
    except (ValueError, OSError, RecursionError) as e:  # RecursionError: deeply nested JSON
        print(f"refused: {e.__class__.__name__ if isinstance(e, RecursionError) else e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
