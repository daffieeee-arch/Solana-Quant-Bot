"""Paper engine on a synthetic feed: F7 trigger and filters, entry and exits (take profit, stop
loss, time), risk limits and the kill switch, and restart idempotency of the journal."""

import os

import pytest
import yaml

from backtest.replay import CurveLeg
from backtest.strategies import Clock
from backtest.venues import Curve, curve_buy_exact_out, curve_sell
from paper.engine import Engine
from paper.feed import Event, Feed, TokenInfo
from paper.journal import Journal

SOL = 1_000_000_000
START = Curve(1_073_000_000_000_000 - 400_000_000_000_000, 30 * SOL + 40 * SOL,
              793_100_000_000_000 - 400_000_000_000_000, 40 * SOL, 95, 30)
CREATE = 450_000


class Synth(Feed):
    """One or more pump tokens; trades given as (slot, trader, 'buy'|'sell', tokens)."""

    def __init__(self, tokens, crash_after=None):
        self.meta_bounds = (0, 10_000_000)
        self.tok, self.legs, self.crash_after = {}, {}, crash_after
        for mint, (creator, trades) in tokens.items():
            self.tok[mint] = TokenInfo(mint, CREATE, creator, False, "11111111111111111111111111111111", None, None)
            s, legs = START, []
            for tx, (slot, trader, side, t) in enumerate(trades):
                f = curve_buy_exact_out(s, t) if side == "buy" else curve_sell(s, t)
                legs.append(CurveLeg(slot, tx, (slot, tx, 0, 0), side == "buy", t, f.curve_quote, f.fees, side, s, f.state,
                                     trader, max_cost=10**15 if side == "buy" else None, min_out=0 if side == "sell" else None,
                                     direct=True))
                s = f.state
            self.legs[mint] = legs

    def token(self, mint):
        return self.tok.get(mint)

    def pool(self, pool):
        return None

    def curve_history(self, mint, upto_slot):
        return [l for l in self.legs[mint] if l.slot <= upto_slot]

    def events(self):
        evs = sorted((Event(l.slot, l.tx, 0, 0, 1_790_000_000 + int(l.slot * 0.4), "curve", m, l)
                      for m, legs in self.legs.items() for l in legs), key=lambda e: (e.order, e.key))
        for i, e in enumerate(evs):
            if self.crash_after is not None and i == self.crash_after:
                raise RuntimeError("simulated crash")
            yield e


def risk_file(tmp_path, **over):
    r = {"size_sol": 0.5, "max_open_positions": 5, "max_exposure_sol": 2.5, "daily_loss_limit_sol": 1.0,
         "kill_switch": False, "families": ["F7"]}
    r.update(over)
    p = tmp_path / f"risk-{len(os.listdir(tmp_path))}.yaml"
    p.write_text(yaml.safe_dump(r))
    return str(p)


def run(tmp_path, tokens, risk, variants=((0.04, 600),), name="j", crash_after=None):
    j = Journal(str(tmp_path / name))
    eng = Engine(Synth(tokens, crash_after), j, risk, variants=list(variants), d=1, q=0.5, n2_k=0, clock=Clock(),
                 backup=False)
    eng.run()
    return j


T = 1_000_000_000_000  # tokens in a typical trade (~0.1 SOL)


def trigger_token(creator="C", after=()):
    """Buys (inflow), then a large non-creator sell at slot 500_040 (the F7 trigger), then `after`."""
    trades = [(500_000, "A", "buy", 20 * T), (500_010, "A2", "buy", 20 * T), (500_040, "B", "sell", 60 * T)]
    return (creator, trades + list(after))


def test_f7_trigger_entry_and_take_profit(tmp_path):
    j = run(tmp_path, {"M": trigger_token(after=[(500_100, "W", "buy", 150 * T), (500_200, "Z", "buy", T)])},
            risk_file(tmp_path))
    sig = j.rows("signals")
    assert [(s["key"], s["t"], s["status"]) for s in sig] == [("M", 500_040, "entered")]
    o = j.rows("orders")[0]
    assert o["status"] == "filled" and o["entry_slot"] == 500_041
    x = j.rows("exits")[0]
    assert x["exit_reason"] == "take_profit" and x["pnl"] > 0


def test_stop_loss_and_time_exit(tmp_path):
    j = run(tmp_path, {"M": trigger_token(after=[(500_100, "S", "sell", 150 * T), (500_200, "Z", "buy", T)])},
            risk_file(tmp_path), name="sl")
    assert j.rows("exits")[0]["exit_reason"] == "stop_loss"
    j = run(tmp_path, {"M": trigger_token(after=[(501_000, "Z", "buy", T)])}, risk_file(tmp_path),
            variants=((0.5, 150),), name="time")
    x = j.rows("exits")[0]
    assert x["exit_reason"] == "time" and x["exit_decision_slot"] == 500_041 + 150


def test_filters_block_the_signal(tmp_path):
    # The creator sold within the last 200 slots: no signal.
    creator_sold = ("C", [(499_900, "C", "buy", 10 * T), (499_950, "C", "sell", 5 * T)] + trigger_token()[1])
    j = run(tmp_path, {"M": creator_sold}, risk_file(tmp_path))
    assert j.rows("signals") == []
    # No inflow over the 50 slots before: no signal.
    j = run(tmp_path, {"M": ("C", [(500_040, "B", "sell", 60 * T)])}, risk_file(tmp_path), name="noflow")
    assert j.rows("signals") == []


def test_risk_limits_and_kill_switch(tmp_path):
    two = {"M1": trigger_token(), "M2": trigger_token()}
    j = run(tmp_path, two, risk_file(tmp_path, max_open_positions=1), name="maxopen")
    st = sorted((s["key"], s["status"], s["reason"]) for s in j.rows("signals"))
    assert st == [("M1", "entered", None), ("M2", "skipped", "max open positions")]
    j = run(tmp_path, two, risk_file(tmp_path, kill_switch=True), name="kill")
    assert {s["reason"] for s in j.rows("signals")} == {"kill switch"}
    bad = tmp_path / "bad.yaml"
    bad.write_text("size_sol: -1\n")
    j = run(tmp_path, two, str(bad), name="bad")
    assert all(s["status"] == "skipped" and s["reason"].startswith("risk config") for s in j.rows("signals"))


def _content(j):
    return {t: j.rows(t) for t in ("signals", "orders", "fills", "exits", "heartbeat", "positions")}


def test_restart_gives_the_same_journal(tmp_path):
    toks = {"M1": trigger_token(after=[(500_100, "W", "buy", 150 * T)]),
            "M2": trigger_token(after=[(500_300, "S", "sell", 150 * T), (500_400, "Z", "buy", T)])}
    full = _content(run(tmp_path, toks, risk_file(tmp_path), name="full"))
    with pytest.raises(RuntimeError):
        run(tmp_path, toks, risk_file(tmp_path), name="restart", crash_after=6)  # dies halfway
    again = _content(run(tmp_path, toks, risk_file(tmp_path), name="restart"))  # resumed: same journal directory
    assert again == full
    assert full["exits"] and full["positions"] == []
