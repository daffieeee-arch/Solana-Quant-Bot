"""Tests for lab/ui/server.py against the real analytics.duckdb (light, a few seconds).

Run: "$LAB_PY" -m unittest discover -s lab/ui/tests -v
"""
import json
import os
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


if __name__ == "__main__":
    unittest.main()
