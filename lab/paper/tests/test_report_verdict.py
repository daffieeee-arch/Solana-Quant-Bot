"""The paper report's stop-rule counter: net P&L per filled trade after all costs (owner, 2026-10-10)."""

import pytest

from paper.report import MIN_TRADES, _verdict, stop_rule_inputs

FAILED_TX = 505_000  # lamports, a failed entry's cost


def _exits(filled, failed, days=("2026-10-16",), pnl=1_000_000):
    out = []
    for i in range(filled):
        out.append({"day": days[i % len(days)], "pnl": pnl, "tokens": 10})
    for i in range(failed):
        out.append({"day": days[i % len(days)], "pnl": -FAILED_TX, "tokens": 0})
    return out


def test_failed_entries_are_no_trades_for_the_minimum():
    assert MIN_TRADES == 200
    by_day, filled, n = stop_rule_inputs(_exits(199, 5))
    assert n == 199 and sum(len(v) for v in by_day.values()) == 204
    assert _verdict(by_day, n, filled)[3] == "too few trades"  # 204 exits, but 199 trades


def test_their_cost_stays_in_the_mean_per_trade():
    by_day, filled, n = stop_rule_inputs(_exits(200, 5))
    mean, _, _, verdict = _verdict(by_day, n, filled)
    assert n == 200 and mean == pytest.approx((200 * 1_000_000 - 5 * FAILED_TX) / 200)
    assert verdict == "too few days"  # 200 trades on one day: no day interval, no verdict yet
    days = tuple(f"2026-10-{d}" for d in range(16, 21))
    by_day, filled, n = stop_rule_inputs(_exits(200, 5, days=days))
    assert _verdict(by_day, n, filled)[3] in ("GO", "ADJUST")  # 200 trades and 5 days: a verdict


def test_the_day_bootstrap_counts_filled_trades_per_day():
    days = tuple(f"2026-10-{d}" for d in range(16, 22))
    by_day, filled, n = stop_rule_inputs(_exits(240, 12, days=days))
    assert dict(filled) == {d: 40 for d in days} and n == 240
    mean, lo, hi, _ = _verdict(by_day, n, filled)
    # every day: 40 trades of +1,000,000 and 2 failed entries: the per-trade mean is the same on every day
    assert lo == pytest.approx(mean) and hi == pytest.approx(mean) == (40 * 1_000_000 - 2 * FAILED_TX) / 40


def test_the_stop_rule_and_the_backtest_share_the_minimum():
    """MIN_TRADES lives twice: paper/stoprule.py (stdlib, the web app loads it by path) and backtest.report (the
    tournament's verdict). They must not drift (LAB review P2, #309.1)."""
    from backtest import report as backtest_report
    from paper import stoprule

    assert stoprule.MIN_TRADES == backtest_report.MIN_TRADES == MIN_TRADES == 200
