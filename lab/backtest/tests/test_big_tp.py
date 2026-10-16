"""The big take-profits (coordinator 2026-10-16): F7 tp50_H6, tp100_H6 and flow300_tp100_H6, no stop-loss, cap 6 h,
only with --f7-grid big; every other grid builds exactly what it built before."""

from types import SimpleNamespace

import pytest

from backtest.strategies import (
    EPOCH_SLOTS,
    F7_BIG_GRID,
    H6_MS,
    Clock,
    FlowIndex,
    FlowTime,
    FlowTpTime,
    Signal,
    TpTime,
    f7_exit_rule,
    f7_exit_variant,
)
from backtest.tournament import exit_rule_for, grid_set, long_cap_ms, work_items

CLOCK = Clock({1044: 267.3})
T = 1044 * EPOCH_SLOTS


class Scn:
    def exit_net(self, mark):
        return mark

    def entry_total(self, cost):
        return cost


def ctx(mark, held, slot=None):
    return {"mark": mark, "cost": 100, "slots_held": held, "slot": slot if slot is not None else 100 + held}


def test_names_and_rules():
    assert [f7_exit_variant(*g) for g in F7_BIG_GRID] == ["tp50_H6", "tp100_H6", "flow300_tp100_H6"]
    tp = f7_exit_rule(F7_BIG_GRID[0], Scn(), T, CLOCK, None)
    assert isinstance(tp, TpTime) and (tp.tp, tp.sl, tp.max_slots) == (0.50, None, CLOCK.slots(H6_MS, T))
    ftp = f7_exit_rule(F7_BIG_GRID[2], Scn(), T, CLOCK, "F")
    assert isinstance(ftp, FlowTpTime) and isinstance(ftp, FlowTime)
    assert (ftp.flows, ftp.n_flow, ftp.tp, ftp.sl, ftp.max_slots) == ("F", CLOCK.slots(300_000, T), 1.00, None, CLOCK.slots(H6_MS, T))


def test_the_take_profit_on_the_net_return_and_time_without_a_stop():
    r = TpTime(1.00, None, 1_000, Scn())
    assert r(ctx(199, 10)) is None and r(ctx(200, 10)) == "take_profit"
    assert r(ctx(1, 10)) is None  # no stop-loss: a drained pool is held to the cap
    assert r(ctx(150, 1_000)) == "time" and r.deadline(5) == 1_005


def test_flow300_with_the_take_profit_first():
    stream = SimpleNamespace(orientation="normal", pool_legs=[
        SimpleNamespace(slot=100, kind="buy", quote_net=5, farmer=False, pre=SimpleNamespace(e=0, virt=0)),
        SimpleNamespace(slot=130, kind="sell", quote_net=9, farmer=False, pre=SimpleNamespace(e=0, virt=0))])
    r = FlowTpTime(FlowIndex(stream), 20, 1.00, None, 1_000, Scn())
    assert r(ctx(120, 10, 110)) is None  # before n_flow: no flow check
    assert r(ctx(120, 20, 120)) is None  # [100, 120]: +5
    assert r(ctx(120, 30, 130)) == "outflow"  # [110, 130]: -9
    assert r(ctx(250, 30, 130)) == "take_profit"  # the take-profit comes before the outflow
    assert r(ctx(10, 5, 105)) is None  # no stop-loss
    assert r(ctx(120, 1_000, 120)) == "time"


def test_the_big_grid_is_its_own_and_off_by_default():
    assert grid_set("v1,big", ("v1", "exits", "long", "big"), ("v1", "exits")) == {"v1", "big"}
    assert grid_set("all", ("v1", "exits", "long", "big"), ("v1", "exits")) == {"v1", "exits"}  # all is not big
    with pytest.raises(ValueError):
        grid_set("bigger", ("v1", "exits", "long", "big"), ("v1", "exits"))


def _sig(variant, params, family="F7"):
    return Signal(family, variant, "M", T, "pool", 0, params, pool="P", orientation="reversed")


def test_big_exits_load_their_own_six_hours_and_the_others_keep_theirs():
    tp = _sig("tp4_T600", (0.04, 600))
    big = [_sig(f7_exit_variant(*g), g) for g in F7_BIG_GRID] + [_sig(f7_exit_variant(*F7_BIG_GRID[1]), F7_BIG_GRID[1], "N2_F7")]
    assert [long_cap_ms(s) for s in big] == [H6_MS] * 4 and long_cap_ms(tp) is None
    alone, mixed = work_items([tp], CLOCK), work_items([tp] + big, CLOCK)
    assert len(alone) == 1 and len(mixed) == 2
    short = [w for w in mixed if tp in w[4]][0]
    assert short[2:4] == alone[0][2:4]  # tp4_T600's window is unchanged
    lw = [w for w in mixed if big[0] in w[4]][0]
    assert lw[3] - lw[2] > CLOCK.slots(H6_MS, T)


def test_the_flow_take_profit_gets_the_pools_flow_index():
    stream = SimpleNamespace(orientation="reversed", pool_legs=[])
    r = exit_rule_for(SimpleNamespace(family="N2_F7", params=F7_BIG_GRID[2], t=T), Scn(), 1, CLOCK, stream)
    assert isinstance(r, FlowTpTime) and isinstance(r.flows, FlowIndex)
    r = exit_rule_for(SimpleNamespace(family="F7", params=F7_BIG_GRID[0], t=T), Scn(), 1, CLOCK, stream)
    assert isinstance(r, TpTime)
