"""Expected values for lab/core's exit rules (core/src/exits.rs) and flow index (core/src/flow.rs), M1 C2-2 of the Rust
live route (research/m1-c2-positionsim-spec-2026-10-19.md §1.6, §3.8, §6.1), from the unmodified paper engine; prints
Rust source (data modules, no tests):

  python core/tools/exit_cases.py exits > core/src/exits/cases.rs
  python core/tools/exit_cases.py flow > core/src/flow/cases.rs

Run from lab/ with PYTHONPATH=. and the lab Python. backtest/tests/test_exit_table.py pins both files to this output and
the exits.rs table to paper.engine.variant.

- The rules are the ones paper.engine's `_enter` builds (engine.py:884-890), from the method itself: a stand-in engine
  (ignore-risk, no restart) runs `Engine._enter` until the pool open, which is patched to stop there with the rule. So
  the tp:T rule's hard-coded stop-loss -0.08 and the windows from the clock are the engine's own.
- A decision calls the rule with PositionSim's context (replay.py:412-413: slots_held = slot - entry_slot). A flow
  rule's index is the engine's LiveFlowIndex, fed with `add` as the engine feeds it; its `flow` calls are recorded per
  decision through an instance wrapper (the class is not touched), with the legs it keeps afterwards (the prune).
- The searches for boundary marks use the rules' own expression, exit_net(mark) / entry_total(cost) - 1. Each case
  asserts what it is for, so a case that stops testing its edge fails here.
"""

import struct
import sys
from collections import defaultdict, deque
from fractions import Fraction
from types import SimpleNamespace as NS
from unittest import mock

from backtest import replay
from backtest.costs import Scenario
from backtest.replay import PoolLeg
from backtest.store import load_defaults
from backtest.strategies import F7_GRID, Clock, ms
from backtest.venues import Pool
from paper import engine as E

SPECS = ("0.04:600", "flow300_H6", "flow300_tp100_H6")  # the M1 table (spec §1.6): R1, R2 and the paper start
DEV2 = {1033: 316.36, 1034: 315.9, 1035: 316.53, 1036: 317.13, 1037: 266.46, 1038: 266.83, 1039: 267.12, 1040: 266.17,
        1041: 265.83, 1042: 267.83, 1043: 267.93, 1044: 268.26, 1045: 267.65, 1046: 267.35}
FWD = {1053: 218.29}
T1041, T1042, T1053, T1064 = 450_100_000, 450_200_000, 455_000_000, 459_648_000
SOL = 1_000_000_000
POOL = Pool(1_000_000, 1_000_000, 20, 5, 5, 0, virt=0)  # the legs' pre state: sol_side's depth, which no flow reads


def bits(x):
    return struct.unpack("<Q", struct.pack("<d", float(x)))[0]


# ----------------------------------------------------------------------------- the engine's rules


class _Stop(Exception):
    pass


def engine_rule(spec, t, clock, scn, orientation="reversed"):
    """(name, params, rule) as paper.engine's `_enter` builds them for a pool entry at t + 1 (engine.py:880-890)."""
    name, params = E.variant(spec)
    g = NS(q=0.5, risk=NS(size_lamports=500_000_000, size_sol=0.5))
    eng = NS(_group=lambda p: g, replaying=False, ignore_risk=True, scn=scn, clock=clock, recent=defaultdict(deque),
             last_state={}, tol=0.05, tau=replay.Tau("empirical"), _hist_state_fn=lambda key, q=None: None)
    p = E.Pending("F7", name, "P", "pool", orientation, t, t + 1, params, 0)
    got = {}

    def stop(stream, t, size, d, q, rule, tol, tau, hist_state=None):
        got["rule"] = rule
        raise _Stop

    with mock.patch.object(E.replay, "open_pool_position", stop):
        try:
            E.Engine._enter(eng, p, t + 1, [])
        except _Stop:
            pass
    return name, params, got["rule"]


def table_row(spec):
    """exits.rs VARIANTS' row of a spec: (name, spec, kind, tp, sl, flow seconds, cap, the cap in ms)."""
    name, params, rule = engine_rule(spec, 1, Clock(), SCNS[BASE][1])
    kind = type(rule).__name__
    if kind == "TpSlTime":
        assert not isinstance(params[0], str)
        flow_s, cap, cap_ms = None, ("V1Slots", params[1]), ms(params[1])
    else:
        assert params[0] in ("flow", "flowtp") and len(params) == 4 and params[2] is None
        flow_s = params[1] if params[0] == "flow" else params[1][0]
        cap, cap_ms = ("Ms", params[3]), params[3]
    return name, spec, kind, getattr(rule, "tp", None), rule.sl, flow_s, cap, cap_ms


# ----------------------------------------------------------------------------- inputs


def scenarios():
    unit = Scenario("unit", 0, 0, 0, 1.0, 0, "lost", 0.0)  # exit_net(m) = m, entry_total(c) = c below 2^53
    cfg = load_defaults()
    return [("unit", unit)] + [(n, Scenario.from_cfg(n, cfg)) for n in ("optimistic", "base", "pessimistic")]


SCNS = scenarios()
UNIT, OPT, BASE, PESS = range(4)


def half_even_clock():
    """A clock whose epochs 10, 11, 12 make 300_000 ms, 21_600_000 ms and ms(600) exactly k + 0.5 slots, where Python's
    round goes to the even k."""
    out = {}
    for e, d in ((10, 300_000), (11, 21_600_000), (12, ms(600))):
        for k in range(1_000, 200_000, 7):
            m = d / (k + 0.5)
            if d / m == k + 0.5 and round(d / m) != int(d / m + 0.5):
                out[e] = m
                break
    assert len(out) == 3, out
    return out


CLOCKS = [("dev2", DEV2), ("fwd1053", FWD), ("ref", {}), ("half_even", half_even_clock())]
DEV, FW, REF, HALF = range(4)


def leg(slot, tx, kind, base, quote_net=0, farmer=False, outer=0, inner=0):
    return PoolLeg(slot, tx, (slot, tx, outer, inner), kind, False, base, quote_net, POOL, POOL, "T", farmer=farmer)


def run_legs(slot0, n, per_slot, base0):
    """Leg k of a run: slot slot0 + k // per_slot, tx k % per_slot; a buy when k % 3 == 0, else a sell; base
    base0 + 1_000 k, quote_net twice that; a farmer when k % 5 == 4 (flow.rs tests `run_legs`, the same formula)."""
    return [leg(slot0 + k // per_slot, k % per_slot, "buy" if k % 3 == 0 else "sell", base0 + 1_000 * k, 2 * (base0 + 1_000 * k),
                farmer=k % 5 == 4) for k in range(n)]


def run_check(legs):
    return sum(l.slot for l in legs), sum(l.base for l in legs), sum(1 for l in legs if l.farmer)


# ----------------------------------------------------------------------------- flow index cases


class FlowCase:
    def __init__(self, name, orientation):
        self.name, self.reversed, self.f, self.ops = name, orientation == "reversed", E.LiveFlowIndex(orientation), []

    def state(self, full):
        f = self.f
        s = (len(f.slots), len(f.seen), f.slots[0] if f.slots else None, f.cum[0], f.cum[-1])
        return s + (((list(f.slots), list(f.cum), sorted(f.seen)),) if full else (None,))

    def add(self, legs):
        self.f.add(legs)
        self.ops.append((("add", legs), self.state(len(self.f.slots) <= 40)))

    def run(self, slot0, n, per_slot, base0):
        legs = run_legs(slot0, n, per_slot, base0)
        self.f.add(legs)
        self.ops.append((("run", (slot0, n, per_slot, base0), run_check(legs)), self.state(len(self.f.slots) <= 40)))

    def flow(self, lo, hi):
        before = len(self.f.slots)
        v = self.f.flow(lo, hi)
        self.ops.append((("flow", lo, hi, v), self.state(len(self.f.slots) <= 40)))
        return v, before, len(self.f.slots)


def flow_cases():
    out = []
    # The window's edges, the kinds that do not count, a duplicate order, farmers, late legs (reversed: a sell is the
    # token buy, + base).
    c = FlowCase("edges_reversed", "reversed")
    c.add([leg(100, 0, "sell", 5), leg(100, 1, "buy", 3), leg(102, 0, "sell", 7), leg(103, 0, "boost", 1_000),
           leg(103, 1, "deposit", 1_000), leg(104, 0, "withdraw", 1_000), leg(105, 0, "sell", 1_000, farmer=True),
           leg(107, 0, "buy", 2)])
    assert c.flow(100, 107)[0] == 7 and c.flow(101, 106)[0] == 7 and c.flow(102, 102)[0] == 7
    assert c.flow(103, 104)[0] == 0 and c.flow(100, 100)[0] == 2 and c.flow(108, 10**9)[0] == 0 and c.flow(0, 99)[0] == 0
    c.flow(106, 101)  # lo > hi: the difference of the two bisections, as Python's
    c.add([leg(102, 0, "sell", 999), leg(103, 0, "sell", 50)])  # a seen order again (ignored); a boost's order as a sell
    assert c.flow(102, 103)[0] == 57
    c.add([leg(101, 0, "buy", 4)])  # late: after slot 100's legs
    c.add([leg(102, 5, "sell", 6), leg(102, 4, "buy", 1, farmer=True)])  # late, into slot 102 after its legs (bisect_right)
    c.add([leg(107, 3, "sell", 9)])  # the last slot again: appended, not late
    c.add([leg(90, 0, "sell", 11), leg(90, 0, "buy", 12)])  # before every leg; its order twice in one call
    assert c.flow(90, 107)[0] == 11 - 3 + 5 - 4 + 7 + 6 + 50 - 2 + 9
    c.flow(101, 102), c.flow(90, 90), c.flow(107, 107)
    out.append(c)
    # A normal pool: a buy is the token buy, + quote_net; base plays no part.
    c = FlowCase("edges_normal", "normal")
    c.add([leg(200, 0, "buy", 1_000, 30), leg(200, 1, "sell", 1_000, 12), leg(201, 0, "sell", 5, 40, farmer=True),
           leg(203, 0, "buy", 7, 8), leg(202, 2, "sell", 9, 4)])
    assert c.flow(200, 203)[0] == 30 - 12 + 8 - 4 and c.flow(201, 202)[0] == -4
    c.flow(203, 203), c.flow(199, 199)
    out.append(c)
    # Sums past i64: three reversed sells near 2^63 and a buy.
    c = FlowCase("i128_sums", "reversed")
    big = 9_000_000_000_000_000_000
    c.add([leg(300, 0, "sell", big), leg(300, 1, "sell", big), leg(301, 0, "sell", big), leg(302, 0, "buy", 2**63 - 1)])
    v = c.flow(300, 301)[0]
    assert v == 3 * big and v > 2**63
    c.flow(300, 302), c.flow(299, 400)
    c.add([leg(299, 0, "buy", big)])  # a late leg that rebuilds sums past i64
    c.flow(299, 302), c.flow(300, 302)
    out.append(c)
    # The prune: i > 256 and i > len // 2, len before the prune. 513 legs: i = 256 keeps, i = 257 prunes (513 // 2 = 256).
    c = FlowCase("prune_513", "reversed")
    c.run(1_000, 513, 1, 5 * SOL)
    assert c.flow(1_256, 1_300)[1:] == (513, 513)
    assert c.flow(1_257, 1_300)[1:] == (513, 256)
    c.flow(1_257, 1_400), c.flow(1_200, 1_600)  # lo below what is kept: from the first kept leg
    c.add(run_legs(1_000, 1, 1, 5 * SOL))  # a pruned order again: counted again, as a late leg in front
    c.add(run_legs(1_000, 300, 1, 5 * SOL)[290:])  # orders still seen: ignored
    c.flow(1_000, 1_000), c.flow(990, 1_600)
    out.append(c)
    # The floor alone: 300 legs, so i > len // 2 holds; i = 256 keeps, i = 257 prunes.
    c = FlowCase("prune_floor", "reversed")
    c.run(1_000, 300, 1, 4 * SOL)
    assert c.flow(1_256, 1_299)[1:] == (300, 300)
    assert c.flow(1_257, 1_299)[1:] == (300, 43)
    out.append(c)
    # 514 legs: i = 257 is not > 514 // 2; i = 258 prunes.
    c = FlowCase("prune_514", "reversed")
    c.run(1_000, 514, 1, 3 * SOL)
    assert c.flow(1_257, 1_513)[1:] == (514, 514)
    assert c.flow(1_258, 1_513)[1:] == (514, 256)
    out.append(c)
    # Two legs a slot: i counts legs, not slots; i = 258 > 256 but not > 300 keeps, i = 302 prunes; the seen orders of
    # the pruned slots go, a kept slot's stay.
    c = FlowCase("prune_two_a_slot", "reversed")
    c.run(1_000, 600, 2, 7 * SOL)
    assert c.flow(1_128, 1_299)[1:] == (600, 600)
    assert c.flow(1_129, 1_299)[1:] == (600, 600)
    assert c.flow(1_151, 1_299)[1:] == (600, 298)
    c.add(run_legs(1_000, 302, 2, 7 * SOL)[300:])  # slot 1150's orders: pruned, so they come back (late)
    c.add(run_legs(1_000, 304, 2, 7 * SOL)[302:])  # slot 1151's: still seen
    c.flow(1_150, 1_151), c.flow(1_151, 1_151)
    c.run(1_300, 40, 1, 7 * SOL)
    c.flow(1_200, 1_339)
    out.append(c)
    return out


# ----------------------------------------------------------------------------- exit rule cases


class RuleCase:
    def __init__(self, name, spec, clock, t, entry, scn, cost):
        self.name, self.spec, self.clock, self.t, self.entry, self.scn, self.cost = name, spec, clock, t, entry, scn, cost
        self.variant, _, self.rule = engine_rule(spec, t, Clock(CLOCKS[clock][1]), SCNS[scn][1])
        self.flows = getattr(self.rule, "flows", None)
        self.calls, self.ops = [], []
        if self.flows is not None:
            orig = self.flows.flow

            def flow(lo, hi):
                v = orig(lo, hi)
                self.calls.append((lo, hi, v))
                return v

            self.flows.flow = flow
        w = E._windows(self.rule)
        self.windows = (w["n_flow_slots"], w["cap_slots"], self.rule.deadline(entry))

    def ret(self, mark):
        s = SCNS[self.scn][1]
        t = s.entry_total(self.cost)
        return s.exit_net(mark) / t - 1 if t != 0 else None

    def add(self, legs):
        self.flows.add(legs)
        self.ops.append(("add", legs))

    def run(self, slot0, n, per_slot, base0):
        legs = run_legs(slot0, n, per_slot, base0)
        self.flows.add(legs)
        self.ops.append(("run", (slot0, n, per_slot, base0), run_check(legs)))

    def decide(self, slot, mark):
        self.calls.clear()
        ctx = dict(slot=slot, mark=mark, cost=self.cost, entry_slot=self.entry, slots_held=slot - self.entry)
        reason = self.rule(ctx)
        kept = len(self.flows.slots) if self.flows is not None else None
        self.ops.append(("decide", slot, mark, self.ret(mark), reason, list(self.calls), kept))
        return reason, list(self.calls), kept


def first_mark(c, x, lo=0, hi=1 << 70):
    """The smallest mark whose net return is >= x (the return grows with the mark)."""
    while lo < hi:
        m = (lo + hi) // 2
        if c.ret(m) >= x:
            hi = m
        else:
            lo = m + 1
    return lo


def naive_ret(c, mark):
    s = SCNS[c.scn][1]
    return float(s.exit_net(mark)) / float(s.entry_total(c.cost)) - 1


def straddle(spec, scn, x, at_or_below):
    """A cost and mark above 2^53 where Python's correctly rounded int / int and a naive f64 division decide the
    threshold x differently: the case that holds a port to `true_div`."""
    for k in range(20_000):
        cost = (1 << 61) + k * 7_919_000_017
        c = RuleCase("search", spec, DEV, T1041, T1041 + 2, scn, cost)
        m = first_mark(c, x, 1 << 60, 1 << 63)
        for mark in (m, m - 1, m - 512, m + 512, m - 1024, m + 1024):
            r, n = c.ret(mark), naive_ret(c, mark)
            if (r <= x if at_or_below else r >= x) != (n <= x if at_or_below else n >= x):
                return cost, mark
    raise AssertionError(f"no straddle for {spec} at {x}")


def rule_cases():
    out = []
    # No net return equals 0.04 or -0.08 exactly: x - 1 is exact for x in [0.5, 2) (Sterbenz), and neither 1 + 0.04 nor
    # 1 - 0.08 (as the f64 constants) is an f64. So `>=` and `>` (`<=` and `<`) decide alike there; the cases test the
    # neighbours. tp 1.0 is met exactly (an exact 2x), so FlowTpTime's edge is tested as such.
    assert (Fraction(1) + Fraction(0.04)).denominator > 2**52 and (Fraction(1) - Fraction(0.08)).denominator > 2**53
    # tp4_T600 (TpSlTime 0.04, -0.08, cap 603 at 1041): the spec's f64 examples on the unit scenario (net = mark,
    # total = cost): 104/100 - 1 = 0.040000000000000036 is a take-profit, 92/100 - 1 = -0.07999999999999996 no stop
    # ((N - T) / T would be exactly -0.08 and stop), mark 0 stops; then the cap and the order of the checks at it.
    c = RuleCase("tp_unit", "0.04:600", DEV, T1041, T1041 + 2, UNIT, 100)
    e, cap = c.entry, c.rule.max_slots
    assert cap == 603 and c.windows == (None, 603, e + 603)
    assert c.decide(e, 104)[0] == "take_profit" and c.ret(104) == 0.040000000000000036
    assert c.decide(e, 103)[0] is None
    assert c.decide(e + 1, 92)[0] is None and c.ret(92) == -0.07999999999999996 and (92 - 100) / 100 == -0.08
    assert c.decide(e + 1, 91)[0] == "stop_loss" and c.decide(e + 2, 0)[0] == "stop_loss"
    assert c.decide(e + cap - 1, 100)[0] is None and c.decide(e + cap, 100)[0] == "time"
    assert c.decide(e + cap, 104)[0] == "take_profit" and c.decide(e + cap, 91)[0] == "stop_loss"
    assert c.decide(e + cap + 5, 100)[0] == "time" and c.decide(e + cap + 5, 92)[0] == "time"
    out.append(c)
    # The real scenarios at the entry size: the first mark that takes the profit and the one below it, the last mark
    # that stops and the one above it, mark 0 (base: exit_net(0) = 793,222 still stops).
    for name, scn, clock, t, cost in (("tp_base", BASE, DEV, T1041, 500_000_000), ("tp_optimistic", OPT, DEV, T1042, 500_000_000),
                                      ("tp_pessimistic", PESS, FW, T1053, 499_999_999)):
        c = RuleCase(name, "0.04:600", clock, t, t + 3, scn, cost)
        e = c.entry
        tp = first_mark(c, 0.04)
        stop = first_mark(c, -0.08) - 1
        while c.ret(stop + 1) <= -0.08:  # the last mark at or below -0.08
            stop += 1
        assert c.ret(tp) >= 0.04 > c.ret(tp - 1) and c.ret(stop) <= -0.08 < c.ret(stop + 1)
        assert c.decide(e, tp)[0] == "take_profit" and c.decide(e, tp - 1)[0] is None
        assert c.decide(e + 7, stop)[0] == "stop_loss" and c.decide(e + 7, stop + 1)[0] is None
        assert c.decide(e + 9, 0)[0] == "stop_loss"
        assert c.decide(e + c.rule.max_slots, tp - 1)[0] == "time" and c.decide(e + c.rule.max_slots, tp)[0] == "take_profit"
        out.append(c)
    # Above 2^53 the division must be Python's: a cost and mark where a naive f64 division decides the other way, at the
    # take-profit and at the stop-loss.
    for name, x, below in (("tp_true_div_tp", 0.04, False), ("tp_true_div_sl", -0.08, True)):
        cost, mark = straddle("0.04:600", BASE, x, below)
        c = RuleCase(name, "0.04:600", DEV, T1042, T1042 + 2, BASE, cost)
        assert ((c.ret(mark) <= x) != (naive_ret(c, mark) <= x)) if below else ((c.ret(mark) >= x) != (naive_ret(c, mark) >= x))
        c.decide(c.entry + 1, mark)
        c.decide(c.entry + 1, mark - 512), c.decide(c.entry + 1, mark + 512)
        out.append(c)

    # flow300_H6 (FlowTime, n_flow 1129, cap 81255 at 1041): the flow query only from n_flow slots held on; the window
    # [slot - n_flow, slot] with both ends in and a later slot's legs (already added, engine.py:379-380) out; 0 is an
    # outflow; farmers add 0; no stop-loss and no return computed (cost 0 makes entry_total 0 on the unit scenario).
    c = RuleCase("flow_window", "flow300_H6", DEV, T1041, T1041 + 2, UNIT, 0)
    e, n, cap = c.entry, c.rule.n_flow, c.rule.max_slots
    assert (n, cap) == (1_129, 81_255) and c.windows == (1_129, 81_255, e + 81_255) and c.ret(5) is None
    c.add([leg(T1041 - 30, 0, "buy", 40), leg(e - 1, 0, "sell", 3), leg(e, 0, "sell", 5)])  # recent + the entry slot
    assert c.decide(e, 10**9)[:2] == (None, [])
    assert c.decide(e + n - 1, 0)[:2] == (None, [])
    c.add([leg(e + n, 0, "sell", 11), leg(e + n + 1, 0, "buy", 10**6)])  # the next slot's legs are in before the decision
    assert c.decide(e + n, 0)[:2] == (None, [(e, e + n, 16)])  # lo = e (5) and hi = e + n (11) in; e - 1, e + n + 1 out
    assert c.decide(e + n + 1, 0)[0] == "outflow"  # [e + 1, e + n + 1]: 11 - 10**6
    c.add([leg(e + n + 2, 0, "sell", 10**6 - 11)])
    assert c.decide(e + n + 2, 0)[:2] == ("outflow", [(e + 2, e + n + 2, 0)])  # exactly 0 is an outflow
    c.add([leg(e + n + 3, 0, "sell", 10**7, farmer=True), leg(e + n + 3, 1, "sell", 1)])
    assert c.decide(e + n + 3, 0)[:2] == (None, [(e + 3, e + n + 3, 1)])  # the farmer adds 0
    out.append(c)
    # The deadline: at slots_held == cap the flow check runs first, so it can be an outflow instead of time.
    for name, last in (("flow_deadline_time", "sell"), ("flow_deadline_outflow", "buy")):
        c = RuleCase(name, "flow300_H6", DEV, T1042, T1042 + 2, BASE, 500_000_000)
        e, n, cap = c.entry, c.rule.n_flow, c.rule.max_slots
        assert (n, cap) == (1_120, 80_648)
        c.add([leg(e, 0, "sell", 50)])
        c.add([leg(e + cap - 5, 0, "sell", 7)])
        assert c.decide(e + cap - 1, 0)[0] is None
        c.add([leg(e + cap, 0, last, 8)])  # the deadline slot's legs, in before the decision at it
        assert c.decide(e + cap, 0)[:2] == ("time" if last == "sell" else "outflow", [(e + cap - n, e + cap, 15 if last == "sell" else -1)])
        assert c.decide(e + cap + 1, 0)[0] == ("time" if last == "sell" else "outflow")
        out.append(c)
    # The prune inside the rule: 600 legs from the entry; at n + 300 the query's i = 300 is not > 600 // 2, at n + 301
    # it prunes to 299 legs; a pruned order sent again counts again (late, in front).
    c = RuleCase("flow_prune", "flow300_H6", DEV, T1041, T1041 + 2, BASE, 500_000_000)
    e, n = c.entry, c.rule.n_flow
    c.run(e, 600, 1, 2 * SOL)
    assert c.decide(e + n + 300, 0)[2] == 600
    assert c.decide(e + n + 301, 0)[2] == 299
    c.add(run_legs(e, 1, 1, 2 * SOL))
    assert c.decide(e + n + 302, 0)[2] == 300
    c.run(e + n + 303, 3, 1, 9 * SOL)
    c.decide(e + n + 303, 0), c.decide(e + n + 400, 0)
    out.append(c)
    # fwd1053 (n 1374, cap 98951) and epoch 1064 without a measured slot time (the last measured one, 218.29 ms).
    for name, t in (("flow_fwd1053", T1053), ("flow_epoch_1064", T1064)):
        c = RuleCase(name, "flow300_H6", FW, t, t + 3, PESS, 500_000_000)
        e, n = c.entry, c.rule.n_flow
        assert (n, c.rule.max_slots) == (1_374, 98_951)
        c.add([leg(e, 0, "buy", 4), leg(e + n, 0, "sell", 3)])
        assert c.decide(e + n, 0)[0] == "outflow"
        c.add([leg(e + n + 1, 0, "sell", 2)])
        assert c.decide(e + n + 1, 0)[0] is None
        out.append(c)

    # flow300_tp100_H6 (FlowTpTime 1.0): the take-profit on every call, before n_flow, before the outflow (then no flow
    # query at all) and at the deadline; an exact 2x is a hit (base: exit_net(1,003,675,694) = 2 x 502,234,458).
    c = RuleCase("flowtp_base", "flow300_tp100_H6", DEV, T1041, T1041 + 2, BASE, 500_000_000)
    e, n, cap = c.entry, c.rule.n_flow, c.rule.max_slots
    two = 1_003_675_694
    assert c.ret(two) == 1.0 and c.ret(two - 1) < 1.0 and c.windows == (1_129, 81_255, e + 81_255)
    c.add([leg(e, 0, "buy", 9)])
    assert c.decide(e, two)[:2] == ("take_profit", []) and c.decide(e + 1, two - 1)[:2] == (None, [])
    assert c.decide(e + n, two - 1)[:2] == ("outflow", [(e, e + n, -9)])
    assert c.decide(e + n, two)[:2] == ("take_profit", [])
    assert c.decide(e + n + 1, 0)[0] == "outflow"  # no stop-loss: mark 0 only meets the flow rule
    c.add([leg(e + n + 2, 0, "sell", 100)])
    assert c.decide(e + n + 2, 0)[0] is None
    c.add([leg(e + cap - 10, 0, "sell", 100)])
    assert c.decide(e + cap, two - 1)[0] == "time" and c.decide(e + cap, two)[:2] == ("take_profit", [])
    c.add([leg(e + cap, 0, "buy", 101)])
    assert c.decide(e + cap, two - 1)[0] == "outflow" and c.decide(e + cap + 1, two)[0] == "take_profit"
    out.append(c)
    # The take-profit where a flow query would prune: no query, the legs stay; the next call without it prunes.
    c = RuleCase("flowtp_no_eager_query", "flow300_tp100_H6", DEV, T1041, T1041 + 2, BASE, 500_000_000)
    e, n = c.entry, c.rule.n_flow
    c.run(e, 600, 1, 2 * SOL)
    assert c.decide(e + n + 301, two)[1:] == ([], 600)
    assert c.decide(e + n + 301, 0)[2] == 299
    out.append(c)
    # The real scenarios at the 2x edge: the first mark with ret >= 1.0 and the one below, before n_flow.
    for name, scn, clock, t in (("flowtp_optimistic", OPT, DEV, T1042), ("flowtp_pessimistic", PESS, FW, T1053)):
        c = RuleCase(name, "flow300_tp100_H6", clock, t, t + 3, scn, 499_999_999)
        m = first_mark(c, 1.0)
        assert c.decide(c.entry + 1, m)[0] == "take_profit" and c.decide(c.entry + 1, m - 1)[0] is None
        out.append(c)
    # Above 2^53: a mark whose N / T rounds onto 2.0 without N == 2T, and one where a naive division decides otherwise.
    cost, mark = straddle("flow300_tp100_H6", BASE, 1.0, False)
    c = RuleCase("flowtp_true_div", "flow300_tp100_H6", DEV, T1041, T1041 + 2, BASE, cost)
    assert (c.ret(mark) >= 1.0) != (naive_ret(c, mark) >= 1.0)
    c.decide(c.entry + 1, mark), c.decide(c.entry + 1, mark - 512), c.decide(c.entry + 1, mark + 512)
    s = SCNS[BASE][1]
    m = first_mark(c, 1.0, 1 << 60, 1 << 63)
    assert c.ret(m) == 1.0 and s.exit_net(m) != 2 * s.entry_total(cost)
    assert c.decide(c.entry + 2, m)[0] == "take_profit"
    out.append(c)
    return out


def windows():
    """(variant, clock, t, entry, n_flow_slots, cap_slots, deadline) of the engine's rules: dev2 around 1041 -> 1042,
    fwd1053, epoch 1064 without a measured slot time, REF_MS, and the half-even ties."""
    rows = []
    at = [(DEV, T1041), (DEV, 450_143_999), (DEV, 450_144_000), (DEV, T1042), (FW, T1053), (FW, T1064), (REF, 1),
          (HALF, 10 * 432_000), (HALF, 11 * 432_000), (HALF, 12 * 432_000)]
    for spec in SPECS:
        for clock, t in at:
            name, params, rule = engine_rule(spec, t, Clock(CLOCKS[clock][1]), SCNS[BASE][1])
            w = E._windows(rule)
            row = table_row(spec)
            flow_ms = row[5] * 1000 if row[5] is not None else None
            clk = Clock(CLOCKS[clock][1])
            assert w["cap_slots"] == clk.slots(row[7], t) and w["n_flow_slots"] == (clk.slots(flow_ms, t) if flow_ms else None)
            rows.append((name, clock, t, t + 2, w["n_flow_slots"], w["cap_slots"], rule.deadline(t + 2)))
    return rows


def refusals():
    """Every name Python knows (F7_GRID and EXIT_VARIANTS), the other tp:T names the lossy rule gives, and per segment
    set whether Python's own Group refuses a flow name there (engine.py:164-166)."""
    names = [E.variant(g)[0] for g in F7_GRID] + list(E.EXIT_VARIANTS)
    assert len(names) == len(set(names)) == 36
    extra = [E.variant(s)[0] for s in ("0.05:600", "0.04:601", "0.045:600", "1.0:600")]
    segs = [None, [], ["curve"], ["pool_normal"], ["pool_reversed"], ["curve", "pool_normal"], ["curve", "pool_reversed"],
            ["pool_normal", "pool_reversed"], ["curve", "pool_normal", "pool_reversed"], ["pool_reversed", "pool_reversed"]]
    seg_rows = []
    for s in segs:
        refused = []
        for spec in SPECS:
            try:
                E.Group("G", [spec], segments=s)
                refused.append(False)
            except ValueError:
                refused.append(True)
        seg_rows.append((s, refused))
    return names, extra, seg_rows


def run_groups():
    """The groups of R1/R2 (primary K3 `--variants 0.04:600`, segments None, plus H) and of the paper start (K3 from
    k3-paper-2026-10-12.yaml plus H), from the engine's own loaders; and two Engine refusals of a name used twice."""
    import os

    here = os.path.join(os.path.dirname(os.path.abspath(E.__file__)))
    _, k3 = E.load_config(os.path.join(here, "k3-paper-2026-10-12.yaml"))
    _, h = E.load_groups(os.path.join(here, "h-arms-2026-10-16.yaml"))
    hs = E.config_groups(h)
    runs = {"r1_r2": [E.Group("K3", ["0.04:600"])] + hs,
            "paper_start": [E.Group("K3", list(k3["variants"]), farm=k3["farm"], segments=k3["segments"])] + hs}
    out = {k: [(g.name, i == 0, [n for n, _ in g.variants], sorted(g.segments) if g.segments else None) for i, g in enumerate(gs)]
           for k, gs in runs.items()}
    dups = []
    for label, primary, groups in (("across_groups", ["0.04:600"], [("H", ["0.04:600"], None)]),
                                   ("in_one_group", ["0.04:600"], [("H", ["flow300_H6", "flow300_H6"], ["pool_reversed"])])):
        gs = [E.Group(n, v, segments=s) for n, v, s in groups]
        try:
            E.Engine(None, None, None, variants=primary, groups=gs, ignore_risk=True)
            refused = False
        except ValueError as x:
            refused = "distinct names" in str(x)
        dups.append((label, [("K3", True, [E.variant(v)[0] for v in primary], None)]
                     + [(g.name, False, [n for n, _ in g.variants], sorted(g.segments) if g.segments else None) for g in gs], refused))
    return out, dups


# ----------------------------------------------------------------------------- Rust


class Some:
    def __init__(self, v):
        self.v = v


class Raw(str):
    """Rust source as it is (a bit pattern, a float's repr, an enum variant)."""


def opt(x, f=lambda v: v):
    return None if x is None else Some(f(x))


def hexbits(x):
    return Raw(f"0x{bits(x):016x}")


def rs(x):
    """A Python value as a Rust literal: None, Some, Raw, bools, ints, strs, tuples, lists (as slices)."""
    if x is None:
        return "None"
    if isinstance(x, Some):
        return f"Some({rs(x.v)})"
    if isinstance(x, Raw):
        return str(x)
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return repr(x)
    if isinstance(x, str):
        return '"' + x + '"'
    if isinstance(x, tuple):
        return "(" + ", ".join(rs(v) for v in x) + ")"
    if isinstance(x, list):
        return "&[" + ", ".join(rs(v) for v in x) + "]"
    raise TypeError(x)


def rs_leg(l):
    return rs((l.slot, *l.order[1:], Raw(l.kind.capitalize()), l.base, l.quote_net, l.farmer))


def rs_legs(legs, indent):
    pad = " " * indent
    return "&[\n" + "".join(f"{pad}    {rs_leg(l)},\n" for l in legs) + f"{pad}]"


def segs(s):
    """A segments list as GroupSpec keeps it: None when null or empty, else a flag per Segment."""
    return opt(s or None, lambda s: Raw(rs([n in s for n in ("curve", "pool_normal", "pool_reversed")])[1:]))


def array(o, name, typ, rows, doc):
    o.append(doc + f"pub(super) const {name}: [{typ}; {len(rows)}] = [\n" + "".join(f"    {rs(r)},\n" for r in rows) + "];\n\n")


def head(which, kinds):
    return (f"//! Generated by lab/core/tools/exit_cases.py {which} from the unmodified paper engine: do not edit (the\n"
            "//! generator's doc says how each value is made; backtest/tests/test_exit_table.py pins this file to its output).\n\n"
            f"use crate::legs::PoolKind::{{self, {', '.join(sorted(kinds))}}};\n\n"
            "/// A pool leg: (slot, tx, outer, inner, kind, base, quote_net, farmer).\n"
            "pub(super) type L = (u64, u32, u16, u16, PoolKind, i64, i64, bool);\n\n")


RUN_DOC = "    /// flow::tests::run_legs(slot0, n, per_slot, base0), with Python's (sum of slots, sum of bases, farmers).\n"
RUN_OP = "    Run { slot0: u64, n: u32, per_slot: u32, base0: i64, check: (u64, i128, u32) },\n"
FLOW_TYPES = ("pub(super) enum Op {\n    /// add(legs)\n    Add(&'static [L]),\n" + RUN_DOC + RUN_OP
              + "    /// flow(lo, hi) and its value.\n    Flow(u64, u64, i128),\n}\n\n"
              "/// Every slot, every sum and the seen orders (slot, tx, outer, inner), sorted.\n"
              "pub(super) type Full = (&'static [u64], &'static [i128], &'static [(u64, u32, u16, u16)]);\n\n"
              "/// The index after an op: legs kept, orders seen, the first slot, cum[0], cum[-1]; on the small cases `Full`.\n"
              "pub(super) struct State {\n    pub len: usize,\n    pub seen: usize,\n    pub first: Option<u64>,\n    pub cum0: i128,\n"
              "    pub cum_last: i128,\n    pub full: Option<Full>,\n}\n\n"
              "pub(super) struct Case {\n    pub name: &'static str,\n    pub reversed: bool,\n    pub ops: &'static [(Op, State)],\n}\n\n")
EXIT_TYPES = ("pub(super) enum Op {\n    /// rule.flows_mut().add(legs)\n    Add(&'static [L]),\n" + RUN_DOC + RUN_OP
              + "    /// decide(slot, mark): Python's net return (None: entry_total is 0), the reason, the flow queries the call made\n"
              "    /// and the legs the index keeps after it (None: no index).\n"
              "    Decide {\n        slot: u64,\n        mark: i128,\n        ret: Option<u64>,\n        reason: Option<&'static str>,\n"
              "        calls: &'static [(u64, u64, i128)],\n        kept: Option<usize>,\n    },\n}\n\n"
              "pub(super) struct Case {\n    pub name: &'static str,\n    pub variant: &'static str,\n    pub clock: usize,\n"
              "    pub t: u64,\n    pub entry: u64,\n    pub scn: usize,\n    pub cost: i128,\n"
              "    /// (n_flow_slots, cap_slots, deadline(entry))\n    pub windows: (Option<u64>, u64, Option<u64>),\n"
              "    pub ops: &'static [Op],\n}\n\n")


def rs_run(op):
    (s0, n, ps, b0), chk = op[1], op[2]
    return f"Op::Run {{ slot0: {s0}, n: {n}, per_slot: {ps}, base0: {b0}, check: {rs(chk)} }}"


def gen_flow():
    cases = flow_cases()
    kinds = {l.kind.capitalize() for c in cases for op, _ in c.ops if op[0] == "add" for l in op[1]}
    o = [head("flow", kinds), FLOW_TYPES, f"pub(super) const CASES: [Case; {len(cases)}] = [\n"]
    for c in cases:
        o.append(f"    Case {{\n        name: {rs(c.name)},\n        reversed: {rs(c.reversed)},\n        ops: &[\n")
        for op, (ln, seen, first, c0, cl, full) in c.ops:
            if op[0] == "add":
                o.append(f"            (\n                Op::Add({rs_legs(op[1], 16)}),\n")
            elif op[0] == "run":
                o.append(f"            (\n                {rs_run(op)},\n")
            else:
                o.append(f"            (\n                Op::Flow({op[1]}, {op[2]}, {op[3]}),\n")
            st = f"len: {ln}, seen: {seen}, first: {rs(opt(first))}, cum0: {c0}, cum_last: {cl}, full: {rs(opt(full))}"
            o.append(f"                State {{ {st} }},\n            ),\n")
        o.append("        ],\n    },\n")
    o.append("];\n")
    return "".join(o)


def gen_exits():
    cases = rule_cases()
    kinds = {l.kind.capitalize() for c in cases for op in c.ops if op[0] == "add" for l in op[1]}
    o = [head("exits", kinds)]
    rows = [(n, sp, k, opt(tp, hexbits), opt(sl, hexbits), opt(fs), cap, hexbits(cm))
            for n, sp, k, tp, sl, fs, cap, cm in (table_row(s) for s in SPECS)]
    o.append("/// A table row: (name, spec, kind, tp bits, sl bits, flow seconds, (cap kind, cap), the cap's ms bits).\n"
             "pub(super) type Row = (&'static str, &'static str, &'static str, Option<u64>, Option<u64>, Option<u64>, (&'static str, u64), u64);\n\n")
    array(o, "TABLE", "Row", rows, "/// paper.engine.variant(spec) and the rule `_enter` builds for it, per spec of the M1 table.\n")
    array(o, "CLOCKS", "(&str, &[(u64, f64)])", [(n, sorted(m.items())) for n, m in CLOCKS], "/// (name, epoch -> ms per slot)\n")
    o.append("/// (variant, clock, t, entry_slot, n_flow_slots, cap_slots, deadline(entry_slot)) of the rule the engine builds at t.\n"
             "pub(super) type Window = (&'static str, usize, u64, u64, Option<u64>, u64, Option<u64>);\n\n")
    array(o, "WINDOWS", "Window", [(n, c, t, e, opt(nf), cap, opt(dl)) for n, c, t, e, nf, cap, dl in windows()], "")
    o.append("/// (name, fee_per_tx, tip, failed_tx, land_prob, ata_rent, rent_mode, adverse_per_side)\n"
             "pub(super) type Scn = (&'static str, i128, i128, i128, f64, i128, &'static str, f64);\n\n")
    array(o, "SCENARIOS", "Scn", [(n, s.fee_per_tx, s.tip, s.failed_tx, s.land_prob, s.ata_rent, s.rent_mode, s.adverse_per_side)
                                  for n, s in SCNS],
          "/// unit (no costs: net = mark, total = cost below 2^53) and week2-defaults.yaml's scenarios (Scenario.from_cfg).\n")
    o.append(EXIT_TYPES)
    o.append(f"pub(super) const CASES: [Case; {len(cases)}] = [\n")
    for c in cases:
        o.append(f"    Case {{\n        name: {rs(c.name)},\n        variant: {rs(c.variant)},\n        clock: {c.clock},\n        t: {c.t},\n"
                 f"        entry: {c.entry},\n        scn: {c.scn},\n        cost: {c.cost},\n"
                 f"        windows: {rs((opt(c.windows[0]), c.windows[1], opt(c.windows[2])))},\n        ops: &[\n")
        for op in c.ops:
            if op[0] == "add":
                o.append(f"            Op::Add({rs_legs(op[1], 12)}),\n")
            elif op[0] == "run":
                o.append(f"            {rs_run(op)},\n")
            else:
                _, slot, mark, ret, reason, calls, kept = op
                d = f"slot: {slot}, mark: {mark}, ret: {rs(opt(ret, hexbits))}, reason: {rs(opt(reason))}, calls: {rs(calls)}, kept: {rs(opt(kept))}"
                o.append(f"            Op::Decide {{ {d} }},\n")
        o.append("        ],\n    },\n")
    o.append("];\n\n")
    names, extra, seg_rows = refusals()
    o.append("/// Every name Python's engine knows (F7_GRID's nine, then EXIT_VARIANTS' 27), in that order.\n"
             f"pub(super) const PYTHON_NAMES: [&str; {len(names)}] = [\n"
             + "".join("    " + ", ".join(rs(n) for n in names[i:i + 6]) + ",\n" for i in range(0, len(names), 6)) + "];\n\n")
    o.append("/// The names of other tp:T specs (0.05:600, 0.04:601, 0.045:600 (the lossy tp4_T600), 1.0:600).\n"
             f"pub(super) const TP_T_NAMES: [&str; {len(extra)}] = {rs(extra)[1:]};\n\n")
    array(o, "SEGMENTS", "(Option<[bool; 3]>, [bool; 3])", [(segs(s), Raw(rs(r)[1:])) for s, r in seg_rows],
          "/// (segments, whether Python's Group refuses each of the table's names there, in table order)\n")
    runs, dups = run_groups()
    o.append("/// A group: (name, primary, variant names, segments)\n"
             "pub(super) type G = (&'static str, bool, &'static [&'static str], Option<[bool; 3]>);\n\n")
    array(o, "RUNS", "(&str, &[G])", [(k, [(n, p, vs, segs(s)) for n, p, vs, s in gs]) for k, gs in runs.items()],
          "/// The groups of R1/R2 and of the paper start, from the engine's loaders and the real config files.\n")
    array(o, "DUPLICATES", "(&str, &[G], bool)", [(k, [(n, p, vs, segs(s)) for n, p, vs, s in gs], r) for k, gs, r in dups],
          "/// Groups with a name used twice, and whether Python's Engine refuses them (engine.py:237-239).\n")
    return "".join(o).rstrip("\n") + "\n"


def generate(which):
    return {"exits": gen_exits, "flow": gen_flow}[which]()


if __name__ == "__main__":
    print(generate(sys.argv[1]), end="")
