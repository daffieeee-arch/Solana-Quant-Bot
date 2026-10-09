"""costs.settle_sql (used by the report over millions of rows) equals costs.settle row by row."""

import duckdb
import pyarrow as pa

from backtest.costs import Scenario, settle, settle_sql
from backtest.replay import Result

SCENARIOS = [
    Scenario("optimistic", 11_000, 0, 11_000, 0.99, 1_513_840, "refunded", 0.0),
    Scenario("base", 605_000, 100_000, 505_000, 0.97, 1_513_840, "refunded_on_full_exit", 0.0),
    Scenario("pessimistic", 3_005_000, 1_000_000, 2_005_000, 0.93, 1_513_840, "lost", 0.005),
]
ROWS = [
    # skipped, entry_failed, cost, proceeds, exit_attempts
    (False, False, 500_000_000, 512_345_678, 1),
    (False, False, 10_000_000_000, 9_012_345_677, 3),
    (False, False, 25_000_000_001, 0, 2),
    (False, False, 2_000_000_000, 2_100_000_003, 0),
    (False, True, 0, 0, 0),
    (True, False, 0, 0, 0),
]


def test_settle_sql_matches_settle():
    table = pa.table({k: [r[i] for r in ROWS] for i, k in enumerate(("skipped", "entry_failed", "cost", "proceeds",
                                                                        "exit_attempts"))})
    con = duckdb.connect()
    con.register("r", table)
    for scn in SCENARIOS:
        pnl, ret = settle_sql(scn)
        got = con.execute(f"SELECT {pnl}, {ret} FROM r").fetchall()
        for row, (p_sql, r_sql) in zip(ROWS, got):
            skipped, failed, cost, proceeds, attempts = row
            if skipped:
                assert p_sql is None and r_sql is None
                continue
            res = Result("M", 0, entry_failed=failed, cost=cost, proceeds=proceeds, exit_attempts=attempts or 1)
            p, r, _ = settle(res, scn)
            assert p_sql == p, (scn.name, row)
            assert (r_sql is None) == (r is None)
            if r is not None:
                assert abs(r_sql - r) < 1e-12, (scn.name, row)
