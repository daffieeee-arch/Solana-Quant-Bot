"""Expected results for lab/core's positions (core/src/positions.rs), M1 C2-3 of the Rust live route
(research/m1-c2-positionsim-spec-2026-10-19.md §3.5-3.7, §6.1 positions), from the unmodified backtest.replay; prints
Rust source (a data module, no tests):

  python core/tools/position_cases.py > core/src/positions/cases.rs

Run from lab/ with PYTHONPATH=. and the lab Python. backtest/tests/test_position_table.py pins the file to this output.

- The streams are synthetic. Each leg is made by the venue math (backtest.venues) from the state the previous leg
  left, so the legs chain as real ones do; its limits, trader, router flag and fee tiers are the case's. Every leg is
  also checked against lab/core's own reading of a leg (the pre state from the post state, or the post from the pre).
- A case opens the position with replay.open_pool_position or open_curve_position on its stream, as the paper engine
  calls them (engine.py:880-958), and feeds it: replay.feed_batch, or the calls the engine makes, in order:
  - 'entry': on_slot(S, the entry slot's legs, start = our insertion index in them), only when legs follow us there;
  - ('slot', s): on_slot(s, the legs of slot s);
  - ('advance', u) and 'end'.
  A flow rule's index gets every leg up to the entry slot at the open and each later slot's legs before its on_slot
  (engine.py:379-380, 906-907).
- on_complete only records the slot: the batch replay's graduation sale is the caller's in Rust (Step::Completed).
- The rules are the engine's own (exit_cases.engine_rule) on the base scenario. The slow clock (epoch 0 at 30 s a
  slot) makes tp4_T600's cap 5 slots and flow300's window 10 and cap 720; the fast one (0.4 ms) puts tp4_T600's cap
  past MAX_HOLD.
- Recorded after every call: what it returned, the completion slot, pending, pending_min (None as 0), last_slot,
  decided_for, deadline_done and done; at the end the whole Result (the marks as count, peak by engine._peak, last),
  the venue state and the holdings (trader, hist, cf). Each case asserts what it is for, so a case that stops testing
  its edge fails here.
"""

import inspect
import os
import sys
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import exit_cases as X  # noqa: E402
from backtest import replay  # noqa: E402
from backtest import venues as V  # noqa: E402
from backtest.replay import CurveLeg, PoolLeg, Tau  # noqa: E402
from backtest.strategies import Clock  # noqa: E402
from backtest.venues import Curve, Pool  # noqa: E402
from paper import engine as E  # noqa: E402

Raw, Some, opt, rs, hexbits = X.Raw, X.Some, X.opt, X.rs, X.hexbits

SOL = 1_000_000_000
SIZE = SOL // 2
SCN_NAME, SCN = X.SCNS[X.BASE]
CLOCKS = [("slow", {0: 30_000.0}), ("fast", {0: 0.4})]
SLOW, FAST = range(2)
TP4, FLOW, FLOWTP = "0.04:600", "flow300_H6", "flow300_tp100_H6"
MAYHEM = replay.MAYHEM_AGENT
MAX_HOLD = inspect.signature(replay.open_pool_position).parameters["max_slots"].default  # the engine passes none
assert MAX_HOLD == inspect.signature(replay.open_curve_position).parameters["max_slots"].default == 400_000
NONE = 0xFFFF_FFFF
IDS = {**{c: i + 1 for i, c in enumerate("ABCDEFGHIJKL")}, MAYHEM: 99, None: NONE}
MAYHEM_ID = 99
VARIANTS = {"buy": "Buy", "buy_exact_sol_in": "BuyExactSolIn", "buy_v2": "BuyV2", "buy_v3": "BuyV3",
            "buy_exact_quote_in_v2": "BuyExactQuoteInV2", "buy_exact_quote_in_v3": "BuyExactQuoteInV3",
            "multi_hop_curve_swap": "MultiHopCurveSwap", "sell": "Sell", "sell_v2": "SellV2", "sell_v3": "SellV3", "other": "Other"}

CURVE0 = Curve(1_073_000_000_000_000, 30_000_000_000, 793_100_000_000_000, 0, 95, 30)
POOL0 = Pool(200_000_000_000_000, 80 * SOL, 20, 5, 5, 0, 0)
REV0 = Pool(80 * SOL, 200_000_000_000_000, 20, 5, 5, 0, 0)  # WSOL base, the token as quote


# ----------------------------------------------------------------------------- streams


def rust_curve_pre(post, t, q, is_buy):
    """lab/core CurveLeg::pre: a buy took t tokens out and q quote in."""
    s = 1 if is_buy else -1
    return replace(post, vt=post.vt + s * t, vq=post.vq - s * q, rt=post.rt + s * t, rq=post.rq - s * q)


class Curves:
    """A curve's legs, each made by the venue math from the state the previous one left."""

    def __init__(self, state=CURVE0):
        self.s, self.legs = state, []

    def tiers(self, fee_bps, creator_bps):
        self.s = replace(self.s, fee_bps=fee_bps, creator_bps=creator_bps)

    def leg(self, slot, tx, trader, kind, amount, *, variant=None, budget=None, max_cost=None, min_tokens=None,
            min_out=None, direct=True, outer=0, inner=0):
        s = self.s
        f = {"buy_in": V.curve_buy_exact_in, "buy_out": V.curve_buy_exact_out, "sell": V.curve_sell}[kind](s, amount)
        assert f is not None, (slot, tx, kind, amount)
        variant = variant or {"buy_in": "buy_exact_sol_in", "buy_out": "buy", "sell": "sell"}[kind]
        is_buy = kind != "sell"
        assert rust_curve_pre(f.state, f.tokens, f.curve_quote, is_buy) == s
        leg = CurveLeg(slot, tx, (slot, tx, outer, inner), is_buy, f.tokens, f.curve_quote, f.fees, variant, s, f.state,
                       trader, budget, max_cost, min_tokens, min_out, direct)
        self.legs.append(leg)
        self.s = f.state
        return leg


class Pools:
    """A pool's legs, each made by the venue math from the state the previous one left."""

    def __init__(self, state=POOL0):
        self.s, self.legs = state, []

    def set(self, **kw):
        self.s = replace(self.s, **kw)

    def leg(self, slot, tx, trader, kind, amount=None, *, exact_out=False, limit_quote=None, limit_base=None, direct=True,
            lp=None, base_quote=None, outer=0, inner=0):
        s = self.s
        lp_amount = lp_supply = None
        if kind == "buy" and not exact_out:  # spend `amount` (the limit_quote of an exact-in buy)
            f = V.pool_buy_exact_in(s, amount)
            base, qn, post = f.tokens, f.trader_quote, f.state
            limit_quote = amount if limit_quote is None else limit_quote
        elif kind == "buy":
            f = V.pool_buy_exact_out(s, amount)
            base, qn, post = amount, f.trader_quote, f.state
        elif kind == "sell":
            f = V.pool_sell(s, amount)
            base, qn, post = amount, f.trader_quote, f.state
        elif kind == "boost":
            n = amount
            base, qn = s.b * (n - 1) // (s.e + n - 1), n
            post = replace(s, b=s.b - base, e=s.e + n)
        elif kind == "withdraw":
            lp_amount, lp_supply = lp
            post = V.pool_withdraw(s, lp_amount, lp_supply)
            base, qn = s.b - post.b, s.real * lp_amount // lp_supply
        else:
            assert kind == "deposit"
            base, qn = base_quote
            lp_amount, lp_supply = lp
            post = V.pool_deposit(s, base, qn)
        assert post is not None, (slot, tx, kind, amount)
        e_delta = post.e - s.e
        b = s.b + base if kind in ("sell", "deposit") else s.b - base  # lab/core PoolLeg::post
        assert post == replace(s, b=b, e=s.e + e_delta), (slot, tx, kind)
        leg = PoolLeg(slot, tx, (slot, tx, outer, inner), kind, exact_out, base, qn, s, post, trader, limit_quote, limit_base,
                      None, direct, lp_amount, lp_supply)
        self.legs.append(leg)
        self.s = post
        return leg


# ----------------------------------------------------------------------------- running a case


class Case:
    def __init__(self, name, venue, legs, t, *, spec=TP4, clock=SLOW, d=1, q=0.5, size=SIZE, tol=0.05, tau="empirical",
                 max_hold=MAX_HOLD, window_end=None, prior=None,
                 ops=("batch",)):
        assert venue in ("curve", "normal", "reversed")
        self.name, self.venue, self.legs, self.t, self.spec, self.clock = name, venue, list(legs), t, spec, clock
        self.d, self.q, self.size, self.tol, self.tau, self.max_hold = d, q, size, tol, tau, max_hold
        self.window_end, self.prior, self.ops = window_end, prior, list(ops)
        self.entry = t + d
        self.steps = []
        self.run()

    def slot_legs(self, s):
        return [l for l in self.legs if l.slot == s]

    def run(self):
        orientation = "reversed" if self.venue == "reversed" else "normal"
        _, _, rule = X.engine_rule(self.spec, self.t, Clock(CLOCKS[self.clock][1]), SCN, orientation)
        self.variant = E.variant(self.spec)[0]
        flows = getattr(rule, "flows", None)
        S, tau, rec = self.entry, Tau(self.tau), []
        oc = lambda slot, finish: (rec.append(slot), finish())[1]  # noqa: E731
        with mock.patch.object(replay, "WINDOW_END", self.window_end):
            if flows is not None:
                flows.add([l for l in self.legs if l.slot <= S])
            if self.venue == "curve":
                stream = NS(mint="M", curve=self.legs, pool_legs=[], pool_open_slot=None, complete_slot=None, orientation="normal")
                r, sim, i = replay.open_curve_position(stream, self.t, self.size, self.d, self.q, rule, self.tol, tau,
                                                       self.max_hold, on_complete=oc)
                feed = self.legs
            else:
                head = []
                if self.prior is not None:
                    ps, st = self.prior
                    head = [NS(slot=ps, tx=-1, kind="state", pre=st, post=st)]
                feed = head + self.legs
                stream = NS(mint="P", pool_legs=feed, orientation=orientation)
                r, sim, i = replay.open_pool_position(stream, self.t, self.size, self.d, self.q, rule, self.tol, tau,
                                                      self.max_hold)
            if sim is None:
                self.opened = ("completed", rec[-1]) if (r.cf_graduation and not r.skipped and not r.entry_failed) else ("no",)
                assert self.opened[0] == "no" or rec == [S]
            else:
                self.i = i - len(feed) + len(self.legs)  # in self.legs (without the engine's state leg)
                self.opened = ("open", self.i)
                for op in self.ops:
                    n = len(rec)
                    if op == "batch":
                        replay.feed_batch(sim, feed, i)
                        ret = None
                    elif op == "entry":
                        legs = self.slot_legs(S)
                        a = next((k for k, l in enumerate(self.legs) if l.slot >= S), len(self.legs))
                        ret = sim.on_slot(S, legs, start=self.i - a) if self.i - a < len(legs) else None
                    elif op == "end":
                        ret = sim.end()
                    elif op[0] == "slot":
                        legs = self.slot_legs(op[1])
                        assert legs and op[1] > S, op
                        if flows is not None:
                            flows.add(legs)
                        ret = sim.on_slot(op[1], legs)
                    else:
                        assert op[0] == "advance"
                        ret = sim.advance(op[1])
                    done = rec[-1] if len(rec) > n else None
                    self.steps.append((op, ret, done, (sim.pending, sim.pending_min or 0, sim.last_slot, sim.decided_for,
                                                       sim.deadline_done, sim.done)))
        self.r, self.sim = r, sim
        self.peak = E._peak(r)
        if sim is not None:
            self.state = sim.state
            self.hold = sorted((IDS[k], v, sim.hold.cf[k]) for k, v in sim.hold.hist.items())
            assert set(sim.hold.hist) == set(sim.hold.cf)


def pre_state(case):
    return replace(case.prior[1])


# ----------------------------------------------------------------------------- the cases


def pool_cases():
    out = []
    t = 1_000

    # take_profit: buys after our entry lift the price; the exit at the decision + d, in the batch replay
    p = Pools()
    p.leg(990, 0, "A", "buy", 3 * SOL)
    p.leg(1_000, 1, "B", "sell", 10**12)
    p.leg(1_001, 0, "C", "buy", SOL)
    p.leg(1_001, 2, "D", "buy", SOL)
    p.leg(1_002, 0, "E", "buy", 4 * SOL)
    p.leg(1_003, 0, "A", "buy", SOL)
    p.leg(1_004, 0, "F", "sell", 10**12)
    c = Case("pool_take_profit_batch", "normal", p.legs, t)
    assert c.r.exit_reason == "take_profit" and c.r.exit_slot == c.r.exit_decision_slot + 1
    out.append(c)

    # stop_loss through the explicit calls; the pending exit fills at its slot at our insertion point (q 0.5 of 3 txs:
    # the second), so the slot's first tx runs before it
    p = Pools()
    p.leg(995, 0, "A", "buy", 2 * SOL)
    for k, who in enumerate("BCD"):
        p.leg(1_001, k, who, "buy", SOL // 2)
    p.leg(1_002, 0, "A", "sell", 10**13)
    p.leg(1_003, 0, "E", "sell", 10**12)
    p.leg(1_003, 3, "F", "sell", 10**12)
    p.leg(1_003, 4, "G", "sell", 10**12)
    p.leg(1_005, 0, "B", "sell", 10**12)
    c = Case("pool_stop_loss_in_slot_at_q", "normal", p.legs, t,
             ops=["entry", ("slot", 1_002), ("slot", 1_003), ("slot", 1_005), "end"])
    assert c.r.exit_reason == "stop_loss" and c.r.exit_slot == 1_003 and c.r.exit_decision_slot == 1_002
    out.append(c)

    # time in a quiet gap: advance passes the deadline (entry + 5), the exit at deadline + d
    p = Pools()
    p.leg(1_000, 0, "A", "buy", SOL)
    p.leg(1_001, 0, "B", "buy", SOL // 10)
    p.leg(1_001, 1, "C", "buy", SOL // 10)
    p.leg(1_030, 0, "D", "buy", SOL // 10)
    c = Case("pool_time_quiet_gap", "normal", p.legs, t, q=0.0,
             ops=["entry", ("advance", 1_003), ("advance", 1_004), ("advance", 1_010), "end"])
    assert c.r.exit_reason == "time" and c.r.exit_slot == 1_007 and c.r.exit_decision_slot == 1_006
    out.append(c)

    # a failed exit and its retry: the decision's mark minus 5 % is not met at the fill (a big sell before our
    # insertion), pending clears; the next slot change decides again
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_002, 0, "B", "buy", 6 * SOL)
    p.leg(1_003, 0, "C", "sell", 4 * 10**13)
    p.leg(1_003, 1, "D", "buy", SOL // 10)
    p.leg(1_004, 0, "E", "buy", SOL // 10)
    p.leg(1_006, 0, "F", "buy", 2 * SOL)
    c = Case("pool_failed_exit_retry", "normal", p.legs, t, q=0.99,
             ops=["entry", ("slot", 1_002), ("slot", 1_003), ("slot", 1_004), ("slot", 1_006), "end"])
    assert c.r.exit_attempts == 2 and c.steps[2][3][0] is None
    out.append(c)

    # our buy moves the price, so historical legs re-run worse: direct limits (their own) and router legs (tau), on
    # every pool kind of trade
    for tau_mode in ("empirical", "zero", "inf"):
        p = Pools()
        p.leg(999, 0, "A", "buy", SOL)
        l = p.leg(1_001, 0, "B", "buy", SOL)  # direct exact-in: its limit_base just at the logged tokens
        l.limit_base = l.base
        l = p.leg(1_001, 1, "C", "buy", 10**12, exact_out=True)  # direct exact-out: limit_quote at the logged quote
        l.limit_quote = l.quote_net
        p.leg(1_001, 2, "D", "buy", SOL, direct=False)  # router exact-in
        p.leg(1_001, 3, "E", "buy", 10**12, exact_out=True, direct=False)  # router exact-out
        p.leg(1_002, 0, "F", "buy", 2 * SOL)
        l = p.leg(1_003, 0, "F", "sell", 10**12)  # direct sell, limit at the logged quote
        l.limit_quote = l.quote_net
        p.leg(1_003, 1, "G", "sell", 10**12, direct=False)  # router sell
        p.leg(1_003, 2, "H", "sell", 10**12, limit_quote=None)  # direct, no limit: no check
        p.leg(1_004, 0, "I", "buy", SOL // 10, limit_base=None)  # direct exact-in without limit_base
        l = p.leg(1_006, 0, "K", "buy", 40 * SOL)  # reverted: a limit_base no fill can meet ...
        l.limit_base = 2 * l.base
        p.leg(1_006, 1, "L", "sell", 4 * 10**11, direct=False)  # ... so these small router sells (size bucket 0) re-run
        p.leg(1_006, 2, "J", "sell", 4 * 10**11, direct=False)  # far below their log
        c = Case(f"pool_reverts_tau_{tau_mode}", "normal", p.legs, 999, tau=tau_mode, spec=FLOW,
                 ops=["entry", ("slot", 1_001), ("slot", 1_002), ("slot", 1_003), ("slot", 1_004), ("slot", 1_006), "end"])
        assert c.r.reverted_direct >= 3 and (c.r.reverted_router >= 1) == (tau_mode != "inf")
        assert tau_mode == "inf" or c.r.reverted_router >= 1
        out.append(c)

    # inexact holdings (a normal pool): B's re-run buy gets fewer tokens, so B's sell of everything is cut by the
    # difference; C's reverted buy books hist t and cf 0, so C's sell is dropped
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_001, 0, "B", "buy", 3 * SOL)
    l = p.leg(1_001, 1, "C", "buy", SOL)
    l.limit_base = l.base
    b_tokens = p.legs[1].base
    p.leg(1_002, 0, "B", "sell", b_tokens)
    p.leg(1_002, 1, "C", "sell", l.base)
    p.leg(1_003, 0, None, "buy", SOL // 2)
    p.leg(1_003, 1, None, "sell", 10**12)
    c = Case("pool_inexact_holdings", "normal", p.legs, 999, spec=FLOW,
             ops=["entry", ("slot", 1_001), ("slot", 1_002), ("slot", 1_003), "end"])
    assert c.r.sells_scaled >= 1 and c.r.sells_dropped >= 1
    assert (IDS["C"], 0, 0) in c.hold  # its reverted buy (+t, 0), then its dropped sell (-t, 0)
    out.append(c)

    # a multi-leg transaction rolled back: a buy that runs, then a sell over its own limit; the whole tx goes, the
    # holdings come back, the diag too (its sell was scaled before failing), and hist is booked with cf 0
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_001, 0, "B", "buy", 2 * SOL)
    p.leg(1_001, 1, "B", "buy", SOL // 3)  # a later B buy in another tx
    p.leg(1_002, 0, "C", "buy", SOL, inner=0)
    l = p.leg(1_002, 0, "B", "sell", 10**13, inner=1)  # same tx: B sells more than it got (scaled), over its limit
    l.limit_quote = 2 * l.quote_net
    p.leg(1_002, 1, "D", "boost", SOL // 5)
    p.leg(1_002, 2, "E", "boost", 1)  # a boost of 1: no-op
    p.leg(1_003, 0, "C", "sell", 10**12)
    c = Case("pool_multi_leg_rollback", "normal", p.legs, 999, spec=FLOW,
             ops=["entry", ("slot", 1_001), ("slot", 1_002), ("slot", 1_003), "end"])
    assert c.r.reverted_direct == 1
    out.append(c)

    # boost, deposit and withdraw re-run; a tx of a boost and a failing buy drops the boost's state too
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_001, 0, "B", "deposit", base_quote=(10**13, 4 * SOL), lp=(1_000, 100_000))
    p.leg(1_001, 1, "C", "withdraw", lp=(2_000, 100_000))
    p.leg(1_002, 0, "D", "boost", SOL, inner=0)
    l = p.leg(1_002, 0, "D", "buy", SOL, inner=1)
    l.limit_base = l.base
    p.leg(1_002, 1, "E", "boost", SOL // 2)
    c = Case("pool_boost_deposit_withdraw", "normal", p.legs, 999, spec=FLOW,
             ops=["entry", ("slot", 1_001), ("slot", 1_002), "end"])
    assert c.r.reverted_direct == 1
    out.append(c)

    # the vault cap: virtual quote, then a withdraw that leaves less real quote than our sale's gross
    p = Pools(replace(POOL0, e=POOL0.e + 60 * SOL, virt=60 * SOL))
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_002, 0, "B", "withdraw", lp=(99_900, 100_000))
    c = Case("pool_vault_cap", "normal", p.legs, t, spec=FLOW, ops=["entry", ("slot", 1_002), ("advance", 2_000), "end"])
    assert c.r.vault_capped and c.r.exit_slot is not None
    out.append(c)

    # fee tiers come from each leg's pre state
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.set(lp_bps=25, protocol_bps=5, creator_bps=30, cashback_bps=10)
    p.leg(1_001, 0, "B", "buy", 2 * SOL, direct=False)
    p.leg(1_001, 1, "C", "sell", 10**12, direct=False)
    p.set(virt=0)
    c = Case("pool_fee_tiers", "normal", p.legs, 999, spec=FLOW, ops=["entry", ("slot", 1_001), "end"])
    assert c.state.cashback_bps == 10
    out.append(c)

    # the entry fails: a big buy in the entry slot before our insertion (q 0.99)
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_001, 0, "B", "buy", 20 * SOL)
    p.leg(1_001, 1, "C", "buy", SOL)
    c = Case("pool_entry_slippage", "normal", p.legs, t, q=0.99)
    assert c.r.entry_failed and c.r.exit_reason == "entry_slippage"
    out.append(c)
    # ... a tolerance of 0.9 lets it through
    out.append(Case("pool_entry_slippage_tolerated", "normal", p.legs, t, q=0.99, tol=0.9))
    assert out[-1].opened[0] == "open"

    # no state; a pool where our buy gets no fill (expected None)
    c = Case("pool_no_state", "normal", [], t)
    assert c.r.skipped and c.r.exit_reason == "no_pool_state"
    out.append(c)
    p = Pools(Pool(10, 10**15, 20, 5, 5))
    p.leg(999, 0, "A", "deposit", base_quote=(1, 1), lp=(1, 10))
    c = Case("pool_no_fill_is_a_failed_entry", "normal", p.legs, t)
    assert c.r.entry_failed
    out.append(c)

    # the engine's state leg (Recent empty): with the entry slot's legs, and a quiet entry
    st = replace(POOL0, b=POOL0.b - 10**12, e=POOL0.e + SOL)
    p = Pools(st)
    p.leg(1_001, 0, "A", "buy", SOL)
    p.leg(1_001, 1, "B", "buy", SOL)
    p.leg(1_003, 0, "C", "buy", 5 * SOL)
    out.append(Case("pool_prior_state_leg", "normal", p.legs, t, prior=(980, st), ops=["entry", ("slot", 1_003), "end"]))
    out.append(Case("pool_prior_state_leg_batch", "normal", p.legs, t, prior=(980, st)))
    out.append(Case("pool_prior_state_only", "normal", p.legs[2:], t, prior=(980, st)))
    out.append(Case("pool_slot_legs_only", "normal", p.legs, t))  # s_dec is the entry slot's first pre

    # window end: an entry at or after it; end() with the deadline beyond it; a pending exit at or after it
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_001, 0, "B", "buy", SOL // 10)
    c = Case("pool_after_window_end", "normal", p.legs, t, window_end=1_001)
    assert c.r.exit_reason == "after_window_end"
    out.append(c)
    c = Case("pool_window_end_beyond", "normal", p.legs, t, window_end=1_005, ops=["entry", "end"])
    assert c.r.exit_reason == "window_end" and c.r.exit_slot == 1_004
    out.append(c)
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_005, 0, "B", "withdraw", lp=(100_000, 100_000))
    p.leg(1_790, 0, "C", "deposit", base_quote=(POOL0.b, POOL0.e), lp=(100_000, 100_000))
    c = Case("pool_window_end_pending", "normal", p.legs, t, d=3, window_end=1_792, spec=FLOW,
             ops=[("slot", 1_005), ("advance", 1_760), ("slot", 1_790), "end"])
    assert c.r.exit_reason == "window_end" and c.r.exit_slot == 1_791 and c.r.exit_decision_slot == 1_790
    assert c.sim.deadline_done
    out.append(c)

    # end of data: a future deadline decided by end(), filled after the data (deadline + d)
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_001, 0, "B", "buy", SOL // 10)
    c = Case("pool_end_future_deadline", "normal", p.legs, t, d=2, ops=["entry", "end"])
    assert c.r.exit_reason == "time" and c.r.exit_slot == c.r.exit_decision_slot + 2 > 1_002
    out.append(c)

    # end(): the deadline_done path (replay.py:532-539) — advance takes the deadline after the last event, its exit
    # fails on a drained pool, end() closes with no attempt more and exit_slot None
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_002, 0, "B", "withdraw", lp=(100_000, 100_000))
    c = Case("pool_end_deadline_done", "normal", p.legs, t, spec=FLOW, ops=["entry", ("slot", 1_002), ("advance", 1_800), "end"])
    assert c.r.exit_slot is None and c.r.exit_attempts == 1 and c.sim.deadline_done
    out.append(c)
    # ... and end() deciding the deadline itself, its last try failing (exit_slot None, one attempt)
    c = Case("pool_end_decides_deadline_fails", "normal", p.legs, t, spec=FLOW, ops=["entry", ("slot", 1_002), "end"])
    assert c.r.exit_slot is None and c.r.exit_attempts == 1 and not c.sim.deadline_done
    out.append(c)

    # MAX_HOLD: the engine's 400,000 on the fast clock (tp4_T600's cap beyond it); a small max_slots too
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    for k in range(1, 5):
        p.leg(1_001 + 100_000 * k, 0, "B", "buy", SOL // 100)
    p.leg(401_002, 0, "C", "buy", SOL // 100)
    p.leg(401_002, 1, "D", "buy", SOL // 100)
    c = Case("pool_max_hold", "normal", p.legs, t, clock=FAST)
    assert c.r.exit_reason == "time" and c.r.exit_slot > 401_002
    out.append(c)
    c = Case("pool_max_hold_small", "normal", p.legs, t, clock=FAST, max_hold=150_000,
             ops=["entry", ("slot", 101_001), ("slot", 201_001), ("slot", 301_001), "end"])
    out.append(c)

    # flow rules: outflow; flowtp's take_profit before n_flow; time at the cap with inflow throughout
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    for k in range(1, 13):
        p.leg(1_001 + k, 0, "B", "sell", 10**11)
    c = Case("pool_flow_outflow", "normal", p.legs, t, spec=FLOW, ops=["entry"] + [("slot", 1_001 + k) for k in range(1, 13)] + ["end"])
    assert c.r.exit_reason == "outflow"
    out.append(c)
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_002, 0, "B", "buy", 40 * SOL)
    p.leg(1_003, 0, "C", "buy", 40 * SOL)
    c = Case("pool_flowtp_take_profit", "normal", p.legs, t, spec=FLOWTP, ops=["entry", ("slot", 1_002), ("slot", 1_003), "end"])
    assert c.r.exit_reason == "take_profit"
    out.append(c)
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    slots = [1_001 + 100 * k for k in range(1, 8)] + [1_715, 1_730]  # each decision's window holds its own slot's buy
    for s in slots:
        p.leg(s, 0, "B", "buy", SOL // 50)
    c = Case("pool_flow_time", "normal", p.legs, t, spec=FLOW, ops=["entry"] + [("slot", s) for s in slots] + ["end"])
    assert c.r.exit_reason == "time"
    out.append(c)

    # a window end below a decision advance already made: the pending exit moves to W - 1 and so does its decision slot
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    c = Case("pool_window_end_after_advance", "normal", p.legs, t, d=3, window_end=1_006, ops=[("advance", 1_008), "end"])
    assert c.r.exit_reason == "window_end" and c.r.exit_decision_slot == 1_005 and c.r.exit_slot == 1_005
    out.append(c)
    # end() keeps a failed exit's reason: the last slot decided, the deadline passed, the try failed (a drained pool)
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    p.leg(1_002, 0, "B", "withdraw", lp=(100_000, 100_000))
    p.leg(1_007, 0, "C", "deposit", base_quote=(0, 0), lp=(0, 1))
    c = Case("pool_end_keeps_a_stale_reason", "normal", p.legs, t, ops=[("slot", 1_002), ("slot", 1_007), ("advance", 1_020), "end"])
    assert c.r.exit_reason == "stop_loss" and c.r.exit_decision_slot == 1_007 and c.r.exit_slot is None
    out.append(c)
    # a zero tolerance passes a fill equal to the expected one (tokens < expected * 1.0 is False)
    p = Pools()
    p.leg(999, 0, "A", "buy", SOL)
    c = Case("pool_entry_tol_zero_equal", "normal", p.legs, t, tol=0.0)
    assert c.opened[0] == "open"
    out.append(c)
    return out


def reversed_cases():
    out = []
    t = 2_000
    # a token buy in a reversed pool is a pool sell of WSOL; holdings are off, so no sell is ever cut
    p = Pools(REV0)
    p.leg(1_999, 0, "A", "sell", SOL)
    p.leg(2_001, 0, "B", "sell", 3 * SOL)
    p.leg(2_002, 0, "C", "sell", 4 * SOL)
    p.leg(2_002, 1, "B", "buy", SOL // 2, exact_out=True, direct=False)  # a token sell for exactly 0.5 SOL
    c = Case("rev_take_profit", "reversed", p.legs, t)
    assert c.r.exit_reason == "take_profit"
    out.append(c)
    p = Pools(REV0)
    p.leg(1_999, 0, "A", "sell", SOL)
    for k in range(1, 14):
        p.leg(2_001 + k, 0, "B", "buy", 10**12)  # token sells: buys of WSOL, spending 1e12 token units
    l = p.leg(2_016, 0, "C", "buy", 10**12)
    l.limit_base = l.base
    p.leg(2_016, 1, "D", "sell", 2 * SOL)  # a WSOL sell by a trader with no history: holdings are off, never cut
    c = Case("rev_flow_outflow", "reversed", p.legs, t, spec=FLOW,
             ops=["entry"] + [("slot", 2_001 + k) for k in range(1, 14)] + [("slot", 2_016), "end"])
    assert c.r.exit_reason == "outflow" and c.r.sells_scaled == 0
    out.append(c)
    p = Pools(REV0)
    p.leg(1_999, 0, "A", "sell", SOL)
    slots = [2_001 + 100 * k for k in range(1, 8)] + [2_715, 2_730]
    for s in slots:
        p.leg(s, 0, "B", "sell", SOL // 50)
    c = Case("rev_flowtp_time", "reversed", p.legs, t, spec=FLOWTP, q=0.0, ops=["entry"] + [("slot", s) for s in slots] + ["end"])
    assert c.r.exit_reason == "time"
    out.append(c)
    return out


def curve_cases():
    out = []
    t = 3_000

    # take_profit with seeded holdings; several txs a slot, several legs a tx
    k = Curves()
    k.leg(2_990, 0, "A", "buy_in", 2 * SOL)
    k.leg(2_995, 0, "B", "buy_in", SOL)
    k.leg(2_995, 1, "A", "sell", 10**13)
    k.leg(3_000, 0, "C", "buy_in", SOL)
    k.leg(3_001, 0, "D", "buy_in", SOL // 2)
    k.leg(3_001, 1, "E", "buy_in", SOL // 2, outer=0)
    k.leg(3_001, 1, "E", "buy_in", SOL // 2, outer=1)
    k.leg(3_002, 0, "F", "buy_in", 5 * SOL)
    k.leg(3_003, 0, "B", "sell", 10**13)
    c = Case("curve_take_profit_batch", "curve", k.legs, t)
    assert c.r.exit_reason == "take_profit"
    out.append(c)
    c = Case("curve_take_profit_calls", "curve", k.legs, t, q=0.0, ops=["entry", ("slot", 3_002), ("slot", 3_003), "end"])
    out.append(c)

    # exact holdings: A's re-run buy gets fewer tokens, so A's sell of everything gives cf (scaled); D's buy over its
    # min_tokens reverts (hist t, cf 0), so D's sell is dropped; a pre-entry holder G sells part (a * c // h)
    k = Curves()
    k.leg(2_990, 0, "G", "buy_in", SOL)
    k.leg(3_001, 0, "A", "buy_in", 2 * SOL)
    l = k.leg(3_001, 1, "D", "buy_in", SOL)
    l.min_tokens = l.t
    k.leg(3_002, 0, "A", "sell", k.legs[1].t)
    k.leg(3_002, 1, "D", "sell", l.t)
    k.leg(3_003, 0, "G", "sell", k.legs[0].t // 3)
    c = Case("curve_exact_holdings", "curve", k.legs, 2_999, spec=TP4, tau="inf",
             ops=["entry", ("slot", 3_001), ("slot", 3_002), ("slot", 3_003), "end"])
    assert c.r.sells_scaled >= 1 and c.r.sells_dropped >= 1 and c.r.reverted_direct == 1
    out.append(c)

    # reverts: exact-in min_tokens, exact-out max_cost, sell min_out (direct); the same as router legs (tau); a buy
    # without its budget (q + fee); a direct leg without its argument falls back to tau
    for tau_mode in ("empirical", "zero"):
        k = Curves()
        k.leg(2_999, 0, "A", "buy_in", 3 * SOL)
        l = k.leg(3_001, 0, "B", "buy_in", SOL // 10, budget=SOL // 10)
        l.min_tokens = l.t
        l = k.leg(3_001, 1, "C", "buy_out", 10**12)
        l.max_cost = l.q + l.fee
        k.leg(3_001, 2, "D", "buy_in", SOL // 10, direct=False)
        k.leg(3_001, 3, "E", "buy_out", 10**12, direct=False)
        k.leg(3_001, 4, "F", "buy_in", SOL // 10)  # direct, no budget nor min_tokens: q + fee, tau
        l = k.leg(3_002, 0, "A", "sell", 10**13)
        l.min_out = l.q - l.fee
        k.leg(3_002, 1, "A", "sell", 10**13, direct=False)
        k.leg(3_002, 2, "A", "sell", 10**13, variant="sell_v2")  # direct without min_out: tau
        k.leg(3_002, 3, "B", "buy_in", SOL // 10, variant="buy_exact_quote_in_v2", budget=SOL // 10)
        k.leg(3_002, 4, "B", "buy_in", SOL // 10, variant="other")
        l = k.leg(3_003, 0, "K", "buy_in", 10 * SOL)  # reverted: a min_tokens no fill can meet ...
        l.min_tokens = 2 * l.t
        k.leg(3_003, 1, "L", "sell", 10**14, direct=False)  # ... so this router sell re-runs far below its log
        c = Case(f"curve_reverts_tau_{tau_mode}", "curve", k.legs, 2_999, tau=tau_mode,
                 ops=["entry", ("slot", 3_001), ("slot", 3_002), ("slot", 3_003), "end"])
        assert c.r.reverted_direct >= 3 and c.r.reverted_router >= 1
        out.append(c)

    # the Mayhem agent: alone, and in a tx with another leg (the whole tx goes)
    k = Curves()
    k.leg(2_999, 0, "A", "buy_in", SOL)
    k.leg(3_001, 0, MAYHEM, "buy_in", SOL)
    k.leg(3_001, 1, "B", "buy_in", SOL, inner=0)
    k.leg(3_001, 1, MAYHEM, "sell", 10**12, inner=1)
    k.leg(3_002, 0, "C", "buy_in", SOL)
    c = Case("curve_mayhem", "curve", k.legs, 2_999, ops=["entry", ("slot", 3_001), ("slot", 3_002), "end"])
    assert c.r.mayhem_reverted == 2
    out.append(c)

    # completion during the hold: another trader buys the rest; and mid-tx (the completing buy and a leg after it in
    # one tx: the next leg fails on the complete curve, so the whole tx reverts and nothing completes)
    near = replace(CURVE0, vt=300_000_000_000_000, vq=107_000_000_000, rt=20_100_000_000_000, rq=77_000_000_000)
    k = Curves(near)
    k.leg(2_999, 0, "A", "buy_in", SOL // 10)
    k.leg(3_002, 0, "B", "buy_out", k.s.rt - 10**12, direct=False)  # left 1e12 tokens; after our buy it takes the rest
    k.leg(3_003, 0, "C", "buy_in", SOL)
    c = Case("curve_completes_mid_hold", "curve", k.legs, t, tau="inf", ops=["entry", ("slot", 3_002), ("slot", 3_003), "end"])
    assert c.steps[1][2] == 3_002
    out.append(c)
    k = Curves(near)
    k.leg(2_999, 0, "A", "buy_in", SOL // 10)
    k.leg(3_002, 0, "B", "buy_out", k.s.rt - 10**12, direct=False, inner=0)
    k.leg(3_002, 0, "D", "sell", 10**9, inner=1)
    k.leg(3_003, 0, "C", "sell", 10**12)
    c = Case("curve_completes_mid_tx_reverts", "curve", k.legs, t, tau="inf", spec=TP4,
             ops=["entry", ("slot", 3_002), ("slot", 3_003), "end"])
    assert all(s[2] is None for s in c.steps) and c.r.reverted_direct == 1
    out.append(c)
    # our own buy completes the curve
    k = Curves(replace(near, rt=1_000_000_000_000))
    k.leg(2_999, 0, "A", "buy_in", SOL // 1_000)
    c = Case("curve_our_buy_completes", "curve", k.legs, t)
    assert c.opened[0] == "completed" and c.r.cf_graduation
    out.append(c)

    # skips: no history; a complete curve at the decision; no fill for a tiny size; complete at our insertion; slippage
    out.append(Case("curve_no_history", "curve", [], t))
    k = Curves(replace(near, rt=1_000_000_000))
    k.leg(2_999, 0, "A", "buy_out", 1_000_000_000)
    c = Case("curve_complete_at_decision", "curve", k.legs, t)
    assert c.r.exit_reason == "no_curve_state"
    out.append(c)
    k = Curves()
    k.leg(2_999, 0, "A", "buy_in", SOL)
    c = Case("curve_no_fill", "curve", k.legs, t, size=1)
    assert c.r.exit_reason == "no_fill"
    out.append(c)
    k = Curves(replace(near, rt=5_000_000_000_000))
    k.leg(2_999, 0, "A", "buy_in", SOL // 1_000)
    k.leg(3_001, 0, "C", "buy_in", SOL // 100)
    k.leg(3_001, 1, "B", "buy_out", k.s.rt)  # the slot's last tx completes; q 1.0 puts us after it
    c = Case("curve_complete_at_insertion", "curve", k.legs, t, q=1.0)
    assert c.r.exit_reason == "curve_complete"
    out.append(c)
    k = Curves()
    k.leg(2_999, 0, "A", "buy_in", SOL)
    k.leg(3_001, 0, "B", "buy_in", 10 * SOL)
    k.leg(3_001, 1, "C", "buy_in", SOL)
    c = Case("curve_entry_slippage", "curve", k.legs, t, q=0.99)
    assert c.r.entry_failed
    out.append(c)
    c = Case("curve_after_window_end", "curve", k.legs, t, window_end=3_001)
    out.append(c)
    c = Case("curve_entry_tol_zero_equal", "curve", k.legs[:1], t, tol=0.0)
    assert c.opened[0] == "open"
    out.append(c)

    # a quiet entry (no legs in the entry slot), d 2, fee tiers changing
    k = Curves()
    k.leg(2_980, 0, "A", "buy_in", SOL)
    k.leg(2_999, 0, "B", "buy_in", SOL)
    k.tiers(100, 5)
    k.leg(3_004, 0, "C", "buy_in", 3 * SOL, direct=False)
    k.leg(3_005, 0, "A", "sell", 10**13)
    c = Case("curve_quiet_entry_d2", "curve", k.legs, t, d=2, ops=[("advance", 3_002), ("slot", 3_004), ("slot", 3_005), "end"])
    out.append(c)
    out.append(Case("curve_quiet_entry_d2_batch", "curve", k.legs, t, d=2))

    # q edges: three txs with two legs each in the entry slot, q 0.0, 0.34 (k 1), 0.67 (k 2), 0.99
    k = Curves()
    k.leg(2_999, 0, "A", "buy_in", SOL)
    for tx, who in ((3, "B"), (5, "C"), (9, "D")):
        k.leg(3_001, tx, who, "buy_in", SOL // 4, inner=0)
        k.leg(3_001, tx, who, "sell", 10**11, inner=1)
    k.leg(3_002, 0, "E", "buy_in", SOL)
    for q in (0.0, 0.34, 0.67, 0.99):
        out.append(Case(f"curve_q_{q}", "curve", k.legs, t, q=q))
    return out


# ----------------------------------------------------------------------------- single re-runs


class Rerun:
    """One historical leg re-run on a state with the trader's holdings, by replay._rerun_curve / _rerun_pool."""

    def __init__(self, label, state, leg, tau, exact=True, enabled=True, hold=None, expect=None):
        self.label, self.state, self.leg, self.tau, self.exact, self.enabled, self.hold = label, state, leg, tau, exact, enabled, hold
        h = replay.Holdings(exact, enabled)
        if hold is not None:
            h.hist[leg.trader], h.cf[leg.trader] = hold
        diag = {"sells_dropped": 0, "sells_scaled": 0}
        f = replay._rerun_curve if isinstance(leg, CurveLeg) else replay._rerun_pool
        self.want = f(state, leg, Tau(tau), h, diag)
        self.diag = (diag["sells_dropped"], diag["sells_scaled"])
        assert (h.hist.get(leg.trader), h.cf.get(leg.trader)) == (hold or (None, None))  # a re-run only reads them
        if expect is not None:
            assert (self.want is not None) == (expect == "pass"), (label, self.want)


def vcurve(order, trader, is_buy, t, q, fee, post, variant, args=(None, None, None, None), direct=True):
    """A curve leg as lab/core reads it: the pre state from the post state (only its fee tiers are re-run)."""
    return CurveLeg(order[0], order[1], order, is_buy, t, q, fee, variant, rust_curve_pre(post, t, q, is_buy), post, trader,
                    *args, direct)


def vpool(order, trader, kind, base, qn, pre, *, exact_out=False, e_delta=0, x0=None, x1=None, direct=True):
    """A pool leg as lab/core reads it: the post state from the pre state (only its tiers and virt are re-run)."""
    b = pre.b + base if kind in ("sell", "deposit") else pre.b - base
    post = replace(pre, b=b, e=pre.e + e_delta)
    trade, lp = kind in ("buy", "sell"), kind in ("withdraw", "deposit")
    return PoolLeg(order[0], order[1], order, kind, exact_out, base, qn, pre, post, trader, x0 if trade else None,
                   x1 if trade else None, None, direct, x0 if lp else None, x1 if lp else None)


BUYS = ["buy", "buy_exact_sol_in", "buy_v2", "buy_v3", "buy_exact_quote_in_v2", "buy_exact_quote_in_v3",
        "multi_hop_curve_swap", "other"]
SELLS = ["sell", "sell_v2", "sell_v3", "other"]


def near(rng, x, lo, hi):
    return max(0, int(x * rng.uniform(lo, hi)))


def random_reruns(rng, n):
    out = []
    for k in range(n):
        tau = rng.choice(["empirical"] * 3 + ["zero", "inf"])
        order = (6_000 + k, rng.randrange(0, 3_000), rng.randrange(0, 5), rng.randrange(0, 5))
        if k % 2 == 0:  # curve
            vt = rng.randrange(10**14, 11 * 10**14)
            st = Curve(vt, rng.randrange(10**9, 2 * 10**11), 0 if rng.random() < 0.05 else rng.randrange(1, vt - 10**13),
                       rng.randrange(0, 10**11), rng.choice([0, 95, 100]), rng.choice([0, 5, 30]))
            is_buy, big = rng.random() < 0.5, rng.random() < 0.15
            t = rng.randrange(1, 10**17 if big else 5 * 10**13)
            q = rng.randrange(1, 10**18 if big else 3 * 10**10)
            fee = rng.randrange(0, q // 20 + 1)
            arg = lambda p, x, lo, hi: near(rng, x, lo, hi) if rng.random() < p else None  # noqa: E731
            args = (arg(0.5, q + fee, 0.9, 1.1), arg(0.5, q + fee, 0.95, 1.2), arg(0.5, t, 0.8, 1.0), arg(0.5, q - fee, 0.8, 1.0))
            post = Curve(rng.randrange(0, 2**62), rng.randrange(0, 2**62), rng.randrange(0, 2**62), rng.randrange(0, 2**62),
                         rng.choice([0, 95, 100, 125]), rng.choice([0, 5, 30, 50]))
            trader = MAYHEM if rng.random() < 0.05 else rng.choice("AB")
            hold = None if rng.random() < 0.3 else (rng.randrange(-t, 2 * t + 1), rng.randrange(-t, 2 * t + 1))
            leg = vcurve(order, trader, is_buy, t, q, fee, post, rng.choice(BUYS if is_buy else SELLS), args, rng.random() < 0.6)
            out.append(Rerun(f"random_curve_{k}", st, leg, tau, hold=hold))
        else:  # pool
            b, e = rng.randrange(10**9, 10**18), rng.randrange(10**8, 10**14)
            st = Pool(b if rng.random() > 0.05 else 0, e, rng.randrange(0, 30), rng.randrange(0, 10), rng.randrange(0, 40),
                      rng.randrange(0, 20), 0 if rng.random() < 0.6 else rng.randrange(0, e))
            pre = Pool(rng.randrange(10**9, 10**18), rng.randrange(10**8, 10**14), rng.randrange(0, 30), rng.randrange(0, 10),
                       rng.randrange(0, 40), rng.randrange(0, 20), 0 if rng.random() < 0.5 else rng.randrange(0, 10**12))
            kind = rng.choice(["buy", "buy", "sell", "sell", "boost", "withdraw", "deposit"])
            exact_out = kind == "buy" and rng.random() < 0.4
            base = rng.randrange(1, b // 5 + 2) if rng.random() < 0.9 else rng.randrange(b, 2 * b)
            qn = rng.randrange(0, e // 5 + 2) if kind != "boost" else rng.choice([0, 1, 2, rng.randrange(3, e)])
            x0 = x1 = None
            if kind == "buy":
                x0 = near(rng, qn, 1.0, 1.2) if (not exact_out or rng.random() < 0.5) else None
                x1 = near(rng, base, 0.8, 1.0) if rng.random() < 0.5 else None
            elif kind == "sell":
                x0 = near(rng, qn, 0.8, 1.0) if rng.random() < 0.5 else None
            elif kind in ("withdraw", "deposit"):
                x1 = rng.choice([0, rng.randrange(1, 10**12)])
                x0 = rng.randrange(0, x1 + 1)
            enabled = rng.random() < 0.8
            hold = None if rng.random() < 0.3 else (rng.randrange(-base, 2 * base + 1), rng.randrange(-base, 2 * base + 1))
            leg = vpool(order, rng.choice("AB"), kind, base, qn, pre, exact_out=exact_out, e_delta=rng.randrange(-e, e),
                        x0=x0, x1=x1, direct=rng.random() < 0.6)
            out.append(Rerun(f"random_pool_{k}", st, leg, tau, exact=False, enabled=enabled, hold=hold))
    return out


def tau_order(venue, side, amount, slot):
    """The first order (slot, tx, 0, 0) whose empirical tolerance is a limit (between 0.001 and 0.6)."""
    for tx in range(1_000):
        x = Tau("empirical").tol(NS(order=(slot, tx, 0, 0)), venue, side, amount)
        if x is not None and 0.001 < x < 0.6:
            return (slot, tx, 0, 0)
    raise AssertionError((venue, side, amount))


def raw_x(venue, side, amount, order):
    """Tau.tol's interpolated quantile before its `x >= 0.99` test (replay.py:146-153), to choose orders by it."""
    t = replay._tau_table()
    qs = t["table" if venue == "curve" else "pool_table"][f"{side}:{replay._bucket(amount, t['size_buckets_lamports'])}"]
    u = (hash(order) % 10_000) / 10_000 * (len(qs) - 1)
    lo = int(u)
    hi = min(lo + 1, len(qs) - 1)
    return qs[lo] + (qs[hi] - qs[lo]) * (u - lo)


def order_where(venue, side, amount, slot, ok):
    """The first order (slot, tx, 0, 0) whose interpolated quantile satisfies ok."""
    for tx in range(20_000):
        o = (slot, tx, 0, 0)
        if ok(raw_x(venue, side, amount, o)):
            return o
    raise AssertionError((venue, side, amount))


def pick(cands, f, target):
    """The first candidate with f(c) == target."""
    c = next((c for c in cands if f(c) == target), None)
    assert c is not None, target
    return c


def not_floored_curve(cst, tiers, leg, t, x):
    """A tau sell where replay.py's int / int (a float) and a floored quotient give different limits around the fill:
    the holdings (h 3t, c) cut the sell to c // 3, the fee moves the logged net."""
    for c in range(2 * t, 2 * t + 5_000):
        a = t * c // (3 * t)
        fq = V.curve_sell(tiers, a).trader_quote
        fee0 = leg.q - int(fq * t / (a * (1 - x)))
        for fee in range(max(0, fee0 - 3), min(leg.q, fee0 + 4)):
            n = max(0, leg.q - fee)
            if int(n * a / t * (1 - x)) == fq + 1 and int((n * a // t) * (1 - x)) <= fq:
                return Rerun("curve_tau_min_o_not_floored", cst, replace(leg, fee=fee), "empirical", hold=(3 * t, c), expect="fail")
    raise AssertionError("no curve sell where flooring first differs")


def not_floored_pool(pst, run, leg, base, x):
    """The same for a pool sell cut by inexact holdings (h base, c: a = base - (base - c)); quote_net moves the log."""
    for c in range(base // 2, base // 2 + 5_000):
        a = c
        fq = V.pool_sell(run, a).trader_quote
        n0 = int(fq / (1 - x) * base / a)
        for n in range(n0 - 3, n0 + 5):
            if int(n * a / base * (1 - x)) == fq + 1 and int((n * a // base) * (1 - x)) <= fq:
                assert Tau("empirical").tol(replace(leg, quote_net=n), "pool", "sell", n) == x
                return Rerun("pool_tau_sell_not_floored", pst, replace(leg, quote_net=n), "empirical", exact=False,
                             hold=(base, c), expect="fail")
    raise AssertionError("no pool sell where flooring first differs")


def boundary_reruns():
    """Each limit exactly at the re-run's fill (passes) and one past it (fails), own limits and tau's; a sell scaled by
    the holdings; the tau sells' int / int where flooring first would differ."""
    out = []
    emp = Tau("empirical")
    cst = replace(CURVE0, vt=CURVE0.vt - 10**14, vq=CURVE0.vq + 4 * SOL, rt=CURVE0.rt - 10**14, rq=4 * SOL)
    post = replace(cst, fee_bps=100, creator_bps=5)  # the leg's tiers, not the state's
    tiers = replace(cst, fee_bps=100, creator_bps=5)
    o = (7_000, 11, 0, 0)

    def c_x(leg, side):
        return emp.tol(leg, "curve", side, leg.q)

    # exact-in buy: min_tokens (own) and int(t * (1 - x)) (tau)
    f = V.curve_buy_exact_in(tiers, SOL)
    for m, ok in ((f.tokens, "pass"), (f.tokens + 1, "fail")):
        leg = vcurve(o, "A", True, f.tokens, SOL - 10**7, 10**7, post, "buy_exact_sol_in", (SOL, None, m, None))
        out.append(Rerun(f"curve_min_tokens_{ok}", cst, leg, "inf", expect=ok))
    leg = vcurve(tau_order("curve", "buy", SOL - 10**7, 7_001), "A", True, f.tokens, SOL - 10**7, 10**7, post, "buy_exact_sol_in",
                 (SOL, None, None, None), direct=False)
    x = c_x(leg, "buy")
    assert x is not None and x > 0
    for target, ok in ((f.tokens, "pass"), (f.tokens + 1, "fail")):
        t = pick(range(int(target / (1 - x)) - 5, int(target / (1 - x)) + 6), lambda t: int(t * (1 - x)), target)
        out.append(Rerun(f"curve_tau_min_t_{ok}", cst, replace(leg, t=t, pre=rust_curve_pre(post, t, leg.q, True)), "empirical", expect=ok))
    # exact-out buy: max_cost (own) and int((q + fee) * (1 + x)) (tau, the fee moves it; q keeps the size bucket)
    f = V.curve_buy_exact_out(tiers, 10**13)
    for m, ok in ((f.trader_quote, "pass"), (f.trader_quote - 1, "fail")):
        leg = vcurve(o, "A", True, 10**13, f.curve_quote, f.fees, post, "buy", (None, m, None, None))
        out.append(Rerun(f"curve_max_cost_{ok}", cst, leg, "inf", expect=ok))
    leg = vcurve(tau_order("curve", "buy", f.curve_quote, 7_002), "A", True, 10**13, f.curve_quote, f.fees, post, "buy", direct=False)
    x = c_x(leg, "buy")
    assert x is not None
    for target, ok in ((f.trader_quote, "pass"), (f.trader_quote - 1, "fail")):
        g = lambda q: int((q + leg.fee) * (1 + x))  # noqa: E731  (q moves it within its size bucket: the same x)
        q = pick(range(int(target / (1 + x)) - leg.fee - 5, int(target / (1 + x)) - leg.fee + 6), g, target)
        assert emp.tol(replace(leg, q=q), "curve", "buy", q) == x
        out.append(Rerun(f"curve_tau_max_c_{ok}", cst, replace(leg, q=q, pre=rust_curve_pre(post, leg.t, q, True)), "empirical", expect=ok))
    # a sell scaled by exact holdings (h 3t, c 2t: a = 2t/3 of t): min_out * a // t (own), and the tau's true division
    t = 3 * 10**12
    a = t * (2 * t) // (3 * t)
    f = V.curve_sell(tiers, a)
    hold = (3 * t, 2 * t)
    for m, ok in ((f.trader_quote * t // a + 1, "pass"), ((f.trader_quote + 1) * t // a + 1, "fail")):
        assert (m * a // t <= f.trader_quote) == (ok == "pass")
        leg = vcurve(o, "B", False, t, 10**8, 10**6, post, "sell", (None, None, None, m))
        out.append(Rerun(f"curve_min_out_scaled_{ok}", cst, leg, "inf", hold=hold, expect=ok))
    leg = vcurve(tau_order("curve", "sell", 3 * 10**8, 7_003), "B", False, t, 3 * 10**8, 0, post, "sell", direct=False)
    x = c_x(leg, "sell")
    assert x is not None
    true = lambda fee: int(max(0, leg.q - fee) * a / t * (1 - x))  # noqa: E731  (replay.py's expression)
    floor = lambda fee: int((max(0, leg.q - fee) * a // t) * (1 - x))  # noqa: E731
    fee0 = leg.q - int(f.trader_quote * t / (a * (1 - x)))  # true() is near f.trader_quote here, and falls with the fee
    fees = range(max(0, fee0 - 3_000), min(leg.q, fee0 + 3_000))
    for target, ok in ((f.trader_quote, "pass"), (f.trader_quote + 1, "fail")):
        fee = pick(fees, true, target)
        out.append(Rerun(f"curve_tau_min_o_{ok}", cst, replace(leg, fee=fee), "empirical", hold=hold, expect=ok))
    out.append(not_floored_curve(cst, tiers, leg, t, x))

    # pools: the state's virt and tiers are the leg's
    pst = replace(POOL0, virt=5 * SOL, e=POOL0.e + 5 * SOL)
    lpre = replace(POOL0, lp_bps=25, creator_bps=30, cashback_bps=10, virt=7 * SOL, e=POOL0.e + 7 * SOL)
    run = replace(pst, lp_bps=25, creator_bps=30, cashback_bps=10, virt=7 * SOL)
    po = (7_100, 5, 0, 0)

    def p_x(leg, side):
        return emp.tol(leg, "pool", side, leg.quote_net)

    f = V.pool_buy_exact_out(run, 10**12)
    for lim, ok in ((f.trader_quote, "pass"), (f.trader_quote - 1, "fail")):
        out.append(Rerun(f"pool_exact_out_limit_{ok}", pst, vpool(po, "A", "buy", 10**12, f.trader_quote, lpre, exact_out=True, x0=lim),
                         "inf", exact=False, expect=ok))
    leg = vpool(tau_order("pool", "buy", f.trader_quote, 7_101), "A", "buy", 10**12, f.trader_quote, lpre, exact_out=True, direct=False)
    x = p_x(leg, "buy")
    assert x is not None
    for target, ok in ((f.trader_quote, "pass"), (f.trader_quote - 1, "fail")):
        qn = pick(range(int(target / (1 + x)) - 5, int(target / (1 + x)) + 6), lambda n: int(n * (1 + x)), target)
        assert emp.tol(replace(leg, quote_net=qn), "pool", "buy", qn) == x
        out.append(Rerun(f"pool_tau_exact_out_{ok}", pst, replace(leg, quote_net=qn), "empirical", exact=False, expect=ok))
    f = V.pool_buy_exact_in(run, SOL)
    for lim, ok in ((f.tokens, "pass"), (f.tokens + 1, "fail")):
        out.append(Rerun(f"pool_exact_in_limit_{ok}", pst, vpool(po, "A", "buy", f.tokens, SOL, lpre, x0=SOL, x1=lim), "inf",
                         exact=False, expect=ok))
    leg = vpool(tau_order("pool", "buy", SOL, 7_102), "A", "buy", f.tokens, SOL, lpre, x0=SOL, direct=False)
    x = p_x(leg, "buy")
    assert x is not None
    for target, ok in ((f.tokens, "pass"), (f.tokens + 1, "fail")):
        base = pick(range(int(target / (1 - x)) - 5, int(target / (1 - x)) + 6), lambda b: int(b * (1 - x)), target)
        out.append(Rerun(f"pool_tau_exact_in_{ok}", pst, replace(leg, base=base), "empirical", exact=False, expect=ok))
    # a sell cut by inexact holdings (h base, c base - base/4: a = 3/4 base)
    base = 4 * 10**11  # small: every quote_net searched stays in size bucket 0, so x stays the order's
    hold = (base, base - base // 4)
    a = base - base // 4
    f = V.pool_sell(run, a)
    for lim, ok in ((f.trader_quote * base // a, "pass"), ((f.trader_quote + 1) * base // a + 1, "fail")):
        assert (lim * a // base <= f.trader_quote) == (ok == "pass")
        out.append(Rerun(f"pool_sell_limit_scaled_{ok}", pst, vpool(po, "B", "sell", base, 10**9, lpre, x0=lim), "inf",
                         exact=False, hold=hold, expect=ok))
    leg = vpool(tau_order("pool", "sell", f.trader_quote * base // a, 7_103), "B", "sell", base, f.trader_quote * base // a, lpre,
                direct=False)
    x = p_x(leg, "sell")
    assert x is not None
    true = lambda n: int(n * a / base * (1 - x))  # noqa: E731
    floor = lambda n: int((n * a // base) * (1 - x))  # noqa: E731
    lo = int(f.trader_quote / (1 - x) * base / a) - 50
    for target, ok in ((f.trader_quote, "pass"), (f.trader_quote + 1, "fail")):
        qn = pick(range(lo, lo + 200), true, target)
        assert emp.tol(replace(leg, quote_net=qn), "pool", "sell", qn) == x
        out.append(Rerun(f"pool_tau_sell_{ok}", pst, replace(leg, quote_net=qn), "empirical", exact=False, hold=hold, expect=ok))
    out.append(not_floored_pool(pst, run, leg, base, x))
    # the leg's virt sets the real vault a withdraw takes from; a drained pool fails an exact-in buy before its budget
    out.append(Rerun("pool_withdraw_leg_virt", pst, vpool(po, "C", "withdraw", 0, 0, lpre, x0=30_000, x1=100_000), "inf",
                     exact=False, expect="pass"))
    out.append(Rerun("pool_withdraw_no_supply", pst, vpool(po, "C", "withdraw", 0, 0, lpre, x0=0, x1=0), "inf", exact=False,
                     expect="fail"))
    out.append(Rerun("pool_drained_buy_no_limit", replace(pst, b=0), vpool(po, "A", "buy", 10, 10, lpre), "inf", exact=False,
                     expect="fail"))
    for n in (0, 1, 2, 3 * SOL):
        out.append(Rerun(f"pool_boost_{n}", pst, vpool(po, "D", "boost", 0, n, lpre), "inf", exact=False, expect="pass"))

    # The tolerance's edge (replay.py:153): a draw just below 0.99 is a limit, int(t * (1 - x)) with 1 - x near 1 %; a
    # draw at 0.99 or above (the 1.0 plateau of the table's top quantiles included) is none, so a re-run far below its
    # log passes. The logged amounts are 200 times the re-run's fill.
    f = V.curve_buy_exact_in(tiers, SOL)
    q, fee = SOL - 10**7, 10**7
    for label, ok, expect in (("below_0_99", lambda x: 0.98 <= x < 0.99, "fail"), ("at_0_99", lambda x: x >= 0.99, "pass")):
        o = order_where("curve", "buy", q, 7_201, ok)
        leg = vcurve(o, "A", True, 200 * f.tokens, q, fee, post, "buy_exact_sol_in", (SOL, None, None, None), direct=False)
        out.append(Rerun(f"curve_tau_{label}", cst, leg, "empirical", expect=expect))
    a = 10**9
    fq = V.pool_sell(run, a).trader_quote
    qn = 5 * SOL  # size bucket 2, where the table's top quantiles are 1.0
    for label, ok, expect in (("below_0_99", lambda x: 0.98 <= x < 0.99, "fail"), ("plateau_1_0", lambda x: x == 1.0, "pass")):
        o = order_where("pool", "sell", qn, 7_202, ok)
        assert 100 * fq < qn
        out.append(Rerun(f"pool_tau_{label}", pst, vpool(o, "B", "sell", a, qn, lpre, direct=False), "empirical", exact=False,
                         expect=expect))
    return out


def all_reruns():
    import random

    return boundary_reruns() + random_reruns(random.Random(20_261_019), 400)


def all_cases():
    cases = pool_cases() + reversed_cases() + curve_cases()
    names = [c.name for c in cases]
    assert len(names) == len(set(names))
    for c in cases:
        if c.venue == "curve" and c.legs:
            assert any(l.slot <= c.t for l in c.legs), c.name  # the engine's curve key has a leg at or before t (D4)
    return cases


# ----------------------------------------------------------------------------- Rust


def arr(xs):
    return Raw("[" + ", ".join(rs(x) for x in xs) + "]")


def rs_leg(l):
    o = Raw(rs(tuple(l.order)))
    if isinstance(l, CurveLeg):
        post = l.post
        args = arr([opt(l.budget), opt(l.max_cost), opt(l.min_tokens), opt(l.min_out)])
        body = (o, IDS[l.trader], l.is_buy, (l.t, l.q, l.fee), arr([post.vt, post.vq, post.rt, post.rq]),
                arr([post.fee_bps, post.creator_bps]), Raw(f"CurveVariant::{VARIANTS[l.variant]}"), args, l.direct)
        return "L::C(" + rs(body) + ")"
    pre = l.pre
    x0 = l.limit_quote if l.kind in ("buy", "sell") else (l.lp_amount if l.kind in ("withdraw", "deposit") else None)
    x1 = l.limit_base if l.kind in ("buy", "sell") else (l.lp_supply if l.kind in ("withdraw", "deposit") else None)
    body = (o, IDS[l.trader], Raw(f"PoolKind::{l.kind.capitalize()}"), l.exact_out, (l.base, l.quote_net, l.post.e - l.pre.e),
            arr([pre.b, pre.e, pre.virt]), arr([pre.lp_bps, pre.protocol_bps, pre.creator_bps, pre.cashback_bps]),
            arr([opt(x0), opt(x1)]), l.direct)
    return "L::P(" + rs(body) + ")"


def rs_state(s):
    if isinstance(s, Curve):
        return f"St::C({arr([s.vt, s.vq, s.rt, s.rq, s.fee_bps, s.creator_bps])})"
    return f"St::P({arr([s.b, s.e, s.lp_bps, s.protocol_bps, s.creator_bps, s.cashback_bps, s.virt])})"


def rs_op(op):
    if op == "batch":
        return "Op::Batch"
    if op == "entry":
        return "Op::Entry"
    if op == "end":
        return "Op::End"
    return {"slot": "Op::Slot", "advance": "Op::Advance"}[op[0]] + f"({op[1]})"


def rs_result(c):
    r = c.r
    pk = c.peak
    peak = None if pk["peak_slot"] is None else Some((pk["peak_slot"], pk["peak_mark"]))
    last = Some(tuple(r.marks[-1])) if r.marks else None
    f = [("decision_slot", r.decision_slot), ("entry_slot", r.entry_slot), ("size", r.size), ("tokens", r.tokens),
         ("cost", r.cost), ("exit_decision_slot", opt(r.exit_decision_slot)), ("exit_slot", opt(r.exit_slot)),
         ("proceeds", r.proceeds), ("exit_reason", opt(r.exit_reason)), ("entry_failed", r.entry_failed),
         ("skipped", r.skipped), ("exit_attempts", r.exit_attempts), ("cf_graduation", r.cf_graduation),
         ("seed_pool", r.seed_pool), ("vault_capped", r.vault_capped), ("reverted_direct", r.reverted_direct),
         ("reverted_router", r.reverted_router), ("sells_dropped", r.sells_dropped), ("sells_scaled", r.sells_scaled),
         ("mayhem_reverted", r.mayhem_reverted), ("marks", len(r.marks)), ("peak", peak), ("last_mark", last)]
    return "R {\n" + "".join(f"            {k}: {rs(v)},\n" for k, v in f) + "        }"


TYPES = """/// A curve leg: (order, trader, is_buy, (t, q, fee), [vt, vq, rt, rq] after it, [fee_bps, creator_bps], variant,
/// [budget, max_cost, min_tokens, min_out], direct).
pub(super) type C = ((u64, u32, u16, u16), u32, bool, (u64, u64, u64), [u64; 4], [u32; 2], CurveVariant, [Option<u64>; 4], bool);

/// A pool leg: (order, trader, kind, exact_out, (base, quote_net, e_delta), [b, e, virt] before it, the four fee tiers,
/// [x0, x1] (limit_quote and limit_base, or lp_amount and lp_supply), direct).
pub(super) type P = ((u64, u32, u16, u16), u32, PoolKind, bool, (i64, i64, i64), [i64; 3], [u32; 4], [Option<u64>; 2], bool);

pub(super) enum L {
    C(C),
    P(P),
}

/// A venue state: a curve's [vt, vq, rt, rq, fee_bps, creator_bps], a pool's [b, e, lp, protocol, creator, cashback, virt].
#[derive(Debug)]
pub(super) enum St {
    C([i128; 6]),
    P([i128; 7]),
}

pub(super) enum Op {
    /// feed_batch from our insertion index
    Batch,
    /// on_slot(entry slot, its legs, our index in them), when legs follow us there
    Entry,
    /// on_slot(slot, its legs), after the flow index got them
    Slot(u64),
    Advance(u64),
    End,
}

/// After a call: (its return: None for advance, feed_batch and an entry with nothing after us; the completion slot;
/// pending, pending_min, last_slot, decided_for, deadline_done, done).
pub(super) type After = (Option<bool>, Option<u64>, (Option<u64>, i128, u64, Option<u64>, bool, bool));

pub(super) enum Opened {
    No,
    Completed(u64),
    Open(usize),
}

/// replay.py's Result (the marks: count, engine._peak's (slot, mark), the last).
pub(super) struct R {
    pub decision_slot: u64,
    pub entry_slot: u64,
    pub size: i128,
    pub tokens: i128,
    pub cost: i128,
    pub exit_decision_slot: Option<u64>,
    pub exit_slot: Option<u64>,
    pub proceeds: i128,
    pub exit_reason: Option<&'static str>,
    pub entry_failed: bool,
    pub skipped: bool,
    pub exit_attempts: u64,
    pub cf_graduation: bool,
    pub seed_pool: bool,
    pub vault_capped: bool,
    pub reverted_direct: u64,
    pub reverted_router: u64,
    pub sells_dropped: u64,
    pub sells_scaled: u64,
    pub mayhem_reverted: u64,
    pub marks: u64,
    pub peak: Option<(u64, i128)>,
    pub last_mark: Option<(u64, i128)>,
}

pub(super) struct Case {
    pub name: &'static str,
    /// "curve", "normal" or "reversed"
    pub venue: &'static str,
    /// (variant, clock)
    pub rule: (&'static str, usize),
    pub t: u64,
    pub d: u64,
    pub q: u64,
    pub size: i128,
    pub tol: u64,
    pub tau: &'static str,
    pub max_hold: u64,
    pub window_end: Option<u64>,
    /// the engine's state leg: (slot, [b, e, virt], fees)
    pub prior: Option<(u64, [i128; 3], [i128; 4])>,
    pub legs: &'static [L],
    pub opened: Opened,
    pub ops: &'static [(Op, After)],
    pub result: R,
    /// the venue state and the holdings (trader, hist, cf, sorted) at the end, with a position
    pub state: Option<St>,
    pub holdings: &'static [(u32, i128, i128)],
}

"""


def generate():
    cases = all_cases()
    o = ["//! Generated by lab/core/tools/position_cases.py from the unmodified backtest.replay: do not edit (the generator's\n"
         "//! doc says how each value is made; backtest/tests/test_position_table.py pins this file to its output).\n\n"
         "use crate::legs::{CurveVariant, PoolKind};\n\n"]
    o.append(f"pub(super) const MAYHEM: u32 = {MAYHEM_ID};\n\n")
    n, s = SCN_NAME, SCN
    o.append("/// The base scenario: (name, fee_per_tx, tip, failed_tx, land_prob, ata_rent, rent_mode, adverse_per_side)\n"
             f"pub(super) const SCN: (&str, i128, i128, i128, f64, i128, &str, f64) = "
             f"{rs((n, s.fee_per_tx, s.tip, s.failed_tx, s.land_prob, s.ata_rent, s.rent_mode, s.adverse_per_side))};\n\n")
    o.append(f"/// (name, epoch -> ms per slot)\npub(super) const CLOCKS: [(&str, &[(u64, f64)]); {len(CLOCKS)}] = [\n"
             + "".join(f"    {rs((nm, sorted(m.items())))},\n" for nm, m in CLOCKS) + "];\n\n")
    o.append(TYPES)
    reruns = all_reruns()
    o.append("/// One re-run: (label, the state, the leg, tau, (exact, enabled) holdings, the trader's (hist, cf), Python's result\n"
             "/// (the state after it, the cf delta) or None, the diag (dropped, scaled)).\n"
             "pub(super) type Rr = (&'static str, St, L, &'static str, (bool, bool), Option<(i128, i128)>, Option<(St, i128)>, (u64, u64));\n\n")
    o.append(f"pub(super) static RERUNS: [Rr; {len(reruns)}] = [\n")
    for r in reruns:
        want = None if r.want is None else Some((Raw(rs_state(r.want[0])), r.want[1]))
        o.append(f"    ({rs(r.label)}, {rs_state(r.state)}, {rs_leg(r.leg)}, {rs(r.tau)}, {rs((r.exact, r.enabled))}, "
                 f"{rs(opt(r.hold))}, {rs(want)}, {rs(r.diag)}),\n")
    o.append("];\n\n")
    o.append(f"pub(super) static CASES: [Case; {len(cases)}] = [\n")
    for c in cases:
        prior = None
        if c.prior is not None:
            ps, st = c.prior
            prior = Some((ps, arr([st.b, st.e, st.virt]), arr([st.lp_bps, st.protocol_bps, st.creator_bps, st.cashback_bps])))
        opened = {"no": "Opened::No", "completed": f"Opened::Completed({c.opened[-1]})", "open": f"Opened::Open({c.opened[-1]})"}[c.opened[0]]
        o.append(f"    Case {{\n        name: {rs(c.name)},\n        venue: {rs(c.venue)},\n        rule: {rs((c.variant, c.clock))},\n"
                 f"        t: {c.t},\n        d: {c.d},\n        q: {hexbits(c.q)},\n        size: {c.size},\n        tol: {hexbits(c.tol)},\n"
                 f"        tau: {rs(c.tau)},\n        max_hold: {c.max_hold},\n        window_end: {rs(opt(c.window_end))},\n"
                 f"        prior: {rs(prior)},\n        legs: &[\n")
        o.extend(f"            {rs_leg(l)},\n" for l in c.legs)
        o.append(f"        ],\n        opened: {opened},\n        ops: &[\n")
        for op, ret, done, probe in c.steps:
            pending, pmin, last, dfor, ddone, dn = probe
            after = (opt(ret), opt(done), (opt(pending), pmin, last, opt(dfor), ddone, dn))
            o.append(f"            ({rs_op(op)}, {rs(after)}),\n")
        o.append(f"        ],\n        result: {rs_result(c)},\n")
        o.append(f"        state: {rs_state(c.state) if c.sim is not None else 'None'},\n".replace("state: St", "state: Some(St").replace(")),\n", ")),\n"))
        if c.sim is not None:
            o[-1] = f"        state: Some({rs_state(c.state)}),\n"
        o.append(f"        holdings: {rs(c.hold) if c.sim is not None else '&[]'},\n    }},\n")
    o.append("];\n")
    return "".join(o)


if __name__ == "__main__":
    print(generate(), end="")
