"""Without our order, re-running the history must reproduce the logged states."""

import os

import pytest

from backtest import replay, streams

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev.duckdb")


@pytest.fixture(scope="module")
def sample():
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    con = streams.open_store(STORE, threads=2, memory="2GB")
    mints = [r[0] for r in con.execute("""
        SELECT mint FROM mints WHERE NOT mayhem AND quote_mint IN ('11111111111111111111111111111111',
          'So11111111111111111111111111111111111111112') AND pool IS NOT NULL
        USING SAMPLE 300 ROWS (reservoir, 7)""").fetchall()]
    return streams.load_streams(con, mints)


def test_curve_identity(sample):
    tau = replay.Tau("inf")
    legs_total = mismatched = reverted = 0
    for s in sample.values():
        if not s.curve:
            continue
        state = s.curve[0].pre
        for leg in s.curve:
            nxt = replay._rerun_curve(state, leg, tau)
            legs_total += 1
            if nxt is None:
                reverted += 1
                state = leg.post
                continue
            if (nxt.vt, nxt.vq, nxt.rt, nxt.rq) != (leg.post.vt, leg.post.vq, leg.post.rt, leg.post.rq):
                mismatched += 1
            state = leg.post  # resync so one mismatch does not cascade
    assert legs_total > 1000
    assert reverted == 0
    assert mismatched <= legs_total * 0.002, f"{mismatched}/{legs_total}"


def test_pool_identity(sample):
    legs_total = mismatched = reverted = 0
    for s in sample.values():
        for leg in s.pool_legs:
            if leg.kind not in ("buy", "sell"):
                continue
            nxt = replay._rerun_pool(leg.pre, leg)
            legs_total += 1
            if nxt is None:
                reverted += 1
            elif (nxt.b, nxt.e) != (leg.post.b, leg.post.e):
                mismatched += 1
    assert legs_total > 1000
    assert reverted <= legs_total * 0.001, f"{reverted}/{legs_total}"
    assert mismatched == 0


def test_pool_chain_matches_next_pre(sample):
    """Post-state computed from one event equals the next event's logged pre-state."""
    pairs = bad = 0
    for s in sample.values():
        legs = [l for l in s.pool_legs if l.kind in ("buy", "sell")]
        for a, b in zip(legs, legs[1:]):
            pairs += 1
            bad += (a.post.b, a.post.e) != (b.pre.b, b.pre.e)
    assert pairs > 1000
    assert bad <= pairs * 0.01, f"{bad}/{pairs}"
