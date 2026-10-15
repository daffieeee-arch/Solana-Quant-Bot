"""backtest.wash: the rt_tx and wash_1h flags, against the audit script's own SQL (a4c, sha256 7e6980c3)."""

import os

import duckdb
import pytest

from backtest import wash

LEGS = [  # pool, slot, tx_index, trader, token_buy, sol_amount, kind
    # P1 @100: A's sell and B's buy in one transaction -> rt_tx
    ("P1", 100, 5, "A", False, 2.0, "sell"), ("P1", 100, 5, "B", True, 2.0, "buy"),
    # P1 @200: A sells and buys back in another transaction of the same slot -> no rt_tx
    ("P1", 200, 1, "A", False, 1.0, "sell"), ("P1", 200, 2, "A", True, 1.0, "buy"),
    # P2 @100: the transaction's buy is in another pool -> no rt_tx
    ("P2", 100, 3, "A", False, 1.0, "sell"), ("P3", 100, 3, "A", True, 1.0, "buy"),
    # P2 @300: A only buys at t -> no trigger leg (NULL)
    ("P2", 300, 1, "A", True, 1.0, "buy"),
    # P1 @400: a boost's buy in the same transaction does not count -> no rt_tx
    ("P1", 400, 7, "A", False, 3.0, "sell"), ("P1", 400, 7, "X", True, 0.5, "boost"),
    # P1 @500: two trigger legs, one of them in a round trip -> rt_tx
    ("P1", 500, 1, "A", False, 1.0, "sell"), ("P1", 500, 2, "A", False, 1.0, "sell"), ("P1", 500, 2, "C", True, 4.0, "buy"),
    # earlier P1 volume for wash_1h: a round trip of 6 SOL and a plain buy of 2 SOL
    ("P1", 50, 1, "D", True, 3.0, "buy"), ("P1", 50, 1, "D", False, 3.0, "sell"), ("P1", 60, 1, "E", True, 2.0, "buy"),
]
TRIGGERS = [("F7", "P1", 100, "A"), ("F7", "P1", 200, "A"), ("F7", "P2", 100, "A"), ("F7", "P2", 300, "A"),
            ("F7", "P1", 400, "A"), ("F7", "P1", 500, "A"), ("N2_F7", "P1", 500, None)]


def a4c(con, hour):
    """The audit script's flag SQL verbatim (one bucket), for the comparison."""
    lo, hi = con.execute("SELECT min(t), max(t) FROM tr").fetchone()
    con.execute(f"""CREATE OR REPLACE TEMP TABLE txp AS
    SELECT pool, slot, tx_index, bool_or(token_buy) AND bool_or(NOT token_buy) AS rt, sum(sol_amount) AS sol
    FROM s.pool WHERE kind IN ('buy', 'sell') AND slot BETWEEN {lo - hour} AND {hi} AND pool IN (SELECT DISTINCT pool FROM tr)
    GROUP BY 1, 2, 3""")
    rt = con.execute("""SELECT tr.pool, tr.t, tr.trigger_trader, bool_or(p.rt)
    FROM tr JOIN s.pool l ON l.pool = tr.pool AND l.slot = tr.t AND l.trader = tr.trigger_trader AND NOT l.token_buy AND l.kind IN ('buy', 'sell')
    JOIN txp p ON p.pool = l.pool AND p.slot = l.slot AND p.tx_index = l.tx_index GROUP BY ALL ORDER BY ALL""").fetchall()
    w = con.execute(f"""SELECT tr.pool, tr.t, sum(p.sol) FILTER (WHERE p.rt) / NULLIF(sum(p.sol), 0)
    FROM (SELECT DISTINCT pool, t FROM tr) tr JOIN txp p ON p.pool = tr.pool AND p.slot >= tr.t - {hour} AND p.slot < tr.t
    GROUP BY ALL ORDER BY ALL""").fetchall()
    return rt, w


@pytest.fixture()
def con():
    c = duckdb.connect()
    c.execute("ATTACH ':memory:' AS s")
    c.execute("CREATE TABLE s.pool (pool VARCHAR, slot BIGINT, tx_index BIGINT, trader VARCHAR, token_buy BOOLEAN, sol_amount DOUBLE, kind VARCHAR)")
    c.executemany("INSERT INTO s.pool VALUES (?, ?, ?, ?, ?, ?, ?)", LEGS)
    c.execute("CREATE TABLE tr (family VARCHAR, pool VARCHAR, t BIGINT, trigger_trader VARCHAR)")
    c.executemany("INSERT INTO tr VALUES (?, ?, ?, ?)", TRIGGERS)
    return c


def test_rt_tx_per_trigger(con):
    wash.flags(con, "tr", buckets=1, wash=False)
    got = {(p, t): rt for p, t, _, rt in con.execute("SELECT * FROM f_rt").fetchall()}
    assert got == {("P1", 100): True, ("P1", 200): False, ("P2", 100): False, ("P1", 400): False, ("P1", 500): True}
    # no trigger leg (P2 @300) and no trigger wallet (N2): no row, so NULL after a LEFT JOIN
    assert con.execute("SELECT count(*) FROM tr LEFT JOIN f_rt USING (pool, t, trigger_trader) WHERE rt_tx IS NULL").fetchone()[0] == 2


@pytest.mark.parametrize("buckets", [1, 3, 8])
def test_equal_to_the_audit_sql(con, buckets):
    hour = 1_000
    ref_rt, ref_w = a4c(con, hour)
    wash.flags(con, "SELECT pool, t, trigger_trader FROM tr", buckets=buckets, hour_slots=hour)
    assert con.execute("SELECT * FROM f_rt ORDER BY ALL").fetchall() == ref_rt
    assert con.execute("SELECT * FROM f_w ORDER BY ALL").fetchall() == ref_w
    # P1 @100: 6 of the 8 SOL in the hour before were a round trip (D at slot 50)
    w = dict(((p, t), s) for p, t, s in ref_w)
    assert w[("P1", 100)] == pytest.approx(6 / 8)


def test_no_triggers(con):
    wash.flags(con, "SELECT * FROM tr WHERE false")
    assert con.execute("SELECT count(*) FROM f_rt").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM f_w").fetchone()[0] == 0
    assert not con.execute("SELECT table_name FROM duckdb_tables() WHERE temporary AND table_name LIKE '_wash%'").fetchall()


@pytest.mark.skipif(not os.environ.get("LAB_RTTX_REGRESS"), reason="dev2 store and f7rev run: set LAB_RTTX_REGRESS=1 (heavy, NAS)")
def test_reproduces_the_audit_on_the_f7rev_dev_run():
    from checks.rt_tx_regress import check

    assert check(os.environ.get("LAB_RTTX_PARTS"), os.environ.get("LAB_RTTX_STORE")) == []
