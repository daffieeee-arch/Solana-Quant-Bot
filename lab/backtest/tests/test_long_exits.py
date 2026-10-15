"""The long exits ("hold while the coin rises", coordinator 2026-10-15): F7 trail20/30/50_H6, trail30_H1,
flow300_H6[_sl8] and M1/M1P/M1E trail30/50 and flow300; the default grids leave every run as it was."""

from types import SimpleNamespace

import pytest

from backtest import overlay
from backtest.strategies import (
    EPOCH_SLOTS,
    F7_LONG_GRID,
    H1_MS,
    H6_MS,
    M1_FLOW,
    M1_MAX_HOLD_MS,
    Clock,
    FlowIndex,
    FlowTime,
    M1Exit,
    M1FlowExit,
    Signal,
    TrailTime,
    f7_exit_rule,
    f7_exit_variant,
    m1_long_variant,
)
from backtest.tournament import MAX_HOLD_MS, exit_rule_for, grid_set, long_cap_ms, work_items

CLOCK = Clock({1044: 267.3})
T = 1044 * EPOCH_SLOTS


class Scn:
    def exit_net(self, mark):
        return mark

    def entry_total(self, cost):
        return cost


def test_f7_long_grid_names_and_caps():
    names = [f7_exit_variant(*g) for g in F7_LONG_GRID]
    assert names == ["trail20_H6", "trail30_H6", "trail50_H6", "trail30_H1", "flow300_H6", "flow300_H6_sl8"]
    trail = f7_exit_rule(("trail", 0.30, None, H1_MS), Scn(), T, CLOCK, None)
    assert isinstance(trail, TrailTime) and trail.max_slots == CLOCK.slots(H1_MS, T) and trail.sl is None
    flow = f7_exit_rule(("flow", 300, -0.08, H6_MS), Scn(), T, CLOCK, "F")
    assert isinstance(flow, FlowTime) and (flow.n_flow, flow.max_slots, flow.sl) == (CLOCK.slots(300_000, T), CLOCK.slots(H6_MS, T), -0.08)
    # spec E's 3-tuples keep T600
    assert f7_exit_rule(("trail", 0.05, None), Scn(), T, CLOCK, None).max_slots == 600


def test_grid_sets():
    assert grid_set("v1", ("v1", "exits", "long"), ("v1", "exits")) == {"v1"}
    assert grid_set("all", ("v1", "exits", "long"), ("v1", "exits")) == {"v1", "exits"}
    assert grid_set("v1,long", ("v1", "exits", "long"), ("v1", "exits")) == {"v1", "long"}
    with pytest.raises(ValueError):
        grid_set("v2", ("v1", "long"), ("v1", "long"))


def _sig(family, variant, params, t=T, pool="P"):
    return Signal(family, variant, "M", t, "pool", 0, params, pool=pool, orientation="reversed")


def test_long_f7_exits_get_clusters_and_windows_of_their_own():
    tp = _sig("F7", "tp4_T600", (0.04, 600))
    long_ = _sig("F7", "trail20_H6", ("trail", 0.20, None, H6_MS))
    n2 = _sig("N2_F7", "trail20_H6", ("trail", 0.20, None, H6_MS))
    spec_e = _sig("F7", "hold180", ("hold", 180, None))
    assert [long_cap_ms(s) for s in (tp, long_, n2, spec_e)] == [None, H6_MS, H6_MS, None]
    alone = work_items([tp, spec_e], CLOCK)
    mixed = work_items([tp, spec_e, long_, n2], CLOCK)
    assert len(alone) == 1 and len(mixed) == 2
    short = [w for w in mixed if long_ not in w[4]][0]
    assert short[2:4] == alone[0][2:4] and short[4] == alone[0][4]  # the other variants' window is unchanged
    lw = [w for w in mixed if long_ in w[4]][0]
    assert lw[3] - lw[2] > CLOCK.slots(H6_MS, T)  # the long cluster loads its six hours
    assert MAX_HOLD_MS["F7"] < H6_MS


def test_m1_flow_exit_keeps_the_hard_stop_and_time():
    stream = SimpleNamespace(orientation="normal", pool_legs=[
        SimpleNamespace(slot=100, kind="buy", quote_net=5, farmer=False, pre=SimpleNamespace(e=0, virt=0)),
        SimpleNamespace(slot=130, kind="sell", quote_net=9, farmer=False, pre=SimpleNamespace(e=0, virt=0))])
    r = M1FlowExit(FlowIndex(stream), 20, 1_000)
    ctx = lambda mark, held, slot: {"mark": mark, "cost": 100, "slots_held": held, "slot": slot}  # noqa: E731
    assert r(ctx(120, 10, 110)) is None and r.peak == 120  # before n_flow: no flow check
    assert r(ctx(110, 20, 120)) is None  # [100, 120]: +5
    assert r(ctx(110, 30, 130)) == "outflow"  # [110, 130]: -9
    assert r(ctx(75, 5, 105)) == "stop_loss"  # -25 % of the cost, as M1Exit
    assert M1FlowExit(FlowIndex(stream), 20, 1_000)(ctx(110, 1_000, 120)) == "time"


def test_m1_dispatch_and_names():
    stream = SimpleNamespace(orientation="normal", pool_legs=[])
    flow = exit_rule_for(SimpleNamespace(family="M1E", params=(1.5, M1_FLOW, 60_000), t=T), Scn(), 1, CLOCK, stream)
    assert isinstance(flow, M1FlowExit) and flow.max_slots == CLOCK.slots(M1_MAX_HOLD_MS, T) and flow.n_flow == CLOCK.slots(300_000, T)
    ins = SimpleNamespace(holdings=lambda s: 0, sold_since=lambda a, b: (0, 0))
    wrapped = exit_rule_for(SimpleNamespace(family="N2_M1", params=(2, M1_FLOW), t=T), Scn(), 1, CLOCK, stream, None, ins)
    assert isinstance(wrapped, overlay.O7Exit) and isinstance(wrapped.base, M1FlowExit)  # O7 as with M1Exit
    trail = exit_rule_for(SimpleNamespace(family="M1", params=(2, 0.30), t=T), Scn(), 1, CLOCK, stream)
    assert isinstance(trail, M1Exit) and trail.trail == 0.30
    assert [m1_long_variant("M1", 2, 0.30), m1_long_variant("M1", 3, M1_FLOW), m1_long_variant("M1P", 1.5, 0.50),
            m1_long_variant("M1E", 1.5, M1_FLOW, 300_000)] == ["k2_trail30", "k3_flow300", "k15_trail50", "m5_flow300"]


def test_peak_mark_and_its_first_slot():
    from backtest.tournament import _peak

    assert _peak(SimpleNamespace(marks=[(10, 100), (12, 140), (15, 140), (20, 90)])) == {"peak_mark": 140, "peak_slot": 12}
    assert _peak(SimpleNamespace(marks=[])) == {"peak_mark": None, "peak_slot": None}
    assert _peak(SimpleNamespace()) == {"peak_mark": None, "peak_slot": None}
