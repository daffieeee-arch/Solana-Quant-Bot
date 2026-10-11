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


def test_capital_sim():
    from backtest.report import capital_sim

    sol = 10**9
    # Two overlapping 5 SOL trades fill 10 SOL; the third (overlapping) is skipped for cash.
    trades = [(100, 200, 5 * sol, 1 * sol, False), (110, 300, 5 * sol, -2 * sol, False), (150, 250, 5 * sol, 0, False),
              (400, 500, 5 * sol, 0, True)]  # failed entry: pnl 0 here
    final, mdd, taken, skipped = capital_sim(trades, start=10 * sol, max_positions=5)
    assert (taken, skipped) == (3, 1)
    assert final == 9 * sol  # +1 -2
    assert abs(mdd - (11 - 9) / 11) < 1e-12  # peak 11 after the first exit, then 9
    final2, _, taken2, skipped2 = capital_sim(trades[:3], start=100 * sol, max_positions=1)
    assert (taken2, skipped2) == (1, 2) and final2 == 101 * sol


def test_overlay_effects_pairs_each_trigger_with_its_own_base(tmp_path):
    """O7 rows are compared with the base row of the same trigger and configuration only (curve
    positions have no pool: they must not pair across triggers)."""
    import pyarrow.parquet as pq

    from backtest.report import overlay_effects
    from backtest.store import load_defaults

    sol = 1_000_000_000
    rows = []  # two curve triggers; the overlay vetoes the first and exits the second 0.1 SOL better
    for mint, t, base_back, ov_back, reason in (("A", 100, 0.4 * sol, 0, "o7_veto"), ("B", 200, 0.6 * sol, 0.7 * sol, "time")):
        for overlay, back, why in (("none", base_back, "time"), ("O7V3", ov_back, reason)):
            rows.append(dict(family="F7", variant="tp4_T600", size_sol=0.5, d=1, tau="empirical", q=0.5, mint=mint, t=t,
                             pool=None, overlay=overlay, day="2026-09-01", skipped=False, entry_failed=False,
                             cost=int(0.5 * sol), proceeds=int(back), exit_attempts=1, exit_reason=why))
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "r.parquet")
    (o,) = overlay_effects(str(tmp_path / "r.parquet"), load_defaults())
    assert (o["n"], o["veto_share"]) == (2, 0.5)
    a_base = settle(Result("A", 0, cost=int(0.5 * sol), proceeds=int(0.4 * sol), exit_attempts=1),
                    Scenario.from_cfg("base", load_defaults()))[0]
    b_diff = 0.1  # same fees on both sides
    assert abs(o["delta_sol"] - ((0 - a_base) / sol + b_diff) / 2) < 1e-9
