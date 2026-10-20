#!/usr/bin/env python3
"""The NAS's forced command for the UI's job key (spec v0.3 §5.1). authorized_keys on the NAS:

  restrict,command="/usr/bin/python3 -I /lab/bin/uijobs/ui_queue.py" ssh-ed25519 AAAA… lab-ui-jobs

The client's command line arrives in SSH_ORIGINAL_COMMAND and may only be:
  sums           the pinned templates' sha256 ({"backtest-f7": "<hex>"}), read-only
  put            a job (JSON on stdin, at most schema.MAX_BYTES), checked against schema.py, written atomically to
                 <UI_JOBS_DIR>/queue/ui-<id>.json; prints the id
  status <id>    the job's state: queued, running, done or failed (status.json), or unknown
  get <id>       the job's result.json (at most MAX_RESULT bytes)
Nothing else: no paths, no listing, no other files. Exit 0 on success, 2 when refused (reason on stderr).
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # schema.py beside this file (/lab/bin/uijobs, installed by the coordinator)
import schema  # noqa: E402

ROOT = os.environ.get("UI_JOBS_DIR", "/lab/jobs-ui")
MAX_RESULT = 65_536


def _state(jid):
    """status.json of a started job, else where the job file is."""
    st = os.path.join(ROOT, "results", jid, "status.json")
    if os.path.isfile(st):
        with open(st) as f:
            return json.load(f)
    for d, state in (("queue", "queued"), ("running", "running")):
        if os.path.isfile(os.path.join(ROOT, d, f"ui-{jid}.json")):
            return {"id": jid, "state": state}
    return {"id": jid, "state": "unknown"}


def run(command, stdin, out):
    parts = (command or "").split()
    if parts == ["put"]:
        job = schema.validate(stdin.read(schema.MAX_BYTES + 1))
        if _state(job["id"])["state"] != "unknown":
            raise ValueError("this id is already used")
        q = os.path.join(ROOT, "queue")
        os.makedirs(q, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=q, prefix=".put-", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(job, f)
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
        if not os.path.isfile(path):
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
