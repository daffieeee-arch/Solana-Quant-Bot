"""Tests for lab/notebooks: every notebook passes `marimo check` and runs headless on fixture data
(and on missing data); the systemd unit serves read-only on the Tailscale IP with token auth; and
labdata.connect stays within the DuckDB limits with analytics attached read-only.

Run: "$LAB_PY" lab/notebooks/tests/test_notebooks.py   (lab-py: needs marimo and duckdb)
"""
import glob
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
NB = os.path.dirname(HERE)
UNIT = os.path.join(NB, "..", "ops", "systemd", "lab-notebooks.service")
sys.path.insert(0, NB)



def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


NOTEBOOKS = sorted(p for p in glob.glob(os.path.join(NB, "*.py")) if "marimo.App(" in read(p))


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def fixtures(root):
    """Minimal run files in the shapes the notebooks read."""
    bt = os.path.join(root, "backtests")
    main = {"point": "d2_q0.5", "mean": 0.0002, "lo": -0.009, "hi": 0.006, "filled": 3789}
    write(os.path.join(bt, "holdout-1047-1051", "holdout_eval.json"), {
        "candidates": {"K3": {"spec": {"id": "K3", "role": "main", "family": "F7", "segment": "pool_reversed",
                                       "variant": "tp4_T600", "farm": "all", "size_sol": 0.5},
                              "groups": {"B+C": {"main": main}}}},
        "decision": {"group": "B+C", "pick": None, "highest_mean_if_none": "K3"}})
    group = {"period": "days 10-18", "overlay": "none", "variant": "k20_trail20", "size": 0.5, "d": 1,
             "trades": 87, "mean": 0.21, "lo": -0.052, "hi": 0.53, "win": 0.32, "vs_n2": 0.348}
    write(os.path.join(bt, "m1x-dev-1033-1046-453d44e", "m1x.json"), {
        "binding": "days 10-18",
        "families": {"M1P": {"groups": [group, dict(group, period="days 1-9", mean=-0.15)],
                             "verdict": {"useful": False, "rule": "test rule"}}}})
    write(os.path.join(bt, "paired-exactin-2026-10-12", "compare.json"), {"fwd1053-1054": [{
        "family": "F7", "segment": "pool_reversed", "size_sol": 0.5, "n": 5223, "identical": 5223,
        "exit_reason_changed": 0, "ret_new": -0.018, "ret_old": -0.018, "d_mean": 0.0}]})


def run_notebook(path, data_root):
    """Run the notebook headless (no server) and return its rendered outputs as text."""
    os.environ["LAB_DATA_ROOT"] = data_root
    name = os.path.splitext(os.path.basename(path))[0]
    sys.modules.pop(name, None)
    module = importlib.import_module(name)
    outputs, _ = module.app.run()
    return json.dumps([repr(o) if not hasattr(o, "text") else o.text for o in outputs], ensure_ascii=False)


class Notebooks(unittest.TestCase):
    def test_found(self):
        self.assertIn(os.path.join(NB, "uitslagen.py"), NOTEBOOKS)

    def test_marimo_check(self):
        marimo = os.path.join(os.path.dirname(sys.executable), "marimo")
        for path in NOTEBOOKS:
            r = subprocess.run([marimo, "check", path], capture_output=True, text=True,
                               env=dict(os.environ, MARIMO_SKIP_UPDATE_CHECK="1"))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_runs_on_fixtures(self):
        with tempfile.TemporaryDirectory() as root:
            fixtures(root)
            text = run_notebook(os.path.join(NB, "uitslagen.py"), root)
        for expected in ("Geen kandidaat geslaagd", "K3", "niet nuttig", "k20_trail20", "+21,0 %", "fwd1053-1054"):
            self.assertIn(expected, text)
        self.assertNotIn("niet gevonden", text)

    def test_missing_files_are_shown_not_fatal(self):
        with tempfile.TemporaryDirectory() as root:
            text = run_notebook(os.path.join(NB, "uitslagen.py"), root)
        self.assertEqual(text.count("Bestand niet gevonden"), 3)

    def test_notebooks_do_not_write(self):
        for path in NOTEBOOKS:
            src = read(path)
            self.assertNotRegex(src, r"open\([^)]*['\"][wax]\+?['\"]|\.to_parquet|\.to_csv|COPY .* TO|subprocess|os\.system")


def blindspot_fixtures(root):
    """One audit file and the F7-reversed wash summary, in the shapes blinde_vlekken.py reads."""
    out = os.path.join(root, "research", "blindspots", "out")
    write(os.path.join(out, "blindspots-smoke-1054.json"), {
        "slots": {"lo": 100, "hi": 1099, "n": 995},
        "actor": [{"venue": "curve", "n": 13021, "user_is_fee_payer": 0.782, "sol_user_not_payer": 267.9}],
        "round_trip_volume": [{"venue": "pool_reversed", "legs": 17700, "sol": 28877.0, "sol_round_trip": 20739.8, "share": 0.718}],
        "token_delta": [{"venue": "pool_reversed", "side": "buy", "variant": "sell", "n": 12554, "equal": 1.0, "differs": 0.0, "no_delta": 0.0}],
        "route": [{"venue": "curve", "outer_program": "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9", "n": 3522, "share": 0.27}],
        "failed": {"n": 117689, "fees_sol": 4.18, "priority_sol": 3.59, "payers": 1892},
        "landed": {"n": 65669, "fees_sol": 8.16, "tips_sol": 1.24, "payers": 21547},
        "failed_by_payer": [{"failure_share": ">= 90 %", "payers": 97, "landed": 890, "failed": 71941, "failed_fees_sol": 0.84}],
        "owner_to_owner": {"n": 1131, "mints": 207, "senders": 456, "receivers": 550, "receivers_who_trade": 163},
        "holders_in_trade_tx": {"n": 898, "not_fee_payer": 516, "received": 342, "sent": 174},
        "fan_out_examples": [{"slot": 455328800, "tx_index": 727, "mint": "Tqj8yFmagrg7oorpQkVGYR52r96RFTamvWfth9bpump",
                              "source_owner": "GzDHuxPySSghTH6ySStMLgFwapTc8mtLydsmzVDAfGUE", "receivers": 10, "tokens": "1"}]})
    write(os.path.join(out, "f7rev-wash.json"), {
        "summary": {"triggers": 40737, "round_trip_share": 0.27, "buys_back_50": 0.466},
        "returns": [{"family": "F7", "group": "rt_tx", "d": 1, "period": "dag 10-18", "n": 6797, "mean": -0.0035, "lo": -0.0054, "hi": -0.0018},
                    {"family": "F7", "group": "wash_1h", "d": 1, "period": "dag 10-18", "n": 1, "mean": 0.0, "lo": 0.0, "hi": 0.0}]})


class BlindeVlekken(unittest.TestCase):
    NB = os.path.join(NB, "blinde_vlekken.py")

    def test_runs_on_fixtures(self):
        with tempfile.TemporaryDirectory() as root:
            blindspot_fixtures(root)
            text = run_notebook(self.NB, root)
        for expected in ("Blinde vlekken", "78,2 %", "71,8 %", "smoke-1054", "117 689", "1 131", "27,0 %", "FLASHX…txB9",
                         "trigger is wash-leg", "-0,35 %"):
            self.assertIn(expected, text)
        self.assertNotIn("Nog geen audit", text)

    def test_missing_files_are_shown_not_fatal(self):
        with tempfile.TemporaryDirectory() as root:
            text = run_notebook(self.NB, root)
        self.assertIn("Nog geen audit", text)
        self.assertIn("is er nog niet", text)


class Unit(unittest.TestCase):
    def setUp(self):
        self.text = read(UNIT)
        self.exec = re.search(r"^ExecStart=(.*)$", self.text, re.M).group(1)

    def test_app_mode_only(self):
        self.assertRegex(self.exec, r"/marimo run lab/notebooks ")
        for flag in (" edit", "--include-code", "--watch", "--no-token", "--sandbox ", "--token-password "):
            self.assertNotIn(flag, self.exec)

    def test_tailscale_bind_and_token(self):
        self.assertIn("--host 100.112.193.63", self.exec)
        self.assertNotIn("0.0.0.0", self.text)
        port = int(re.search(r"--port (\d+)", self.exec).group(1))
        self.assertNotIn(port, (3000, 8443, 8787, 8771, 8501))
        self.assertIn("--token --token-password-file %h/.config/lab-notebooks/token", self.exec)
        self.assertRegex(self.text, r"(?m)^StandardOutput=null$")  # marimo prints the URL with the token

    def test_serves_pinned_deploy_worktree(self):
        self.assertRegex(self.text, r"(?m)^WorkingDirectory=%h/Solana-project/worktrees/lab-notebooks$")
        self.assertNotIn("worktrees/lab-ui", self.text)

    def test_start_limit(self):
        self.assertRegex(self.text, r"(?m)^StartLimitIntervalSec=1h$")
        self.assertRegex(self.text, r"(?m)^StartLimitBurst=5$")

    def test_resource_limits(self):
        mem = re.search(r"(?m)^MemoryMax=(\d+)([MG])$", self.text)
        self.assertIsNotNone(mem)
        self.assertLessEqual(int(mem.group(1)) * (1 if mem.group(2) == "M" else 1024), 2048)
        self.assertRegex(self.text, r"(?m)^CPUQuota=\d+%$")


class LabData(unittest.TestCase):
    def test_limits_and_read_only(self):
        import duckdb
        import labdata
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "analytics.duckdb")
            w = duckdb.connect(db)
            w.execute("CREATE TABLE t AS SELECT 1 AS x")
            w.close()
            old = labdata.ANALYTICS
            labdata.ANALYTICS = db
            try:
                con = labdata.connect()
                self.assertEqual(con.execute("SELECT x FROM a.t").fetchone(), (1,))
                with self.assertRaises(duckdb.Error):
                    con.execute("INSERT INTO a.t VALUES (2)")
                limit = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
                self.assertTrue(limit.endswith(("MiB", "MB", "GiB", "GB")), limit)
                value = float(re.match(r"[\d.]+", limit).group(0)) * (1 if "M" in limit else 1024)
                self.assertLessEqual(value, 1024)
                self.assertEqual(con.execute("SELECT current_setting('threads')").fetchone()[0], 2)
                con.close()
            finally:
                labdata.ANALYTICS = old


if __name__ == "__main__":
    unittest.main(verbosity=1)
