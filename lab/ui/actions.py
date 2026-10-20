"""The interactive UI's security layer (B0; spec v0.3 §2): who is calling, login, CSRF, confirmation, rate limits,
the Tailscale exposure check, and the hand-off to the executor. The web server never runs a process or touches the
bot: an action that passes every check becomes one request file in <data>/control/requests/ (atomic rename),
which lab-control (lab/ops/control/executor.py, the owner's user) validates again and executes.

- Identity: tailscaled's LocalAPI whois on the peer address (unix socket, read-only), at prepare and at commit;
  the node's StableID must be in the actors file (ui-actors.json). tailscaled unreachable: refused.
- Login: a pairing code (lab/ui/pair.py prints it in a VPS shell; only its SHA-256 is handed over through
  <data>/control/pairing/, consumed and deleted here) becomes a session: a random id in an HttpOnly,
  SameSite=Strict cookie on /api/action, valid 12 h and only from the node that paired; only its SHA-256 is stored.
  Five wrong codes from a node lock pairing for 15 minutes.
- CSRF: POST with Content-Type application/json, the header X-Lab-Action: 1, an Origin equal to the served origin
  and a per-session token in the body.
- Confirmation: prepare returns a single-use nonce (60 s) and the exact text; only commit files the request.
- Rate: one action in flight and 10 per hour per node.
- Exposure: actions are refused while Tailscale Funnel is on or any Serve entry points at this port.
Every refusal is audited in the web app's own subdirectory (audit/web/<YYYY-MM>.jsonl, the only audit path it may
write under variant A); the executor audits what it does (audit/<YYYY-MM>.jsonl, read-only for the web app).
Secrets live in the state directory (LAB_UI_STATE; variant B: ~/.config/solana-lab, variant A: /var/lib/lab-ui),
never in the repo or a log.
"""

import hashlib
import http.client
import json
import os
import secrets
import socket
import threading
import time
from urllib.parse import quote

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
CONTROL = os.environ.get("LAB_CONTROL", os.path.join(DATA, "control"))
STATE = os.environ.get("LAB_UI_STATE", os.path.expanduser("~/.config/solana-lab"))
TAILSCALED = os.environ.get("LAB_TAILSCALED_SOCK", "/run/tailscale/tailscaled.sock")
SESSION_SECS, NONCE_SECS, PAIR_LOCK_SECS = 12 * 3600, 60, 15 * 60
MAX_FAILS, PER_HOUR, MAX_BODY = 5, 10, 4096
COOKIE = "lab_session"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _load_schema():
    """lab/ops/uijobs/schema.py by its path: the one schema of a UI research job (spec v0.3 §5.1)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ops", "uijobs", "schema.py")
    import importlib.util
    spec = importlib.util.spec_from_file_location("lab_uijobs_schema", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jobs = _load_schema()


def _job_params(params):
    """A NAS job's params, checked against the job schema (the executor and the NAS check again)."""
    try:
        return jobs.validate({"id": "0" * 26, "template": "backtest-f7", "template_sha256": "0" * 64, "params": params})["params"]
    except (ValueError, TypeError, RecursionError) as e:
        raise Refused(400, f"job: {e}") from None


def _job_text(p):
    return (f"Backtest op de NAS: {p['variant']} naast K3 (tp4_T600), {p['segment']}, farm {p['farm']}, {str(p['size_sol']).rstrip('0').rstrip('.').replace('.', ',')} SOL, "
            f"{p['d_ms']} ms, store {p['store']} epochs {p['epoch_from']}–{p['epoch_to']}. Verkennend: telt als een trial in het "
            "register. Verandert niets aan de paper-bot.")


# The actions the web server may file: action -> (validate params, the text to confirm). The executor has its own
# list and checks again (lab/ops/control/executor.py), and the NAS a third time for a job.
def _no_params(params):
    if params != {}:
        raise Refused(400, "ping takes no parameters")
    return {}


import re as _re
NOTE_MAX = 500
_NOTE_DROP = _re.compile(r"[\u0000-\u0009\u000b-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def note_params(p):
    """An owner's note (backlog 5): {"text"} plus a day {"day"} or a position {"key", "variant", "t"}. The text is the
    owner's own words: control and bidi characters dropped, at most NOTE_MAX characters; the page escapes it. The
    executor checks the same (lab/ops/control/executor.py)."""
    if not isinstance(p, dict) or set(p) not in ({"text", "day"}, {"text", "key", "variant", "t"}):
        raise Refused(400, "note: text and a day, or text and key, variant, t")
    text = _NOTE_DROP.sub("", p["text"]).strip() if isinstance(p["text"], str) else ""
    if not 1 <= len(text) <= NOTE_MAX:
        raise Refused(400, f"note: 1 to {NOTE_MAX} characters")
    if "day" in p:
        if not (isinstance(p["day"], str) and _re.fullmatch(r"20\d\d-\d\d-\d\d", p["day"])):
            raise Refused(400, "note: day as YYYY-MM-DD")
        return {"text": text, "day": p["day"]}
    if not (isinstance(p["key"], str) and 32 <= len(p["key"]) <= 44 and p["key"].isalnum()
            and isinstance(p["variant"], str) and _re.fullmatch(r"[A-Za-z0-9_]{1,40}", p["variant"])
            and type(p["t"]) is int and p["t"] > 0):
        raise Refused(400, "note: a position as key, variant, t")
    return {"text": text, "key": p["key"], "variant": p["variant"], "t": p["t"]}


def _note_text(p):
    target = f"dag {p['day']}" if "day" in p else f"trade {p['key'][:4]}…{p['key'][-4:]} ({p['variant']}, slot {p['t']})"
    short = p["text"] if len(p["text"]) <= 80 else p["text"][:79] + "…"
    return f"Notitie bij {target}: “{short}”. Opgeslagen in een eigen bestand, niet in het journaal; de bot leest het nooit."


def notes(day=None, key=None, variant=None, t=None, limit=50):
    """The owner's notes for a day or a position (<control>/notes/notes.jsonl, written by the executor), newest first."""
    out = []
    try:
        with open(os.path.join(CONTROL, "notes", "notes.jsonl")) as f:
            for line in f:
                try:
                    n = json.loads(line)
                except ValueError:
                    continue
                if (day and n.get("day") == day) or (key and n.get("key") == key and n.get("variant") == variant and n.get("t") == t):
                    out.append({k: n.get(k) for k in ("id", "ts", "text", "day", "key", "variant", "t")})
    except OSError:
        pass
    return sorted(out, key=lambda n: n.get("ts") or 0, reverse=True)[:limit]


ACTIONS = {
    "ping": (_no_params, lambda p: "Test: de executor antwoordt (verandert niets)."),
    "nas_job": (_job_params, _job_text),
    "note": (note_params, _note_text),
}
DESCRIPTIONS = {"ping": "Test: de executor antwoordt (verandert niets).",
                "nas_job": "Een backtest op de NAS uit een vast sjabloon (strategiekiezer); verandert niets aan de paper-bot.",
                "note": "Een notitie bij een trade of een dag, in een eigen bestand; de bot leest het nooit."}


def jobs_view(limit=20):
    """The UI's research jobs, newest first: queued here, sent, on the NAS, done (with the result), refused.
    Read-only; the result is the tournament report's own rows (lab/ops/uijobs/result.py)."""
    out = {}
    q, st = os.path.join(CONTROL, "nas-queue"), os.path.join(CONTROL, "nas-status")
    for n in os.listdir(q) if os.path.isdir(q) else []:
        if n.endswith(".json") and not n.startswith("."):
            try:
                with open(os.path.join(q, n)) as f:
                    j = json.load(f)
                out[j["id"]] = {"id": j["id"], "state": "queued_here", "params": j.get("params"), "ts": j.get("ts")}
            except (OSError, ValueError, KeyError):
                continue
    for n in os.listdir(st) if os.path.isdir(st) else []:
        try:
            with open(os.path.join(st, n)) as f:
                j = json.load(f)
            out[j["id"]] = {"id": j["id"], "state": j.get("state"), "params": j.get("params") or (out.get(j["id"]) or {}).get("params"),
                            "reason": j.get("reason"), "ts": j.get("sent_unix") or j.get("checked_unix")}
        except (OSError, ValueError, KeyError):
            continue
    res_dir = os.environ.get("LAB_NAS_RESULTS", os.path.join(DATA, "nas-results", "ui"))
    for jid, j in out.items():
        p = os.path.join(res_dir, jid, "result.json")
        if os.path.isfile(p):
            try:
                with open(p) as f:
                    j["result"] = json.load(f)
            except (OSError, ValueError):
                pass
    return sorted(out.values(), key=lambda j: j["id"], reverse=True)[:limit]


def dispatcher_state(now=None):
    """Whether the NAS dispatcher runs (its heartbeat <control>/nas-dispatch.json, written every pass): 'off' (never
    ran: the NAS side is not installed yet), 'stale' (no pass for 10 minutes), 'error' (the last NAS call failed)
    or 'ok'."""
    try:
        with open(os.path.join(CONTROL, "nas-dispatch.json")) as f:
            hb = json.load(f)
    except (OSError, ValueError):
        return {"state": "off"}
    age = (now or time.time()) - (hb.get("last_run_unix") or 0)
    return {"state": "stale" if age > 600 else ("ok" if hb.get("ok") else "error"), "last_run_unix": hb.get("last_run_unix"),
            "last_error": hb.get("last_error")}


def job_options():
    """The job form's choices, straight from the job schema (so the page and the checks never drift)."""
    return {"grids": jobs.GRIDS, "k3": jobs.K3, "segments": list(jobs.SEGMENTS), "farms": list(jobs.FARMS), "sizes": list(jobs.SIZES),
            "delays_ms": list(jobs.DELAYS_MS), "max_epochs": jobs.MAX_EPOCHS,
            "stores": {k: {"from": v[1], "to": v[2]} for k, v in jobs.STORES.items()}}


def job_in_flight():
    """Whether a UI job is still on its way (queued here, sent, queued or running on the NAS): one at a time."""
    q = os.path.join(CONTROL, "nas-queue")
    if os.path.isdir(q) and any(n.endswith(".json") and not n.startswith(".") for n in os.listdir(q)):
        return True
    st = os.path.join(CONTROL, "nas-status")
    for n in os.listdir(st) if os.path.isdir(st) else []:
        try:
            with open(os.path.join(st, n)) as f:
                if json.load(f).get("state") in ("sent", "queued", "running"):
                    return True
        except (OSError, ValueError):
            continue
    return False

_lock = threading.Lock()
_nonces = {}  # nonce -> (node id, action, params, text, expires)


class Refused(Exception):
    """An action refused, with the HTTP status and a reason safe to show."""

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


def ulid(now=None):
    """A ULID: 48 bits of milliseconds and 80 random bits, Crockford base32 (time-ordered ids)."""
    n = (int((now or time.time()) * 1000) << 80) | secrets.randbits(80)
    return "".join(CROCKFORD[(n >> (5 * i)) & 31] for i in range(25, -1, -1))


def _sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _month():
    return time.strftime("%Y-%m", time.gmtime())


def _web_audit():
    # The web app's own audit directory: under variant A the unit binds only this one writable, so a broken web
    # server can never rewrite or delete the executor's audit beside it (LAB review on #457).
    return os.path.join(CONTROL, "audit", "web")


def audit(event, **fields):
    """One line in audit/web/<YYYY-MM>.jsonl (append, fsync): the web server's own events and refusals."""
    d = _web_audit()
    os.makedirs(d, exist_ok=True)
    line = json.dumps({"ts": int(time.time()), "source": "web", "event": event, **fields}, sort_keys=True) + "\n"
    fd = os.open(os.path.join(d, f"{_month()}.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    try:
        os.write(fd, line.encode())
        os.fsync(fd)
    finally:
        os.close(fd)


# --- Tailscale -----------------------------------------------------------------------------------------------

class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path, timeout=3):
        super().__init__("local-tailscaled.sock", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def _localapi(path):
    c = _UnixHTTP(TAILSCALED)
    try:
        c.request("GET", path, headers={"Host": "local-tailscaled.sock"})
        r = c.getresponse()
        if r.status != 200:
            raise Refused(403, f"tailscaled: HTTP {r.status}")
        return json.loads(r.read(1 << 20))
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise Refused(503, f"tailscaled not reachable ({e.__class__.__name__})") from None
    finally:
        c.close()


def whois(peer):
    """(stable node id, node name) of a tailnet peer address 'ip:port', from tailscaled. Refused without one."""
    d = _localapi("/localapi/v0/whois?addr=" + quote(peer, safe=":"))
    node = d.get("Node") or {}
    if not node.get("StableID"):
        raise Refused(403, "unknown tailnet node")
    return str(node["StableID"]), str(node.get("Name") or "")[:80]


def exposure(port):
    """None when actions may run; else why not: Funnel on, or a Serve entry pointing at this port."""
    cfg = _localapi("/localapi/v0/serve-config") or {}
    if any(cfg.get("AllowFunnel") or {}):
        return "Tailscale Funnel is on"
    needle = f":{port}"
    for host in (cfg.get("Web") or {}).values():
        for h in (host.get("Handlers") or {}).values():
            if needle in str(h.get("Proxy") or ""):
                return f"a Tailscale Serve entry points at port {port}"
    for t in (cfg.get("TCP") or {}).values():
        if needle in str(t.get("TCPForward") or ""):
            return f"a Tailscale Serve TCP forward points at port {port}"
    return None


# --- state files ---------------------------------------------------------------------------------------------

def _load(name, default):
    try:
        with open(os.path.join(STATE, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(name, obj):
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    path = os.path.join(STATE, name)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.replace(path + ".tmp", path)


def actors():
    """Stable node ids allowed to act (ui-actors.json: {"nodes": [{"id": ..., "name": ...}]}), the owner's
    phone and laptop only (spec §9 Q2)."""
    return {str(n.get("id")) for n in (_load("ui-actors.json", {}).get("nodes") or []) if n.get("id")}


def _node(peer):
    node, name = whois(peer)
    if node not in actors():
        raise Refused(403, "this device may not act (not in ui-actors.json)")
    return node, name


# --- pairing and sessions ------------------------------------------------------------------------------------

def pair(peer, code):
    """A pairing code (from lab/ui/pair.py) for a new session: (cookie value, csrf token, expires)."""
    node, name = _node(peer)
    now = time.time()
    with _lock:
        st = _load("ui-sessions.json", {"sessions": {}, "fails": {}})
        f = st["fails"].get(node, {"n": 0, "until": 0})
        if f["until"] > now:
            raise Refused(429, "pairing locked for 15 minutes after 5 wrong codes")
        clean = "".join(ch for ch in str(code).upper() if ch.isalnum())[:64]
        want = _sha(clean)
        d = os.path.join(CONTROL, "pairing")
        match = None
        for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            p = os.path.join(d, fn)
            try:
                with open(p) as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            if rec.get("expires_unix", 0) < now:
                _unlink(p)  # expired: gone
            elif secrets.compare_digest(str(rec.get("sha256", "")), want):
                match = p
        if match is None:
            f = {"n": f["n"] + 1, "until": now + PAIR_LOCK_SECS if f["n"] + 1 >= MAX_FAILS else 0}
            st["fails"][node] = f
            _save("ui-sessions.json", st)
            raise Refused(403, "wrong or expired code")
        _unlink(match)  # single use: the hand-over file is consumed
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        exp = int(now + SESSION_SECS)
        st["sessions"] = {k: v for k, v in st["sessions"].items() if v["expires"] > now}
        st["sessions"][_sha(sid)] = {"node": node, "name": name, "csrf": csrf, "expires": exp, "created": int(now)}
        st["fails"].pop(node, None)
        _save("ui-sessions.json", st)
    audit("paired", node=node, name=name)
    return sid, csrf, exp


def _unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def session(peer, cookie):
    """The caller's session (node, name, csrf, expires); refused without a valid one from the same node."""
    if not cookie:
        raise Refused(401, "not paired")
    node, name = _node(peer)
    s = _load("ui-sessions.json", {"sessions": {}}).get("sessions", {}).get(_sha(cookie))
    if not s or s["expires"] <= time.time():
        raise Refused(401, "session expired")
    if s["node"] != node:
        raise Refused(403, "session from another device")
    return dict(s, name=name)


# --- CSRF, prepare, commit -----------------------------------------------------------------------------------

def check_post(headers, origin_ok, body):
    """CSRF and size checks of an action POST, before anything is parsed."""
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise Refused(415, "application/json only")
    if headers.get("X-Lab-Action") != "1":
        raise Refused(403, "missing X-Lab-Action")
    if not origin_ok(headers.get("Origin")):
        raise Refused(403, "wrong Origin")
    if len(body) > MAX_BODY:
        raise Refused(413, "body too large")
    try:
        data = json.loads(body or b"{}")
    except (ValueError, RecursionError):
        raise Refused(400, "not JSON") from None
    if not isinstance(data, dict):
        raise Refused(400, "a JSON object")
    return data


def _csrf(s, data):
    if not secrets.compare_digest(str(data.get("csrf", "")), s["csrf"]):
        raise Refused(403, "wrong CSRF token")


def prepare(peer, cookie, data, port):
    """Validate an action and return its single-use nonce and the exact text to confirm."""
    s = session(peer, cookie)
    _csrf(s, data)
    why = exposure(port)
    if why:
        raise Refused(423, f"actions are off: {why}")
    action, params = data.get("action"), data.get("params") or {}
    if action not in ACTIONS:
        raise Refused(400, "unknown action")
    if not isinstance(params, dict):
        raise Refused(400, "params: an object")
    check, text = ACTIONS[action]
    params = check(params)
    if action == "nas_job" and job_in_flight():
        raise Refused(429, "er loopt al een backtest-job (één tegelijk)")
    now = time.time()
    _rate(s["node"], now)
    nonce = secrets.token_urlsafe(24)
    txt = text(params)
    with _lock:
        for k in [k for k, v in _nonces.items() if v[4] < now]:
            del _nonces[k]
        _nonces[nonce] = (s["node"], action, params, txt, now + NONCE_SECS)
    audit("prepare", node=s["node"], action=action)
    return {"nonce": nonce, "text": txt, "expires_unix": int(now + NONCE_SECS)}


def commit(peer, cookie, data, port):
    """Who is calling again, then the request file for the executor; its id."""
    s = session(peer, cookie)
    _csrf(s, data)
    why = exposure(port)
    if why:
        raise Refused(423, f"actions are off: {why}")
    with _lock:
        n = _nonces.pop(str(data.get("nonce", "")), None)
    if not n or n[4] < time.time():
        raise Refused(409, "confirmation expired or already used")
    node, action, params, _, _ = n
    if node != s["node"]:
        raise Refused(403, "confirmation from another device")
    now = time.time()
    _rate(node, now)
    rid = ulid(now)
    req = {"id": rid, "action": action, "params": params, "node": node, "name": s["name"], "ts": int(now)}
    d = os.path.join(CONTROL, "requests")
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{rid}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w") as f:
        json.dump(req, f)
    os.replace(tmp, os.path.join(d, f"{rid}.json"))
    audit("commit", node=node, action=action, request_id=rid)
    return {"id": rid}


def _rate(node, now):
    """One action in flight (a request without a result) and PER_HOUR per node, from the audit's commits."""
    d = os.path.join(CONTROL, "requests")
    if os.path.isdir(d) and any(n.endswith(".json") for n in os.listdir(d)):
        raise Refused(429, "another action is still being executed")
    n = 0
    try:
        with open(os.path.join(_web_audit(), f"{_month()}.jsonl")) as f:
            for line in f:
                e = json.loads(line)
                n += e.get("event") == "commit" and e.get("node") == node and e.get("ts", 0) > now - 3600
    except (OSError, ValueError):
        pass
    if n >= PER_HOUR:
        raise Refused(429, f"at most {PER_HOUR} actions an hour")


def result(rid):
    """The executor's result of a request, or its state."""
    if not (isinstance(rid, str) and len(rid) == 26 and all(c in CROCKFORD for c in rid)):
        raise Refused(400, "bad id")
    p = os.path.join(CONTROL, "results", f"{rid}.json")
    if os.path.isfile(p):
        with open(p) as f:
            return json.load(f)
    if os.path.isfile(os.path.join(CONTROL, "requests", f"{rid}.json")):
        return {"id": rid, "state": "pending"}
    return {"id": rid, "state": "unknown"}


def log(limit=100):
    """The last audit lines, web and executor, newest first (the read-only Logboek page): the last two months of
    each (audit/web/ and audit/; the latter also holds the web app's lines from before 2026-10-21, web-<YYYY-MM>)."""
    out = []
    for d in (_web_audit(), os.path.join(CONTROL, "audit")):
        names = sorted(n for n in os.listdir(d) if n.endswith(".jsonl")) if os.path.isdir(d) else []
        for name in names[-4:]:
            try:
                with open(os.path.join(d, name)) as f:
                    out += [json.loads(line) for line in f if line.strip()]
            except (OSError, ValueError):
                continue
    return sorted(out, key=lambda e: e.get("ts", 0), reverse=True)[:limit]
