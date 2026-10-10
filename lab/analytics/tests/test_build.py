"""Tests for lab/analytics/build.py on a small development slice (light, ~10 s).

Run: "$LAB_PY" -m unittest discover -s lab/analytics/tests -v
"""
import os
import subprocess
import sys
import tempfile
import unittest

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import build  # noqa: E402

SLICE = "450144000:450160000"


class BuildSlice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls.tmp.name, "analytics.duckdb")
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "build.py"),
                            "--slots", SLICE, "--out", cls.out, "--memory", "900MB"],
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        cls.con = duckdb.connect(cls.out, read_only=True)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def one(self, sql):
        return self.con.execute(sql).fetchone()[0]

    def test_swapped_in_and_no_leftovers(self):
        self.assertTrue(os.path.exists(self.out))
        self.assertFalse(os.path.exists(self.out + ".building"))

    def test_no_holdout_slots_except_marker_labels(self):
        self.assertEqual(build.validate(self.con), [])
        self.assertEqual(self.one("SELECT count(*) FROM market_events WHERE slot >= 452304000 "
                                  "AND kind <> 'protocol_marker'"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM market_events WHERE kind = 'protocol_marker' "
                                  "AND (mint IS NOT NULL OR sol IS NOT NULL OR time IS NOT NULL)"), 0)

    def test_every_column_documented(self):
        self.assertEqual(self.one("""
            SELECT count(*) FROM duckdb_columns() c
            ANTI JOIN data_dictionary d ON d.table_name = c.table_name AND d.column_name = c.column_name
            WHERE c.schema_name = 'main' AND c.database_name = current_database()"""), 0)

    def test_candles_are_consistent(self):
        self.assertEqual(self.one("SELECT count(*) FROM token_candles_1m WHERE NOT (low <= open AND open <= high "
                                  "AND low <= close AND close <= high AND trades > 0)"), 0)

    def test_trades_add_up(self):
        a = self.one("SELECT sum(curve_trades) FROM token_summary")
        b = self.one("SELECT sum(trades) FROM token_candles_1m WHERE venue = 'curve' AND mint IN "
                     "(SELECT mint FROM token_summary)")
        self.assertEqual(a, b)

    def test_create_slot_graduations_flagged(self):
        self.assertEqual(self.one("SELECT count(*) FROM graduations WHERE in_create_slot <> (slots_to_graduate = 0)"), 0)


SMOKE_RUN = os.path.join(os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab"),
                         "backtests", "smoke-4")


@unittest.skipUnless(os.path.exists(os.path.join(SMOKE_RUN, "summary.parquet")), "no finished smoke run")
class TournamentImport(unittest.TestCase):
    def test_settled_pnl_matches_run_summary(self):
        con = duckdb.connect(config={"memory_limit": "900MB", "threads": 2})
        build.strategies.load(con, SMOKE_RUN)  # raises if any group's total differs from summary.parquet
        n = con.execute("SELECT count(*) FROM strategy_results").fetchone()[0]
        self.assertEqual(n, con.execute("SELECT sum(n) FROM strategy_summary WHERE scenario = 'base'").fetchone()[0])
        groups, worst = con.execute("""SELECT count(*), max(abs(c.cum_pnl_sol - s.total_pnl_sol)) FROM strategy_summary s
                              JOIN (SELECT family, variant, size_sol, d, tau, scenario, arg_max(cum_pnl_sol, step) AS cum_pnl_sol,
                                           max(step) AS last, any_value(n) AS n
                                    FROM strategy_curves GROUP BY ALL) c USING (family, variant, size_sol, d, tau, scenario)
                              WHERE c.last = c.n""").fetchone()
        self.assertEqual(groups, con.execute("SELECT count(*) FROM strategy_summary").fetchone()[0])
        self.assertLess(worst, 1e-6)
        self.assertLessEqual(con.execute("SELECT max(c) FROM (SELECT count(*) c FROM strategy_curves GROUP BY family, variant, size_sol, d, tau, scenario)").fetchone()[0], 2 * 400 + 2)
        self.assertEqual(build.validate(con, require_tables=False), [])

    def test_refuses_holdout_run(self):
        with tempfile.TemporaryDirectory() as d:
            for f in ("results.parquet", "summary.parquet"):
                open(os.path.join(d, f), "w").close()
            with open(os.path.join(d, "config.json"), "w") as f:
                f.write('{"store": {"period": "holdout", "slot_start": 452304000, "slot_end_exclusive": 454464000}}')
            with self.assertRaises(SystemExit):
                build.strategies.load(duckdb.connect(), d)


class SyntheticRunWithPartsAndNewKeys(unittest.TestCase):
    """Run layout from v2 on: parts/ only, extra summary keys, farm = 'all' roll-up rows (in memory, tiny files)."""

    KEYS = ["family", "variant", "size_sol", "d", "tau", "q", "segment", "regime", "farm", "overlay"]

    def make_run(self, d, corrupt_all=False):
        import json
        import random
        rnd = random.Random(7)
        rows = []
        for farm in ("organic", "flagged"):
            for overlay in ("none", "O7V1"):
                for i in range(40):
                    cost = rnd.randrange(400_000_000, 600_000_000)
                    rows.append(("F6", "v", 0.5, 1, None, 0.5, "pool_reversed", "270ms", farm, overlay,
                                 f"m{i}", "pool", "2026-09-30", 451_000_000 + i, 451_000_002 + i, 451_000_100 + i,
                                 "tp", i % 13 == 0, False, 0.0, int(cost * rnd.uniform(0.7, 1.3)), cost,
                                 1 + (i % 3 == 0), False))
        cols = self.KEYS + ["mint", "venue", "day", "t", "entry_slot", "exit_slot", "exit_reason", "entry_failed",
                            "cf_graduation", "hist_ret", "proceeds", "cost", "exit_attempts", "skipped"]
        con = duckdb.connect(config={"temp_directory": ""})
        con.execute("CREATE TABLE p (" + ", ".join(f"{c} {t}" for c, t in zip(cols, [
            "VARCHAR", "VARCHAR", "DOUBLE", "BIGINT", "VARCHAR", "DOUBLE", "VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR",
            "VARCHAR", "VARCHAR", "VARCHAR", "BIGINT", "BIGINT", "BIGINT", "VARCHAR", "BOOLEAN", "BOOLEAN", "DOUBLE",
            "BIGINT", "BIGINT", "BIGINT", "BOOLEAN"])) + ")")
        con.executemany(f"INSERT INTO p VALUES ({', '.join('?' * len(cols))})", rows)
        os.makedirs(os.path.join(d, "parts"))
        for n, half in enumerate(("t % 2 = 0", "t % 2 = 1")):
            con.execute(f"COPY (SELECT * FROM p WHERE {half}) TO '{d}/parts/part-{n:06d}.parquet' (FORMAT parquet)")
        summary = []
        for name, per_tx, failed, rent, mode, adv in build.strategies.scenarios(
                build.strategies.read_costs(build.strategies.DEFAULTS)):
            refund = rent if mode in ("refunded", "refunded_on_full_exit") else 0
            tot = {}
            for r in rows:
                pr, co, att, ef = r[20], r[21], r[22], r[17]
                pnl = -failed if ef else ((int(pr * (1 - adv)) - per_tx + refund - max(0, att - 1) * failed)
                                          - (int(co * (1 + adv)) + per_tx + rent))
                for key in (r[:10], r[:8] + ("all",) + r[9:10]):
                    tot[key] = tot.get(key, 0) + pnl
            for key, v in tot.items():
                off = 1.0 if corrupt_all and key[8] == "all" and name == "base" else 0.0
                summary.append(key + (name, 40, 40, v / 1e9 + off))
        con.execute("CREATE TABLE s AS SELECT * FROM (VALUES " + ", ".join(
            "(" + ", ".join("NULL" if x is None else repr(x) for x in r) + ")" for r in summary) + ") v(" +
            ", ".join(self.KEYS) + ", scenario, n, filled, total_pnl_sol)")
        con.execute(f"COPY s TO '{d}/summary.parquet' (FORMAT parquet)")
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump({"run_id": "synthetic", "store": {"period": "dev", "slot_start": 450144000,
                                                        "slot_end_exclusive": 452304000}}, f)

    def test_parts_new_keys_and_rollup(self):
        with tempfile.TemporaryDirectory() as d:
            self.make_run(d)
            con = duckdb.connect(config={"temp_directory": ""})
            build.strategies.load(con, d)
            self.assertEqual(con.execute("SELECT count(*) FROM strategy_results").fetchone()[0], 160)
            groups = con.execute("SELECT count(DISTINCT (farm, overlay)) FROM strategy_curves").fetchone()[0]
            self.assertEqual(groups, 4)  # organic/flagged x none/O7V1; no curve for the farm = 'all' roll-up

    def test_wrong_rollup_total_is_caught(self):
        with tempfile.TemporaryDirectory() as d:
            self.make_run(d, corrupt_all=True)
            with self.assertRaises(SystemExit):
                build.strategies.load(duckdb.connect(config={"temp_directory": ""}), d)


class ValidatorCatchesHoldout(unittest.TestCase):
    def test_holdout_slot_is_rejected(self):
        con = duckdb.connect()
        con.execute("CREATE TABLE token_summary (mint VARCHAR, create_slot UBIGINT)")
        con.execute("INSERT INTO token_summary VALUES ('x', 452304000)")
        errors = build.validate(con, require_tables=False)
        self.assertTrue(any("hold-out" in e for e in errors), errors)

    def test_marker_only_allowed_in_market_events(self):
        con = duckdb.connect()
        con.execute("CREATE TABLE market_events (slot UBIGINT, kind VARCHAR)")
        con.execute("INSERT INTO market_events VALUES (453800004, 'protocol_marker')")
        self.assertFalse(any("market_events" in e for e in build.validate(con, require_tables=False)))
        con.execute("INSERT INTO market_events VALUES (452304001, 'large_buy')")
        self.assertTrue(any("market_events.slot" in e for e in build.validate(con, require_tables=False)))


if __name__ == "__main__":
    unittest.main()
