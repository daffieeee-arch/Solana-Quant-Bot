"""Synthetic checks for time deadlines in quiet gaps and for tracked holdings of other traders."""

from backtest import replay
from backtest.costs import Scenario
from backtest.replay import CurveLeg, Stream, Tau, simulate_curve_position
from backtest.strategies import TpSlTime
from backtest.venues import Curve, curve_buy_exact_in, curve_buy_exact_out, curve_sell

SOL = 1_000_000_000
BASE = Curve(1_073_000_000_000_000 - 400_000_000_000_000, 30 * SOL + 20 * SOL,
             793_100_000_000_000 - 400_000_000_000_000, 20 * SOL, 95, 30)


def leg_buy(state, slot, tx, trader, tokens, max_cost=None):
    f = curve_buy_exact_out(state, tokens)
    return CurveLeg(slot, tx, (slot, tx, 0, 0), True, tokens, f.curve_quote, f.fees, "buy", state, f.state, trader,
                    max_cost=max_cost, direct=True), f.state


def leg_buy_in(state, slot, tx, trader, budget, ix=0):
    f = curve_buy_exact_in(state, budget)
    return CurveLeg(slot, tx, (slot, tx, ix, 0), True, f.tokens, f.curve_quote, f.fees, "buy_exact_sol_in", state, f.state,
                    trader, budget=budget, direct=True), f.state


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


def test_buy_and_sell_in_one_transaction_sees_the_counterfactual_buy():
    s = BASE
    legs = []
    # X buys 2 SOL worth and sells everything in the same transaction. After our buy X gets fewer
    # tokens, so the sell can only be those tokens.
    l, s = leg_buy_in(s, 102, 0, "X", 2 * SOL)
    legs.append(l)
    l, s = leg_sell(s, 102, 0, "X", l.t)
    l.order = (102, 0, 1, 0)
    legs.append(l)
    l, s = leg_buy(s, 104, 0, "Y", 1_000_000_000, max_cost=10**15)
    legs.append(l)
    stream = Stream("M", 0, "C", legs)
    r = simulate_curve_position(stream, 100, SOL, 1, 0.5, TpSlTime(10.0, -10.0, 1_000, scenario()), 0.05, Tau("inf"))
    assert r.reverted_direct == 0 and r.reverted_router == 0
    assert r.sells_scaled == 1


def test_failed_transaction_rolls_back_its_earlier_legs():
    s = BASE
    legs = []
    # Transaction at 102: X buys (no limit), then buys again with zero slack, which fails after
    # our buy. The whole transaction reverts, so X holds nothing and its later sell is dropped.
    l, s = leg_buy_in(s, 102, 0, "X", 2 * SOL)
    legs.append(l)
    first = l.t
    l, s = leg_buy(s, 102, 0, "X", 1_000_000_000_000)
    l.order = (102, 0, 1, 0)
    l.max_cost = l.q + l.fee
    legs.append(l)
    l, s = leg_sell(s, 103, 0, "X", first)
    legs.append(l)
    stream = Stream("M", 0, "C", legs)
    r = simulate_curve_position(stream, 100, SOL, 1, 0.5, TpSlTime(10.0, -10.0, 1_000, scenario()), 0.05, Tau("inf"))
    assert r.reverted_direct == 1
    assert r.sells_dropped == 1 and r.sells_scaled == 0
