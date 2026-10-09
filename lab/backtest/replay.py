"""Counterfactual replay of one position against a token's historical event stream.

Our buy is inserted at slot t + d after a fraction q of that slot's transactions in the token.
Every later historical trade is then re-executed against the counterfactual state, with its own
slippage limit (from instruction arguments or PumpSwap events; a tolerance tau where the curve
arguments are missing). A transaction whose leg would fail is reverted as a whole. Marks are
taken at the end of every slot with events; the exit rule decides at slot u and the sale fills at
u + d. A curve that completes while we hold sells in the pool at open + 1 (historical pool
state; the pool seed does not depend on who holds the tokens).

All quantities are integers in lamports / raw token units.
"""

from dataclasses import dataclass, field, replace

from . import venues as V
from .venues import Curve, Pool

EXACT_IN_CURVE = {"buy_exact_sol_in", "buy_exact_quote_in_v2", "other"}
EXACT_OUT_POOL = {"66063d1201daebea", "b817ee6167c5d33d"}


@dataclass(slots=True)
class CurveLeg:
    slot: int
    tx: int
    order: tuple
    is_buy: bool
    t: int
    q: int
    fee: int
    variant: str
    pre: Curve
    post: Curve
    trader: str
    budget: int = None
    max_cost: int = None
    min_tokens: int = None
    min_out: int = None


@dataclass(slots=True)
class PoolLeg:
    slot: int
    tx: int
    order: tuple
    kind: str
    exact_out: bool
    base: int
    quote_net: int
    pre: Pool
    post: Pool
    trader: str
    limit_quote: int = None
    limit_base: int = None
    boost_left: int = None


@dataclass
class Stream:
    mint: str
    create_slot: int
    creator: str
    curve: list
    pool_legs: list = field(default_factory=list)
    pool_open_slot: int = None
    complete_slot: int = None


_TAU_TABLE = None


def _tau_table():
    global _TAU_TABLE
    if _TAU_TABLE is None:
        import json
        import os

        with open(os.path.join(os.path.dirname(__file__), "tau_empirical.json")) as f:
            _TAU_TABLE = json.load(f)
    return _TAU_TABLE


def _bucket(amount, edges):
    for i, e in enumerate(edges):
        if amount < e:
            return i
    return len(edges)


@dataclass
class Tau:
    """Slippage tolerance for historical curve trades without instruction arguments.

    zero: any worse fill fails. inf: never fails. empirical: each trade gets a tolerance drawn
    deterministically (hash of its position) from the distribution measured on epochs with
    arguments (tau_empirical.json, per side and size bucket); >= 0.99 means no limit.
    """

    mode: str = "empirical"

    def _draw(self, leg, side, amount):
        t = _tau_table()
        qs = t["table"].get(f"{side}:{_bucket(amount, t['size_buckets_lamports'])}")
        if not qs:
            return None
        u = (hash(leg.order) % 10_000) / 10_000 * (len(qs) - 1)
        lo = int(u)
        hi = min(lo + 1, len(qs) - 1)
        tol = qs[lo] + (qs[hi] - qs[lo]) * (u - lo)
        return None if tol >= 0.99 else tol

    def max_cost(self, leg, logged_cost):
        if self.mode == "inf":
            return None
        if self.mode == "zero":
            return logged_cost
        tol = self._draw(leg, "buy", leg.q)
        return None if tol is None else int(logged_cost * (1 + tol))

    def min_tokens(self, leg):
        if self.mode == "inf":
            return None
        if self.mode == "zero":
            return leg.t
        tol = self._draw(leg, "buy", leg.q)
        return None if tol is None else int(leg.t * (1 - tol))

    def min_out(self, leg, logged_out):
        if self.mode == "inf":
            return None
        if self.mode == "zero":
            return logged_out
        tol = self._draw(leg, "sell", leg.q)
        return None if tol is None else int(logged_out * (1 - tol))


@dataclass
class Result:
    mint: str
    decision_slot: int
    entry_slot: int = None
    venue: str = None
    size: int = 0
    tokens: int = 0
    cost: int = 0  # lamports paid at entry (venue amount + fees)
    exit_decision_slot: int = None
    exit_slot: int = None
    proceeds: int = 0
    exit_reason: str = None
    entry_failed: bool = False  # our entry transaction landed and failed (pays the failed-tx cost)
    skipped: bool = False  # no entry was possible (no state, curve complete): no transaction, no cost
    exit_attempts: int = 0
    cf_graduation: bool = False
    reverted_txs: int = 0
    marks: list = field(default_factory=list)


def _state_curve_before(stream, idx):
    if idx > 0:
        return stream.curve[idx - 1].post
    if stream.curve:
        return stream.curve[0].pre
    return None


def _insertion_index(legs, slot, q_frac):
    """Index of the first leg that comes after our transaction at `slot` (position q_frac)."""
    i = 0
    n = len(legs)
    while i < n and legs[i].slot < slot:
        i += 1
    j = i
    txs = []
    while j < n and legs[j].slot == slot:
        if not txs or txs[-1] != legs[j].tx:
            txs.append(legs[j].tx)
        j += 1
    k = int(q_frac * len(txs))
    if k >= len(txs):
        return j
    cut_tx = txs[k]
    while i < j and legs[i].tx != cut_tx:
        i += 1
    return i


def _rerun_curve(state, leg, tau):
    """Re-execute one historical curve leg on the counterfactual state; None if it fails."""
    if state.complete:
        return None
    # Fees as charged on that trade (creator fee settings can change over a coin's life).
    state = replace(state, fee_bps=leg.pre.fee_bps, creator_bps=leg.pre.creator_bps)
    if leg.is_buy:
        if leg.variant in EXACT_IN_CURVE:
            budget = leg.budget if leg.budget is not None else leg.q + leg.fee
            f = V.curve_buy_exact_in(state, budget)
            if f is None:
                return None
            min_t = leg.min_tokens if leg.min_tokens is not None else tau.min_tokens(leg)
            if min_t is not None and f.tokens < min_t:
                return None
            return f.state
        f = V.curve_buy_exact_out(state, min(leg.t, state.rt))
        if f is None:
            return None
        max_c = leg.max_cost if leg.max_cost is not None else tau.max_cost(leg, leg.q + leg.fee)
        if max_c is not None and f.trader_quote > max_c:
            return None
        return f.state
    f = V.curve_sell(state, leg.t)
    if f is None:
        return None
    min_o = leg.min_out if leg.min_out is not None else tau.min_out(leg, max(0, leg.q - leg.fee))
    if min_o is not None and f.trader_quote < min_o:
        return None
    return f.state


def _mark_curve(state, tokens):
    f = V.curve_sell(state, tokens)
    return f.trader_quote if f else 0


def _pool_state_at(stream, slot, q_frac):
    """Historical pool state just before our transaction at `slot` (position q_frac)."""
    legs = stream.pool_legs
    if not legs:
        return None, 0
    i = _insertion_index(legs, slot, q_frac)
    if i == 0:
        return legs[0].pre, 0
    return legs[i - 1].post, i


def _pool_sell_value(stream, slot, q_frac, tokens):
    state, _ = _pool_state_at(stream, slot, q_frac)
    if state is None:
        return None
    f = V.pool_sell(state, tokens)
    return f.trader_quote if f else 0


def simulate_curve_position(stream, decision_slot, size, d, q_frac, exit_rule, tol, tau, max_slots=400_000):
    """Buy `size` lamports on the curve at decision_slot + d and run until exit_rule says sell.

    exit_rule(ctx) -> reason or None, called at the end of every slot with events while holding;
    ctx has: slot, mark, cost, entry_slot, curve (state), slots_held.
    """
    r = Result(stream.mint, decision_slot, size=size, venue="curve")
    legs = stream.curve
    entry_slot = decision_slot + d
    r.entry_slot = entry_slot

    # Expected fill at decision time: end-of-slot state at the decision slot.
    i_dec = _insertion_index(legs, decision_slot + 1, 0.0)
    s_dec = _state_curve_before(stream, i_dec)
    if s_dec is None or s_dec.complete:
        r.skipped, r.exit_reason = True, "no_curve_state"
        return r
    expected = V.curve_buy_exact_in(s_dec, size)
    if expected is None:
        r.skipped, r.exit_reason = True, "no_fill"
        return r

    i = _insertion_index(legs, entry_slot, q_frac)
    state = _state_curve_before(stream, i)
    if state.complete:
        r.skipped, r.exit_reason = True, "curve_complete"
        return r
    fill = V.curve_buy_exact_in(state, size)
    if fill is None or fill.tokens < expected.tokens * (1 - tol):
        r.entry_failed, r.exit_reason = True, "entry_slippage"
        return r
    r.tokens, r.cost = fill.tokens, fill.trader_quote
    state = fill.state
    if fill.completes:
        r.cf_graduation = True

    pending_exit_slot = None
    pending_min = None
    last_slot = entry_slot
    n = len(legs)

    def finish_in_pool(reason):
        open_slot = stream.pool_open_slot
        if open_slot is None:
            r.exit_reason = reason + "_no_pool"
            r.proceeds = 0
            return r
        r.exit_decision_slot = open_slot
        r.exit_slot = open_slot + 1
        r.proceeds = _pool_sell_value(stream, open_slot + 1, q_frac, r.tokens) or 0
        r.exit_reason = reason
        return r

    while i < n:
        leg = legs[i]
        # Close the previous slot: mark and ask the exit rule.
        if leg.slot != last_slot:
            if pending_exit_slot is None and not state.complete:
                mark = _mark_curve(state, r.tokens)
                r.marks.append((last_slot, mark))
                reason = exit_rule({"slot": last_slot, "mark": mark, "cost": r.cost, "entry_slot": entry_slot,
                                    "curve": state, "slots_held": last_slot - entry_slot})
                if reason:
                    r.exit_decision_slot, r.exit_reason = last_slot, reason
                    pending_exit_slot, pending_min = last_slot + d, int(mark * (1 - tol))
            last_slot = leg.slot
        if pending_exit_slot is not None and (leg.slot > pending_exit_slot or
                                              (leg.slot == pending_exit_slot and i >= _insertion_index(legs, pending_exit_slot, q_frac))):
            f = V.curve_sell(state, r.tokens)
            r.exit_attempts += 1
            if f is not None and f.trader_quote >= pending_min:
                r.exit_slot, r.proceeds = pending_exit_slot, f.trader_quote
                return r
            pending_exit_slot = None  # failed: decide again next slot
        if leg.slot - entry_slot > max_slots:
            break
        # Re-execute the whole historical transaction atomically.
        j = i
        tx_state = state
        ok = True
        while j < n and legs[j].slot == leg.slot and legs[j].tx == leg.tx:
            nxt = _rerun_curve(tx_state, legs[j], tau)
            if nxt is None:
                ok = False
                break
            tx_state = nxt
            j += 1
        while j < n and legs[j].slot == leg.slot and legs[j].tx == leg.tx:
            j += 1
        if ok:
            state = tx_state
        else:
            r.reverted_txs += 1
        i = j
        if state.complete:
            if stream.complete_slot is None or leg.slot < stream.complete_slot:
                r.cf_graduation = True
            return finish_in_pool("graduation")

    # Stream ended while holding.
    if state.complete:
        return finish_in_pool("graduation")
    if pending_exit_slot is not None:
        f = V.curve_sell(state, r.tokens)
        r.exit_attempts += 1
        r.exit_slot, r.proceeds = pending_exit_slot, f.trader_quote if f else 0
        return r
    mark = _mark_curve(state, r.tokens)
    r.exit_decision_slot, r.exit_slot, r.proceeds, r.exit_reason = last_slot, last_slot + d, mark, r.exit_reason or "end_of_data"
    return r


def simulate_pool_position(stream, decision_slot, size, d, q_frac, exit_rule, tol, max_slots=400_000):
    """Buy in the pool at decision_slot + d; later pool trades are re-executed with their limits."""
    r = Result(stream.mint, decision_slot, size=size, venue="pool")
    legs = stream.pool_legs
    entry_slot = decision_slot + d
    r.entry_slot = entry_slot
    s_dec, _ = _pool_state_at(stream, decision_slot + 1, 0.0)
    if s_dec is None:
        r.skipped, r.exit_reason = True, "no_pool_state"
        return r
    expected = V.pool_buy_exact_in(s_dec, size)
    state, i = _pool_state_at(stream, entry_slot, q_frac)
    fill = V.pool_buy_exact_in(state, size) if state else None
    if fill is None or expected is None or fill.tokens < expected.tokens * (1 - tol):
        r.entry_failed, r.exit_reason = True, "entry_slippage"
        return r
    r.tokens, r.cost, state = fill.tokens, fill.trader_quote, fill.state
    pending_exit_slot, pending_min, last_slot = None, None, entry_slot
    n = len(legs)
    while i < n:
        leg = legs[i]
        if leg.slot != last_slot:
            if pending_exit_slot is None:
                f = V.pool_sell(state, r.tokens)
                mark = f.trader_quote if f else 0
                r.marks.append((last_slot, mark))
                reason = exit_rule({"slot": last_slot, "mark": mark, "cost": r.cost, "entry_slot": entry_slot,
                                    "pool": state, "slots_held": last_slot - entry_slot})
                if reason:
                    r.exit_decision_slot, r.exit_reason = last_slot, reason
                    pending_exit_slot, pending_min = last_slot + d, int(mark * (1 - tol))
            last_slot = leg.slot
        if pending_exit_slot is not None and (leg.slot > pending_exit_slot or
                                              (leg.slot == pending_exit_slot and i >= _insertion_index(legs, pending_exit_slot, q_frac))):
            f = V.pool_sell(state, r.tokens)
            r.exit_attempts += 1
            if f is not None and f.trader_quote >= pending_min:
                r.exit_slot, r.proceeds = pending_exit_slot, f.trader_quote
                return r
            pending_exit_slot = None
        if leg.slot - entry_slot > max_slots:
            break
        j, tx_state, ok = i, state, True
        while j < n and legs[j].slot == leg.slot and legs[j].tx == leg.tx:
            nxt = _rerun_pool(tx_state, legs[j])
            if nxt is None:
                ok = False
                break
            tx_state = nxt
            j += 1
        while j < n and legs[j].slot == leg.slot and legs[j].tx == leg.tx:
            j += 1
        if ok:
            state = tx_state
        else:
            r.reverted_txs += 1
        i = j
    f = V.pool_sell(state, r.tokens)
    r.exit_decision_slot = r.exit_decision_slot or last_slot
    r.exit_slot, r.proceeds = (pending_exit_slot or last_slot + d), (f.trader_quote if f else 0)
    r.exit_reason = r.exit_reason or "end_of_data"
    return r


def _rerun_pool(state, leg):
    # Fee tier as charged on that trade (PumpSwap tiers depend on market cap).
    state = replace(state, lp_bps=leg.pre.lp_bps, protocol_bps=leg.pre.protocol_bps, creator_bps=leg.pre.creator_bps)
    if leg.kind == "boost":
        # Protocol buy-and-burn with vault quote: apply as a fee-free exact-in buy.
        n = leg.quote_net
        if n is None or n <= 1:
            return state
        b = state.b * (n - 1) // (state.e + n - 1)
        return Pool(state.b - b, state.e + n, state.lp_bps, state.protocol_bps, state.creator_bps)
    if leg.kind == "buy":
        if leg.exact_out:
            f = V.pool_buy_exact_out(state, leg.base)
            if f is None or (leg.limit_quote is not None and f.trader_quote > leg.limit_quote):
                return None
            return f.state
        f = V.pool_buy_exact_in(state, leg.limit_quote)
        if f is None or (leg.limit_base is not None and f.tokens < leg.limit_base):
            return None
        return f.state
    if leg.kind == "sell":
        f = V.pool_sell(state, leg.base)
        if f is None or (leg.limit_quote is not None and f.trader_quote < leg.limit_quote):
            return None
        return f.state
    return state
