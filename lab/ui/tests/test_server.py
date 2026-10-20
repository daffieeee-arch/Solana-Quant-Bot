"""Tests for lab/ui/server.py against the real analytics.duckdb (light, a few seconds).

Run: "$LAB_PY" -m unittest discover -s lab/ui/tests -v
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server.py")
HOLDOUT_START = 452_304_000


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.static = tempfile.TemporaryDirectory()
        with open(os.path.join(cls.static.name, "index.html"), "w") as f:
            f.write("<!doctype html><title>t</title>")
        cls.port = free_port()
        cls.proc = subprocess.Popen([sys.executable, SERVER, "--port", str(cls.port), "--static", cls.static.name],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.static.cleanup()

    def req(self, path, method="GET"):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def get(self, path):
        status, _, body = self.req(path)
        self.assertEqual(status, 200, body[:200])
        return json.loads(body)

    def test_meta_lists_only_tabs_with_data(self):
        m = self.get("/api/meta")
        self.assertIn("overview", m["tabs"])
        self.assertLess(m["meta"]["slot_end_exclusive"], HOLDOUT_START + 1)

    def test_tokens_search_and_paging(self):
        a = self.get("/api/tokens?limit=5")["tokens"]
        b = self.get("/api/tokens?limit=5&offset=5")["tokens"]
        self.assertEqual(len(a), 5)
        self.assertFalse({t["mint"] for t in a} & {t["mint"] for t in b})
        self.assertEqual(self.get("/api/tokens?q=%25%25%25_")["tokens"], [])

    def test_token_detail_fast_and_dev_only(self):
        mint = self.get("/api/tokens?limit=1&graduated=1")["tokens"][0]["mint"]
        t0 = time.time()
        d = self.get(f"/api/token?mint={mint}")
        self.assertLess(time.time() - t0, 2.0, "token page must load within ~2 s")
        self.assertTrue(d["candles"])
        self.assertTrue(all(r["slot"] < HOLDOUT_START for r in d["tape"]))

    def test_ticker_markers_are_labels_only(self):
        d = self.get("/api/ticker?limit=20")
        self.assertTrue(all(e["slot"] < HOLDOUT_START for e in d["events"]))
        self.assertEqual({m["slot"] for m in d["markers"]}, {452520000, 453800004})

    def test_strategy_curve_narrows_by_run_keys(self):
        s = self.get("/api/strategies")
        if not s["summary"]:
            self.skipTest("no tournament run loaded")
        r = next(x for x in s["summary"] if not x["family"].startswith("N2_") and x["filled"])
        base = f"/api/strategy_curve?family={r['family']}&variant={r['variant']}&d={r['d']}&tau={r['tau']}"
        keys = "".join(f"&{k}={r[k]}" for k in s["keys"] if r[k] is not None)
        own = [c for c in self.get(base + keys)["curves"] if c["family"] == r["family"] and c["size_sol"] == r["size_sol"]]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["points"][-1][0], r["n"], "one group: the curve ends at the summary's trade count")
        # A key the run does not have is ignored, an unknown value of one it has matches nothing.
        self.get(base + "&overlay=none")
        if s["keys"]:
            self.assertEqual(self.get(base + f"&{s['keys'][0]}=no-such-value")["curves"], [])

    def test_strategies_whole_grid_gzipped(self):
        s = self.get("/api/strategies")
        if not s["summary"]:
            self.skipTest("no tournament run loaded")
        import duckdb
        con = duckdb.connect(os.path.join("/home/chupa/Solana-project/data-old-faithful-one/lab/analytics/analytics.duckdb"),
                             read_only=True, config={"memory_limit": "500MB", "threads": 2})
        n = con.execute("SELECT count(*) FROM strategy_summary WHERE scenario = 'base'").fetchone()[0]
        self.assertEqual(len(s["summary"]), n, "no silent row cap")
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/strategies", headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(r, timeout=30) as resp:
            self.assertEqual(resp.headers["Content-Encoding"], "gzip")
            import gzip
            self.assertEqual(len(json.loads(gzip.decompress(resp.read()))["summary"]), n)

    def test_cases_read_from_files_dev_only(self):
        idx = self.get("/api/cases")
        if not idx["days"] and not idx["windows"]:
            self.skipTest("no cases-v1 files")
        self.assertIn("cases", self.get("/api/meta")["tabs"])
        for key, param in ((idx["days"][0], "day"), (idx["windows"][0], "window")) if idx["windows"] else ((idx["days"][0], "day"),):
            d = self.get(f"/api/cases?{param}={key}")
            self.assertTrue(d["rows"] and d["lists"])
            for r in d["rows"]:
                self.assertTrue(all(v < HOLDOUT_START for k, v in r.items() if "slot" in k and isinstance(v, int)))
                self.assertIn(r["list"], d["lists"])
            self.assertIn("self_funded", d["flags"])
        c = self.get(f"/api/candles?mint={d['rows'][0]['mint']}")
        self.assertTrue(c["candles"])
        self.assertEqual(self.req("/api/cases?day=../../etc/passwd")[0], 404)
        self.assertEqual(self.req("/api/cases?window=7d-1")[0], 404)

    def test_strategy_overview_candidates_and_families(self):
        o = self.get("/api/strategy_overview")
        if not o["run"]:
            self.skipTest("no tournament run loaded")
        fams = o["families"]
        self.assertTrue(fams)
        self.assertTrue(all(f["n"] >= 200 and not f["family"].startswith("N2_") for f in fams))
        self.assertEqual([f["mean_ret"] for f in fams], sorted((f["mean_ret"] for f in fams), reverse=True))
        self.assertEqual(o["regime"], self.get(f"/api/strategy_overview?regime={o['regime']}")["regime"])
        self.assertEqual(self.get("/api/strategy_overview?regime=x%27%29%3B%20DROP")["regime"], o["regime"])  # fixed set only
        note = o["run"]["note"]
        if note:
            self.assertTrue(all(isinstance(c["ev"], (float, type(None))) for c in note["candidates"]))

    def test_static_types_come_from_a_fixed_table(self):
        sys.path.insert(0, os.path.dirname(SERVER))
        import server
        self.assertEqual(server.STATIC_TYPES[".webmanifest"], "application/manifest+json; charset=utf-8")
        # An unknown or crafted extension never reaches the header: it falls back to octet-stream.
        status, h, _ = self.req("/x.%0d%0aSet-Cookie:a=b")
        self.assertNotIn("Set-Cookie", h)
        self.assertTrue(all("\n" not in v and "\r" not in v for v in h.values()))

    def test_paper_tab_reads_the_journal(self):
        p = self.get("/api/paper")
        if not p["journal"]:
            self.skipTest("no paper journal")
        self.assertIn("paper", self.get("/api/meta")["tabs"])
        self.assertTrue(all(not (HOLDOUT_START <= r["entry_slot"] < 454_464_000) for r in p["journal"]["positions"]))
        self.assertIn(p["journal"]["stop_rule"]["verdict"], ("GO", "ADJUST", "STOP", "too few trades", "too few days"))

    def test_paper_chart_markers_and_candles(self):
        p = self.get("/api/paper")
        if not p["journal"] or not p["journal"]["positions"]:
            self.skipTest("no paper journal")
        pos = p["journal"]["positions"]
        c = self.get(f"/api/paper_chart?key={pos[0]['key']}")
        self.assertTrue(c["trades"])
        self.assertTrue(all(t["slot"] < HOLDOUT_START and t["time"] for t in c["trades"]))
        self.assertTrue(all(x["time"] for x in c["candles"]))
        rev = next((x for x in pos if x.get("orientation") == "reversed"), None)
        if rev:  # no pump.fun history: price candles from the store's pool trades
            r = self.get(f"/api/paper_chart?key={rev['key']}")
            self.assertEqual(r["unit"], "price")
        if c.get("mint"):
            t = self.get(f"/api/paper_trades?mint={c['mint']}")["trades"]
            self.assertGreaterEqual(len(t), len([x for x in c["trades"]]))
        self.assertEqual(self.req("/api/paper_chart?key=../../x")[0], 404)
        self.assertEqual(self.req("/api/paper_trades?mint=x%27%20OR%201%3D1")[0], 404)

    def test_wallet_triton_and_stream(self):
        tabs = self.get("/api/meta")["tabs"]
        if "wallet" in tabs:
            w = self.get("/api/wallet")
            self.assertIn("paper", w)
        if "triton" in tabs:
            self.assertIn("streams", self.get("/api/triton")["triton"])
        # SSE: the first event arrives at once, as text/event-stream.
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/stream", timeout=10) as resp:
            self.assertTrue(resp.headers["Content-Type"].startswith("text/event-stream"))
            lines = [resp.readline() for _ in range(4)]
        self.assertIn(b"event: change\n", lines)

    def test_bad_input(self):
        self.assertEqual(self.req("/api/token?mint=../../etc/passwd")[0], 404)
        self.assertEqual(self.req("/api/nope")[0], 404)
        self.assertEqual(self.req("/api/meta", method="POST")[0], 405)

    def test_static_cannot_escape_root(self):
        status, _, body = self.req("/../../../../etc/passwd")
        self.assertNotIn(b"root:", body)
        status, _, body = self.req("/%2e%2e/%2e%2e/etc/hostname")
        self.assertIn(b"<title>t</title>", body)

    def test_security_headers(self):
        _, h, _ = self.req("/api/meta")
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")


class CasesGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.dirname(SERVER))
        import server
        cls.server = server

    def test_dev_only(self):
        self.assertTrue(self.server.dev_only({"peak_slot": HOLDOUT_START - 1, "trades": HOLDOUT_START + 5}))
        self.assertFalse(self.server.dev_only({"last_slot": HOLDOUT_START}))
        self.assertFalse(self.server.dev_only({"t15_t_dec": HOLDOUT_START + 1}))

    def test_holdout_result_only_from_backtests(self):
        self.assertIsNone(self.server.holdout_result("../../etc/passwd", ["K1"]))
        self.assertIsNone(self.server.holdout_result("analytics/analytics.duckdb", ["K1"]))
        n = self.server.run_note("families-v2-1033-1046")
        r = n["holdout_result"]
        if r is None:
            self.skipTest("no hold-out evaluation")
        self.assertEqual(set(r["candidates"]), {c["id"] for c in n["candidates"]})
        for c in r["candidates"].values():
            self.assertLessEqual(c["lo"], c["mean"])
            self.assertLessEqual(c["mean"], c["hi"])
        self.assertEqual(n["phase"], "Hold-out: niet geslaagd" if r["pick"] is None else f"Hold-out: geslaagd ({r['pick']})")

    def test_run_note_is_plain_text_and_stays_in_notes_dir(self):
        self.assertIsNone(self.server.run_note("no-such-run"))
        self.assertIsNone(self.server.run_note("../../server"))
        n = self.server.run_note("families-v2-1033-1046")
        self.assertIn("hold-out", n["holdout"])
        self.assertTrue(all(isinstance(p, str) for p in n["points"]))

    def test_cases_connection_reads_only_the_cases_dir(self):
        con = self.server.cases_con()
        with self.assertRaises(Exception):
            con.execute("SELECT * FROM read_csv('/etc/passwd')").fetchall()
        with self.assertRaises(Exception):
            con.execute("SET enable_external_access=true")


class Binding(unittest.TestCase):
    def refused(self, *args):
        r = subprocess.run([sys.executable, SERVER, *args], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(r.returncode, 0)

    def test_never_all_interfaces(self):
        self.refused("--host", "0.0.0.0")

    def test_forbidden_ports(self):
        for p in ("3000", "8443", "8787", "8771"):
            self.refused("--port", p)


class Unit(unittest.TestCase):
    """lab/ops/systemd/lab-ui-web.service: the pinned deploy worktree, the Tailscale IP, bounded resources."""

    def setUp(self):
        with open(os.path.join(os.path.dirname(SERVER), "..", "ops", "systemd", "lab-ui-web.service")) as f:
            self.text = f.read()
        self.exec = re.search(r"^ExecStart=(.*)$", self.text, re.M).group(1)

    def test_serves_pinned_deploy_worktree(self):
        self.assertRegex(self.text, r"(?m)^WorkingDirectory=%h/Solana-project/worktrees/lab-ui-deploy$")
        self.assertNotRegex(self.text, r"worktrees/lab-ui(?!-deploy)")
        self.assertIn(" -I lab/ui/server.py ", self.exec)

    def test_tailscale_bind_and_port(self):
        self.assertIn("--host 100.112.193.63", self.exec)
        self.assertNotIn("0.0.0.0", self.text)
        port = int(re.search(r"--port (\d+)", self.exec).group(1))
        self.assertNotIn(port, (3000, 8443, 8787, 8771, 8502))

    def test_limits(self):
        self.assertRegex(self.text, r"(?m)^MemoryMax=1G$")
        self.assertRegex(self.text, r"(?m)^CPUQuota=\d+%$")
        self.assertRegex(self.text, r"(?m)^StartLimitBurst=5$")
        self.assertRegex(self.text, r"(?m)^NoNewPrivileges=yes$")



class SystemUnit(unittest.TestCase):
    """lab/ops/systemd/lab-ui-web.system.service (variant A): the same app and bind as the user unit, as the system
    user lab-ui with the home hidden except read-only binds; no write path, no user bus, no secrets; and the ACL
    script only ever grants read."""
    OPS = os.path.join(os.path.dirname(SERVER), "..", "ops")

    def setUp(self):
        with open(os.path.join(self.OPS, "systemd", "lab-ui-web.system.service")) as f:
            self.text = f.read()
        with open(os.path.join(self.OPS, "systemd", "lab-ui-web.service")) as f:
            self.user = f.read()

    def test_same_command_as_the_user_unit(self):
        user_exec = re.search(r"^ExecStart=(.*)$", self.user, re.M).group(1).replace("%h", "/home/chupa")
        self.assertEqual(re.search(r"^ExecStart=(.*)$", self.text, re.M).group(1), user_exec)
        self.assertRegex(self.text, r"(?m)^WorkingDirectory=/home/chupa/Solana-project/worktrees/lab-ui-deploy$")

    def test_own_user_and_sandbox(self):
        for line in ("User=lab-ui", "ProtectHome=tmpfs", "ProtectSystem=strict", "InaccessiblePaths=/run/user",
                     "NoNewPrivileges=yes", "CapabilityBoundingSet=", "PrivateTmp=yes", "IPAddressDeny=any", "MemoryMax=1G"):
            self.assertRegex(self.text, rf"(?m)^{re.escape(line)}$")
        self.assertNotRegex(self.text, r"(?m)^ReadWritePaths=")  # read-only until B0 adds the requests directory
        binds = re.findall(r"(?m)^BindReadOnlyPaths=-?(\S+)$", self.text)
        self.assertTrue(binds and all(b.startswith("/home/chupa/") for b in binds))
        self.assertFalse(any(".config" in b or b.rstrip("/") in ("/home/chupa", "/home/chupa/Solana-project") for b in binds))
        self.assertNotRegex(self.text, r"(?m)^BindPaths=")

    def test_grant_script_reads_only(self):
        with open(os.path.join(self.OPS, "ui-grant-read.sh")) as f:
            sh = f.read()
        acls = re.findall(r'setfacl[^\n]*"(d?:?u:\$U:[^"]*)"', sh)
        self.assertTrue(acls)
        self.assertTrue(all(a.split(":")[-1] in ("x", "rX") for a in acls), acls)
        code = [ln for ln in sh.split("# Never:")[0].splitlines() if not ln.lstrip().startswith("#")]
        self.assertFalse([ln for ln in code if ".config" in ln])  # granted nowhere; only the closing check names it


if __name__ == "__main__":
    unittest.main()
