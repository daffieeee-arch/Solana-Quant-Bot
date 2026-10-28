"""venues.py's fill functions computed by lab-core (Rust, through the PyO3 module `labcore`), behind LAB_CORE=rust:
the same names, arguments and dataclasses, so a tournament with and without it can be compared row for row (M0 of
the Rust live route, lab/docs/rust-live-route-design-2026-10-16.md §12). Without `labcore` installed, setting
LAB_CORE=rust fails at import: never a silent fallback to Python. A division by zero, possible only with a negative
virtual quote (outside every real state), raises ZeroDivisionError in Python and a pyo3 PanicException from lab-core
(Rust panics on a zero divisor): either way the fill raises, but the exception's type differs.

Build: `maturin build --release -m lab/core-py/Cargo.toml` (the NAS bookworm runner), then pip-install the wheel.
"""

from dataclasses import replace

import labcore as _c

from .venues import Curve, Fill, Pool, POOL_EXACT_IN_RULE  # noqa: F401 (the dataclasses stay venues.py's)

# An old installed extension must not silently run rule 1 behind rule-2 Python code.
if getattr(_c, "POOL_EXACT_IN_RULE", None) != POOL_EXACT_IN_RULE:
    raise RuntimeError("labcore exact-in rule mismatch: rebuild the extension with this lab source")

__all__ = ["fee", "net_for_budget", "curve_buy_exact_in", "curve_buy_exact_out", "curve_sell", "pool_buy_exact_in",
           "pool_buy_exact_out", "pool_sell", "pool_sell_capped", "pool_withdraw", "pool_deposit", "rev_buy_token",
           "rev_sell_token"]


def _curve_args(c):
    return c.vt, c.vq, c.rt, c.rq, c.fee_bps, c.creator_bps


def _pool_args(p):
    return p.b, p.e, p.lp_bps, p.protocol_bps, p.creator_bps, p.cashback_bps, p.virt


def _cfill(c, r):
    if r is None:
        return None
    t, q, f, tq, vt, vq, rt, rq, completes = r
    return Fill(t, q, f, tq, replace(c, vt=vt, vq=vq, rt=rt, rq=rq), completes)


def _pfill(p, r):
    if r is None:
        return None
    t, q, f, tq, b, e = r
    return Fill(t, q, f, tq, replace(p, b=b, e=e))


def fee(amount, bps):
    return _c.fee(amount, bps)


def net_for_budget(budget, bps_list):
    return _c.net_for_budget(budget, list(bps_list))


def curve_buy_exact_in(c, budget):
    return _cfill(c, _c.curve_buy_exact_in(*_curve_args(c), budget))


def curve_buy_exact_out(c, t):
    return _cfill(c, _c.curve_buy_exact_out(*_curve_args(c), t))


def curve_sell(c, t):
    return _cfill(c, _c.curve_sell(*_curve_args(c), t))


def pool_buy_exact_in(p, budget):
    return _pfill(p, _c.pool_buy_exact_in(*_pool_args(p), budget))


def pool_buy_exact_out(p, b):
    return _pfill(p, _c.pool_buy_exact_out(*_pool_args(p), b))


def pool_sell(p, a):
    return _pfill(p, _c.pool_sell(*_pool_args(p), a))


def pool_sell_capped(p, a):
    return _pfill(p, _c.pool_sell_capped(*_pool_args(p), a))


def pool_withdraw(p, lp_in, lp_supply):
    r = _c.pool_withdraw(*_pool_args(p), lp_in, lp_supply)
    return None if r is None else replace(p, b=r[0], e=r[1])


def pool_deposit(p, base_in, quote_in):
    return replace(p, b=p.b + base_in, e=p.e + quote_in)


def rev_buy_token(p, sol):
    return _pfill(p, _c.rev_buy_token(*_pool_args(p), sol))


def rev_sell_token(p, tokens):
    return _pfill(p, _c.rev_sell_token(*_pool_args(p), tokens))
