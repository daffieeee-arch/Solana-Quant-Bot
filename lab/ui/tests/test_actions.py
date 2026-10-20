"""The interactive UI's security layer (lab/ui/actions.py, B0) and the executor (lab/ops/control/executor.py), spec
v0.3 §2: who is calling (tailscaled whois at prepare and commit, the actors file), pairing (the hash hand-over,
single use, lockout), sessions bound to a node, CSRF, the exposure check, single-use confirmations, rate limits,
the request file and the executor's own allowlist; no secret in a file the code does not need, or in the audit.

Run: python3 lab/ui/tests/test_actions.py (stdlib; the server's Python)
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OPS = os.path.join(UI, "..", "ops")


def read(path):
    with open(path) as f:
        return f.read()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


PHONE, OTHER = "nPHONE1CNTRL", "nOTHER1CNTRL"
ORIGIN = "http://100.112.193.63:8501"
JSON = {"Content-Type": "application/json", "X-Lab-Action": "1", "Origin": ORIGIN}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["LAB_CONTROL"] = os.path.join(self.tmp, "control")
        os.environ["LAB_UI_STATE"] = os.path.join(self.tmp, "state")
        self.a = load("t_actions", os.path.join(UI, "actions.py"))
        self.pair_mod = load("t_pair", os.path.join(UI, "pair.py"))
        self.ex = load("t_executor", os.path.join(OPS, "control", "executor.py"))
        self.nodes = {"100.64.0.1": PHONE, "100.64.0.2": OTHER}
        self.serve = {}
        self.a._localapi = self.fake_localapi
        os.makedirs(self.a.STATE)
        with open(os.path.join(self.a.STATE, "ui-actors.json"), "w") as f:
            json.dump({"nodes": [{"id": PHONE, "name": "iphone"}]}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp)
        for k in ("LAB_CONTROL", "LAB_UI_STATE"):
            del os.environ[k]

    def fake_localapi(self, path):
        if path.startswith("/localapi/v0/whois?addr="):
            ip = path.split("=", 1)[1].split(":")[0]
            if ip not in self.nodes:
                return {}
            return {"Node": {"StableID": self.nodes[ip], "Name": "dev." + ip}, "UserProfile": {"LoginName": "x"}}
        if path == "/localapi/v0/serve-config":
            return self.serve
        raise AssertionError(path)

    def code(self):
        code, _ = self.pair_mod.new_code()
        import hashlib
        d = os.path.join(self.a.CONTROL, "pairing")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{time.time_ns()}.json"), "w") as f:
            json.dump({"sha256": hashlib.sha256(code.encode()).hexdigest(), "expires_unix": time.time() + 300}, f)
        return code

    def session(self, ip="100.64.0.1"):
        sid, csrf, _ = self.a.pair(f"{ip}:5000", self.code())
        return sid, csrf

    def refused(self, status, fn, *args):
        with self.assertRaises(self.a.Refused) as c:
            fn(*args)
        self.assertEqual(c.exception.status, status, c.exception.reason)


class Pairing(Base):
    def test_code_hand_over_is_single_use_and_bound_to_the_node(self):
        code = self.code()
        sid, csrf, exp = self.a.pair("100.64.0.1:5000", code.lower()[:4] + "-" + code[4:])  # case and dashes are fine
        self.assertEqual(os.listdir(os.path.join(self.a.CONTROL, "pairing")), [])  # consumed
        self.refused(403, self.a.pair, "100.64.0.1:5000", code)  # single use
        s = self.a.session("100.64.0.1:6000", sid)
        self.assertEqual((s["node"], s["csrf"]), (PHONE, csrf))
        with open(os.path.join(self.a.STATE, "ui-sessions.json")) as f:
            stored = f.read()
        self.assertNotIn(sid, stored)  # only its hash
        self.assertEqual(os.stat(os.path.join(self.a.STATE, "ui-sessions.json")).st_mode & 0o777, 0o600)

    def test_only_listed_devices_and_lockout(self):
        self.refused(403, self.a.pair, "100.64.0.2:5000", self.code())  # a tailnet node not in ui-actors.json
        self.refused(403, self.a.pair, "100.64.0.9:5000", self.code())  # not a tailnet node
        code = self.code()
        for _ in range(5):
            self.refused(403, self.a.pair, "100.64.0.1:5000", "WRONG" * 4)
        self.refused(429, self.a.pair, "100.64.0.1:5000", code)  # locked, even with the right code

    def test_expired_code(self):
        import hashlib
        code, _ = self.pair_mod.new_code()
        d = os.path.join(self.a.CONTROL, "pairing")
        os.makedirs(d)
        with open(os.path.join(d, "old.json"), "w") as f:
            json.dump({"sha256": hashlib.sha256(code.encode()).hexdigest(), "expires_unix": time.time() - 1}, f)
        self.refused(403, self.a.pair, "100.64.0.1:5000", code)
        self.assertEqual(os.listdir(d), [])

    def test_pair_cli_hands_over_the_hash_only(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.pair_mod.CONTROL = self.a.CONTROL
            self.pair_mod.main()
        code = out.getvalue().strip().split()[-1].replace("-", "")
        self.assertEqual(len(code), 20)
        d = os.path.join(self.a.CONTROL, "pairing")
        (name,) = os.listdir(d)
        with open(os.path.join(d, name)) as f:
            self.assertNotIn(code, f.read())
        self.a.pair("100.64.0.1:5000", code)

    def test_session_from_another_device_or_expired(self):
        sid, _ = self.session()
        self.nodes["100.64.0.1"] = OTHER  # the same address now belongs to another node
        self.refused(403, self.a.session, "100.64.0.1:5000", sid)
        self.nodes["100.64.0.1"] = PHONE
        st = json.loads(read(os.path.join(self.a.STATE, "ui-sessions.json")))
        for s in st["sessions"].values():
            s["expires"] = time.time() - 1
        self.a._save("ui-sessions.json", st)
        self.refused(401, self.a.session, "100.64.0.1:5000", sid)
        self.refused(401, self.a.session, "100.64.0.1:5000", None)

    def test_tailscaled_down_means_closed(self):
        def down(path):
            raise self.a.Refused(503, "tailscaled not reachable")
        self.a._localapi = down
        self.refused(503, self.a.pair, "100.64.0.1:5000", "X")


class Csrf(Base):
    def test_post_checks(self):
        ok = lambda o: o == ORIGIN  # noqa: E731
        self.assertEqual(self.a.check_post(JSON, ok, b'{"a": 1}'), {"a": 1})
        self.refused(415, self.a.check_post, dict(JSON, **{"Content-Type": "application/x-www-form-urlencoded"}), ok, b"a=1")
        self.refused(415, self.a.check_post, dict(JSON, **{"Content-Type": "text/plain"}), ok, b"{}")
        self.refused(403, self.a.check_post, {k: v for k, v in JSON.items() if k != "X-Lab-Action"}, ok, b"{}")
        self.refused(403, self.a.check_post, dict(JSON, Origin="http://evil.example"), ok, b"{}")
        self.refused(403, self.a.check_post, {k: v for k, v in JSON.items() if k != "Origin"}, ok, b"{}")
        self.refused(413, self.a.check_post, JSON, ok, b"{" + b" " * 5000 + b"}")
        self.refused(400, self.a.check_post, JSON, ok, b"[1]")


class PrepareCommit(Base):
    def test_ping_round_trip_through_the_executor(self):
        sid, csrf = self.session()
        p = self.a.prepare("100.64.0.1:5000", sid, {"csrf": csrf, "action": "ping"}, 8501)
        self.assertIn("verandert niets", p["text"])
        rid = self.a.commit("100.64.0.1:5001", sid, {"csrf": csrf, "nonce": p["nonce"]}, 8501)["id"]
        self.assertEqual(self.a.result(rid)["state"], "pending")
        req = json.loads(read(os.path.join(self.a.CONTROL, "requests", f"{rid}.json")))
        self.assertEqual(set(req), self.ex.KEYS)
        self.refused(409, self.a.commit, "100.64.0.1:5000", sid, {"csrf": csrf, "nonce": p["nonce"]}, 8501)  # single use
        self.assertEqual(self.ex.run(), [(rid, "ok")])
        r = self.a.result(rid)
        self.assertEqual((r["state"], r["result"]["ok"]), ("done", True))
        events = [e["event"] for e in self.a.log()]
        self.assertIn("executed", events)
        self.assertIn("commit", events)
        audit = "".join(read(os.path.join(self.a.CONTROL, "audit", n)) for n in os.listdir(os.path.join(self.a.CONTROL, "audit")))
        self.assertNotIn(sid, audit)
        self.assertNotIn(csrf, audit)

    def test_refusals(self):
        sid, csrf = self.session()
        peer = "100.64.0.1:5000"
        self.refused(403, self.a.prepare, peer, sid, {"csrf": "x", "action": "ping"}, 8501)
        self.refused(400, self.a.prepare, peer, sid, {"csrf": csrf, "action": "shell"}, 8501)
        self.refused(400, self.a.prepare, peer, sid, {"csrf": csrf, "action": "ping", "params": {"cmd": "ls"}}, 8501)
        self.serve = {"AllowFunnel": {"x:443": True}}
        self.refused(423, self.a.prepare, peer, sid, {"csrf": csrf, "action": "ping"}, 8501)
        self.serve = {"Web": {"x:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8501"}}}}}
        self.refused(423, self.a.prepare, peer, sid, {"csrf": csrf, "action": "ping"}, 8501)
        self.serve = {"Web": {"x:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}}, "TCP": {"443": {"HTTPS": True}}}
        p = self.a.prepare(peer, sid, {"csrf": csrf, "action": "ping"}, 8501)  # another project's Serve entry: fine
        self.nodes["100.64.0.1"] = OTHER  # whois again at commit: another device now
        self.refused(403, self.a.commit, peer, sid, {"csrf": csrf, "nonce": p["nonce"]}, 8501)

    def test_one_in_flight_and_ten_an_hour(self):
        sid, csrf = self.session()
        peer = "100.64.0.1:5000"
        p = self.a.prepare(peer, sid, {"csrf": csrf, "action": "ping"}, 8501)
        self.a.commit(peer, sid, {"csrf": csrf, "nonce": p["nonce"]}, 8501)
        self.refused(429, self.a.prepare, peer, sid, {"csrf": csrf, "action": "ping"}, 8501)  # one in flight
        self.ex.run()
        for _ in range(9):
            p = self.a.prepare(peer, sid, {"csrf": csrf, "action": "ping"}, 8501)
            self.a.commit(peer, sid, {"csrf": csrf, "nonce": p["nonce"]}, 8501)
            self.ex.run()
        self.refused(429, self.a.prepare, peer, sid, {"csrf": csrf, "action": "ping"}, 8501)


class Executor(Base):
    def put(self, name, obj):
        d = os.path.join(self.ex.CONTROL, "requests")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))

    def test_own_allowlist_and_shape(self):
        ok = {"id": self.a.ulid(), "action": "ping", "params": {}, "node": PHONE, "name": "x", "ts": 1}
        bad_action = dict(ok, id=self.a.ulid(time.time() + 1), action="systemctl")
        extra = dict(ok, id=self.a.ulid(time.time() + 2), cmd="ls")
        self.put(f"{ok['id']}.json", ok)
        self.put(f"{bad_action['id']}.json", bad_action)
        self.put(f"{extra['id']}.json", extra)
        self.put("notanid.json", "{}")
        out = dict(self.ex.run())
        self.assertEqual(out[ok["id"]], "ok")
        self.assertEqual((out[bad_action["id"]], out[extra["id"]]), ("refused", "refused"))
        self.assertEqual(out["notanid"], "refused")
        self.assertEqual(sorted(os.listdir(os.path.join(self.ex.CONTROL, "requests"))), ["done"])

    def test_variant_a_owner(self):
        self.ex.REQUIRE_OWNER = "lab-ui-does-not-exist"
        r = {"id": self.a.ulid(), "action": "ping", "params": {}, "node": PHONE, "name": "x", "ts": 1}
        self.put(f"{r['id']}.json", r)
        self.assertEqual(self.ex.run(), [(r["id"], "refused")])


if __name__ == "__main__":
    unittest.main()
