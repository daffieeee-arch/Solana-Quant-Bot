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
Every refusal is audited (audit/web-<YYYY-MM>.jsonl); the executor audits what it does (audit/<YYYY-MM>.jsonl).
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
# The actions the web server may file. The executor has its own list and checks again (lab/ops/control).
ACTIONS = {"ping": "Test: de executor antwoordt (verandert niets)."}

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


def audit(event, **fields):
    """One line in audit/web-<YYYY-MM>.jsonl (append, fsync): the web server's own events and refusals."""
    d = os.path.join(CONTROL, "audit")
    os.makedirs(d, exist_ok=True)
    line = json.dumps({"ts": int(time.time()), "source": "web", "event": event, **fields}, sort_keys=True) + "\n"
    fd = os.open(os.path.join(d, f"web-{_month()}.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
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
    except ValueError:
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
    if params != {}:
        raise Refused(400, "ping takes no parameters")
    now = time.time()
    _rate(s["node"], now)
    nonce = secrets.token_urlsafe(24)
    with _lock:
        for k in [k for k, v in _nonces.items() if v[4] < now]:
            del _nonces[k]
        _nonces[nonce] = (s["node"], action, params, ACTIONS[action], now + NONCE_SECS)
    audit("prepare", node=s["node"], action=action)
    return {"nonce": nonce, "text": ACTIONS[action], "expires_unix": int(now + NONCE_SECS)}


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
        with open(os.path.join(CONTROL, "audit", f"web-{_month()}.jsonl")) as f:
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
    """The last audit lines, web and executor, newest first (the read-only Logboek page)."""
    d = os.path.join(CONTROL, "audit")
    out = []
    for name in sorted(os.listdir(d), reverse=True)[:4] if os.path.isdir(d) else []:
        try:
            with open(os.path.join(d, name)) as f:
                out += [json.loads(line) for line in f if line.strip()]
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda e: e.get("ts", 0), reverse=True)[:limit]
