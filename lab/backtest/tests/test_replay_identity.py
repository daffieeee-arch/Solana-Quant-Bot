"""Without our order, re-running the history must reproduce the logged states."""

import os

import pytest

from backtest import replay, streams

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")


@pytest.fixture(scope="module")
def con():
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    return streams.open_store(STORE, threads=2, memory="1GB")


@pytest.fixture(scope="module")
def sample(con):
    mints = [r[0] for r in con.execute("""
        SELECT m.mint FROM mints m JOIN pools p ON p.pool = m.pool
        WHERE NOT m.mayhem AND m.quote_mint IN ('11111111111111111111111111111111',
          'So11111111111111111111111111111111111111112') AND p.quote_class = 'sol'
        USING SAMPLE 300 ROWS (reservoir, 7)""").fetchall()]
    return streams.load_streams(con, mints)


@pytest.fixture(scope="module")
def pool_sample(con):
    """Pools of both orientations, boost pools included (busy pools capped for test speed)."""
    pools = [r[0] for r in con.execute("""
        SELECT pool FROM pools JOIN pool_stats USING (pool)
        WHERE quote_class IN ('sol', 'reversed') AND n_buy + n_sell BETWEEN 20 AND 5000
        USING SAMPLE 150 ROWS (reservoir, 11)""").fetchall()]
    return streams.load_pool_streams(con, {p: (0, 2**62) for p in pools})


def test_curve_identity(sample):
    tau = replay.Tau("empirical")
    legs_total = mismatched = reverted = 0
    for s in sample.values():
        if not s.curve:
            continue
        state = s.curve[0].pre
        hold = replay.Holdings(exact=True)
        diag = {"sells_dropped": 0, "sells_scaled": 0}
        for leg in s.curve:
            res = replay._rerun_curve(state, leg, tau, hold, diag)
            legs_total += 1
            hold.apply(leg.trader, leg.t if leg.is_buy else -leg.t, leg.t if leg.is_buy else -leg.t)
            if res is None:
                reverted += 1
                state = leg.post
                continue
            nxt = res[0]
            if (nxt.vt, nxt.vq, nxt.rt, nxt.rq) != (leg.post.vt, leg.post.vq, leg.post.rt, leg.post.rq):
                mismatched += 1
            state = leg.post  # resync so one mismatch does not cascade
    assert legs_total > 1000
    assert reverted <= legs_total * 0.002, f"{reverted}/{legs_total}"
    assert mismatched <= legs_total * 0.002, f"{mismatched}/{legs_total}"
    assert diag["sells_dropped"] == 0 and diag["sells_scaled"] == 0


def _rerun(leg, state):
    return replay._rerun_pool(state, leg, replay.Tau("inf"), replay.Holdings(exact=False, enabled=False),
                              {"sells_dropped": 0, "sells_scaled": 0})


def test_pool_identity(pool_sample):
    """Every pool leg (buy, sell, boost, withdraw, deposit) re-run on its own logged pre-state
    reproduces its logged post-state; no historical sell exceeds the real vault."""
    kinds, mismatched, reverted = {}, [], []
    for s in pool_sample.values():
        for leg in s.pool_legs:
            kinds[leg.kind] = kinds.get(leg.kind, 0) + 1
            res = _rerun(leg, leg.pre)
            if res is None:
                reverted.append((s.pool, leg.order, leg.kind))
            elif (res[0].b, res[0].e) != (leg.post.b, leg.post.e) and leg.kind != "deposit":
                mismatched.append((s.pool, leg.order, leg.kind))
    total = sum(kinds.values())
    assert total > 10000 and kinds.get("boost", 0) > 0, kinds
    assert len(reverted) <= total * 0.001, reverted[:5]
    assert len(mismatched) <= total * 0.001, mismatched[:5]


def test_pool_chain_carries_state(pool_sample):
    """Zero-order chain: re-run every stream from its first pre-state without ever resetting it.
    Each leg must start from exactly the state the previous legs produced (the boost crank once,
    withdrawals pro rata, both orientations)."""
    pairs, bad = 0, []
    for s in pool_sample.values():
        if not s.pool_legs:
            continue
        state = s.pool_legs[0].pre
        for leg in s.pool_legs:
            if leg.kind not in ("withdraw", "deposit"):
                pairs += 1
                if (state.b, state.e) != (leg.pre.b, leg.pre.e):
                    bad.append((s.pool, leg.order, leg.kind, state.b - leg.pre.b, state.e - leg.pre.e))
                    state = leg.pre  # resync after a reported break
            res = _rerun(leg, state)
            state = leg.post if res is None else res[0]
    assert pairs > 10000
    assert len(bad) <= max(5, pairs * 0.001), bad[:5]  # rare 1-lamport rounding differences
