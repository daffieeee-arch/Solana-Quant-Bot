"""Counterfactual replay of one position against a token's historical event stream.

Our buy is inserted at slot t + d after a fraction q of that slot's transactions in the token.
Every later historical transaction is then re-executed against the counterfactual state:

* Slippage limits. Direct calls to pump / PumpSwap keep their own limits (instruction arguments
  or PumpSwap event limits). Calls through routers do not: routers compute the inner limits in
  the same transaction (zero slack) and their real tolerance is unknown, so they get a tolerance
  tau like trades without arguments (drawn per trade from the distribution of direct calls).
* Holdings. Historical traders whose buys were reverted or filled smaller cannot sell tokens they
  do not have: on the curve a sell is scaled by H'/H (full exits become H'), in pool windows the
  missing tokens are subtracted; a sell of nothing is dropped.
* Atomicity. A transaction whose leg fails is reverted as a whole.

Marks are taken at the end of every slot with events and at time deadlines inside quiet gaps;
the exit rule decides at slot u and the sale fills at u + d. A curve that completes while we hold
sells in the pool at open + 1: in the historical pool when there is one, otherwise in a standard
seed pool (flagged).

All quantities are integers in lamports / raw token units.
"""

import json
import os
from dataclasses import dataclass, field, replace

from . import venues as V
from .venues import Curve, Pool

EXACT_IN_CURVE = {"buy_exact_sol_in", "buy_exact_quote_in_v2", "other"}
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
# Standard PumpSwap seed after migration (median of all dev-period pools) and its fee tier.
SEED_POOL = Pool(206_900_000_000_000, 84_990_359_060, 2, 93, 30)


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
    direct: bool = True


@dataclass(slots=True)
class PoolLeg:
    slot: int
    tx: int
    order: tuple
    kind: str  # buy | sell | boost | withdraw | deposit (pool orientation)
    exact_out: bool
    base: int
    quote_net: int  # quote paid by the trader (buy, fees included) or received (sell); boost: quote used
    pre: Pool
    post: Pool
    trader: str
    limit_quote: int = None
    limit_base: int = None
    boost_left: int = None
    direct: bool = True
    lp_amount: int = None  # withdraw: LP tokens burned; deposit: minted
    lp_supply: int = None
    farmer: bool = False  # the trader was volume farming in this pool (store.py, backward-looking)


@dataclass
class Stream:
    mint: str
    create_slot: int
    creator: str
    curve: list
    pool_legs: list = field(default_factory=list)
    pool_open_slot: int = None
    complete_slot: int = None
    pool: str = None
    orientation: str = "normal"  # reversed: WSOL is the pool's base, the token its quote


_TAU_TABLE = None


def _tau_table():
    global _TAU_TABLE
    if _TAU_TABLE is None:
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
    """Tolerance for trades whose own limit is unknown (router calls, missing curve arguments).

    zero: any worse fill fails. inf: never fails. empirical: a tolerance drawn deterministically
    per trade (hash of its position) from the distribution of direct calls (tau_empirical.json,
    per venue, side and size bucket); >= 0.99 means no limit.
    """

    mode: str = "empirical"

    def tol(self, leg, venue, side, amount):
        if self.mode == "inf":
            return None
        if self.mode == "zero":
            return 0.0
        t = _tau_table()
        qs = t["table" if venue == "curve" else "pool_table"].get(f"{side}:{_bucket(amount, t['size_buckets_lamports'])}")
        if not qs:
            return None
        u = (hash(leg.order) % 10_000) / 10_000 * (len(qs) - 1)
        lo = int(u)
        hi = min(lo + 1, len(qs) - 1)
        x = qs[lo] + (qs[hi] - qs[lo]) * (u - lo)
        return None if x >= 0.99 else x


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
    seed_pool: bool = False  # graduated counterfactually with no historical pool: standard seed used
    vault_capped: bool = False  # the real vault could not pay for all our tokens; the rest was written off
    reverted_direct: int = 0
    reverted_router: int = 0
    sells_dropped: int = 0
    sells_scaled: int = 0
    hist_mid_entry: float = None  # historical mid price (no us) at entry and exit
    hist_mid_exit: float = None
    marks: list = field(default_factory=list)


def _insertion_index(legs, slot, q_frac):
    """Index of the first leg that comes after our transaction at `slot` (position q_frac)."""
    lo, hi = 0, len(legs)
    while lo < hi:
        mid = (lo + hi) // 2
        if legs[mid].slot < slot:
            lo = mid + 1
        else:
            hi = mid
    i = j = lo
    n = len(legs)
    txs = []
    while j < n and legs[j].slot == slot:
        if not txs or txs[-1] != legs[j].tx:
            txs.append(legs[j].tx)
        j += 1
    k = int(q_frac * len(txs))
    if k >= len(txs):
        return j
    while i < j and legs[i].tx != txs[k]:
        i += 1
    return i


def _hist_state(legs, idx):
    if idx > 0:
        return legs[idx - 1].post
    return legs[0].pre if legs else None


class Holdings:
    """Historical vs counterfactual token holdings of other traders (curve: from the create)."""

    def __init__(self, exact, enabled=True):
        self.exact = exact
        self.enabled = enabled  # off in reversed pools: there the sold base is WSOL, not the token
        self.hist = {}
        self.cf = {}

    def seed(self, legs):
        for l in legs:
            if getattr(l, "is_buy", None) is not None:
                d = l.t if l.is_buy else -l.t
                self.hist[l.trader] = self.hist.get(l.trader, 0) + d
                self.cf[l.trader] = self.cf.get(l.trader, 0) + d

    def sell_amount(self, trader, a):
        if not self.enabled:
            return a
        h = self.hist.get(trader, 0)
        c = self.cf.get(trader, h)
        if c >= h:
            return a
        if self.exact:
            if h <= 0 or c <= 0:
                return 0
            return c if a >= h else a * c // h
        return max(0, a - (h - c))

    def apply(self, trader, hist_delta, cf_delta):
        self.hist[trader] = self.hist.get(trader, 0) + hist_delta
        self.cf[trader] = self.cf.get(trader, 0) + cf_delta


def _rerun_curve(state, leg, tau, hold, diag):
    """Re-execute one historical curve leg; returns (state, cf_token_delta) or None if it fails."""
    if state.complete:
        return None
    state = replace(state, fee_bps=leg.pre.fee_bps, creator_bps=leg.pre.creator_bps)
    own_limits = leg.direct
    if leg.is_buy:
        if leg.variant in EXACT_IN_CURVE:
            budget = leg.budget if leg.budget is not None else leg.q + leg.fee
            f = V.curve_buy_exact_in(state, budget)
            if f is None:
                return None
            if own_limits and leg.min_tokens is not None:
                min_t = leg.min_tokens
            else:
                x = tau.tol(leg, "curve", "buy", leg.q)
                min_t = None if x is None else int(leg.t * (1 - x))
            if min_t is not None and f.tokens < min_t:
                return None
            return f.state, f.tokens
        f = V.curve_buy_exact_out(state, min(leg.t, state.rt))
        if f is None:
            return None
        if own_limits and leg.max_cost is not None:
            max_c = leg.max_cost
        else:
            x = tau.tol(leg, "curve", "buy", leg.q)
            max_c = None if x is None else int((leg.q + leg.fee) * (1 + x))
        if max_c is not None and f.trader_quote > max_c:
            return None
        return f.state, f.tokens
    a = hold.sell_amount(leg.trader, leg.t)
    if a == 0:
        diag["sells_dropped"] += 1
        return state, 0
    if a < leg.t:
        diag["sells_scaled"] += 1
    f = V.curve_sell(state, a)
    if f is None:
        return None
    logged_net = max(0, leg.q - leg.fee)
    if own_limits and leg.min_out is not None:
        min_o = leg.min_out * a // leg.t
    else:
        x = tau.tol(leg, "curve", "sell", leg.q)
        min_o = None if x is None else int(logged_net * a / leg.t * (1 - x))
    if min_o is not None and f.trader_quote < min_o:
        return None
    return f.state, -a


def _rerun_pool(state, leg, tau, hold, diag):
    """Re-execute one historical pool leg; returns (state, cf_token_delta) or None."""
    pre = leg.pre
    state = replace(state, lp_bps=pre.lp_bps, protocol_bps=pre.protocol_bps, creator_bps=pre.creator_bps,
                    cashback_bps=pre.cashback_bps, virt=pre.virt)
    if leg.kind == "boost":
        # Fee-free buy with the quote the crank actually used; the tokens are burned.
        n = leg.quote_net
        if n is None or n <= 1:
            return state, 0
        b = state.b * (n - 1) // (state.e + n - 1)
        return replace(state, b=state.b - b, e=state.e + n), 0
    if leg.kind == "withdraw":
        s = V.pool_withdraw(state, leg.lp_amount, leg.lp_supply)
        return (s, 0) if s is not None else None
    if leg.kind == "deposit":
        return V.pool_deposit(state, leg.base, leg.quote_net), 0
    if leg.kind == "buy":
        if leg.exact_out:
            f = V.pool_buy_exact_out(state, leg.base)
            if f is None:
                return None
            if leg.direct:
                lim = leg.limit_quote
            else:
                x = tau.tol(leg, "pool", "buy", leg.quote_net)
                lim = None if x is None else int(leg.quote_net * (1 + x))
            if lim is not None and f.trader_quote > lim:
                return None
            return f.state, f.tokens
        f = V.pool_buy_exact_in(state, leg.limit_quote)
        if f is None:
            return None
        if leg.direct:
            lim = leg.limit_base
        else:
            x = tau.tol(leg, "pool", "buy", leg.quote_net)
            lim = None if x is None else int(leg.base * (1 - x))
        if lim is not None and f.tokens < lim:
            return None
        return f.state, f.tokens
    if leg.kind == "sell":
        a = hold.sell_amount(leg.trader, leg.base)
        if a == 0:
            diag["sells_dropped"] += 1
            return state, 0
        if a < leg.base:
            diag["sells_scaled"] += 1
        f = V.pool_sell(state, a)
        if f is None:
            return None
        if leg.direct:
            lim = None if leg.limit_quote is None else leg.limit_quote * a // leg.base
        else:
            x = tau.tol(leg, "pool", "sell", leg.quote_net)
            lim = None if x is None else int(leg.quote_net * a / leg.base * (1 - x))
        if lim is not None and f.trader_quote < lim:
            return None
        return f.state, -a
    return state, 0


def _price(state):
    """Token mid price in quote units (curve, normal pool)."""
    if state is None:
        return None
    if isinstance(state, Curve):
        return state.vq / state.vt
    return state.e / state.b if state.b > 0 and state.e > 0 else None


def _price_reversed(state):
    """Token mid price in SOL for a reversed pool (base WSOL, quote token)."""
    if state is None or state.b <= 0 or state.e <= 0:
        return None
    return state.b / state.e


def _run(r, legs, i, state, hold, rerun, sell, mark_ctx, exit_rule, d, q_frac, tol, tau, on_complete, max_slots,
         price=_price):
    """Shared event loop for curve and pool positions."""
    diag = {"sells_dropped": 0, "sells_scaled": 0}
    entry_slot = r.entry_slot
    deadline = exit_rule.deadline(entry_slot) if hasattr(exit_rule, "deadline") else None
    pending, pending_min = None, None
    last_slot = entry_slot
    n = len(legs)

    def decide(slot):
        nonlocal pending, pending_min
        f = sell(state, r.tokens)
        mark = f.trader_quote if f else 0
        r.marks.append((slot, mark))
        reason = exit_rule(dict(mark_ctx(state), slot=slot, mark=mark, cost=r.cost, entry_slot=entry_slot,
                                slots_held=slot - entry_slot))
        if reason:
            r.exit_decision_slot, r.exit_reason = slot, reason
            pending, pending_min = slot + d, int(mark * (1 - tol))

    def try_exit(fill_slot):
        nonlocal pending
        f = sell(state, r.tokens)
        r.exit_attempts += 1
        if f is not None and f.trader_quote >= pending_min:
            r.exit_slot, r.proceeds = fill_slot, f.trader_quote
            r.vault_capped = f.tokens < r.tokens
            r.hist_mid_exit = price(_hist_state(legs, _insertion_index(legs, fill_slot, q_frac)))
            return True
        pending = None
        return False

    def finish():
        r.sells_dropped, r.sells_scaled = diag["sells_dropped"], diag["sells_scaled"]
        return r

    while i < n:
        leg = legs[i]
        if leg.slot != last_slot:
            if pending is None and not getattr(state, "complete", False):
                decide(last_slot)
            # A time deadline inside the quiet gap before this leg.
            if pending is None and deadline is not None and last_slot < deadline < leg.slot:
                decide(deadline)
            if pending is not None and pending < leg.slot:
                if try_exit(pending):
                    return finish()
            last_slot = leg.slot
        if pending is not None and leg.slot == pending and i >= _insertion_index(legs, pending, q_frac):
            if try_exit(pending):
                return finish()
        if leg.slot - entry_slot > max_slots:
            break
        # Re-execute the whole historical transaction atomically. Holdings move per leg, so a later
        # leg of the same transaction (a bot that buys and sells at once) sees the earlier ones;
        # if any leg fails, the applied legs are rolled back.
        j, tx_state, ok, applied, failed_leg = i, state, True, [], None
        diag_before = dict(diag)
        while j < n and legs[j].slot == leg.slot and legs[j].tx == leg.tx:
            res = rerun(tx_state, legs[j], tau, hold, diag)
            if res is None:
                ok, failed_leg = False, legs[j]
                break
            tx_state, cf_delta = res
            _apply_hold(hold, legs[j], cf_delta)
            applied.append((legs[j], cf_delta))
            j += 1
        while j < n and legs[j].slot == leg.slot and legs[j].tx == leg.tx:
            j += 1
        tx_legs = legs[i:j]
        if ok:
            state = tx_state
        else:
            if failed_leg.direct:
                r.reverted_direct += 1
            else:
                r.reverted_router += 1
            for l, cf_delta in applied:
                _apply_hold(hold, l, cf_delta, sign=-1)
            diag.update(diag_before)
            for l in tx_legs:
                _apply_hold(hold, l, 0)
        i = j
        if getattr(state, "complete", False):
            return on_complete(leg.slot, finish)

    # Stream ended while holding.
    if getattr(state, "complete", False):
        return on_complete(last_slot, finish)
    if pending is None:
        decide(last_slot)
    if pending is None and deadline is not None and deadline > last_slot:
        decide(deadline)
    if pending is None:
        r.exit_decision_slot, r.exit_reason = last_slot, r.exit_reason or "end_of_data"
        pending, pending_min = last_slot + d, 0
    if not try_exit(pending):
        f = sell(state, r.tokens)
        r.exit_slot, r.proceeds = pending, f.trader_quote if f else 0
    return finish()


def _apply_hold(hold, leg, cf_delta, sign=1):
    """Book a leg's historical and counterfactual token delta (sign=-1 undoes it)."""
    if isinstance(leg, CurveLeg):
        hist = leg.t if leg.is_buy else -leg.t
    elif leg.kind in ("buy", "sell"):
        hist = leg.base if leg.kind == "buy" else -leg.base
    else:
        return
    hold.apply(leg.trader, sign * hist, sign * cf_delta)


def simulate_curve_position(stream, decision_slot, size, d, q_frac, exit_rule, tol, tau, max_slots=400_000):
    """Buy `size` lamports on the curve at decision_slot + d and run until the exit rule sells."""
    r = Result(stream.mint, decision_slot, size=size, venue="curve")
    legs = stream.curve
    r.entry_slot = decision_slot + d
    s_dec = _hist_state(legs, _insertion_index(legs, decision_slot + 1, 0.0))
    if s_dec is None or s_dec.complete:
        r.skipped, r.exit_reason = True, "no_curve_state"
        return r
    expected = V.curve_buy_exact_in(s_dec, size)
    if expected is None:
        r.skipped, r.exit_reason = True, "no_fill"
        return r
    i = _insertion_index(legs, r.entry_slot, q_frac)
    state = _hist_state(legs, i)
    if state.complete:
        r.skipped, r.exit_reason = True, "curve_complete"
        return r
    r.hist_mid_entry = _price(state)
    fill = V.curve_buy_exact_in(state, size)
    if fill is None or fill.tokens < expected.tokens * (1 - tol):
        r.entry_failed, r.exit_reason = True, "entry_slippage"
        return r
    r.tokens, r.cost = fill.tokens, fill.trader_quote
    hold = Holdings(exact=True)
    hold.seed(legs[:i])

    def on_complete(slot, finish):
        if stream.complete_slot is None or slot < stream.complete_slot:
            r.cf_graduation = True
        r.exit_reason = "graduation"
        open_slot = stream.pool_open_slot
        if open_slot is not None and stream.pool_legs:
            r.exit_decision_slot, r.exit_slot = open_slot, open_slot + 1
            idx = _insertion_index(stream.pool_legs, open_slot + 1, q_frac)
            pstate = _hist_state(stream.pool_legs, idx)
            f = V.pool_sell_capped(pstate, r.tokens)
            r.proceeds = f.trader_quote if f else 0
            r.vault_capped = f is not None and f.tokens < r.tokens
            r.hist_mid_exit = _price(pstate)
        else:
            # No historical pool (or not loaded): sell into a standard seed pool, no other flow.
            r.seed_pool = True
            r.exit_decision_slot = slot
            r.exit_slot = slot + 1
            f = V.pool_sell(SEED_POOL, r.tokens)
            r.proceeds = f.trader_quote if f else 0
            r.hist_mid_exit = _price(SEED_POOL)
        r.exit_attempts += 1
        return finish()

    if fill.completes:
        r.cf_graduation = True
        return on_complete(r.entry_slot, lambda: r)
    return _run(r, legs, i, fill.state, hold, _rerun_curve, V.curve_sell, lambda s: {"curve": s},
                exit_rule, d, q_frac, tol, tau, on_complete, max_slots)


def simulate_pool_position(stream, decision_slot, size, d, q_frac, exit_rule, tol, tau=None, max_slots=400_000):
    """Buy the token in the pool at decision_slot + d; later pool events are re-executed with their
    limits. In a reversed pool (WSOL base) the token is bought with a pool sell of WSOL and sold
    with an exact-in pool buy; other traders' holdings are not tracked there."""
    tau = tau or Tau()
    r = Result(stream.mint, decision_slot, size=size, venue="pool")
    legs = stream.pool_legs
    r.entry_slot = decision_slot + d
    if stream.orientation == "reversed":
        buy, sell, price, hold = V.rev_buy_token, V.rev_sell_token, _price_reversed, Holdings(exact=False, enabled=False)
    else:
        buy, sell, price, hold = V.pool_buy_exact_in, V.pool_sell_capped, _price, Holdings(exact=False)
    s_dec = _hist_state(legs, _insertion_index(legs, decision_slot + 1, 0.0)) if legs else None
    if s_dec is None:
        r.skipped, r.exit_reason = True, "no_pool_state"
        return r
    expected = buy(s_dec, size)
    i = _insertion_index(legs, r.entry_slot, q_frac)
    state = _hist_state(legs, i)
    r.hist_mid_entry = price(state)
    fill = buy(state, size)
    if fill is None or expected is None or fill.tokens < expected.tokens * (1 - tol):
        r.entry_failed, r.exit_reason = True, "entry_slippage"
        return r
    r.tokens, r.cost = fill.tokens, fill.trader_quote
    return _run(r, legs, i, fill.state, hold, _rerun_pool, sell, lambda s: {"pool": s},
                exit_rule, d, q_frac, tol, tau, None, max_slots, price=price)
