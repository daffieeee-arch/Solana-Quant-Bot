"""Source parity, not merely agreement between our two implementations."""
import csv
from dataclasses import replace
from pathlib import Path

import pytest

from backtest import venues as V
from backtest.replay import PoolLeg, Tau, _rerun_pool

FIXTURE = Path(__file__).resolve().parents[2] / 'core/tests/data/pumpswap-exact-in.tsv'
with FIXTURE.open() as f:
    ROWS = list(csv.DictReader(f, delimiter='\t'))


def inputs(row):
    n = lambda k: int(row[k])
    p = V.Pool(n('b'), n('e'), n('lp_bps'), n('protocol_bps'), n('creator_bps'), virt=n('virt'))
    return n, p


@pytest.mark.parametrize('row', ROWS, ids=lambda r: f"{r['slot']}-{r['tx']}-{r['outer']}-{r['inner']}")
def test_recorded_exact_in(row):
    n, p = inputs(row)
    f = V.pool_buy_exact_in(p, n('budget'))
    assert f is not None
    assert (f.tokens, f.curve_quote, f.fees, f.trader_quote, f.state.b, f.state.e) == (
        n('tokens'), n('net'), n('lp_fee') + n('protocol_fee') + n('creator_fee'),
        n('debit'), n('post_b'), n('post_e'))
    assert f.state.virt == p.virt


def test_real_replay_limit_boundary():
    """Authentic input, synthetic min-out one above recorded output: old overfill passed."""
    n, p = inputs(ROWS[0])
    post = replace(p, b=n('post_b'), e=n('post_e'))
    leg = PoolLeg(n('slot'), n('tx'), (n('slot'), n('tx'), n('outer'), n('inner')),
                  'buy', False, n('tokens'), n('debit'), p, post, 'fixture',
                  limit_quote=n('budget'), limit_base=n('tokens'), direct=True)
    accepted = _rerun_pool(p, leg, Tau('zero'), None, {})
    assert accepted is not None and accepted[0] == post
    assert _rerun_pool(p, replace(leg, limit_base=n('tokens') + 1), Tau('zero'), None, {}) is None


def test_unproven_cashback_keeps_legacy_model():
    # Compatibility, NOT evidence of on-chain correctness for cashback.
    n, p = inputs(ROWS[0]); p = replace(p, cashback_bps=5)
    f = V.pool_buy_exact_in(p, n('budget'))
    assert (f.tokens, f.curve_quote, f.fees, f.trader_quote, f.state.b, f.state.e) == (
        2077539380, 2553264, 31918, 2585182, 123303675044889, 151540822453)


@pytest.mark.parametrize('budget', [-1, 0, 1, 2])
def test_unfunded_net_refused(budget):
    _, p = inputs(ROWS[0])
    assert V.pool_buy_exact_in(p, budget) is None
