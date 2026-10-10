"""The simulator must reproduce real historical fills exactly (sampled from one chunk with args)."""

import os

import pytest

from backtest import data
from backtest.venues import (
    Curve,
    Pool,
    curve_buy_exact_in,
    curve_buy_exact_out,
    curve_sell,
    pool_buy_exact_in,
    pool_buy_exact_out,
    pool_sell,
)

MAYHEM_AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
N = int(os.environ.get("LAB_TEST_SAMPLE", "20000"))


@pytest.fixture(scope="module")
def con():
    chunks = [c for c in data.complete_chunks() if c[0] == 450576000]  # epoch 1043, has args
    if not chunks:
        pytest.skip("epoch 1043 chunk not available")
    c = data.connect(threads=2, memory="2GB")
    c.execute(f"CREATE VIEW trades AS SELECT * FROM {data.parquet('pump/TradeEvent', chunks)}")
    c.execute(f"CREATE VIEW buys AS SELECT * FROM {data.parquet('pump_amm/BuyEvent', chunks)}")
    c.execute(f"CREATE VIEW sells AS SELECT * FROM {data.parquet('pump_amm/SellEvent', chunks)}")
    return c


def curve_rows(con, discs, extra=""):
    lit = ",".join(f"'{d}'" for d in discs)
    # Filter first, then a seeded sample: the same rows on every run.
    return con.execute(f"""
      SELECT * FROM (
        SELECT is_buy, token_amount, COALESCE(quote_amount, sol_amount), fee, creator_fee,
               virtual_token_reserves, COALESCE(virtual_quote_reserves, virtual_sol_reserves),
               real_token_reserves, COALESCE(real_quote_reserves, real_sol_reserves),
               fee_basis_points, creator_fee_basis_points, parent_ix_args {extra}
        FROM trades WHERE parent_ix_disc IN ({lit}) AND "user" <> '{MAYHEM_AGENT}'
          AND COALESCE(cashback, 0) = 0)
      USING SAMPLE {N} ROWS (reservoir, 7)""").fetchall()


def pre_curve(r):
    is_buy, t, q, _, _, vt, vq, rt, rq, fb, cb = r[:11]
    if is_buy:
        return Curve(vt + t, vq - q, rt + t, rq - q, fb, cb)
    return Curve(vt - t, vq + q, rt - t, rq + q, fb, cb)


# Amounts must match exactly. Fees match except ~0.1% of trades where the program charged no
# creator fee despite creator_fee_basis_points > 0; we always charge it for our own orders.
FEE_TOLERANCE = 0.005


def test_curve_sells(con):
    rows = curve_rows(con, ["33e685a4017f83ad", "5df6823ce7e940b2", "1c92de7726c469d5"])
    assert rows
    fee_bad = 0
    for r in rows:
        f = curve_sell(pre_curve(r), r[1])
        assert f.curve_quote == r[2], r
        fee_bad += f.fees != r[3] + r[4]
    assert fee_bad <= len(rows) * FEE_TOLERANCE, f"{fee_bad}/{len(rows)}"


def test_curve_buys_exact_out(con):
    rows = curve_rows(con, ["66063d1201daebea", "b817ee6167c5d33d"])
    assert rows
    fee_bad = 0
    for r in rows:
        f = curve_buy_exact_out(pre_curve(r), r[1])
        assert f.curve_quote == r[2], r
        fee_bad += f.fees != r[3] + r[4]
    assert fee_bad <= len(rows) * FEE_TOLERANCE, f"{fee_bad}/{len(rows)}"


def test_curve_buys_exact_in(con):
    import json

    rows = curve_rows(con, ["38fc74089edfcd5f", "c2ab1c46684d5b2f"])
    assert rows
    bad = 0
    for r in rows:
        args = json.loads(r[11])
        budget = int(args.get("spendable_sol_in", args.get("spendable_quote_in")))
        f = curve_buy_exact_in(pre_curve(r), budget)
        if (f.tokens, f.curve_quote) != (r[1], r[2]):
            bad += 1
    assert bad <= len(rows) * 0.002, f"{bad}/{len(rows)}"


def pool_pre(r, lp, prot, cre, b, q, v):
    return Pool(b, q + int(v or 0), lp, prot, cre or 0)


def test_pool_sells(con):
    rows = con.execute(f"""
      SELECT base_amount_in, quote_amount_out, user_quote_amount_out, lp_fee, protocol_fee, coin_creator_fee,
             lp_fee_basis_points, protocol_fee_basis_points, coin_creator_fee_basis_points,
             pool_base_token_reserves, pool_quote_token_reserves, virtual_quote_reserves, COALESCE(cashback, 0),
             coin_creator = '11111111111111111111111111111111'
      FROM sells USING SAMPLE {N} ROWS (reservoir, 7)""").fetchall()
    for a, gross, user_out, lp, prot, cre, lpb, pb, cb, b, q, v, cash, no_creator in rows:
        # Pools without a coin creator pay no creator fee (store.py sets creator_bps = 0 there).
        f = pool_sell(Pool(b, q + int(v or 0), lpb, pb, 0 if no_creator else (cb or 0)), a)
        assert f.curve_quote == gross
        if cash == 0:
            assert f.trader_quote == user_out, (a, gross, user_out, lp, prot, cre)


def test_pool_buys(con):
    rows = con.execute(f"""
      SELECT parent_ix_disc, base_amount_out, quote_amount_in, user_quote_amount_in, max_quote_amount_in,
             lp_fee_basis_points, protocol_fee_basis_points, coin_creator_fee_basis_points,
             pool_base_token_reserves, pool_quote_token_reserves, virtual_quote_reserves
      FROM buys USING SAMPLE {N} ROWS (reservoir, 7)""").fetchall()
    bad = 0
    for disc, b_out, q_in, user_in, max_in, lpb, pb, cb, b, q, v in rows:
        p = Pool(b, q + int(v or 0), lpb, pb, cb or 0)
        if disc in ("66063d1201daebea", "b817ee6167c5d33d"):
            assert pool_buy_exact_out(p, b_out).curve_quote == q_in
        else:
            f = pool_buy_exact_in(p, max_in)
            if (f.tokens, f.curve_quote) != (b_out, user_in):
                bad += 1
    assert bad <= len(rows) * 0.005, f"{bad}/{len(rows)}"
