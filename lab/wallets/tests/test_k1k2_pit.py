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


def test_k1_only_sol_quoted_curves():
    # a curve with another quote mint logs q in that token's units: it is out, known at the creation; a mint without a
    # mints row is out too (quote unknown)
    con = _con()
    con.execute("CREATE TABLE m (mint VARCHAR, quote_mint VARCHAR)")
    con.execute(f"INSERT INTO m VALUES ('A', '{k1_rolling.SOL_KEY}'), ('C', 'pumpCmXqMfrsxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx')")
    con.execute("CREATE TABLE c (mint VARCHAR)")
    con.execute("INSERT INTO c VALUES ('A'), ('C'), ('Z')")
    assert con.execute(f"SELECT list(c.mint ORDER BY c.mint) FROM c LEFT JOIN m USING (mint) WHERE {k1_rolling.SOL_QUOTE}").fetchone()[0] == ["A"]


def _pool_fixture(con):
    # round R: curve completes at 100, their exit xs = 110; pool rows at 104 (bps 125) and 108 (bps 100) and after the
    # exit at 120 (bps 120); round S: completes at 200, xs = 210, pool created (migrate) at 215: no row before 213
    con.execute("CREATE TABLE se (wallet VARCHAR, mint VARCHAR, pool VARCHAR, complete_slot BIGINT, xs BIGINT)")
    con.execute("INSERT INTO se VALUES ('w', 'R', 'PR', 100, 110), ('w', 'S', 'PS', 200, 210)")
    con.execute("CREATE TABLE pools (pool VARCHAR, orientation VARCHAR, b0 BIGINT, q0 BIGINT)")
    con.execute("INSERT INTO pools VALUES ('PR', 'normal', 200000, 85000), ('PS', 'normal', 300000, 90000)")
    con.execute("CREATE TABLE pool (pool VARCHAR, slot BIGINT, tx_index BIGINT, b BIGINT, e BIGINT, lp_bps INT, protocol_bps INT, creator_bps INT)")
    con.execute("""INSERT INTO pool VALUES ('PR', 104, 1, 190000, 90000, 100, 20, 5), ('PR', 108, 7, 180000, 95000, 80, 15, 5),
                                           ('PR', 120, 1, 170000, 99000, 95, 20, 5)""")


def test_pool_state_bps_bound_to_the_reserves_row():
    con = _con()
    _pool_fixture(con)
    con.execute(k1_rolling.pool_states_sql(pool="pool", pools="pools"))
    r = con.execute("SELECT p3.q, p3.t, pb3, p0.q, pb0, nbps, pinit.q, pinit.t FROM pt WHERE mint = 'R'").fetchone()
    # d = 3: the last row before 113 is slot 108 (bps 100), not the first row's 125 and not the later row's 120
    assert r == (95000.0, 180000.0, 100, 95000.0, 100, 2, 85000.0, 200000.0)
    s = con.execute("SELECT p3, pb3, nbps, pinit.q FROM pt WHERE mint = 'S'").fetchone()
    assert s == (None, None, 0, 90000.0)


def test_fallback_round_settles_at_the_pool_creation():
    con = _con()
    con.execute("CREATE TABLE o (fb BIGINT, xs BIGINT, complete_slot BIGINT, migrate_slot BIGINT, p3 INT, pinit INT)")
    con.execute("""INSERT INTO o VALUES (90, 110, 100, 103, 1, 1),      -- pool state before the exit: settled at xs + 3
                                         (190, 210, 200, 215, NULL, 1),  -- no row yet: settled at the creation, 215 > 213
                                         (290, 310, 300, 305, NULL, 1),  -- created before the exit but no trade yet: xs + 3
                                         (390, 410, 400, NULL, NULL, 1), -- never migrated in the store: unknown
                                         (490, 510, NULL, NULL, NULL, NULL)""")
    rows = con.execute(f"SELECT {k1_rolling.settle_sql(3)}, {k1_rolling.exit_slot_sql(3)} FROM o ORDER BY fb").fetchall()
    assert rows == [(k1_rolling.POOL, 113), (k1_rolling.POOL_INIT, 215), (k1_rolling.POOL_INIT, 313), (k1_rolling.UNKNOWN, 413), ("curve", 513)]


def test_capacity_delayed_settlement_blocks_a_new_signal():
    # the fallback round frees its slot at the pool creation (215), not at xs + 3 (213): a signal at 214 is refused
    rows = [(193, 215, 0.1, "d", "S"), (214, 230, 1.0, "d", "T"), (216, 230, 1.0, "d", "U")]
    taken, unknown, pnl, _ = k1_rolling.capacity(rows, 1)
    assert taken == 2 and pnl == {"d": 1.1}
