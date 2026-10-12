"""Tests for lab/ui/paper.py: the Paper tab's numbers equal the engine's daily report, the journal stays
read-only, and hold-out slots never show. Light (a few seconds).

Run: "$LAB_PY" lab/ui/tests/test_paper.py
"""
import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import paper  # noqa: E402

PARITY = "/home/chupa/Solana-project/data-old-faithful-one/lab/journal/parity-450144000-450360000/journal"
DAY = 86_400


def make_journal(d, exits, positions=(), beats=()):
    """A journal with the engine's own schema (lab/paper/journal.py TABLES)."""
    os.makedirs(d, exist_ok=True)
    db = sqlite3.connect(os.path.join(d, "journal.sqlite"))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE signals (family TEXT, variant TEXT, key TEXT, t INTEGER, day TEXT, venue TEXT, orientation TEXT, "
               "depth INTEGER, trigger_trader TEXT, status TEXT, reason TEXT, PRIMARY KEY (family, variant, key, t))")
    db.execute("CREATE TABLE exits (family TEXT, variant TEXT, key TEXT, t INTEGER, day TEXT, entry_slot INTEGER, "
               "exit_decision_slot INTEGER, exit_slot INTEGER, exit_reason TEXT, tokens INTEGER, cost INTEGER, proceeds INTEGER, "
               "pnl INTEGER, ret REAL, exit_attempts INTEGER, cf_graduation INTEGER, seed_pool INTEGER, vault_capped INTEGER, "
               "reverted_direct INTEGER, reverted_router INTEGER, sells_dropped INTEGER, sells_scaled INTEGER, "
               "PRIMARY KEY (family, variant, key, t))")
    db.execute("CREATE TABLE heartbeat (minute INTEGER, day TEXT, last_slot INTEGER, last_event TEXT, events INTEGER, "
               "open_positions INTEGER, gap_slots INTEGER, note TEXT, PRIMARY KEY (minute, note))")
    db.execute("CREATE TABLE positions (family TEXT, variant TEXT, key TEXT, t INTEGER, entry_slot INTEGER, cost INTEGER, "
               "tokens INTEGER, status TEXT, PRIMARY KEY (family, variant, key, t))")
    for fam, key, t, day, pnl, tokens in exits:
        db.execute("INSERT INTO exits (family, variant, key, t, day, entry_slot, exit_slot, exit_reason, tokens, cost, pnl) "
                   "VALUES (?, 'tp4_T600', ?, ?, ?, ?, ?, 'take_profit', ?, 500000000, ?)",
                   [fam, key, t, day, t + 1, t + 5, tokens, pnl])
        db.execute("INSERT INTO signals VALUES (?, 'tp4_T600', ?, ?, ?, 'pool', 'reversed', 0, 'w', 'entered', NULL)",
                   [fam, key, t, day])
    for fam, key, t in positions:
        db.execute("INSERT INTO positions VALUES (?, 'tp4_T600', ?, ?, ?, 500000000, 1000, 'open')", [fam, key, t, t + 1])
    for minute, day, note, gap in beats:
        db.execute("INSERT INTO heartbeat VALUES (?, ?, 460000000, '', 10, 1, ?, ?)", [minute, day, gap, note])
    db.commit()
    db.close()
    return os.path.join(d, "journal.sqlite")


class ParityJournal(unittest.TestCase):
    def test_numbers_equal_the_engine_daily_report(self):
        if not os.path.exists(os.path.join(PARITY, "journal.sqlite")):
            self.skipTest("no parity journal")
        v = paper.journal_view(os.path.join(PARITY, "journal.sqlite"), now=1_791_800_000)
        # lab/journal/parity-.../journal/2026-09-25/report.md
        self.assertEqual(v["day"], "2026-09-25")
        self.assertEqual((v["totals"]["trades"], v["totals"]["entry_failed"]), (2384, 47))
        self.assertAlmostEqual(v["totals"]["net_sol_per_trade"], -0.0114, places=4)
        self.assertEqual(round(v["totals"]["win_rate"] * 100), 57)
        s = v["stop_rule"]
        self.assertEqual((s["trades"], s["days"], s["verdict"]), (2956, 2, "STOP"))
        self.assertAlmostEqual(s["mean_sol_per_trade"], -0.0121, places=4)
        self.assertIsNone(s["ci_lo"])  # CI from 5 days
        c = v["capital"]
        self.assertEqual((round(c["final_sol"], 3), round(c["max_drawdown"] * 100, 1), c["taken"], c["skipped"]),
                         (0.466, 95.2, 736, 2220))
        self.assertFalse(v["status"]["running"])
        self.assertTrue(v["test_run"])
        # Coin names instead of addresses: curve keys are mints, pool keys go through the store's pools.
        named = [p for p in v["positions"] if p.get("symbol")]
        self.assertGreater(len(named), len(v["positions"]) // 2)
        self.assertTrue(any(p["venue"] == "pool" and p.get("mint") and p["mint"] != p["key"] for p in named))


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_today_open_positions_n2_and_holdout(self):
        now = 1_791_800_000
        today = paper.utc_day(now)
        ex = [("F7", f"Pool{i}", 460_000_000 + i * 10, today, 20_000_000 if i % 2 else -10_000_000, 5) for i in range(6)]
        ex += [("N2_F7", f"Rnd{i}", 460_000_000 + i * 10, today, -5_000_000, 5) for i in range(4)]
        ex += [("F7", "Failed", 460_000_100, today, -100_000, 0)]
        ex += [("F7", "Hidden", 452_400_000, today, 9_000_000_000, 5)]  # a hold-out slot: never shown
        p = make_journal(self.tmp, ex, positions=[("F7", "OpenPool", 460_001_000), ("N2_F7", "OpenRnd", 460_001_000)],
                         beats=[(now // 60 - 1, today, "", 0), (now // 60 - 30, today, "feed gap 1..40", 39)])
        v = paper.journal_view(p, now)
        self.assertTrue(v["status"]["running"])
        self.assertEqual((v["status"]["gaps_today"], v["status"]["largest_gap_slots"]), (1, 39))
        t = v["totals"]
        self.assertEqual((t["trades"], t["entry_failed"], t["open"]), (6, 1, 1))
        self.assertAlmostEqual(t["net_sol"], (3 * 0.02 - 3 * 0.01) - 0.0001)
        self.assertEqual(v["holdout_rows_dropped"], 2)  # the exit and its signal
        self.assertNotIn("Hidden", {r["key"] for r in v["positions"]})
        self.assertEqual(v["positions"][0]["status"], "open")
        self.assertEqual({r["family"] for r in v["positions"]}, {"F7"})  # N2 shadows are not positions of ours
        n2 = v["n2"][0]
        self.assertTrue(n2["has_shadow"])
        self.assertAlmostEqual(n2["diff_today"], (0.03 - 0.0001) / 7 - (-0.005))
        self.assertEqual(v["stop_rule"]["verdict"], "too few trades")

    def test_stop_rule_ci_from_five_days_and_verdict_from_200(self):
        now = 1_791_800_000
        ex = []
        for d in range(6):
            day = paper.utc_day(now - (5 - d) * DAY)
            ex += [("F7", f"P{d}_{i}", 460_000_000 + d * 1000 + i, day, 3_000_000 + (i % 7) * 1_000_000, 5) for i in range(40)]
        v = paper.journal_view(make_journal(self.tmp, ex), now)
        s = v["stop_rule"]
        self.assertEqual((s["trades"], s["days"]), (240, 6))
        self.assertIsNotNone(s["ci_lo"])
        self.assertLessEqual(s["ci_lo"], s["mean_sol_per_trade"])
        self.assertEqual(s["verdict"], "GO")

    def test_journal_is_opened_read_only(self):
        p = make_journal(self.tmp, [("F7", "A", 460_000_000, "2026-10-12", 1, 5)])
        j = paper.Journal(p)
        with self.assertRaises(sqlite3.Error):
            j.db.execute("DELETE FROM exits")
        j.close()


class Recorder(unittest.TestCase):
    def test_sample_is_marked_and_summed_per_stream(self):
        os.environ["LAB_RECORDER_ROOT"] = "/nonexistent"
        try:
            r = paper.recorder_view(1_791_800_000)
        finally:
            del os.environ["LAB_RECORDER_ROOT"]
        self.assertTrue(r["sample"])
        self.assertAlmostEqual(sum(s["gb"] for s in r["streams"]), r["spent_gb"], places=6)
        self.assertEqual(r["level"], 1)
        self.assertEqual(r["reconnects"], 1)

    def test_real_root_wins_over_the_sample(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(paper.SAMPLE_RECORDER, d, dirs_exist_ok=True)
            os.environ["LAB_RECORDER_ROOT"] = d
            try:
                importlib.reload(paper)
                self.assertFalse(paper.recorder_view(1_791_800_000)["sample"])
            finally:
                del os.environ["LAB_RECORDER_ROOT"]
                importlib.reload(paper)


class WalletAndTriton(unittest.TestCase):
    def test_wallet_paper_part_is_the_capital_simulation_and_onchain_is_marked_sample(self):
        if not paper.journal_path():
            self.skipTest("no paper journal")
        w = paper.wallet_view(1_791_800_000)
        p = w["paper"]
        v = paper.journal_view(paper.journal_path(), 1_791_800_000)
        self.assertAlmostEqual(p["balance_sol"], v["capital"]["final_sol"])
        self.assertTrue(all(t["status"] in ("filled", "entry_failed", "skipped") for t in p["txs"]))
        self.assertTrue(w["onchain"]["sample"])  # no live wallet.json yet

    def test_wallet_state_file_is_validated(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "wallet.json")
            with open(f, "w") as fh:
                json.dump({"address": "not base58 !", "balance_lamports": 2_000_000_000, "slot": 460_000_000,
                           "txs": [{"slot": 452_400_000, "kind": "buy"}, {"slot": 460_000_001, "kind": "sell", "status": "landed",
                                                                         "amount_lamports": 1, "symbol": "<b>x</b>" * 10}]}, fh)
            os.environ["LAB_WALLET_STATE"] = f
            try:
                w = paper._wallet_state(None)
            finally:
                del os.environ["LAB_WALLET_STATE"]
        self.assertFalse(w["sample"])
        self.assertIsNone(w["address"])
        self.assertEqual(w["balance_sol"], 2.0)
        self.assertEqual(len(w["txs"]), 1)  # the hold-out slot is dropped
        self.assertLessEqual(len(w["txs"][0]["symbol"]), 20)

    def test_triton_from_the_sample(self):
        os.environ["LAB_RECORDER_ROOT"] = "/nonexistent"
        try:
            t = paper.triton_view(1_791_800_000)
        finally:
            del os.environ["LAB_RECORDER_ROOT"]
        self.assertTrue(t["sample"])
        self.assertAlmostEqual(t["month_gb"], t["recorder"]["spent_gb"], places=6)
        self.assertGreater(t["forecast_usd"], t["month_usd"])
        self.assertTrue(100 < t["latency"]["p50"] < t["latency"]["p90"] < 1000)
        self.assertEqual(sum(t["latency"]["hist"]), t["latency"]["n"])
        self.assertTrue(any(s["dropped"] for s in t["streams"]))

    def test_risk_limits_parse(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("size_sol: 0.5   # per trade\ndaily_loss_limit_sol: 1.0\nkill_switch: false\nfamilies: [F7]\n")
        os.environ["LAB_PAPER_RISK"] = fh.name
        try:
            r = paper.risk_limits()
        finally:
            del os.environ["LAB_PAPER_RISK"]
            os.unlink(fh.name)
        self.assertEqual((r["size_sol"], r["daily_loss_limit_sol"], r["kill_switch"]), (0.5, 1.0, False))


class Capital(unittest.TestCase):
    def test_full_slots_skip_and_failed_entries_pay(self):
        trades = [(i, 100, 1_000_000_000, 100_000_000, False) for i in range(6)] + [(7, None, 0, -1_000_000, True)]
        final, mdd, taken, skipped, curve = paper.capital_sim(trades)
        self.assertEqual((taken, skipped), (5, 2))  # the 6th and the failed one find 5 positions open
        self.assertAlmostEqual(final / 1e9, 10.5)
        self.assertEqual(len(curve), 5)


if __name__ == "__main__":
    unittest.main()
