"""lab-core's venue math (Rust, the `labcore` module behind LAB_CORE=rust) equals backtest/venues.py exactly: the
same Fill, state and None on random and edge-case inputs (M0 of the Rust live route,
lab/docs/rust-live-route-design-2026-10-16.md §12). Skips without `labcore`; run it where the wheel is installed
(the NAS job), without LAB_CORE set so venues.py is the Python reference."""

import random

import pytest

from backtest import venues as V
from backtest.venues import Curve, Pool

core = pytest.importorskip("backtest.venues_core", exc_type=ImportError)

if V.CORE != "python":
    pytest.skip("LAB_CORE is set: venues.py is not the Python reference here", allow_module_level=True)

CURVE_FUNCS = ("curve_buy_exact_in", "curve_buy_exact_out", "curve_sell")
POOL_FUNCS = ("pool_buy_exact_in", "pool_buy_exact_out", "pool_sell", "pool_sell_capped", "rev_buy_token", "rev_sell_token")
N = 20_000


def _amount(rng):
    """Amounts across the scales that occur: lamports and token units from 0 to 1e19, with edge values. Up to 1e19
    reaches beyond 2^63: dev2's largest token amount is 7.46e18 (LAB review of #311)."""
    return rng.choice([0, 1, 2, 3, -1, rng.randrange(1, 1_000), rng.randrange(1, 10**9), rng.randrange(1, 10**13),
                       rng.randrange(1, 10**16), rng.randrange(1 << 63, 10**19)])


def _curve(rng):
    vt = rng.randrange(10**12, 2 * 10**15)
    return Curve(vt, rng.randrange(10**8, 10**12), rng.choice([0, 1, rng.randrange(0, vt)]), rng.randrange(0, 10**12),
                 rng.choice([0, 95, rng.randrange(0, 300)]), rng.choice([0, 5, rng.randrange(0, 300)]))


def _pool(rng):
    e = rng.choice([0, 1, rng.randrange(1, 10**16)])
    return Pool(rng.choice([0, 1, rng.randrange(1, 10**16)]), e, *(rng.choice([0, 2, 20, rng.randrange(0, 200)]) for _ in range(4)),
                virt=rng.choice([0, 0, rng.randrange(-10**10, 10**10), rng.randrange(0, e + 1)]))


def test_fees_and_budgets():
    rng = random.Random(1)
    for _ in range(N):
        a, bps = _amount(rng), rng.randrange(0, 400)
        assert core.fee(a, bps) == V.fee(a, bps)
        lst = [rng.randrange(0, 300) for _ in range(rng.randrange(0, 5))]
        assert core.net_for_budget(a, lst) == V.net_for_budget(a, lst)


@pytest.mark.parametrize("name", CURVE_FUNCS)
def test_curve_fills(name):
    rng = random.Random(sum(map(ord, name)))  # a stable seed per function
    py, rs = getattr(V, name), getattr(core, name)
    nones = 0
    for _ in range(N):
        c, x = _curve(rng), _amount(rng)
        want = py(c, x)
        assert rs(c, x) == want, (c, x)
        nones += want is None
    assert 0 < nones < N  # both outcomes were exercised


@pytest.mark.parametrize("name", POOL_FUNCS)
def test_pool_fills(name):
    rng = random.Random(sum(map(ord, name)))  # a stable seed per function
    py, rs = getattr(V, name), getattr(core, name)
    nones = 0
    for _ in range(N):
        p, x = _pool(rng), _amount(rng)
        want = py(p, x)
        assert rs(p, x) == want, (p, x)
        nones += want is None
    assert 0 < nones < N


def test_withdraw_and_deposit():
    rng = random.Random(3)
    for _ in range(N):
        p = _pool(rng)
        supply = rng.choice([0, -1, rng.randrange(1, 10**12)])
        lp_in = rng.randrange(0, max(1, supply) + 1)
        assert core.pool_withdraw(p, lp_in, supply) == V.pool_withdraw(p, lp_in, supply)
        b, q = _amount(rng), _amount(rng)
        assert core.pool_deposit(p, b, q) == V.pool_deposit(p, b, q)


def test_a_float_is_refused_not_rounded():
    with pytest.raises(TypeError):
        core.curve_sell(Curve(10**15, 30 * 10**9, 8 * 10**14, 0, 95, 5), 1e9)
