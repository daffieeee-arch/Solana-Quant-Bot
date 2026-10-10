"""Buying and immediately selling, with nothing in between, costs exactly the venue fees."""

from backtest.venues import Curve, Pool, curve_buy_exact_in, curve_sell, pool_buy_exact_in, pool_sell

SOL = 1_000_000_000


def test_curve_round_trip_is_fee_only():
    # 95 bps protocol + 30 bps creator per side -> 1 - (1 - f) / (1 + f) = 2.469% round trip.
    c = Curve(1_073_000_000_000_000 - 300_000_000_000_000, 30 * SOL + 12 * SOL, 793_100_000_000_000 - 300_000_000_000_000,
              12 * SOL, 95, 30)
    for size in (SOL // 2, 2 * SOL, 10 * SOL):
        buy = curve_buy_exact_in(c, size)
        sell = curve_sell(buy.state, buy.tokens)
        rt = 1 - sell.trader_quote / buy.trader_quote
        assert abs(rt - (1 - (1 - 0.0125) / (1 + 0.0125))) < 2e-4, (size, rt)
        # The curve is back where it started, up to rounding in the pool's favour.
        assert 0 <= sell.state.vq - c.vq <= 2


def test_pool_round_trip_is_fee_only():
    p = Pool(206_900_000_000_000, 84_990_359_060, 2, 93, 30)
    for size in (SOL // 2, 2 * SOL, 10 * SOL):
        buy = pool_buy_exact_in(p, size)
        sell = pool_sell(buy.state, buy.tokens)
        rt = 1 - sell.trader_quote / buy.trader_quote
        # 1.25% per side; the LP part (2 bps) stays in the pool, so a little comes back on the sell.
        assert abs(rt - (1 - (1 - 0.0125) / (1 + 0.0125))) < 5e-4, (size, rt)
