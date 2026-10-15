"""Spec E's exit rules for F7 pool_reversed (trail, hold, flow; with and without the -8 % stop-loss) and
their wiring: the 9 tp/T variants keep their rule."""

from types import SimpleNamespace

import pytest

from backtest.strategies import (
    EPOCH_SLOTS,
    F7_EXIT_GRID,
    Clock,
    FlowIndex,
    FlowTime,
    HoldTime,
    TpSlTime,
    TrailTime,
    f7_exit_rule,
    f7_exit_variant,
    ms,
)
from backtest.tournament import exit_rule_for


class Scn:
    """Costs that leave the mark as it is: net return = mark / cost - 1."""

    def exit_net(self, mark):
        return mark

    def entry_total(self, cost):
        return cost


def ctx(mark, held, slot=None, cost=100):
    return {"mark": mark, "cost": cost, "slots_held": held, "slot": 1_000 + held if slot is None else slot}


def test_trailing_stop_from_the_peak_mark_and_its_stop_loss():
    r = TrailTime(0.05, None, 600, Scn())
    assert r.deadline(10) == 610
    assert [r(ctx(m, h)) for m, h in ((100, 1), (110, 2), (106, 3))] == [None, None, None]  # 106 > 110 * 0.95
    assert r(ctx(104.5, 4)) == "trailing_stop"  # <= 104.5
    r = TrailTime(0.10, None, 600, Scn())
    assert r(ctx(100, 1)) is None and r(ctx(91, 2)) is None  # -9 %: no stop-loss without _sl8, trail 10 % not hit
    r8 = TrailTime(0.10, -0.08, 600, Scn())
    assert r8(ctx(100, 1)) is None and r8(ctx(91, 2)) == "stop_loss"
    assert TrailTime(0.10, None, 600, Scn())(ctx(100, 600)) == "time"
    assert TrailTime(0.03, None, 600, Scn())(ctx(0, 1)) == "trailing_stop"  # no fill at all (mark 0)


def test_hold_is_time_only_with_the_optional_stop_loss():
    r = HoldTime(None, 225, Scn())
    assert r.deadline(5) == 230
    assert r(ctx(150, 10)) is None  # no take-profit
    assert r(ctx(50, 10)) is None  # no stop-loss without _sl8
    assert r(ctx(100, 225)) == "time"
    assert HoldTime(-0.08, 225, Scn())(ctx(91, 10)) == "stop_loss" and HoldTime(-0.08, 225, Scn())(ctx(93, 10)) is None


def _leg(slot, kind, sol, farmer=False):
    # reversed pool: the token is the quote; a "sell" of the base (WSOL) is a token buy for SOL amount `base`
    return SimpleNamespace(slot=slot, kind=kind, base=sol, farmer=farmer, pre=SimpleNamespace(b=10**12))


def test_flow_exit_after_n_slots_on_organic_outflow():
    stream = SimpleNamespace(orientation="reversed", pool_legs=[
        _leg(100, "sell", 5), _leg(110, "buy", 2), _leg(120, "buy", 9, farmer=True), _leg(130, "buy", 4), _leg(131, "boost", 50)])
    flows = FlowIndex(stream)
    assert flows.flow(100, 130) == 5 - 2 - 4  # the farmer's leg and the boost are out
    r = FlowTime(flows, 20, None, 600, Scn())
    assert r(ctx(100, 15, slot=115)) is None  # before n_flow slots held: not evaluated (flow over [95, 115] = 3 > 0 anyway)
    assert r(ctx(100, 20, slot=120)) is None  # [100, 120]: +5 - 2 = 3 > 0
    assert r(ctx(100, 30, slot=130)) == "outflow"  # [110, 130]: -2 - 4 < 0
    assert r(ctx(100, 60, slot=160)) == "outflow"  # [140, 160]: no trade, flow 0 <= 0
    assert FlowTime(flows, 20, None, 600, Scn())(ctx(100, 600, slot=100)) == "time"  # [80, 100]: +5 > 0, so the cap
    assert FlowTime(flows, 20, -0.08, 600, Scn())(ctx(90, 5, slot=105)) == "stop_loss"


def test_parameters_through_the_clock_and_the_names():
    clock = Clock({1044: 267.3, 1033: 316.4})
    t44, t33 = 1044 * EPOCH_SLOTS, 1033 * EPOCH_SLOTS
    trail = f7_exit_rule(("trail", 0.05, -0.08), Scn(), t44, clock, None)
    assert isinstance(trail, TrailTime) and trail.max_slots == clock.slots(ms(600), t44) == 600 and trail.sl == -0.08
    assert f7_exit_rule(("hold", 180, None), Scn(), t44, clock, None).max_slots == round(180_000 / 267.3)
    assert f7_exit_rule(("hold", 180, None), Scn(), t33, clock, None).max_slots == round(180_000 / 316.4)
    flow = f7_exit_rule(("flow", 30, None), Scn(), t44, clock, "F")
    assert (flow.n_flow, flow.max_slots, flow.flows) == (round(30_000 / 267.3), 600, "F")
    assert len(F7_EXIT_GRID) == 18 and len({f7_exit_variant(*g) for g in F7_EXIT_GRID}) == 18
    assert [f7_exit_variant("trail", 0.1, None), f7_exit_variant("hold", 60, -0.08), f7_exit_variant("flow", 10, -0.08)] == [
        "trail10_T600", "hold60_sl8", "flow10_T600_sl8"]


@pytest.mark.parametrize("family", ["F7", "N2_F7"])
def test_exit_rule_for_keeps_the_tp_variants_and_dispatches_the_new_ones(family):
    clock = Clock({1044: 267.3})
    t = 1044 * EPOCH_SLOTS
    old = exit_rule_for(SimpleNamespace(family=family, params=(0.04, 600), t=t), Scn(), 1, clock)
    assert isinstance(old, TpSlTime) and (old.tp, old.sl, old.max_slots) == (0.04, -0.08, clock.slots(ms(600), t))
    stream = SimpleNamespace(orientation="reversed", pool_legs=[])
    new = exit_rule_for(SimpleNamespace(family=family, params=("flow", 10, None), t=t), Scn(), 1, clock, stream)
    assert isinstance(new, FlowTime) and new.flows.flow(0, 10**12) == 0  # a FlowIndex of the stream when none is passed


def test_section_e_reads_t_from_every_variant_name():
    import duckdb

    from backtest.analysis import variant_t_sql

    names = ["tp4_T600", "tp6_T2400", "trail3_T600_sl8", "flow10_T600", "hold60", "hold300_sl8"]
    got = duckdb.connect().execute(f"SELECT {variant_t_sql('v')} FROM unnest(?::VARCHAR[]) t(v)", [names]).fetchall()
    assert [r[0] for r in got] == [600, 2400, 600, 600, None, None]


def test_trail_and_hold_get_no_flow_index():
    clock = Clock({1044: 267.3})
    stream = SimpleNamespace(orientation="reversed", pool_legs=[])
    for params in (("trail", 0.05, None), ("hold", 60, -0.08)):
        rule = exit_rule_for(SimpleNamespace(family="F7", params=params, t=1044 * EPOCH_SLOTS), Scn(), 1, clock, stream)
        assert not hasattr(rule, "flows")
