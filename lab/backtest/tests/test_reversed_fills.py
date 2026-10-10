"""Exact fills in reversed pools (base WSOL, quote = the token): our token buy (a pool sell of WSOL)
and token sell (an exact-in pool buy paid in tokens) reproduce logged PumpSwap events to the unit,
with each event's own fee tiers. Also checks that normal pools do."""

import os

import pytest

from backtest import data
from backtest.venues import Pool, pool_buy_exact_in, pool_sell, rev_buy_token, rev_sell_token

N = int(os.environ.get("LAB_TEST_SAMPLE", "20000"))
DEFAULT = "11111111111111111111111111111111"


@pytest.fixture(scope="module")
def con():
    chunks = [c for c in data.complete_chunks() if c[0] == 451872000]
    if not chunks:
        pytest.skip("dev chunk not available")
    c = data.connect(threads=2, memory="1GB")
    c.execute(f"CREATE VIEW buys AS SELECT * FROM {data.parquet('pump_amm/BuyEvent', chunks)}")
    c.execute(f"CREATE VIEW sells AS SELECT * FROM {data.parquet('pump_amm/SellEvent', chunks)}")
    return c


def pre(b, q, v, lpb, pb, cb, cc, cash):
    return Pool(b, q + int(v or 0), lpb, pb, 0 if cc == DEFAULT else (cb or 0), cash or 0)


COLS = """pool_base_token_reserves, pool_quote_token_reserves, virtual_quote_reserves, lp_fee_basis_points,
          protocol_fee_basis_points, coin_creator_fee_basis_points, coin_creator, cashback_fee_basis_points"""


@pytest.mark.parametrize("reversed_", [True, False])
def test_token_buys(con, reversed_):
    """Reversed: a token buy is a pool sell of WSOL; normal: an exact-in pool buy of the token."""
    if reversed_:
        rows = con.execute(f"""SELECT * FROM (SELECT base_amount_in, user_quote_amount_out, quote_amount_out, {COLS}
                               FROM sells WHERE base_supply = 0) USING SAMPLE {N} ROWS (reservoir, 7)""").fetchall()
        assert len(rows) > 1000
        bad = sum(1 for sol, tokens, gross, *p in rows
                  if (lambda f: f is None or (f.tokens, f.curve_quote) != (tokens, gross))(rev_buy_token(pre(*p), sol)))
    else:
        rows = con.execute(f"""SELECT * FROM (SELECT max_quote_amount_in, base_amount_out, {COLS}
                               FROM buys WHERE base_supply <> 0 AND parent_ix_disc = 'c62e1552b4d9e870')
                               USING SAMPLE {N} ROWS (reservoir, 7)""").fetchall()
        assert len(rows) > 1000
        bad = sum(1 for budget, out, *p in rows if (lambda f: f is None or f.tokens != out)(pool_buy_exact_in(pre(*p), budget)))
        # Known: in boost pools with a buyback fee (~0.5% of these rows) the program's net quote is a
        # few lamports below the largest feasible one (tokens differ by ~1e-8); not modelled.
        assert bad <= len(rows) * 0.01, f"{bad}/{len(rows)}"
        return
    assert bad <= len(rows) * 0.002, f"{bad}/{len(rows)}"


@pytest.mark.parametrize("reversed_", [True, False])
def test_token_sells(con, reversed_):
    """Reversed: a token sell is an exact-in pool buy paid in tokens; normal: a pool sell of the token."""
    if reversed_:
        rows = con.execute(f"""SELECT * FROM (SELECT max_quote_amount_in, base_amount_out, {COLS}
                               FROM buys WHERE base_supply = 0 AND parent_ix_disc = 'c62e1552b4d9e870')
                               USING SAMPLE {N} ROWS (reservoir, 7)""").fetchall()
        assert len(rows) > 1000
        bad = sum(1 for tokens, sol, *p in rows if (lambda f: f is None or f.trader_quote != sol)(rev_sell_token(pre(*p), tokens)))
    else:
        rows = con.execute(f"""SELECT * FROM (SELECT base_amount_in, user_quote_amount_out, {COLS}
                               FROM sells WHERE base_supply <> 0 AND COALESCE(cashback, 0) = 0)
                               USING SAMPLE {N} ROWS (reservoir, 7)""").fetchall()
        bad = sum(1 for a, out, *p in rows if (lambda f: f is None or f.trader_quote != out)(pool_sell(pre(*p), a)))
    assert bad <= len(rows) * 0.002, f"{bad}/{len(rows)}"


def test_fee_tiers_by_orientation(con):
    """The reason reversed pools are cheaper: their fee tiers (reported, not asserted)."""
    rows = con.execute("""SELECT base_supply = 0 AS rev, lp_fee_basis_points + protocol_fee_basis_points
                            + CASE WHEN coin_creator = '11111111111111111111111111111111' THEN 0
                                   ELSE coin_creator_fee_basis_points END AS bps, count(*) AS n
                          FROM sells GROUP BY ALL ORDER BY rev, n DESC""").fetchall()
    print("fee bps by orientation:", rows[:12])
    assert rows
