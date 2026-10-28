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
    # skipped, entry_failed, cost, proceeds, exit_attempts, exit_slot, exit_reason
    (False, False, 500_000_000, 512_345_678, 1, 900, "time"),  # sold at the first attempt
    (False, False, 10_000_000_000, 9_012_345_677, 3, 950, "take_profit"),  # sold after 2 misses
    (False, False, 25_000_000_001, 0, 2, None, "outflow"),  # unsold: both attempts failed (a drained pool)
    (False, False, 25_000_000_001, 7_000_000_000, 1, None, "take_profit"),  # unsold: the attempt missed, valued at the end
    (False, False, 500_000_000, 0, 2, 703, "written_off"),  # paper's write-off after 2 failed attempts
    (False, False, 2_000_000_000, 2_100_000_003, 0, 800, "graduation"),  # a graduation sale (no attempt counted)
    (False, False, 2_000_000_000, 0, 0, None, "end_of_data"),  # no attempt at all
    (False, True, 0, 0, 0, None, "entry_slippage"),
    (True, False, 0, 0, 0, None, "no_fill"),
]
COLS = ("skipped", "entry_failed", "cost", "proceeds", "exit_attempts", "exit_slot", "exit_reason")


def result(row):
    skipped, failed, cost, proceeds, attempts, exit_slot, reason = row
    return Result("M", 0, entry_failed=failed, cost=cost, proceeds=proceeds, exit_attempts=attempts, exit_slot=exit_slot,
                  exit_reason=reason)


def test_settle_sql_matches_settle():
    table = pa.table({k: [r[i] for r in ROWS] for i, k in enumerate(COLS)})
    con = duckdb.connect()
    con.register("r", table)
    for rule in (1, 2):
        for scn in SCENARIOS:
            pnl, ret = settle_sql(scn, rule)
            got = con.execute(f"SELECT {pnl}, {ret} FROM r").fetchall()
            for row, (p_sql, r_sql) in zip(ROWS, got):
                if row[0]:
                    assert p_sql is None and r_sql is None
                    continue
                p, r, _ = settle(result(row), scn, rule)
                assert p_sql == p, (rule, scn.name, row)
                assert (r_sql is None) == (r is None)
                if r is not None:
                    assert abs(r_sql - r) < 1e-12, (rule, scn.name, row)


def test_every_landed_failed_exit_attempt_pays():
    """Codex review #6: the transaction set (costs' docstring), written out per case, base scenario: the entry (cost,
    per_tx, rent), each failed exit attempt (failed_tx), one final transaction (per_tx: the filling sale, or the burn and
    close when nothing filled) and the rent back (refunded_on_full_exit). Rule 1 left out the last failed attempt of every
    position that did not fill."""
    base = SCENARIOS[1]
    per_tx, failed, rent, cost = base.per_tx(), base.failed_tx, base.ata_rent, 500_000_000
    entry = cost + per_tx + rent

    def pnl(proceeds, failed_attempts):
        return proceeds - per_tx + rent - failed_attempts * failed - entry

    cases = [  # (exit_attempts, exit_slot, exit_reason, proceeds), failed attempts that pay
        ((3, 950, "take_profit", 612_345_678), 2),  # sold after 2 misses: 2 failed sells + the sale
        ((2, 703, "written_off", 0), 2),  # written off after 2 failed attempts: 2 failed sells + the burn and close
        ((1, None, "outflow", 0), 1),  # unsold at the data's end after 1 failed attempt (a drained pool)
        ((2, None, "take_profit", 7_000_000), 2),  # unsold: the last attempt missed, the position valued at the end
        ((1, 900, "time", 612_345_678), 0),  # sold at once
    ]
    for (attempts, exit_slot, reason, proceeds), k in cases:
        r = Result("M", 0, cost=cost, proceeds=proceeds, exit_attempts=attempts, exit_slot=exit_slot, exit_reason=reason)
        assert settle(r, base)[0] == pnl(proceeds, k), (attempts, exit_slot, reason)
        assert settle(r, base, rule=1)[0] == pnl(proceeds, attempts - 1), (attempts, exit_slot, reason)
    # the numbers the review asked for (base, cost 0.5 SOL): a write-off after 2 failures and an unsold row after 1
    wo = Result("M", 0, cost=cost, proceeds=0, exit_attempts=2, exit_slot=703, exit_reason="written_off")
    unsold = Result("M", 0, cost=cost, proceeds=0, exit_attempts=1, exit_slot=None, exit_reason="end_of_data")
    assert (settle(wo, base)[0], settle(wo, base, rule=1)[0]) == (-502_451_236, -501_946_236)
    assert (settle(unsold, base)[0], settle(unsold, base, rule=1)[0]) == (-501_946_236, -501_441_236)


def test_analytics_settles_with_the_same_failed_exits():
    from analytics import strategies
    from backtest import costs

    for rule in (1, 2):
        assert strategies.failed_exits_sql(rule) == costs.failed_exits_sql(rule)
    assert strategies.FILLED_SQL == costs.FILLED_SQL


def test_a_vault_capped_exit_with_dust_gets_the_rent_back():
    """Codex on c29c545, decided 2026-10-19: the exit burns the remainder and closes the account (costs.exit_net's doc),
    so a vault_capped exit settles as any other, in settle and in settle_sql. The dust case: a reversed pool (80 SOL,
    3e14 tokens, fees 25+5 bps) sells all but 1 raw unit of 1,849,999,999,801 tokens; lab/core costs.rs
    a_vault_capped_exit_with_dust_gets_the_rent_back pins the same numbers. Under exact-in rule 2,
    initial net 1,844,466,600,000 + fees 4,611,166,500 + 922,233,300 leaves one unit.
    The former input 1,850,000,000,203 now spends everything; no cost rule changes."""
    import struct

    from backtest.venues import Pool, rev_sell_token

    held = 1_849_999_999_801
    f = rev_sell_token(Pool(b=80_000_000_000, e=300_000_000_000_000, lp_bps=25, protocol_bps=5, creator_bps=0), held)
    assert (held - f.tokens, f.trader_quote) == (1, 488_852_188)  # vault_capped: f.tokens < held
    base = SCENARIOS[1]
    res = Result("M", 0, cost=500_000_000, proceeds=f.trader_quote, exit_attempts=1, vault_capped=True, exit_slot=900,
                 exit_reason="time")
    pnl, ret, total = settle(res, base)
    assert (pnl, struct.unpack("<Q", struct.pack("<d", ret))[0], total) == (-12_589_048, 0xBF99AAEC01A04340, 502_234_458)
    assert base.exit_net(f.trader_quote) - base.exit_net(f.trader_quote, full_exit=False) == base.ata_rent
    # settle_sql: the same row with vault_capped true and false settles the same
    table = pa.table({"skipped": [False, False], "entry_failed": [False, False], "cost": [500_000_000] * 2,
                      "proceeds": [f.trader_quote] * 2, "exit_attempts": [1, 1], "vault_capped": [True, False],
                      "exit_slot": [900, 900], "exit_reason": ["time", "time"]})
    con = duckdb.connect()
    con.register("r", table)
    sql_pnl, sql_ret = settle_sql(base)
    assert con.execute(f"SELECT {sql_pnl}, {sql_ret} FROM r").fetchall() == [(pnl, ret)] * 2


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
                             cost=int(0.5 * sol), proceeds=int(back), exit_attempts=1, exit_reason=why, exit_slot=t + 50))
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "r.parquet")
    (o,) = overlay_effects(str(tmp_path / "r.parquet"), load_defaults())
    assert (o["n"], o["veto_share"]) == (2, 0.5)
    a_base = settle(Result("A", 0, cost=int(0.5 * sol), proceeds=int(0.4 * sol), exit_attempts=1, exit_slot=150, exit_reason="time"),
                    Scenario.from_cfg("base", load_defaults()))[0]
    b_diff = 0.1  # same fees on both sides
    assert abs(o["delta_sol"] - ((0 - a_base) / sol + b_diff) / 2) < 1e-9


def test_summary_compares_s1_with_its_own_farm_split_of_n2(tmp_path):
    """N2_S1 is split by farming like S1 (its controls are drawn per pool); other N2 controls are
    'n/a' and compared with every split."""
    import pyarrow.parquet as pq

    from backtest.report import summarize
    from backtest.store import load_defaults

    sol = 1_000_000_000
    rows = []
    for fam, farm, back in (("S1", "organic", 0.6), ("N2_S1", "organic", 0.5), ("F7", "organic", 0.55), ("N2_F7", "n/a", 0.5)):
        for day in ("2026-09-01", "2026-09-02"):
            rows.append(dict(family=fam, variant="L4h" if "S1" in fam else "tp4_T600", size_sol=0.5, d=1, tau="empirical",
                             q=0.5, segment="pool_normal", regime="270ms", farm=farm, overlay="none", day=day,
                             skipped=False, entry_failed=False, cost=int(0.5 * sol), proceeds=int(back * sol), exit_attempts=1,
                             hist_ret=0.0, exit_reason="time", exit_slot=900, reverted_router=0, reverted_direct=0, sells_dropped=0,
                             seed_pool=False, cf_graduation=False))
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "r.parquet")
    out = {(o["family"], o["farm"]): o for o in summarize(str(tmp_path / "r.parquet"), load_defaults())
           if o["scenario"] == "base"}
    for fam in ("S1", "F7"):
        assert out[(fam, "organic")]["edge_vs_n2"] is not None
        assert abs(out[(fam, "organic")]["edge_vs_n2"]
                   - (out[(fam, "organic")]["mean_ret"] - out[("N2_" + fam, "organic" if fam == "S1" else "n/a")]["mean_ret"])) < 1e-12


def _e70_settle(result, scn):
    """costs.settle at E70's commit (e3bef48), verbatim but for the name: the frozen rule 1."""
    if result.entry_failed:
        loss = scn.failed_tx  # the entry transaction landed and failed on its slippage limit
        return -loss, None, loss
    cost = scn.entry_total(result.cost)
    # Exit attempts that landed and failed on our own slippage limit pay the failed-tx cost.
    net = scn.exit_net(result.proceeds) - max(0, result.exit_attempts - 1) * scn.failed_tx
    return net - cost, net / cost - 1, cost


def _e70_settle_sql(scn):
    """costs.settle_sql at E70's commit (e3bef48), verbatim but for the name."""
    a = float(scn.adverse_per_side)
    refund = scn.ata_rent if scn.rent_mode in ("refunded", "refunded_on_full_exit") else 0
    cost = f"(CAST(trunc(cost::DOUBLE * {1 + a!r}::DOUBLE) AS BIGINT) + {scn.per_tx() + scn.ata_rent})"
    net = (f"(CAST(trunc(proceeds::DOUBLE * {1 - a!r}::DOUBLE) AS BIGINT) - {scn.per_tx()} + {refund}"
           f" - GREATEST(0, COALESCE(exit_attempts, 1) - 1) * {scn.failed_tx})")
    pnl = f"CASE WHEN skipped THEN NULL WHEN entry_failed THEN {-scn.failed_tx} ELSE {net} - {cost} END"
    ret = f"CASE WHEN skipped OR entry_failed THEN NULL ELSE {net}::DOUBLE / {cost} - 1 END"
    return pnl, ret


def test_rule_1_is_e70s_settle():
    """tol_sweep.ALLOWED_DRIFT's proof for costs.py (B2): rule 1, which the frozen comparisons pass (E63/E65/E70, the
    hold-out, rt_tx_regress), is E70's settle_sql byte for byte and its settle row for row, in every scenario of the
    defaults and of this file. The new rule 2 is a separate path."""
    from backtest.store import load_defaults

    cfg = load_defaults()
    scenarios = SCENARIOS + [Scenario.from_cfg(s, cfg) for s in cfg["costs"]["scenarios"]]
    for scn in scenarios:
        assert settle_sql(scn, 1) == _e70_settle_sql(scn), scn.name
        for row in ROWS:
            if not row[0]:
                assert settle(result(row), scn, 1) == _e70_settle(result(row), scn), (scn.name, row)
