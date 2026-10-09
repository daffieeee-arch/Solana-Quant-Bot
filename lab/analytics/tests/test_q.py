"""Tests for lab/bin/q: the hardened session must refuse every escape route.

Run: "$LAB_PY" -m unittest discover -s lab/analytics/tests -v   (light; a few seconds)
"""
import json
import os
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
Q = os.path.join(REPO, "lab", "bin", "q")
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
HOLDOUT_START = 452_304_000


def q(sql, *args, env=None):
    return subprocess.run([Q, *args, sql], capture_output=True, text=True, timeout=120,
                          env={**os.environ, **(env or {})})


class Works(unittest.TestCase):
    def test_select(self):
        r = q("SELECT count(*) AS n FROM mints", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreater(json.loads(r.stdout)["rows"][0][0], 0)

    def test_settings_are_hardened(self):
        r = q("SELECT current_setting('threads'), current_setting('enable_external_access'), "
              "current_setting('lock_configuration'), current_setting('autoload_known_extensions')", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["rows"][0], [2, False, True, False])

    def test_row_cap(self):
        r = q("SELECT * FROM range(2000)", "--json")
        out = json.loads(r.stdout)
        self.assertEqual(len(out["rows"]), 500)
        self.assertTrue(out["truncated"])

    def test_only_development_slots(self):
        r = q("SELECT greatest((SELECT max(slot) FROM blocks), (SELECT max(slot) FROM curve), "
              "(SELECT max(slot) FROM pool), (SELECT max(create_slot) FROM mints))", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(json.loads(r.stdout)["rows"][0][0], HOLDOUT_START)


class MustFail(unittest.TestCase):
    def assertRefused(self, sql):
        r = q(sql)
        self.assertNotEqual(r.returncode, 0, f"should have failed: {sql}\n{r.stdout}")
        return r

    def test_copy_to(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "out.csv")
            self.assertRefused(f"COPY (SELECT 1) TO '{target}'")
            self.assertFalse(os.path.exists(target))

    def test_copy_to_data_dir(self):
        target = os.path.join(DATA, "analytics", "q-test-must-not-exist.csv")
        self.assertRefused(f"COPY (SELECT 1) TO '{target}'")
        self.assertFalse(os.path.exists(target))

    def test_export_database(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertRefused(f"EXPORT DATABASE '{d}/x'")
            self.assertEqual(os.listdir(d), [])

    def test_read_text(self):
        self.assertRefused("SELECT * FROM read_text('/etc/hostname')")

    def test_read_csv(self):
        self.assertRefused("SELECT * FROM read_csv('/etc/passwd')")

    def test_attach_other_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertRefused(f"ATTACH '{d}/other.duckdb' AS other")

    def test_attach_dev_writable(self):
        self.assertRefused(f"ATTACH '{DATA}/store/dev.duckdb' AS rw")

    def test_detach_and_reattach(self):
        self.assertRefused(f"USE memory; DETACH dev; ATTACH '{DATA}/store/dev.duckdb' AS dev")

    def test_install_httpfs(self):
        self.assertRefused("INSTALL httpfs")

    def test_load_httpfs(self):
        self.assertRefused("LOAD httpfs")

    def test_read_parquet_raw_chunks(self):
        self.assertRefused(f"SELECT count(*) FROM read_parquet('{DATA}/events/v1/chunks/*/pump/TradeEvent/*.parquet')")

    def test_enable_external_access(self):
        self.assertRefused("SET enable_external_access=true")

    def test_unlock_configuration(self):
        self.assertRefused("SET lock_configuration=false")

    def test_raise_memory_limit(self):
        self.assertRefused("SET memory_limit='8GB'")

    def test_write_to_dev(self):
        self.assertRefused("CREATE TABLE dev.q_test(i INT)")

    def test_non_select_statements(self):
        for sql in ["CALL pragma_version()", "CHECKPOINT", "CREATE TEMP TABLE z AS SELECT 1",
                    "EXPLAIN ANALYZE SELECT 1", "SELECT 1; DETACH dev", "RESET threads"]:
            self.assertRefused(sql)


class MustNotTouchTheStore(unittest.TestCase):
    """Escape attempts against a throwaway copy, so the real store's locks do not mask a hole."""

    def setUp(self):
        import duckdb  # run with $LAB_PY
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "store"))
        self.db = os.path.join(self.tmp.name, "store", "dev.duckdb")
        con = duckdb.connect(self.db)
        con.execute("CREATE TABLE mints(i INT)")
        con.close()
        self.duckdb = duckdb

    def tearDown(self):
        self.tmp.cleanup()

    def tables(self):
        con = self.duckdb.connect(self.db, read_only=True)
        try:
            return sorted(r[0] for r in con.execute("SHOW TABLES").fetchall())
        finally:
            con.close()

    def run_q(self, sql):
        r = q(sql, env={"LAB_DATA_ROOT": self.tmp.name})
        self.assertNotEqual(r.returncode, 0, f"should have failed: {sql}\n{r.stdout}")

    def test_reattach_writable(self):
        self.run_q(f"USE memory; DETACH dev; ATTACH '{self.db}' AS dev; CREATE TABLE dev.pwn(i INT)")
        self.assertEqual(self.tables(), ["mints"])

    def test_copy_over_the_store(self):
        self.run_q(f"COPY (SELECT 1) TO '{self.db}' (FORMAT csv)")
        self.assertEqual(self.tables(), ["mints"])

    def test_explain_wrapped_copy(self):
        self.run_q(f"EXPLAIN ANALYZE COPY (SELECT 1) TO '{self.db}' (FORMAT csv)")
        self.assertEqual(self.tables(), ["mints"])


if __name__ == "__main__":
    unittest.main()
