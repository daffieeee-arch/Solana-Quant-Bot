"""lab-control: the executor of the interactive UI's requests (spec v0.3 §2.6). The web server only files request
files in <data>/control/requests/; this program, run by lab-control.path as the owner's user, validates each one
again against its own allowlist, executes it, writes results/<id>.json and one audit line per request
(audit/<YYYY-MM>.jsonl), and moves the request to requests/done/. One at a time (flock).

B0 knows one action, `ping` (it changes nothing). Further actions come with their own reviewed PR: B3b the NAS
job (lab/ops/uijobs), and after variant A the bot actions (B1 pause, B2 start/stop). Under variant A,
LAB_CONTROL_REQUIRE_OWNER=lab-ui makes it refuse request files not owned by that user.

  python3 -I lab/ops/control/executor.py     # process what is queued, then exit (the path unit starts it)
"""

import fcntl
import json
import os
import pwd
import re
import time

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
CONTROL = os.environ.get("LAB_CONTROL", os.path.join(DATA, "control"))
REQUIRE_OWNER = os.environ.get("LAB_CONTROL_REQUIRE_OWNER")  # variant A: "lab-ui"
ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
MAX_BYTES = 4096
KEYS = {"id", "action", "params", "node", "name", "ts"}


def _ping(params):
    return {"ok": True, "pong_unix": int(time.time())}


ACTIONS = {"ping": _ping}


def _audit(**fields):
    d = os.path.join(CONTROL, "audit")
    os.makedirs(d, exist_ok=True)
    line = json.dumps({"ts": int(time.time()), "source": "executor", **fields}, sort_keys=True) + "\n"
    fd = os.open(os.path.join(d, time.strftime("%Y-%m.jsonl", time.gmtime())), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    try:
        os.write(fd, line.encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, obj):
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f)
    os.replace(path + ".tmp", path)


def handle(path):
    """One request file: (id, outcome). Never raises for a bad request; it is refused and audited."""
    name = os.path.basename(path)
    rid = name[:-5] if name.endswith(".json") else ""
    t0 = time.time()
    req, outcome, result = {}, "refused", None
    try:
        if not ID_RE.match(rid):
            raise ValueError("bad request file name")
        st = os.stat(path)
        if REQUIRE_OWNER and pwd.getpwuid(st.st_uid).pw_name != REQUIRE_OWNER:
            raise ValueError(f"request not filed by {REQUIRE_OWNER}")
        if st.st_size > MAX_BYTES:
            raise ValueError("request too large")
        with open(path) as f:
            req = json.load(f)
        if not isinstance(req, dict) or set(req) != KEYS or req["id"] != rid:
            raise ValueError("malformed request")
        fn = ACTIONS.get(req["action"])
        if fn is None:
            raise ValueError("action not allowed")
        if not isinstance(req["params"], dict):
            raise ValueError("params")
        result = fn(req["params"])
        outcome = "ok"
    except (ValueError, OSError, KeyError, TypeError) as e:
        result = {"ok": False, "error": str(e)[:300]}
    done = {"id": rid or name[:40], "state": "done" if outcome == "ok" else "refused", "action": req.get("action"),
            "result": result, "ended_unix": int(time.time())}
    os.makedirs(os.path.join(CONTROL, "results"), exist_ok=True)
    if ID_RE.match(rid):
        _write(os.path.join(CONTROL, "results", f"{rid}.json"), done)
    _audit(event="executed" if outcome == "ok" else "refused", request_id=rid or None, action=req.get("action"),
           node=req.get("node"), name=req.get("name"), outcome=outcome, error=None if outcome == "ok" else result.get("error"),
           duration_ms=int((time.time() - t0) * 1000))
    os.makedirs(os.path.join(CONTROL, "requests", "done"), exist_ok=True)
    os.replace(path, os.path.join(CONTROL, "requests", "done", name))
    return rid, outcome


def run():
    """Every queued request, oldest first, under one lock."""
    d = os.path.join(CONTROL, "requests")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(CONTROL, "executor.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        out = []
        for name in sorted(n for n in os.listdir(d) if n.endswith(".json") and not n.startswith(".")):
            out.append(handle(os.path.join(d, name)))
        return out


if __name__ == "__main__":
    run()
