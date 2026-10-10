"""Exact integer fill math for the pump.fun bonding curve and PumpSwap pools.

Every formula here reproduces the logged amounts of all historical trades in the week-1
dataset (see validate_math.py: curve 99.99-100%, PumpSwap 100%):

  curve sell             q = floor(t * Vq / (Vt + t))
  curve buy, exact out   q = floor(t * Vq / (Vt - t)) + 1
  curve buy, exact in    t = floor((q - 1) * Vt / (Vq + q - 1))
  pool sell              gross = floor(E * a / (B + a))
  pool buy, exact out    q = ceil(E * b / (B - b))
  pool buy, exact in     b = floor(B * (n - 1) / (E + n - 1))
  every fee component    ceil(amount * bps / 10000)

Curve: q is the curve-side quote amount. A buyer pays q + protocol fee + creator fee; a seller
receives q - protocol fee - creator fee. Virtual and real reserves move by t and q only.
Pool: E = quote vault + virtual quote reserves. The LP fee stays in the pool; protocol and
creator fees leave it. Fees kept in the pool by v2 trades move vault and virtual reserves in
opposite directions and leave E unchanged, so (B, E) is the pricing state. The virtual part
(`virt`, a boost) only sets the price: a sell can never pay out more than the real vault E - virt
(program error 6063), so such a sell fails.
"""

from dataclasses import dataclass, replace

BPS = 10_000


def fee(amount, bps):
    return -(-amount * bps // BPS)


def net_for_budget(budget, bps_list):
    """Largest n with n + sum(ceil(n * bps / 10000)) <= budget."""
    if budget <= 0:
        return 0
    n = budget * BPS // (BPS + sum(bps_list))
    while n > 0 and n + sum(fee(n, b) for b in bps_list) > budget:
        n -= 1
    while n + 1 + sum(fee(n + 1, b) for b in bps_list) <= budget:
        n += 1
    return n


# ----------------------------------------------------------------------------- curve


@dataclass(frozen=True, slots=True)
class Curve:
    vt: int  # virtual token reserves
    vq: int  # virtual quote reserves
    rt: int  # real token reserves
    rq: int  # real quote reserves
    fee_bps: int
    creator_bps: int

    @property
    def complete(self):
        return self.rt == 0

    def price(self):
        """Spot quote per token (float, for marks and reporting only)."""
        return self.vq / self.vt


@dataclass(frozen=True, slots=True)
class Fill:
    tokens: int  # tokens moved (bought or sold)
    curve_quote: int  # quote moving into / out of the venue's pricing state
    fees: int  # fees paid by the trader on top (buy) or deducted (sell)
    trader_quote: int  # quote paid by the trader (buy) or received (sell)
    state: object  # venue state after the fill
    completes: bool = False


def curve_buy_exact_in(c, budget):
    """Spend at most `budget` lamports (fees included). Caps at the remaining real tokens."""
    if c.complete or budget <= 0:
        return None
    q = net_for_budget(budget, [c.fee_bps, c.creator_bps])
    if q <= 1:
        return None
    t = (q - 1) * c.vt // (c.vq + q - 1)
    if t >= c.rt:
        return curve_buy_exact_out(c, c.rt)
    if t <= 0:
        return None
    f = fee(q, c.fee_bps) + fee(q, c.creator_bps)
    s = replace(c, vt=c.vt - t, vq=c.vq + q, rt=c.rt - t, rq=c.rq + q)
    return Fill(t, q, f, q + f, s)


def curve_buy_exact_out(c, t):
    """Buy exactly t tokens (t <= remaining real tokens)."""
    if c.complete or t <= 0 or t > c.rt or t >= c.vt:
        return None
    q = t * c.vq // (c.vt - t) + 1
    f = fee(q, c.fee_bps) + fee(q, c.creator_bps)
    s = replace(c, vt=c.vt - t, vq=c.vq + q, rt=c.rt - t, rq=c.rq + q)
    return Fill(t, q, f, q + f, s, completes=s.rt == 0)


def curve_sell(c, t):
    if c.complete or t <= 0:
        return None
    q = t * c.vq // (c.vt + t)
    if q > c.rq:
        q = c.rq
    f = fee(q, c.fee_bps) + fee(q, c.creator_bps)
    s = replace(c, vt=c.vt + t, vq=c.vq - q, rt=c.rt + t, rq=c.rq - q)
    return Fill(t, q, f, max(0, q - f), s)


# ----------------------------------------------------------------------------- pool


@dataclass(frozen=True, slots=True)
class Pool:
    b: int  # base (token) reserves
    e: int  # effective quote reserves = quote vault + virtual quote reserves
    lp_bps: int
    protocol_bps: int
    creator_bps: int
    cashback_bps: int = 0
    virt: int = 0  # pricing-only quote (boost); the real vault is e - virt

    def price(self):
        return self.e / self.b

    @property
    def real(self):
        return self.e - self.virt

    @property
    def fee_list(self):
        return [self.lp_bps, self.protocol_bps, self.creator_bps, self.cashback_bps]


def _fees(p, x):
    lp = fee(x, p.lp_bps)
    return lp, lp + fee(x, p.protocol_bps) + fee(x, p.creator_bps) + fee(x, p.cashback_bps)


def pool_buy_exact_in(p, budget):
    """Spend at most `budget` quote (fees included), like buy_exact_quote_in."""
    if p.b <= 0 or p.e <= 0:
        return None
    n = net_for_budget(budget, p.fee_list)
    if n <= 1:
        return None
    b = p.b * (n - 1) // (p.e + n - 1)
    if b <= 0 or b >= p.b:
        return None
    lp, f = _fees(p, n)
    return Fill(b, n, f, n + f, replace(p, b=p.b - b, e=p.e + n + lp))


def pool_buy_exact_out(p, b):
    if b <= 0 or b >= p.b or p.e <= 0:
        return None
    q = -(-p.e * b // (p.b - b))
    lp, f = _fees(p, q)
    return Fill(b, q, f, q + f, replace(p, b=p.b - b, e=p.e + q + lp))


def pool_sell(p, a):
    """Sell `a` tokens; None if the payout exceeds the real vault (the program rejects it)."""
    if a <= 0 or p.b <= 0 or p.e <= 0:
        return None
    gross = p.e * a // (p.b + a)
    if gross > p.real:
        return None
    lp, f = _fees(p, gross)
    return Fill(a, gross, f, max(0, gross - f), replace(p, b=p.b + a, e=p.e - (gross - lp)))


def pool_sell_capped(p, a):
    """Our own exit: sell as many of `a` tokens as the real vault can pay out (the rest stays
    unsold and is written off by the caller). Fill.tokens tells how many were sold."""
    f = pool_sell(p, a)
    if f is not None or a <= 0 or p.b <= 0 or p.e <= 0 or p.virt <= 0:
        return f
    lo, hi = 0, a  # largest k with gross(k) <= real; gross is increasing in k
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if p.e * mid // (p.b + mid) <= p.real:
            lo = mid
        else:
            hi = mid - 1
    return pool_sell(p, lo) if lo > 0 else None


def pool_withdraw(p, lp_in, lp_supply):
    """Remove liquidity: base and real quote leave pro rata; virtual quote stays."""
    if lp_supply <= 0:
        return None
    return replace(p, b=p.b - p.b * lp_in // lp_supply, e=p.e - p.real * lp_in // lp_supply)


def pool_deposit(p, base_in, quote_in):
    return replace(p, b=p.b + base_in, e=p.e + quote_in)
