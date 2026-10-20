"""Tests for lab/ui/paper.py: the Paper tab's numbers equal the engine's daily report, the journal stays
read-only, and hold-out slots never show. Light (a few seconds).

Run: "$LAB_PY" lab/ui/tests/test_paper.py
"""
import importlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

# lab/ui/paper.py by its path, as the server loads it: the name 'paper' is lab/paper's package (OneProcess).
_spec = importlib.util.spec_from_file_location("lab_ui_paper", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "paper.py"))
if "lab_ui_paper" not in sys.modules:
    sys.modules["lab_ui_paper"] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules["lab_ui_paper"])
paper = sys.modules["lab_ui_paper"]

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
        # The report.md there predates #267 (it counted failed entries as trades: 2956, -0.0121); since #267 the
        # report counts the 2890 filled trades, the 66 failed entries' costs included. Two trading days: no day
        # interval, so no verdict (owner, 2026-10-16: from 200 trades and 5 days).
        self.assertEqual((s["trades"], s["entry_failed"], s["days"], s["verdict"]), (2890, 66, 2, "too few days"))
        # A replay on dev data: a test, not paper trading, and the stop rule decides nothing on it.
        self.assertEqual(v["source"], {"feed": "replay", "paper_trading": False, "journal": "parity-450144000-450360000"})
        self.assertFalse(s["assessment"]["applies"])
        self.assertEqual(s["assessment"]["uncertainty"][0]["key"], "not_paper_trading")
        self.assertAlmostEqual(s["mean_sol_per_trade"], -0.0124, places=4)
        self.assertIsNone(s["clock"])  # no clock start: a count of every paper day, no decision
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


class JournalChoice(unittest.TestCase):
    """The page shows the paper run's journal (paper-k3) once it exists, also when a measuring-hour chain test or
    a parity run was written later; before it, the journal written last, counting its WAL file."""

    def test_paper_run_first_then_the_latest_write(self):
        with tempfile.TemporaryDirectory() as d:
            def journal(name, mtime, wal=None):
                db = make_journal(os.path.join(d, "journal", name, "journal"), [])
                os.utime(db, (mtime, mtime))
                for f in (db + "-wal",):
                    if os.path.exists(f):
                        os.remove(f)
                if wal:
                    open(db + "-wal", "wb").close()
                    os.utime(db + "-wal", (wal, wal))
                return db
            old, data = paper.DATA, d
            paper.DATA = data
            try:
                parity = journal("parity-450144000-450360000", 1_000)
                measure = journal("measure-20261020", 500, wal=2_000)  # its writes are still in the WAL
                self.assertEqual(paper.journal_path(), measure)
                os.utime(parity, (3_000, 3_000))
                self.assertEqual(paper.journal_path(), parity)
                run = journal(paper.PAPER_RUN, 100)
                self.assertEqual(paper.journal_path(), run)  # the paper run wins however old its file looks
            finally:
                paper.DATA = old


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


def lab_paper():
    """lab/paper's journal and report, loaded as the package 'labpaper' (here 'paper' is the web app's module).
    The report needs numpy and pyarrow (lab-py) and lab/ on the path for backtest."""
    lab = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    if lab not in sys.path:
        sys.path.append(lab)
    if "labpaper" not in sys.modules:
        spec = importlib.util.spec_from_file_location("labpaper", os.path.join(lab, "paper", "__init__.py"),
                                                      submodule_search_locations=[os.path.join(lab, "paper")])
        pkg = importlib.util.module_from_spec(spec)
        sys.modules["labpaper"] = pkg
        spec.loader.exec_module(pkg)
    return importlib.import_module("labpaper.journal"), importlib.import_module("labpaper.report")


class SameAsTheDailyReport(unittest.TestCase):
    """The Paper tab's stop-rule counter and capital simulation equal lab/paper/report.py's on a journal with variant
    groups (K3 primary, H an arm), a failed entry and a clock start (coordinator, 2026-10-16)."""
    SOL = 1_000_000_000
    GROUPS = {"K3": {"primary": True, "variants": ["tp4_T600"]}, "H": {"primary": False, "variants": ["flow300_H6"]}}
    CLOCK = {"date": "2026-10-26", "reason": "the owner's target", "set_at": "2026-10-22T09:30:00+00:00"}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def journal(self, clock):
        jmod, _ = lab_paper()
        j = jmod.Journal(os.path.join(self.tmp, "run", "journal"))
        j.state("groups", json.dumps(self.GROUPS))
        if clock:
            j.state("clock_start", json.dumps(self.CLOCK))

        def trade(family, variant, t, entry_day, exit_day, pnl, tokens=5):
            j.put("orders", family=family, variant=variant, key=f"P{t}", t=t, day=entry_day, entry_slot=t + 3,
                  size=self.SOL // 2, status="filled" if tokens else "failed")
            j.put("exits", family=family, variant=variant, key=f"P{t}", t=t, day=exit_day, entry_slot=t + 3,
                  exit_decision_slot=t + 50, exit_slot=t + 52 if tokens else None,
                  exit_reason="time" if tokens else "entry_failed", tokens=tokens, cost=self.SOL // 2,
                  proceeds=self.SOL // 2 + pnl if tokens else 0, pnl=pnl, ret=pnl / (self.SOL // 2), exit_attempts=1)

        trade("F7", "tp4_T600", 460_000_000, "2026-10-25", "2026-10-25", -self.SOL // 10)  # before the clock
        trade("F7", "tp4_T600", 460_000_100, "2026-10-25", "2026-10-26", -self.SOL // 10)  # entered before it
        t = 460_001_000
        for d in range(26, 32):  # six clock days, so the day interval exists
            for i in range(7):
                t += 200
                trade("F7", "tp4_T600", t, f"2026-10-{d}", f"2026-10-{d}", (i - 2) * self.SOL // 50)
        trade("F7", "tp4_T600", t + 400, "2026-10-27", "2026-10-27", -505_000, tokens=0)  # failed entry: cost, no trade
        trade("F7", "flow300_H6", t + 600, "2026-10-27", "2026-10-27", 3 * self.SOL)  # group H: a measurement only
        trade("N2_F7", "tp4_T600", t + 800, "2026-10-28", "2026-10-28", self.SOL)  # N2 shadow: never a trade
        j.put("heartbeat", minute=29_330_000, day="2026-10-31", last_slot=t + 900, events=3, open_positions=0,
              gap_slots=0, note="")
        j.commit()
        return j

    def compare(self, clock):
        _, report = lab_paper()
        j = self.journal(clock)
        report.daily_report(j, "2026-10-31")
        with open(os.path.join(j.dir, "2026-10-31", "report.md")) as f:
            md = f.read()
        path = os.path.join(j.dir, "journal.sqlite")
        j.close()
        v = paper.journal_view(path, now=1_793_448_000)  # 2026-10-31 12:00 UTC
        self.assertEqual(v["day"], "2026-10-31")
        s, c = v["stop_rule"], v["capital"]
        import re
        m = re.search(r"- trades (\d+) \(failed entries (\d+), their cost included\), days (\d+), net ([+-][\d.]+) "
                      r"SOL/trade, 95% CI ([+-][\d.]+) … ([+-][\d.]+) SOL: \*\*([A-Za-z ]+)\*\*", md)
        self.assertIsNotNone(m, md)
        self.assertEqual((s["trades"], s["entry_failed"], s["days"]), tuple(int(x) for x in m.group(1, 2, 3)))
        self.assertEqual(f"{s['mean_sol_per_trade']:+.4f}", m.group(4))
        self.assertEqual(s["verdict"], m.group(7))
        # One bootstrap (paper/stoprule.py) for both: the same interval, not one within a tolerance.
        self.assertEqual((f"{s['ci_lo']:+.4f}", f"{s['ci_hi']:+.4f}"), m.group(5, 6))
        m = re.search(r"- final ([\d.]+) SOL, max drawdown ([\d.]+)%, trades (\d+), skipped (\d+)", md)
        self.assertEqual((f"{c['final_sol']:.3f}", f"{c['max_drawdown'] * 100:.1f}", c["taken"], c["skipped"]),
                         (m.group(1), m.group(2), int(m.group(3)), int(m.group(4))))
        self.assertEqual(s["primary"], ["tp4_T600"])
        (arm,) = v["arms"]
        self.assertEqual((arm["group"], arm["variants"][0]["variant"], arm["variants"][0]["trades"]), ("H", "flow300_H6", 1))
        self.assertEqual({x["variant"] for x in v["variants"]}, {"tp4_T600"})  # the page is K3's; H apart
        return s, md

    def test_with_a_clock_start(self):
        s, md = self.compare(clock=True)
        self.assertEqual((s["trades"], s["entry_failed"], s["days"]), (42, 1, 6))  # 6 days x 7, the failed one apart
        self.assertEqual((s["clock"]["date"], s["clock"]["phase"], s["clock"]["clock_day"]), ("2026-10-26", "running", 6))
        self.assertIn("clock day 6, 42 trades: no verdict yet", md)

    def test_without_a_clock_start(self):
        s, md = self.compare(clock=False)
        self.assertEqual((s["trades"], s["entry_failed"], s["days"]), (44, 1, 7))  # every paper day, K3 only
        self.assertIsNone(s["clock"])
        self.assertIn("a count of every paper day, no decision", md)

class DecisionBoundary(unittest.TestCase):
    """Around the stop rule's boundary (the interval's lower bound just above and just below 0) the daily report and
    the Paper tab give the same verdict and the same interval: one bootstrap, 10,000 draws (Codex review point 7).
    Every verdict shows what it rests on and that GO is no permission for live money (owner, 2026-10-16)."""
    SOL = 1_000_000_000
    GROUPS = {"K3": {"primary": True, "variants": ["tp4_T600"], "d_ms": 600, "q": 0.5}}
    COSTS = {"name": "base", "fee_per_tx": 605_000, "tip": 100_000, "failed_tx": 505_000, "land_prob": 0.97,
             "ata_rent": 1_513_840, "rent_mode": "refunded_on_full_exit", "adverse_per_side": 0.0}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def pnls(self, shift):
        """210 filled trades over 10 clock days (21 a day) with uneven days, plus a failed entry; `shift` on every
        filled trade moves the interval's bounds by exactly `shift` (the mean per filled trade)."""
        out = []
        for d in range(10):
            for i in range(21):
                out.append((f"2026-10-{26 + d}" if d < 6 else f"2026-11-0{d - 5}", (d % 3 - 1) * 3_000_000 + (i - 10) * 1_000_000
                            + shift, 5))
        out.append(("2026-10-27", -505_000, 0))
        return out

    def journal(self, name, shift):
        jmod, _ = lab_paper()
        j = jmod.Journal(os.path.join(self.tmp, name, "journal"))
        j.state("groups", json.dumps(self.GROUPS))
        j.state("clock_start", json.dumps({"date": "2026-10-26", "reason": "r", "set_at": "2026-10-22T09:30:00+00:00"}))
        j.state("feed", json.dumps({"kind": "live"}))
        j.state("cost_scenario", json.dumps(self.COSTS))
        t = 460_000_000
        for day, pnl, tokens in self.pnls(shift):
            t += 100
            j.put("orders", family="F7", variant="tp4_T600", key=f"P{t}", t=t, day=day, entry_slot=t + 3, size=self.SOL // 2,
                  status="filled" if tokens else "failed")
            j.put("exits", family="F7", variant="tp4_T600", key=f"P{t}", t=t, day=day, entry_slot=t + 3, exit_slot=t + 50 if tokens else None,
                  exit_reason="time" if tokens else "entry_failed", tokens=tokens, cost=self.SOL // 2 if tokens else 0,
                  proceeds=self.SOL // 2 + pnl if tokens else 0, pnl=pnl, ret=None, exit_attempts=2 if t % 700 == 0 else 1)
        j.put("heartbeat", minute=29_330_000, day="2026-11-04", last_slot=t + 900, events=3, open_positions=0, gap_slots=0, note="")
        j.commit()
        return j

    def both(self, name, shift):
        _, report = lab_paper()
        j = self.journal(name, shift)
        report.daily_report(j, "2026-11-04")
        with open(os.path.join(j.dir, "2026-11-04", "report.md")) as f:
            md = f.read()
        path = os.path.join(j.dir, "journal.sqlite")
        j.close()
        return md, paper.journal_view(path, now=1_793_793_600)["stop_rule"]  # 2026-11-04 12:00 UTC

    def test_the_same_verdict_on_both_sides_of_the_boundary(self):
        import math
        import re
        import time

        _, report = lab_paper()
        sr = report  # the report re-exports stoprule's names
        days = sorted({d for d, _, _ in self.pnls(0)})
        sums = [sum(p for d2, p, _ in self.pnls(0) if d2 == d) for d in days]
        cnts = [sum(1 for d2, _, k in self.pnls(0) if d2 == d and k) for d in days]
        t0 = time.perf_counter()
        lo0, _ = report.verdict({d: [s] for d, s in zip(days, sums)}, sum(cnts), dict(zip(days, cnts)))[1:3]
        self.assertLess(time.perf_counter() - t0, 2.0)  # 10,000 draws in pure Python
        self.assertEqual(sr.DRAWS, 10_000)
        for name, shift, want in (("above", -math.floor(lo0) + 1_000, "GO"), ("below", -math.ceil(lo0) - 1_000, "ADJUST")):
            md, s = self.both(name, shift)
            m = re.search(r"95% CI ([+-][\d.]+) … ([+-][\d.]+) SOL: \*\*([A-Z]+)\*\*", md)
            self.assertEqual((s["verdict"], m.group(3)), (want, want), name)
            self.assertEqual((f"{s['ci_lo']:+.4f}", f"{s['ci_hi']:+.4f}"), m.group(1, 2))
            a = s["assessment"]
            self.assertAlmostEqual(a["basis"]["ci95_lamports"][0], lo0 + shift, delta=1e-3)  # the bounds move by the shift
            self.assertEqual((a["basis"]["filled_trades"], a["basis"]["failed_entries"], a["basis"]["trading_days"],
                              a["basis"]["clock_day"]), (210, 1, 10, 10))
            self.assertTrue(a["applies"])  # live, the clock running, a verdict: before four weeks, no error
            self.assertEqual(s["clock"]["phase"], "due")
            c = a["basis"]["costs_lamports"]
            self.assertEqual((c["network"], c["tips"], c["failed_entries"]), (2 * 210 * 605_000, 2 * 210 * 100_000, 505_000))
            self.assertEqual(c["landing_retries"], 2 * 210 * int((1 / 0.97 - 1) * 505_000))
            self.assertEqual(a["basis"]["costs_expected"], ["landing_retries"])  # an expectation, not counted
            self.assertIn("landing retries (expected)", md)
            self.assertEqual({u["key"] for u in a["uncertainty"]}, {"few_days", "regime", "simulated_landing", "many_trials"})
            self.assertEqual(a["assumptions"][2]["d_ms"], 600)
            self.assertEqual(a["not_permission"], "GO is geen toestemming voor live geld; dat is een aparte eigenaarsbeslissing.")
            self.assertIn("**GO is geen toestemming voor live geld; dat is een aparte eigenaarsbeslissing.**", md)
            self.assertIn("only 10 trading days: below 14 the day bootstrap is coarse", md)
            self.assertIn("### What the verdict rests on", md)


class TokenNames(unittest.TestCase):
    """Names come from the data we hold, in layers: the paper run's store (pools -> mint, mints' name), the journal's
    learned pools, the recorder's segments (pump CreateEvent, token_meta, pool_creations); a key found nowhere keeps
    its mint if known and is looked up again later, reading newer segments only. No outside service."""

    def test_layers_and_retry(self):
        import duckdb
        sol = paper.SOL_MINT
        with tempfile.TemporaryDirectory() as d:
            store = os.path.join(d, "fwd.duckdb")
            con = duckdb.connect(store)
            con.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR)")
            con.execute("CREATE TABLE mints (mint VARCHAR, name VARCHAR, symbol VARCHAR)")
            con.execute("INSERT INTO pools VALUES ('PoolStore', 'MintStore'), ('PoolGap', 'MintGap')")
            con.execute("INSERT INTO mints VALUES ('MintStore', 'Store <b>Coin</b>', 'STO')")
            con.close()
            jdir = os.path.join(d, "paper-k3", "journal")
            os.makedirs(jdir)
            db = sqlite3.connect(os.path.join(jdir, "journal.sqlite"))
            db.execute("CREATE TABLE state (k TEXT PRIMARY KEY, v TEXT)")
            db.execute("CREATE TABLE learned (kind TEXT, key TEXT, seq INTEGER, slot INTEGER, info TEXT, PRIMARY KEY (kind, key))")
            db.execute("INSERT INTO state VALUES ('farmer_seed', ?)", [json.dumps({"store": store})])
            db.execute("INSERT INTO learned VALUES ('pool', 'PoolLearned', 3, 1, ?)",
                       [json.dumps(["sol", "normal", "PoolLearned", "MintLive", "normal", "sol", False, "c", 1])])
            db.commit()
            db.close()
            root = os.path.join(d, "recorder")

            def segment(seq, tables):
                sd = os.path.join(root, "segments", f"{seq:09d}")
                for sub, (cols, rows) in tables.items():
                    os.makedirs(os.path.join(sd, sub))
                    values = ", ".join("(" + ", ".join(f"'{v}'" for v in r) + ")" for r in rows)
                    duckdb.sql(f"COPY (SELECT * FROM (VALUES {values}) t({cols})) TO '{os.path.join(sd, sub, 'part-00000.parquet')}' (FORMAT parquet)")
                with open(os.path.join(sd, "_segment.json"), "w") as f:
                    json.dump({"seq": seq, "first_slot": 470_000_000 + seq, "last_slot": 470_000_000 + seq, "complete": True,
                               "written_unix": int(time.time()) - 100 + seq, "usage": []}, f)
            segment(1, {"pump/CreateEvent": ("mint, name, symbol", [("MintLive", "Live Coin", "LIVE")]),
                        "pool_creations": ("pool, base_mint, quote_mint", [("PoolRev", sol, "MintRev")]),
                        "token_meta": ("mint, name, symbol", [("MintRev", "Reversed Coin", "REV")])})
            with open(os.path.join(root, "budget.json"), "w") as f:
                json.dump({"day": 0, "spent_bytes": 0, "cap_bytes": 1}, f)
            env = {"LAB_PAPER_JOURNAL": jdir, "LAB_RECORDER_ROOT": root}
            os.environ.update(env)
            dev, paper._dev_names = paper._dev_names, lambda keys: {}  # no development data here
            try:
                keys = ["PoolStore", "PoolLearned", "PoolRev", "PoolGap", "Unknown1111"]
                n = paper.token_names(keys)
                self.assertEqual(n["PoolStore"], {"mint": "MintStore", "symbol": "STO", "name": "Store <b>Coin</b>"})  # raw text: the page escapes
                self.assertEqual((n["PoolLearned"]["symbol"], n["PoolLearned"]["mint"]), ("LIVE", "MintLive"))
                self.assertEqual((n["PoolRev"]["symbol"], n["PoolRev"]["mint"]), ("REV", "MintRev"))  # base WSOL: the quote is the token
                self.assertEqual(n["PoolGap"], {"mint": "MintGap"})  # a mint, no name: the page shows its short address
                self.assertEqual(n["Unknown1111"], {})
                # The gap token's create arrives in a newer segment: after NAME_RETRY it is found, reading that one only.
                segment(2, {"pump/CreateEvent": ("mint, name, symbol", [("MintGap", "Gap Coin", "GAP")])})
                self.assertEqual(paper.token_names(["PoolGap"])["PoolGap"], {"mint": "MintGap"})  # not yet: retry later
                paper._name_miss["PoolGap"] = (0, paper._name_miss["PoolGap"][1])
                self.assertEqual(paper.token_names(["PoolGap"])["PoolGap"]["symbol"], "GAP")
                self.assertEqual(paper._name_seq.get("Unknown1111"), 1)  # searched up to segment 1
            finally:
                paper._dev_names = dev
                for k in env:
                    del os.environ[k]
                for k in ("PoolStore", "PoolLearned", "PoolRev", "PoolGap", "Unknown1111"):
                    paper._names.pop(k, None)
                    paper._name_miss.pop(k, None)
                    paper._name_seq.pop(k, None)

    def test_the_page_never_injects_html(self):
        web = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "src")
        for dirpath, _, files in os.walk(web):
            for f in files:
                if f.endswith((".tsx", ".ts")):
                    with open(os.path.join(dirpath, f)) as fh:
                        self.assertNotIn("dangerouslySetInnerHTML", fh.read(), f)  # token names are user-chosen text


class GroupPositions(unittest.TestCase):
    """Group H's own open positions and the day's exits appear apart from K3's (arm_positions, each with its group);
    K3's list, totals and stop rule stay K3's only."""

    def test_arm_positions_are_listed_per_group(self):
        jmod, _ = lab_paper()
        sol = 1_000_000_000
        with tempfile.TemporaryDirectory() as d:
            j = jmod.Journal(os.path.join(d, "paper-k3", "journal"))
            j.state("groups", json.dumps({"K3": {"primary": True, "variants": ["tp4_T600"]}, "H": {"primary": False, "variants": ["flow300_H6"]}}))
            j.state("feed", json.dumps({"kind": "live"}))
            t = 460_000_000
            for variant, pnl in (("tp4_T600", 20_000_000), ("flow300_H6", -100_000_000)):
                t += 100
                j.put("signals", family="F7", variant=variant, key=f"P{t}", t=t, day="2026-10-20", venue="pool", orientation="reversed",
                      depth=0, trigger_trader="w", status="entered")
                j.put("exits", family="F7", variant=variant, key=f"P{t}", t=t, day="2026-10-20", entry_slot=t + 3, exit_slot=t + 50,
                      exit_reason="time", tokens=5, cost=sol // 2, proceeds=sol // 2 + pnl, pnl=pnl, ret=pnl / (sol / 2), exit_attempts=1)
            j.put("positions", family="F7", variant="flow300_H6", key="Popen", t=t + 1, entry_slot=t + 4, cost=sol // 2, tokens=7, status="open")
            j.put("heartbeat", minute=29_874_960, day="2026-10-20", last_slot=t + 900, events=3, open_positions=1, gap_slots=0, note="")
            j.commit()
            path = os.path.join(j.dir, "journal.sqlite")
            j.close()
            v = paper.journal_view(path, now=1_792_497_600)  # 2026-10-20 12:00 UTC
        self.assertEqual([p["variant"] for p in v["positions"]], ["tp4_T600"])
        self.assertEqual(v["totals"]["trades"], 1)
        h = v["arm_positions"]
        self.assertEqual([(p["group"], p["status"], p["key"]) for p in h], [("H", "open", "Popen"), ("H", "time", "P460000200")])
        self.assertAlmostEqual(h[1]["pnl_sol"], -0.1)
        self.assertEqual(h[1]["venue"], "pool")


class GroupsUnknown(unittest.TestCase):
    """A journal without the state 'groups' (job 91's live-risk run, a harness that did not write it): the _H<n>
    variants are group H's, not K3's, so the capital simulation equals the engine's own K3 wallet.json (LAB review:
    4.93 SOL with H counted as K3, 8.38 without); the page says the groups were inferred."""
    JOB91 = "/home/chupa/Solana-project/data-old-faithful-one/lab/tmp/job91/live-risk"

    def test_inferred_from_the_variant_names(self):
        state, inferred = paper.infer_groups({}, [{"family": "F7", "variant": "tp4_T600"}, {"family": "F7", "variant": "flow300_H6"},
                                                  {"family": "N2_F7", "variant": "tp4_T600"}])
        self.assertTrue(inferred)
        self.assertEqual(paper.stoprule.parse_groups(state["groups"]), ({"tp4_T600"}, {"H": ["flow300_H6"]}))
        self.assertEqual(paper.infer_groups({}, [{"family": "F7", "variant": "tp4_T600"}]), ({}, False))  # no arms: unchanged
        with_groups = {"groups": json.dumps({"K3": {"primary": True, "variants": ["flow300_H6"]}})}
        self.assertEqual(paper.infer_groups(with_groups, [{"family": "F7", "variant": "flow300_H6"}]), (with_groups, False))

    def test_job91_capital_equals_the_engines_k3_wallet(self):
        if not os.path.exists(os.path.join(self.JOB91, "journal.sqlite")):
            self.skipTest("no job 91 journal")
        os.environ["LAB_PAPER_JOURNAL"] = self.JOB91
        try:
            w = paper.wallet_view(1_792_497_600)
            v = paper.journal_view(os.path.join(self.JOB91, "journal.sqlite"), 1_792_497_600)
        finally:
            del os.environ["LAB_PAPER_JOURNAL"]
        with open(os.path.join(self.JOB91, "wallet.json")) as f:
            k3 = json.load(f)["balance_lamports"] / 1e9
        self.assertTrue(w["paper"]["groups_inferred"] and v["groups_inferred"])
        self.assertAlmostEqual(w["paper"]["balance_sol"], k3, places=6)  # 8.380868337
        self.assertEqual({p["variant"] for p in v["positions"]}, {"tp4_T600"})
        self.assertEqual([g["group"] for g in v["arms"]], ["H"])


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
                paper.__spec__.loader.exec_module(paper)  # a reload: loaded by path, no finder knows it
                self.assertFalse(paper.recorder_view(1_791_800_000)["sample"])
            finally:
                del os.environ["LAB_RECORDER_ROOT"]
                paper.__spec__.loader.exec_module(paper)  # a reload: loaded by path, no finder knows it


class Marks(unittest.TestCase):
    def test_open_position_pnl_is_mark_pnl_and_paper_wallet_is_paper(self):
        now = 1_791_800_000
        today = paper.utc_day(now)
        with tempfile.TemporaryDirectory() as d:
            path = make_journal(d, [("F7", "Closed", 460_000_000, today, 1_000_000, 5)],
                                positions=[("F7", "OpenPool", 460_001_000)], beats=[(now // 60 - 1, today, "", 0)])
            db = sqlite3.connect(path)
            for c in ("mark_proceeds", "mark_slot", "mark_pnl"):
                db.execute(f"ALTER TABLE positions ADD COLUMN {c} INTEGER")
            # proceeds 0.52 SOL on a 0.5 SOL gross cost, but 0.03 SOL of entry and exit costs: mark_pnl -0.01 SOL
            db.execute("UPDATE positions SET mark_proceeds = 520000000, mark_slot = 460001100, mark_pnl = -10000000")
            db.execute("CREATE TABLE state (k TEXT, v TEXT, PRIMARY KEY (k))")
            db.executemany("INSERT INTO state VALUES (?, ?)", [("config", "/x/paper/k3-paper-2026-10-12.yaml"), ("config_sha256", "ab" * 32)])
            db.commit()
            db.close()
            with open(os.path.join(d, "wallet.json"), "w") as fh:
                json.dump({"mode": "paper", "address": None, "slot": 460001100, "balance_lamports": 9_990_000_000,
                           "txs": [{"signature": None, "slot": 460001001, "kind": "buy", "mint": "OpenPool", "symbol": None,
                                    "amount_lamports": -503000000, "fee_lamports": 5000, "tip_lamports": None, "status": "paper"}],
                           "note": "paper trading"}, fh)
            v = paper.journal_view(path, now)
            o = next(r for r in v["positions"] if r["status"] == "open")
            self.assertAlmostEqual(o["pnl_sol"], -0.01)  # mark_pnl, not mark - cost (+0.02)
            self.assertAlmostEqual(o["ret"], -0.02)
            self.assertEqual(v["status"]["config"], "k3-paper-2026-10-12.yaml")
            w = paper._wallet_state(path)
            self.assertEqual((w["mode"], w["sample"], w["address"]), ("paper", False, None))
            self.assertEqual(w["txs"][0]["status"], "paper")
            self.assertEqual(w["txs"][0]["tip_sol"], 0)


class WalletAndTriton(unittest.TestCase):
    def test_wallet_paper_part_is_the_capital_simulation_and_onchain_is_marked_sample(self):
        # The parity journal (no wallet.json), not whichever journal the page picks: from the paper start on that
        # is paper-k3, which has the engine's wallet.
        if not os.path.exists(os.path.join(PARITY, "journal.sqlite")):
            self.skipTest("no parity journal")
        os.environ["LAB_PAPER_JOURNAL"] = PARITY
        try:
            w = paper.wallet_view(1_791_800_000)
        finally:
            del os.environ["LAB_PAPER_JOURNAL"]
        p = w["paper"]
        v = paper.journal_view(os.path.join(PARITY, "journal.sqlite"), 1_791_800_000)
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

    def test_wallet_has_each_groups_own_wallet_and_risk(self):
        """K3's wallet.json and H's wallet-h.json side by side, each with the risk file the journal records for its
        group (state 'groups', absolute paths next to the configs), H's trades its own variants only; a paper
        transaction without a signature has none (not the text 'None')."""
        jmod, _ = lab_paper()
        sol = 1_000_000_000
        with tempfile.TemporaryDirectory() as d:
            risk = {}
            for name, limit in (("risk.yaml", 1.0), ("risk-h.yaml", 0.25)):
                risk[name] = os.path.join(d, name)
                with open(risk[name], "w") as f:
                    f.write(f"size_sol: 0.5\nmax_open_positions: 5\ndaily_loss_limit_sol: {limit}\nkill_switch: {'true' if limit < 1 else 'false'}\n")
            j = jmod.Journal(os.path.join(d, "paper-k3", "journal"))
            j.state("groups", json.dumps({"K3": {"primary": True, "variants": ["tp4_T600"], "risk": risk["risk.yaml"], "wallet": "wallet.json"},
                                          "H": {"primary": False, "variants": ["flow300_H6"], "risk": risk["risk-h.yaml"], "wallet": "wallet-h.json"}}))
            j.state("feed", json.dumps({"kind": "live"}))
            t = 460_000_000
            for variant, pnl in (("tp4_T600", 20_000_000), ("flow300_H6", -300_000_000), ("flow300_H6", 100_000_000)):
                t += 100
                j.put("orders", family="F7", variant=variant, key=f"P{t}", t=t, day="2026-10-20", entry_slot=t + 3, size=sol // 2, status="filled")
                j.put("exits", family="F7", variant=variant, key=f"P{t}", t=t, day="2026-10-20", entry_slot=t + 3, exit_slot=t + 50,
                      exit_reason="time", tokens=5, cost=sol // 2, proceeds=sol // 2 + pnl, pnl=pnl, ret=pnl / (sol / 2), exit_attempts=1)
            j.put("positions", family="F7", variant="flow300_H6", key="Popen", t=t + 1, entry_slot=t + 4, cost=sol // 2, tokens=7, status="open")
            j.put("heartbeat", minute=29_874_960, day="2026-10-20", last_slot=t + 900, events=3, open_positions=1, gap_slots=0, note="")
            j.commit()
            for name, bal in (("wallet.json", 10.02), ("wallet-h.json", 9.3)):
                with open(os.path.join(j.dir, name), "w") as f:
                    json.dump({"mode": "paper", "address": None, "slot": t + 900, "time_unix": None, "balance_lamports": int(bal * sol),
                               "txs": [{"signature": None, "slot": t + 50, "kind": "sell", "mint": "P1", "amount_lamports": 1}]}, f)
            path = os.path.join(j.dir, "journal.sqlite")
            j.close()
            os.environ["LAB_PAPER_JOURNAL"] = path
            try:
                w = paper.wallet_view(1_792_497_600)  # 2026-10-20 12:00 UTC
            finally:
                del os.environ["LAB_PAPER_JOURNAL"]
        p, (h,) = w["paper"], w["groups"]
        self.assertEqual((p["risk_file"], p["daily_loss_limit_sol"], p["kill_switch"]), ("risk.yaml", 1.0, False))
        self.assertAlmostEqual(p["realised_sol"], 0.02)  # K3's trade only
        self.assertEqual((w["onchain"]["balance_sol"], w["onchain"]["txs"][0]["signature"]), (10.02, None))
        self.assertEqual((h["group"], h["wallet_file"], h["risk_file"], h["daily_loss_limit_sol"], h["kill_switch"]),
                         ("H", "wallet-h.json", "risk-h.yaml", 0.25, True))
        self.assertEqual((h["trades"], h["open_positions"], h["in_positions_sol"]), (2, 1, 0.5))
        self.assertAlmostEqual(h["today_net_sol"], -0.2)
        self.assertEqual(h["wallet"]["balance_sol"], 9.3)
        self.assertIsNotNone(h["wallet"]["txs"][0]["time_unix"])  # the slot's time from the heartbeat

    def test_triton_forecast_from_the_first_recorded_minute(self):
        """A recorder that starts mid-month: the forecast is the spend so far plus the rate since its first minute
        (at most the daily cap) over the days left, not the spend spread over the month from the 1st. The budget is
        the recorder's own cap, and a day stopped on an unknown spend says why."""
        now = 1_792_497_600  # 2026-10-20 12:00 UTC: 12 days left in October
        start = now - 6 * 3600  # the recorder ran 6 hours
        with tempfile.TemporaryDirectory() as root:
            for i in range(6):  # one segment per hour, 0.1 GB each: 0.6 GB, $0.048
                minute = (start + i * 3600) // 60
                d = os.path.join(root, "segments", f"{i:09d}")
                os.makedirs(d)
                with open(os.path.join(d, "_segment.json"), "w") as f:
                    json.dump({"seq": i, "first_slot": 460_000_000 + i, "last_slot": 460_000_000 + i, "complete": True,
                               "written_unix": minute * 60 + 60, "usage": [{"minute": minute, "stream": "tx_watchlist",
                                                                         "bytes": 100_000_000, "messages": 10}]}, f)
            with open(os.path.join(root, "budget.json"), "w") as f:
                json.dump({"day": now // 86_400, "spent_bytes": 600_000_000, "cap_bytes": 50_000_000_000, "spent_gb": 0.6,
                           "stopped": True, "wire_ratio": 1.0, "unknown": "segments/000000009 has no _segment.json"}, f)
            os.environ["LAB_RECORDER_ROOT"] = root
            try:
                t = paper.triton_view(now)
            finally:
                del os.environ["LAB_RECORDER_ROOT"]
        self.assertAlmostEqual(t["month_usd"], 0.048)
        self.assertEqual(t["since_unix"], start // 60 * 60)
        self.assertAlmostEqual(t["rate_usd_per_day"], 0.048 * 4, places=6)  # 6 hours: a quarter of a day
        self.assertAlmostEqual(t["forecast_usd"], 0.048 + 0.192 * 11.5, places=6)  # 11.5 days left after noon
        self.assertEqual(t["recorder"]["budget_usd"], 4.0)  # 50 GB at $0.08
        self.assertIn("no _segment.json", t["recorder"]["unknown"])

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


class FrontEndWording(unittest.TestCase):
    """The Paper tab (web/src/views/Paper.tsx) words every verdict, clock phase and assessment item that
    paper/stoprule.py can give in Dutch, and states the fixed sentence and the clock rule (owner, 2026-10-16)."""
    WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "src")

    def setUp(self):
        with open(os.path.join(self.WEB, "views", "Paper.tsx")) as f:
            self.tsx = f.read()
        with open(os.path.join(self.WEB, "paperTypes.ts")) as f:
            self.types = f.read()

    def keys(self, name):
        """The keys of the object literal `const <name> ... = { ... };` in Paper.tsx."""
        import re
        body = re.search(r"const " + name + r"\b[^\n]*? = \{(.*?)\n\};", self.tsx, re.S).group(1)
        return set(re.findall(r"(?:^|[{,])\s*(\w+):", body, re.M))

    def test_every_item_has_dutch_wording(self):
        import re
        sr = paper.stoprule
        scn = {"fee_per_tx": 1, "tip": 1, "failed_tx": 1, "land_prob": 0.97, "ata_rent": 1, "rent_mode": "lost", "adverse_per_side": 0.01}
        trade = {"day": "2026-10-26", "tokens": 5, "pnl": 1, "cost": 10, "proceeds": 11, "exit_attempts": 2}
        assumptions, uncertainty, costs = set(), set(), set()
        for feed in ("live", "replay"):
            for slot in (1, sr.ALPENGLOW_SLOT):
                a = sr.assessment([dict(trade, entry_slot=slot)], 1, None, None, "too few trades",
                                  {"phase": "running", "clock_day": 1}, scn, {"d_ms": 600, "q": 0.5}, feed)
                assumptions |= {x["key"] for x in a["assumptions"]}
                uncertainty |= {x["key"] for x in a["uncertainty"]}
                costs |= set(a["basis"]["costs_lamports"])
        self.assertEqual(uncertainty, {"not_paper_trading", "few_days", "regime", "simulated_landing", "many_trials"})
        self.assertLessEqual(assumptions, self.keys("ASSUMPTION"))
        self.assertLessEqual(uncertainty, self.keys("UNCERTAINTY"))
        self.assertLessEqual(costs, self.keys("COSTS"))
        self.assertIn(f"const FEW_DAYS = {sr.FEW_DAYS};", self.tsx)
        # Every verdict and clock phase is typed; the badge only for the three decisions; no 'extended' phase left.
        verdicts = {sr.decide(None, None, 0), sr.decide(1, None, sr.MIN_TRADES), *(sr.decide(m, lo, sr.MIN_TRADES) for m, lo in ((1, 1), (1, -1), (-1, -2)))}
        self.assertEqual(verdicts, {*sr.DECISIONS, "too few trades", "too few days"})
        self.assertEqual(set(re.findall(r'"([^"]+)"', re.search(r"type PaperVerdict = (.*);", self.types).group(1))), verdicts)
        self.assertEqual({d for d in verdicts if f"{d}: {{ text:" in self.tsx}, set(sr.DECISIONS))
        phases = {sr.clock_state({"date": "2026-10-26"}, d, v)["phase"] for d, v in (("2026-10-25", "GO"), ("2026-10-27", "too few days"), ("2026-10-27", "GO"))}
        self.assertEqual(set(re.findall(r'"(\w+)"', re.search(r"phase: (.*?);", self.types).group(1))), phases)
        self.assertNotIn("extended", self.tsx + self.types)
        # The badge follows `applies`; a test journal gets its label; the fixed sentence comes from the assessment.
        self.assertIn("a.applies && v && <Badge", self.tsx)
        self.assertIn("TEST · geen paper trading", self.tsx)
        self.assertIn("{a.not_permission}", self.tsx)
        self.assertIn("Vier weken (${s.clock_days} dagen) is de geplande lengte, geen minimum; de grens is dag ${s.clock_max_days}.", self.tsx)


class OneProcess(unittest.TestCase):
    """lab/ui/tests and lab/paper/tests run in one pytest process, in either order: the web app's paper.py is loaded
    by its path as 'lab_ui_paper', so it never takes or meets the package name 'paper' (LAB review, 2026-10-16)."""

    def test_ui_and_paper_tests_together(self):
        import subprocess
        try:
            import pytest  # noqa: F401
        except ImportError:
            self.skipTest("no pytest in this Python")
        lab = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
        ui = ["ui/tests/test_paper.py", "ui/tests/test_server.py::CasesGuards"]
        for files in (["paper/tests/test_clock.py", *ui], [*ui, "paper/tests/test_clock.py"]):
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k", "not OneProcess", *files],
                               cwd=lab, capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, f"{files}\n{r.stdout[-3000:]}")


if __name__ == "__main__":
    unittest.main()
