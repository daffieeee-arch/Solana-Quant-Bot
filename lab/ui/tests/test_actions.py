"""The interactive UI's security layer (lab/ui/actions.py, B0) and the executor (lab/ops/control/executor.py), spec
v0.3 §2: who is calling (tailscaled whois at prepare and commit, the actors file), pairing (the hash hand-over,
single use, lockout), sessions bound to a node, CSRF, the exposure check, single-use confirmations, rate limits,
the request file and the executor's own allowlist; no secret in a file the code does not need, or in the audit.

Run: python3 lab/ui/tests/test_actions.py (stdlib; the server's Python)
"""

import hashlib
import importlib.util
import io
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
        self.disp = load("t_dispatch", os.path.join(OPS, "control", "nas_dispatch.py"))
        self.disp.CONTROL = self.a.CONTROL
        self.disp.RESULTS = os.path.join(self.tmp, "nas-results", "ui")
        self.a.DATA = self.tmp
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
        self.refused(400, self.a.check_post, JSON, ok, b"[" * 2000 + b"]" * 2000)  # nested too deeply: refused, no traceback


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
        # The web app's lines only in its own audit/web/ (the one audit path variant A lets it write), the executor's
        # in audit/ beside it; the log reads both (LAB review on #457).
        ad = os.path.join(self.a.CONTROL, "audit")
        self.assertEqual({json.loads(ln)["source"] for ln in read(os.path.join(ad, "web", os.listdir(os.path.join(ad, "web"))[0])).splitlines()}, {"web"})
        self.assertEqual({json.loads(ln)["source"] for n in os.listdir(ad) if n.endswith(".jsonl") for ln in read(os.path.join(ad, n)).splitlines()}, {"executor"})
        audit = "".join(read(os.path.join(dp, n)) for dp, _, ns in os.walk(ad) for n in ns)
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


JOB = {"variant": "flow300_H6", "segment": "pool_reversed", "farm": "organic", "size_sol": 0.5, "d_ms": 600,
       "store": "dev2", "epoch_from": 1040, "epoch_to": 1046}


class NasJob(Base):
    """B3b: a research job from the UI: params checked at prepare, by the executor and by the dispatcher; one at a
    time; every job a trial; the dispatcher only talks to the forced command (sums, put, status, get)."""

    def submit(self, params=JOB):
        sid, csrf = self.session()
        p = self.a.prepare("100.64.0.1:5000", sid, {"csrf": csrf, "action": "nas_job", "params": params}, 8501)
        rid = self.a.commit("100.64.0.1:5000", sid, {"csrf": csrf, "nonce": p["nonce"]}, 8501)["id"]
        return p, rid

    def test_params_are_checked_before_anything_is_filed(self):
        sid, csrf = self.session()
        for bad in (dict(JOB, epoch_to=1047), dict(JOB, variant="x; rm -rf /"), dict(JOB, size_sol=1), dict(JOB, extra=1)):
            self.refused(400, self.a.prepare, "100.64.0.1:5000", sid, {"csrf": csrf, "action": "nas_job", "params": bad}, 8501)
        self.assertFalse(os.path.exists(os.path.join(self.a.CONTROL, "requests")))

    def test_job_round_trip_through_the_dispatcher(self):
        p, rid = self.submit()
        self.assertIn("flow300_H6", p["text"])
        self.assertIn("Verandert niets aan de paper-bot", p["text"])
        self.assertEqual(self.ex.run(), [(rid, "ok")])
        self.assertTrue(self.a.job_in_flight())
        with open(os.path.join(self.a.CONTROL, "ui-trials.md")) as f:
            md = f.read()
        self.assertIn(f"| UI-{rid} |", md)
        self.assertIn("| id | datum | wat | data | regel vooraf | uitkomst | status | correcties |", md)
        # a second job while the first is on its way: refused at prepare
        sid, csrf = self.session()
        self.refused(429, self.a.prepare, "100.64.0.1:5000", sid, {"csrf": csrf, "action": "nas_job", "params": JOB}, 8501)
        calls, nas = [], {"state": "queued"}

        def fake(cfg, command, stdin=None, timeout=60):
            calls.append((command, stdin))
            if command == "sums":
                return 0, json.dumps({"backtest-f7": "a" * 64}).encode(), ""
            if command == "put":
                job = json.loads(stdin)
                self.assertEqual((job["id"], job["template_sha256"], job["params"]["variant"]), (rid, "a" * 64, "flow300_H6"))
                nas["job_sha256"] = hashlib.sha256(stdin).hexdigest()  # the NAS stores the canonical bytes sent
                return 0, (rid + "\n").encode(), ""
            if command == f"status {rid}":
                return 0, json.dumps(dict(nas, id=rid)).encode(), ""
            if command == f"get {rid}":
                return 0, json.dumps({"id": rid, "rows": [{"variant": "flow300_H6", "mean_pnl_sol": 0.04}]}).encode(), ""
            raise AssertionError(command)

        self.assertEqual(self.disp.run({}, fake), [(rid, "sent", "")])
        self.assertEqual([c[0] for c in calls], ["sums", "put"])
        self.assertEqual(self.disp.run({}, fake), [(rid, "queued", "")])
        nas["state"] = "done"
        self.assertEqual(self.disp.run({}, fake)[0][:2], (rid, "done"))
        self.assertFalse(self.a.job_in_flight())
        (j,) = self.a.jobs_view()
        self.assertEqual((j["state"], j["result"]["rows"][0]["mean_pnl_sol"]), ("done", 0.04))
        self.assertEqual(self.disp.run({}, fake), [])  # nothing left to do

    def test_a_bad_answer_ends_only_that_jobs_step(self):
        _, rid = self.submit()
        self.ex.run()
        ok = lambda cfg, c, stdin=None, timeout=60: (0, json.dumps({"backtest-f7": "a" * 64}).encode() if c == "sums" else b"x", "")  # noqa: E731
        self.assertEqual(self.disp.run({}, ok)[0][:2], (rid, "sent"))
        garbage = lambda cfg, c, stdin=None, timeout=60: (0, b"<html>not json", "")  # noqa: E731
        self.assertEqual(self.disp.run({}, garbage)[0][:2], (rid, "bad answer"))  # no exception, the pass goes on
        self.assertEqual(self.a.jobs_view()[0]["state"], "sent")  # unchanged until a real answer comes

    def test_dispatcher_refuses_without_a_pinned_template_and_retries_a_failed_put(self):
        _, rid = self.submit()
        self.ex.run()
        self.assertEqual(self.disp.run({}, lambda cfg, c, stdin=None, timeout=60: (0, b"{}", "") if c == "sums" else (1, b"", "x")),
                         [(rid, "refused", "the NAS has no pinned backtest-f7")])
        self.assertEqual(self.a.jobs_view()[0]["state"], "refused")
        _, rid2 = self.submit()
        self.ex.run()

        def put_fails(cfg, command, stdin=None, timeout=60):
            return (0, json.dumps({"backtest-f7": "b" * 64}).encode(), "") if command == "sums" else (255, b"", "Connection timed out")
        self.assertEqual(self.disp.run({}, put_fails), [(rid2, "put failed", "Connection timed out")])
        self.assertEqual(self.a.jobs_view()[0]["state"], "queued_here")  # stays queued for the next pass

    def nas_queue(self, lose):
        """The real forced command (lab/ops/uijobs/ui_queue.py) on a NAS directory, through a connection that loses
        the answer of the commands in `lose` (each once) after the NAS has done them."""
        q = load("t_ui_queue", os.path.join(OPS, "uijobs", "ui_queue.py"))
        q.ROOT = os.path.join(self.tmp, "nas")

        def call(cfg, command, stdin=None, timeout=60):
            if command == "sums":
                return 0, json.dumps({"backtest-f7": "a" * 64}).encode(), ""
            out = io.StringIO()
            try:
                q.run(command, io.BytesIO(stdin or b""), out)
            except ValueError as e:
                return 2, b"", f"refused: {e}"
            if command.split()[0] in lose:
                lose.remove(command.split()[0])
                return 255, b"", "Connection timed out"
            return 0, out.getvalue().encode(), ""
        return q, call

    def test_a_put_whose_answer_was_lost_is_found_never_put_twice(self):
        """Codex review (finding 4): the NAS stores the job, the answer is lost. The dispatcher asks `status` and
        finds its own job (same job_sha256): sent, one job on the NAS. If the status is lost as well, the job stays
        queued and the next put is accepted as the same job (idempotent), still one job on the NAS."""
        _, rid = self.submit()
        self.ex.run()
        q, call = self.nas_queue(["put"])
        self.assertEqual(self.disp.run({}, call), [(rid, "sent (put's answer lost, found on the NAS)", "")])
        self.assertEqual(sorted(os.listdir(os.path.join(q.ROOT, "queue"))), [".put.lock", f"ui-{rid}.json"])
        self.assertEqual(self.disp.run({}, call), [(rid, "queued", "")])  # the status pass: its own job
        # The NAS answers for a different job under this id: never taken for ours, never fetched.
        with open(os.path.join(q.ROOT, "queue", f"ui-{rid}.json"), "ab") as f:
            f.write(b" ")
        self.assertEqual(self.disp.run({}, call)[0][:2], (rid, "bad answer"))
        self.assertEqual(self.a.jobs_view()[0]["state"], "queued")  # as the last answer about its own job

    def test_a_lost_put_and_a_lost_status_stay_queued_then_put_again(self):
        _, rid = self.submit()
        self.ex.run()
        q, call = self.nas_queue(["put", "status"])
        self.assertEqual(self.disp.run({}, call), [(rid, "put failed", "Connection timed out")])
        self.assertEqual(self.a.jobs_view()[0]["state"], "queued_here")
        self.assertEqual(self.disp.run({}, call), [(rid, "sent", "")])  # the NAS's put is idempotent
        self.assertEqual([n for n in os.listdir(os.path.join(q.ROOT, "queue")) if n.startswith("ui-")], [f"ui-{rid}.json"])

    def test_another_job_under_the_id_on_the_nas_is_refused_here(self):
        _, rid = self.submit()
        self.ex.run()
        q, call = self.nas_queue([])
        other = {"id": rid, "template": "backtest-f7", "template_sha256": "a" * 64, "params": dict(JOB, farm="all")}
        q.run("put", io.BytesIO(json.dumps(other).encode()), io.StringIO())
        self.assertEqual(self.disp.run({}, call), [(rid, "refused", "the NAS holds another job under this id")])
        self.assertEqual(self.a.jobs_view()[0]["state"], "refused")

    def test_dispatcher_state_for_the_page(self):
        self.assertEqual(self.a.dispatcher_state()["state"], "off")  # never ran: the NAS side is not installed yet
        hb = os.path.join(self.a.CONTROL, "nas-dispatch.json")
        os.makedirs(self.a.CONTROL, exist_ok=True)
        for ok, age, want in ((True, 30, "ok"), (False, 30, "error"), (True, 900, "stale")):
            with open(hb, "w") as f:
                json.dump({"last_run_unix": int(time.time()) - age, "ok": ok, "last_error": None if ok else "put failed: x"}, f)
            self.assertEqual(self.a.dispatcher_state()["state"], want)

    def test_ssh_is_strict_and_settings_are_checked(self):
        argv = []
        real = self.disp.subprocess.run
        self.disp.subprocess.run = lambda a, **k: argv.append(a) or type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()
        try:
            self.disp.ssh({"NAS_UI_KNOWN_HOSTS": "/k", "NAS_UI_KEY": "/key", "NAS_UI_USER": "u", "NAS_UI_HOST": "100.79.0.1"}, "sums")
        finally:
            self.disp.subprocess.run = real
        a = " ".join(argv[0])
        for opt in ("BatchMode=yes", "StrictHostKeyChecking=yes", "UserKnownHostsFile=/k", "GlobalKnownHostsFile=/dev/null",
                    "IdentitiesOnly=yes", "ConnectTimeout=10", "ForwardAgent=no"):
            self.assertIn(opt, a)
        self.assertEqual(argv[0][-1], "sums")
        env = os.path.join(self.tmp, "nas-ui.env")
        with open(env, "w") as f:
            f.write("NAS_UI_HOST=100.79.0.1\nNAS_UI_USER=u\n")
        with self.assertRaises(SystemExit):
            self.disp.settings(env)
        with open(env, "a") as f:
            f.write("NAS_UI_KEY=/k\nNAS_UI_KNOWN_HOSTS=/kh\n")
        self.assertEqual(self.disp.settings(env)["NAS_UI_USER"], "u")
        with open(env, "a") as f:
            f.write("NAS_UI_USER=u;rm\n")
        with self.assertRaises(SystemExit):
            self.disp.settings(env)


class Notes(Base):
    """Backlog 5: the owner's note on a day or a position, confirmed like every action, checked by the web app and
    the executor, written to <control>/notes/notes.jsonl (never the journal); control and bidi characters dropped."""

    def note(self, params):
        sid, csrf = self.session()
        p = self.a.prepare("100.64.0.1:5000", sid, {"csrf": csrf, "action": "note", "params": params}, 8501)
        rid = self.a.commit("100.64.0.1:5000", sid, {"csrf": csrf, "nonce": p["nonce"]}, 8501)["id"]
        self.assertEqual(self.ex.run(), [(rid, "ok")])
        return p

    def test_day_and_position_notes(self):
        key = "Poo1111111111111111111111111111111111111111"
        p = self.note({"day": "2026-10-20", "text": "Eerste dag live\u202e <b>x</b>"})
        self.assertIn("dag 2026-10-20", p["text"])
        self.note({"key": key, "variant": "tp4_T600", "t": 470000010, "text": "Instap te laat?"})
        (d,) = self.a.notes(day="2026-10-20")
        self.assertEqual(d["text"], "Eerste dag live <b>x</b>")  # bidi override dropped; HTML stays text (the page escapes)
        (n,) = self.a.notes(key=key, variant="tp4_T600", t=470000010)
        self.assertEqual(n["text"], "Instap te laat?")
        self.assertEqual(self.a.notes(key=key, variant="tp4_T600", t=1), [])

    def test_bad_notes_are_refused(self):
        sid, csrf = self.session()
        for bad in ({"day": "2026-10-20"}, {"day": "x", "text": "a"}, {"day": "2026-10-20", "text": ""}, {"day": "2026-10-20", "text": "a" * 501},
                    {"key": "../x", "variant": "v", "t": 1, "text": "a"}, {"key": "P" * 40, "variant": "v", "t": "1", "text": "a"},
                    {"day": "2026-10-20", "text": "a", "extra": 1}):
            self.refused(400, self.a.prepare, "100.64.0.1:5000", sid, {"csrf": csrf, "action": "note", "params": bad}, 8501)
        # the executor checks again: a request that skipped the web app's check is refused
        rid = self.a.ulid()
        d = os.path.join(self.ex.CONTROL, "requests")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{rid}.json"), "w") as f:
            json.dump({"id": rid, "action": "note", "params": {"day": "2026-10-20", "text": ""}, "node": PHONE, "name": "x", "ts": 1}, f)
        self.assertEqual(self.ex.run(), [(rid, "refused")])


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
