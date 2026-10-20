"""lab-nas-dispatch: hands the UI's research jobs to the NAS and fetches their results (spec v0.3 §5, §5.1).
The only holder of the NAS key. Parameters only: the NAS's forced command (lab/ops/uijobs/ui_queue.py) takes a
schema-checked JSON and nothing else; the NAS renders the job from a template it has pinned.

Each run (lab-nas-dispatch.timer, every 2 minutes; one at a time by flock):
  1. a job in <control>/nas-queue/: ask the NAS `sums` for the pinned template's sha256, build the job (schema
     check), `put` it, record nas-status/<id>.json = sent with the job's sha256; one job on its way at a time.
     A put without an answer (a timeout, a dropped connection) may still have stored the job: the dispatcher then
     asks `status <id>`; the NAS holding this very job (same job_sha256) counts as sent, an unknown id is put again
     next pass (the NAS's put is idempotent), another job under the id is refused here;
     A put the NAS refused itself (exit 2: schema, size) with the id unknown there is refused here, final;
  2. a job on its way: `status <id>`, whose job_sha256 must be the one recorded (never another job's result);
     once done, `get <id>` -> <data>/nas-results/ui/<id>/result.json; a get the NAS refuses (exit 2: the result
     too large or missing) makes the job failed, final. Another job_sha256 is final (refused
     here); an answer without one, 'unknown' or not JSON is counted in the job's status file and after MAX_UNCLEAR
     passes the job is failed ('no consistent answer'), so one bad id never holds the queue for good. No answer at
     all (the NAS or the network down) is not counted: that says nothing about the job.

Settings in ~/.config/solana-lab/nas-ui.env (no secret in the repo; the key file itself stays where it is):
  NAS_UI_HOST=<the NAS's tailnet IP>  NAS_UI_USER=<user>  NAS_UI_KEY=<path of a key only for this dispatcher>
  NAS_UI_KNOWN_HOSTS=<a known_hosts file with the NAS's host key>
ssh runs with BatchMode, StrictHostKeyChecking=yes against that known_hosts file only, IdentitiesOnly, a short
ConnectTimeout, no agent, no forwarding, and an argv (no shell on this side).

  python3 -I lab/ops/control/nas_dispatch.py
"""

import fcntl
import json
import os
import re
import subprocess
import time

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
CONTROL = os.environ.get("LAB_CONTROL", os.path.join(DATA, "control"))
RESULTS = os.environ.get("LAB_NAS_RESULTS", os.path.join(DATA, "nas-results", "ui"))
ENV_FILE = os.environ.get("LAB_NAS_UI_ENV", os.path.expanduser("~/.config/solana-lab/nas-ui.env"))
ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
TEMPLATE = "backtest-f7"
FINAL = ("done", "failed", "refused")
MAX_UNCLEAR = 10  # status passes (~20 min) without a consistent answer before a job is given up as failed


def _schema():
    import importlib.util
    spec = importlib.util.spec_from_file_location("lab_uijobs_schema_d", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uijobs", "schema.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def settings(path=ENV_FILE):
    """KEY=VALUE lines of nas-ui.env (no shell parsing); the four settings must be there."""
    out = {}
    with open(path) as f:
        for line in f:
            k, sep, v = line.strip().partition("=")
            if sep and k in ("NAS_UI_HOST", "NAS_UI_USER", "NAS_UI_KEY", "NAS_UI_KNOWN_HOSTS"):
                out[k] = v.strip().strip('"')
    missing = {"NAS_UI_HOST", "NAS_UI_USER", "NAS_UI_KEY", "NAS_UI_KNOWN_HOSTS"} - set(out)
    if missing:
        raise SystemExit(f"nas-ui.env: missing {', '.join(sorted(missing))}")
    if not re.fullmatch(r"[0-9A-Za-z._-]{1,64}", out["NAS_UI_USER"]) or not re.fullmatch(r"[0-9A-Za-z.:-]{1,253}", out["NAS_UI_HOST"]):
        raise SystemExit("nas-ui.env: a bad host or user")
    return out


def ssh(cfg, command, stdin=None, timeout=60):
    """One command of the forced command (sums | put | status <id> | get <id>): (exit code, stdout, stderr)."""
    argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={cfg['NAS_UI_KNOWN_HOSTS']}",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=10", "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "ClearAllForwardings=yes",
            "-i", cfg["NAS_UI_KEY"], "-l", cfg["NAS_UI_USER"], cfg["NAS_UI_HOST"], command]
    r = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr.decode(errors="replace")[-300:]


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f)
    os.replace(path + ".tmp", path)


def _status(rid):
    try:
        with open(os.path.join(CONTROL, "nas-status", f"{rid}.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _lookup(cfg, call, rid):
    """The NAS's status of one id, or None when it cannot be had (no answer, not JSON)."""
    try:
        rc, out, _ = call(cfg, f"status {rid}")
        nas = json.loads(out) if rc == 0 else None
    except (ValueError, RecursionError, OSError, subprocess.TimeoutExpired):
        return None
    return nas if isinstance(nas, dict) else None


def run(cfg, call=ssh):
    """One dispatcher pass; a list of what it did (for the log and the tests)."""
    done = []
    st_dir, q = os.path.join(CONTROL, "nas-status"), os.path.join(CONTROL, "nas-queue")
    os.makedirs(st_dir, exist_ok=True)
    os.makedirs(q, exist_ok=True)
    # 2. jobs on their way: status, then the result once final.
    for n in sorted(os.listdir(st_dir)):
        rid = n[:-5]
        st = _status(rid) if n.endswith(".json") and ID_RE.match(rid) else None
        if not st or st.get("state") in FINAL:
            continue
        try:  # one job's bad answer (not JSON, a timeout) never ends the pass for the others (LAB review on #451)
            rc, out, err = call(cfg, f"status {rid}")
            if rc != 0:
                done.append((rid, "status failed", err))
                continue
            nas = json.loads(out)
            if not isinstance(nas, dict):
                raise ValueError("status is not an object")
            mine, theirs = st.get("job_sha256"), nas.get("job_sha256")
            if mine and theirs and theirs != mine:
                reason = "the NAS's job under this id is not the one sent"  # final: never another job's result
                _write(os.path.join(st_dir, f"{rid}.json"), dict(st, state="refused", reason=reason, nas=nas, checked_unix=int(time.time())))
                done.append((rid, "refused", reason))
                continue
            if nas.get("state") == "unknown" or (mine and not theirs):
                raise ValueError(f"no job_sha256 or an unknown id (state {nas.get('state')!r})")
            state = {"done": "done", "failed": "failed", "refused": "refused", "running": "running", "queued": "queued"}.get(nas.get("state"), st["state"])
            if state == "done":
                rc, out, err = call(cfg, f"get {rid}")
                if rc == 2:
                    # The NAS's forced command refused the result (too large, missing): final, never retried for good
                    # (LAB review on #470). An ssh failure (255) or a timeout stays an uncounted retry.
                    reason = f"the NAS refused the result: {err.strip()[:200]}"
                    _write(os.path.join(st_dir, f"{rid}.json"), dict(st, state="failed", reason=reason, nas=nas, checked_unix=int(time.time())))
                    done.append((rid, "failed", reason))
                    continue
                if rc != 0:
                    done.append((rid, "get failed", err))
                    continue
                _write(os.path.join(RESULTS, rid, "result.json"), json.loads(out))
        except (OSError, subprocess.TimeoutExpired) as e:  # no answer: says nothing about this job, not counted
            done.append((rid, "status failed", str(e)[:200]))
            continue
        except (ValueError, RecursionError) as e:  # an answer, but not a consistent one about our job: counted
            k = int(st.get("unclear_passes") or 0) + 1
            if k >= MAX_UNCLEAR:
                reason = f"no consistent answer from the NAS in {k} passes ({str(e)[:150]})"
                _write(os.path.join(st_dir, f"{rid}.json"), dict(st, state="failed", reason=reason, unclear_passes=k, checked_unix=int(time.time())))
                done.append((rid, "failed", reason))
            else:
                _write(os.path.join(st_dir, f"{rid}.json"), dict(st, unclear_passes=k, checked_unix=int(time.time())))
                done.append((rid, "bad answer", f"{str(e)[:200]} ({k}/{MAX_UNCLEAR})"))
            continue
        _write(os.path.join(st_dir, f"{rid}.json"), dict(st, state=state, nas=nas, unclear_passes=0, checked_unix=int(time.time())))
        done.append((rid, state, ""))
    # 1. a queued job, only when none is on its way.
    if any((_status(n[:-5]) or {}).get("state") not in FINAL for n in os.listdir(st_dir) if n.endswith(".json")):
        return done
    for n in sorted(x for x in os.listdir(q) if x.endswith(".json") and not x.startswith(".")):
        rid = n[:-5]
        path = os.path.join(q, n)
        try:
            with open(path) as f:
                local = json.load(f)
            try:
                rc, out, err = call(cfg, "sums")
            except (OSError, subprocess.TimeoutExpired) as e:
                rc, out, err = 255, b"", str(e)[:200]
            if rc != 0:
                done.append((rid, "sums failed", err))
                break
            sha = json.loads(out).get(TEMPLATE)
            if not sha:
                raise ValueError(f"the NAS has no pinned {TEMPLATE}")
            schema = _schema()
            job = schema.validate({"id": rid, "template": TEMPLATE, "template_sha256": sha, "params": local["params"]})
            job_sha = schema.job_sha256(job)
        except (OSError, ValueError, KeyError, RecursionError) as e:
            _write(os.path.join(st_dir, f"{rid}.json"), {"id": rid, "state": "refused", "reason": str(e)[:300], "checked_unix": int(time.time())})
            os.replace(path, os.path.join(q, f".refused-{n}"))
            done.append((rid, "refused", str(e)))
            continue
        try:
            rc, out, err = call(cfg, "put", schema.canonical(job))
        except (OSError, subprocess.TimeoutExpired) as e:
            rc, out, err = 255, b"", str(e)[:200]
        how = "sent"
        if rc != 0:
            # The NAS may have stored the job before its answer was lost (Codex review): look it up, never guess.
            known = _lookup(cfg, call, rid)
            if (not known or known.get("state") == "unknown") and rc == 2:
                # The NAS's forced command refused the job itself (schema, size) and does not hold it: final, the
                # queue goes on (LAB review on #467). An ssh failure (255) or a timeout stays the lost-answer path.
                reason = f"the NAS refused the job: {err.strip()[:200]}"
                _write(os.path.join(st_dir, f"{rid}.json"), {"id": rid, "state": "refused", "reason": reason, "checked_unix": int(time.time())})
                os.replace(path, os.path.join(q, f".refused-{n}"))
                done.append((rid, "refused", reason))
                continue
            if not known or known.get("state") == "unknown":
                done.append((rid, "put failed", err))
                break  # try again next pass; the job stays queued and the NAS's put is idempotent
            if known.get("job_sha256") != job_sha:
                reason = "the NAS holds another job under this id"
                _write(os.path.join(st_dir, f"{rid}.json"), {"id": rid, "state": "refused", "reason": reason, "checked_unix": int(time.time())})
                os.replace(path, os.path.join(q, f".refused-{n}"))
                done.append((rid, "refused", reason))
                continue
            how = "sent (put's answer lost, found on the NAS)"
        _write(os.path.join(st_dir, f"{rid}.json"), {"id": rid, "state": "sent", "params": job["params"], "template_sha256": sha,
                                                     "job_sha256": job_sha, "sent_unix": int(time.time())})
        os.replace(path, os.path.join(q, f".sent-{n}"))
        done.append((rid, how, ""))
        break  # one job at a time
    return done


def main():
    with open(os.path.join(CONTROL, "nas-dispatch.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        did = run(settings())
        for rid, what, err in did:
            print(f"{time.strftime('%FT%TZ', time.gmtime())} {rid} {what} {err}".rstrip(), flush=True)
        # A heartbeat for the page: whether the dispatcher runs at all, and whether its last NAS call failed.
        bad = [f"{w}: {e}" for _, w, e in did if w.endswith("failed") or w == "bad answer"]
        _write(os.path.join(CONTROL, "nas-dispatch.json"), {"last_run_unix": int(time.time()), "ok": not bad, "last_error": bad[-1][:200] if bad else None})


if __name__ == "__main__":
    os.makedirs(CONTROL, exist_ok=True)
    main()
