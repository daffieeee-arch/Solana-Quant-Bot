"""Synthetic checks for time deadlines in quiet gaps and for tracked holdings of other traders."""

from backtest import replay
from backtest.costs import Scenario
from backtest.replay import CurveLeg, Stream, Tau, simulate_curve_position
from backtest.strategies import TpSlTime
from backtest.venues import Curve, curve_buy_exact_out, curve_sell

SOL = 1_000_000_000
BASE = Curve(1_073_000_000_000_000 - 400_000_000_000_000, 30 * SOL + 20 * SOL,
             793_100_000_000_000 - 400_000_000_000_000, 20 * SOL, 95, 30)


def leg_buy(state, slot, tx, trader, tokens, max_cost=None):
    f = curve_buy_exact_out(state, tokens)
    return CurveLeg(slot, tx, (slot, tx, 0, 0), True, tokens, f.curve_quote, f.fees, "buy", state, f.state, trader,
                    max_cost=max_cost, direct=True), f.state


def leg_sell(state, slot, tx, trader, tokens):
    f = curve_sell(state, tokens)
    return CurveLeg(slot, tx, (slot, tx, 0, 0), False, tokens, f.curve_quote, f.fees, "sell", state, f.state, trader,
                    min_out=0, direct=True), f.state


class Cfg(dict):
    pass


def scenario():
    return Scenario("base", 605_000, 100_000, 505_000, 0.97, 1_513_840, "refunded_on_full_exit", 0.0)


def test_time_stop_fires_inside_a_quiet_gap():
    s = BASE
    legs = []
    l, s = leg_buy(s, 100, 0, "A", 1_000_000_000_000)
    legs.append(l)
    l, s = leg_buy(s, 5_000, 0, "B", 1_000_000_000_000)  # next trade long after the deadline
    legs.append(l)
    stream = Stream("M", 0, "C", legs)
    rule = TpSlTime(10.0, -10.0, 150, scenario())  # only the time condition can fire
    r = simulate_curve_position(stream, 100, SOL, 1, 0.5, rule, 0.05, Tau("inf"))
    assert r.exit_reason == "time"
    assert r.exit_decision_slot == 101 + 150
    assert r.exit_slot == 101 + 150 + 1


def test_reverted_buyer_cannot_sell_later():
    s = BASE
    legs = []
    # Trader X buys at slot 102 with zero slack: our buy at 101 makes it fail.
    l, s = leg_buy(s, 102, 0, "X", 5_000_000_000_000)
    l.max_cost = l.q + l.fee
    legs.append(l)
    # ... and sells those tokens at slot 103.
    l, s = leg_sell(s, 103, 0, "X", 5_000_000_000_000)
    legs.append(l)
    l, s = leg_buy(s, 104, 0, "Y", 1_000_000_000, max_cost=10**15)  # Y accepts any price
    legs.append(l)
    stream = Stream("M", 0, "C", legs)
    rule = TpSlTime(10.0, -10.0, 1_000, scenario())
    r = simulate_curve_position(stream, 100, SOL, 1, 0.5, rule, 0.05, Tau("zero"))
    assert r.reverted_direct == 1
    assert r.sells_dropped == 1  # X never got the tokens, so the sell is dropped
