"""Codex round 2 (2026-10-18) regressions on the K1/K2 research code: the first sell after the buy in transaction order,
the causal capacity simulation with unknown outcomes, and a per-entry rule window for K2."""

import duckdb

from wallets.winners import k1_rolling, k2_sim


def _con():
    con = duckdb.connect()
    con.execute("SET memory_limit = '200MB'; SET threads TO 1")
    return con


def test_first_sell_counts_a_later_tx_in_the_buys_slot():
    con = _con()
    con.execute("CREATE TABLE lg (wallet VARCHAR, mint VARCHAR, slot BIGINT, tx_index BIGINT, is_buy BOOLEAN)")
    con.execute("""INSERT INTO lg VALUES
        ('w', 'A', 10, 3, false),   -- a sell before the first buy in the same slot: an older position, not the exit
        ('w', 'A', 10, 5, true),    -- the first buy
        ('w', 'A', 10, 7, false),   -- a sell later in the same slot: the exit (99q used slot > fbe and skipped it)
        ('w', 'A', 20, 1, false),
        ('w', 'B', 10, 5, true),
        ('w', 'B', 12, 1, false),   -- B: the exit is the next slot's sell
        ('w', 'C', 10, 5, true)     -- C: never sold""")
    con.execute(k1_rolling.FIRST_SELL_SQL)
    rows = dict((m, (fs, same)) for m, fs, same in con.execute("SELECT mint, fs, fs_same_slot FROM fsl ORDER BY 1").fetchall())
    assert rows == {"A": (10, True), "B": (12, False)}


def test_capacity_is_causal_and_keeps_unknown_rounds_apart():
    # two signals in the same slot: the caller orders them by (fb, tx_index); the earlier tx takes the slot even though
    # its exit is later (99q ordered ties by the exit)
    rows = [(13, 100, 1.0, "d1", "A"), (13, 20, 5.0, "d1", "B"), (30, 40, None, "d1", "C"), (35, 50, 2.0, "d1", "D"), (60, 70, -1.0, "d2", "E")]
    taken, unknown, pnl, nunk = k1_rolling.capacity(rows, 1)
    assert taken == 1 and unknown == 0  # A (busy until 100) blocks every later signal, B included
    assert pnl == {"d1": 1.0} and nunk == {}
    taken, unknown, pnl, nunk = k1_rolling.capacity(rows, 2)
    # A and B; B frees at 20; C (unknown) takes the slot at 30 and keeps it busy until 40, so D (35) is refused
    assert taken == 4 and unknown == 1
    assert pnl == {"d1": 6.0, "d2": -1.0} and nunk == {"d1": 1}


def test_capacity_one_per_mint():
    rows = [(10, 50, 1.0, "d", "A"), (20, 30, 1.0, "d", "A"), (60, 70, 1.0, "d", "A")]
    taken, unknown, pnl, _ = k1_rolling.capacity(rows, 5)
    assert taken == 2 and pnl == {"d": 2.0}


def test_k2_rule_per_entry_window():
    r1, r2 = k2_sim.r3("1m"), k2_sim.r3("2m")
    for c in ("n_traders_1m", "inflow_1m", "tip_share_1m", "creator_trades_1m"):
        assert c in r1 and c not in r2
    for c in ("n_traders ", "inflow ", "tip_share,", "creator_trades,"):
        assert c in r2 and c not in r1
    assert k2_sim.ENTRY_WINDOW == {60: "1m", 120: "2m"}
    con = _con()
    con.execute("""CREATE TABLE p AS SELECT * FROM (VALUES
        (0, false, 400.0, 50, 40e9, 0.0, 0, 20, 10e9, 0.0, 0),   -- 40 traders only after minute 1: the 2m rule, not the 1m rule
        (0, false, 400.0, 50, 40e9, 0.0, 0, 45, 35e9, 0.0, 0),   -- both
        (0, false, 400.0, 50, 40e9, 0.0, 0, 45, 35e9, 0.1, 0))   -- tipped in minute 1: 2m rule only (tips later than minute 1 are not seen)
        t(creator_prior_pools, creator_traded_before, b0_sol, n_traders, inflow, tip_share, creator_trades, n_traders_1m, inflow_1m, tip_share_1m, creator_trades_1m)""")
    assert con.execute(f"SELECT count(*) FROM p WHERE {r2}").fetchone()[0] == 3
    assert con.execute(f"SELECT count(*) FROM p WHERE {r1}").fetchone()[0] == 1
