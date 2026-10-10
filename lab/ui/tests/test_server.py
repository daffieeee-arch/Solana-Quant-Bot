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
