"""Mayhem curves (2026-10-16): the agent's trade resets the curve's virtual quote reserves, and the replay refuses
to re-execute through it; every universe excludes mayhem tokens and pools.

The fixture is real dev2 data (mint BUwW33…pump, slots 449305384-449305385): a third-party buy, the Mayhem agent's
sell (tx 4MA6AK…, priced on the previous state, then virtual quote 2.104 -> 1.286 SOL) and the two trades after it,
which pump priced on the reset state."""

from types import SimpleNamespace

import duckdb

from backtest import mechanics, replay, streams
from backtest import strategies as S
from backtest import venues as V
from backtest.costs import Scenario
from backtest.replay import MAYHEM_AGENT, Holdings, Stream, Tau, simulate_curve_position
from backtest.strategies import TpSlTime

MINT, CREATE, CREATOR = "BUwW33VjnKhT9oEypxemYwRqFahUvLfxUbLwJnErpump", 449305214, "5mbo1CogP1VEyHExZCkaegSwXwdrbdomQJSZTEHSnJdJ"
MAYHEM_PROGRAM, GMGN = "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e", "GMgnVFR8Jb39LoXsEVzb3DvBy3ywCmdmJquHUy1Lrkqb"
# streams.CURVE_COLS
ROWS = [
    (449305384, 172, 3, 6, "buy", True, 12960436075269, 29629628, 281482, 88889, 95, 30, 966974907857477, 2240288794,
     687074907857477, 298007212, "8u91QkqsN57EMq2jATm7JjQG2uRxeUotJbdcnAvGvHYJ", None, 30000001, None, None, GMGN),
    (449305385, 213, 2, 3, "sell", False, 62486056549039, 135980689, 0, 0, 0, 0, 1029460964406516, 1286285098,
     749560964406516, 162026523, MAYHEM_AGENT, None, None, None, 135980689, MAYHEM_PROGRAM),
    (449305385, 477, 3, 5, "buy", True, 11721829776131, 14814813, 140741, 44445, 95, 30, 1017739134630385, 1301099911,
     737839134630385, 176841336, "Hx44ecKZzK2UqZfVSxBp6B46jLBc2KGUwp4x9bLoR9GJ", None, 15750000, None, None, replay.PUMP),
    (449305385, 654, 3, 5, "buy_exact_sol_in", True, 28963467184867, 38112149, 362066, 114337, 95, 30, 988775667445518,
     1339212060, 708875667445518, 214953485, "GGyXEm6Bf9EqCikZiKC1wsmYfmjBCevUvYhQj7YmmZNt", 38588552, None, 27254791778752,
     None, replay.PUMP),
]


def legs():
    return streams._curve_legs(ROWS)


def reserves(c):
    return c.vt, c.vq, c.rt, c.rq  # a row's state also carries its own fee tiers


def test_the_agent_trade_is_priced_on_the_previous_state_then_resets_virtual_quote():
    before, agent, buy, buy_in = legs()
    no_reset = V.curve_sell(before.post, agent.t)
    assert no_reset.curve_quote == agent.q  # the agent's sell: the previous state's price
    assert (agent.post.vt, agent.post.rt, agent.post.rq) == (no_reset.state.vt, no_reset.state.rt, no_reset.state.rq)
    assert no_reset.state.vq == 2_104_308_105 and agent.post.vq == 1_286_285_098  # ... then the reset
    assert reserves(agent.pre) != reserves(before.post)  # so a pre-state derived from the agent's own row is not the real one


def test_the_next_trades_continue_the_logged_reset_state_exactly():
    before, agent, buy, buy_in = legs()
    assert reserves(buy.pre) == reserves(agent.post) and reserves(buy_in.pre) == reserves(buy.post)
    assert replay._hist_state(legs(), 2) == agent.post  # an insertion after the agent sees the logged state
    tau, diag = Tau("inf"), {"sells_dropped": 0, "sells_scaled": 0}
    state, tokens = replay._rerun_curve(agent.post, buy, tau, Holdings(exact=True), diag)
    assert tokens == buy.t and reserves(state) == reserves(buy.post)  # pump priced the buy on the reset state
    state, tokens = replay._rerun_curve(state, buy_in, tau, Holdings(exact=True), diag)
    assert tokens == buy_in.t and reserves(state) == reserves(buy_in.post)
    # Without the reset the same buy costs 24,236,409 lamports, above its max_sol_cost.
    no_reset = V.curve_sell(before.post, agent.t).state
    assert V.curve_buy_exact_out(no_reset, buy.t).curve_quote == 24_236_409
    assert replay._rerun_curve(no_reset, buy, tau, Holdings(exact=True), diag) is None


def test_the_replay_refuses_the_agent_leg_and_counts_it():
    before, agent, *_ = legs()
    assert replay._rerun_curve(before.post, agent, Tau("inf"), Holdings(exact=True), {"sells_dropped": 0, "sells_scaled": 0}) is None
    scn = Scenario("base", 605_000, 100_000, 505_000, 0.97, 1_513_840, "refunded_on_full_exit", 0.0)
    # Decide at 449305384, enter at the start of 449305385 (q = 0): before the agent's transaction.
    r = simulate_curve_position(Stream(MINT, CREATE, CREATOR, legs()), 449305384, 100_000_000, 1, 0.0,
                                TpSlTime(10.0, -10.0, 1_000, scn), 0.05, Tau("inf"))
    assert r.entry_slot == 449305385 and r.mayhem_reverted == 1 and r.reverted_router == 0
    clean = simulate_curve_position(Stream(MINT, CREATE, CREATOR, [legs()[0]] + legs()[2:]), 449305384, 100_000_000, 1, 0.0,
                                    TpSlTime(10.0, -10.0, 1_000, scn), 0.05, Tau("inf"))
    assert clean.mayhem_reverted == 0


def test_one_agent_address():
    assert MAYHEM_AGENT == S.MAYHEM_AGENT == mechanics.MAYHEM_AGENT


# ----------------------------------------------------------------------------- universes

CLOCK = S.Clock({1044: 267.3})
DEV = (1044 * S.EPOCH_SLOTS, 1046 * S.EPOCH_SLOTS)
SOLQ = S.SOL_QUOTES[0]


def _store():
    """Three graduated tokens with the same M1 run: a clean one, one whose pool is mayhem and one whose mint is."""
    lo, _ = S.decision_bounds({}, *DEV, CLOCK)
    create = lo + 10_000
    assert create < 1045 * S.EPOCH_SLOTS
    c = duckdb.connect()
    c.execute("CREATE TABLE mints (mint VARCHAR, mayhem BOOLEAN, quote_mint VARCHAR, create_slot BIGINT, complete_slot BIGINT)")
    c.execute("""CREATE TABLE pools (pool VARCHAR, mint VARCHAR, quote_class VARCHAR, mayhem BOOLEAN, created_in_period BOOLEAN,
                 b0 BIGINT, q0 BIGINT, create_slot BIGINT)""")
    c.execute("""CREATE TABLE pool (pool VARCHAR, kind VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT,
                 sol_depth BIGINT, trader VARCHAR, token_buy BOOLEAN, farmer BOOLEAN, e BIGINT, e_delta BIGINT, b BIGINT,
                 base BIGINT, sol_amount BIGINT)""")
    for name, pool_mayhem, mint_mayhem in (("ok", False, False), ("maypool", True, False), ("maymint", False, True)):
        c.execute("INSERT INTO mints VALUES (?, ?, ?, ?, ?)", [f"M_{name}", mint_mayhem, SOLQ, create - 1_000, create - 1])
        c.execute("INSERT INTO pools VALUES (?, ?, 'sol', ?, true, 1000, 1000, ?)", [f"P_{name}", f"M_{name}", pool_mayhem, create])
        c.execute("INSERT INTO pool VALUES (?, 'buy', ?, 0, 0, 0, 1000, 'T', true, false, 1000, 2000, 1000, 100, 100)",
                  [f"P_{name}", create + 5])  # price 3000 / 900 = 3.3 x the opening price, inflow 10 % of the depth
    return c, create


def test_curve_and_pool_universes_exclude_mayhem():
    c, create = _store()
    assert c.execute(f"SELECT mint FROM mints m WHERE {S.universe_sql({}, *DEV, CLOCK)} ORDER BY 1").fetchall() == [("M_maypool",), ("M_ok",)]
    # pool positions never re-execute curve legs: the pool universe drops mayhem pools (a mayhem mint's pool phase is exact)
    assert c.execute(f"SELECT pool FROM pools p WHERE {S.pool_universe_sql()} ORDER BY 1").fetchall() == [("P_maymint",), ("P_ok",)]


def test_m1_universes_exclude_mayhem_pools_and_mints():
    c, create = _store()
    assert {r[1] for r in S.m1_candidates_sql(c, {}, *DEV, CLOCK)} == {"P_ok"}
    assert {r[1] for r in S.m1x_candidates_sql(c, {}, *DEV, (60_000, 900_000), CLOCK)} == {"P_ok"}


def test_the_paper_universe_excludes_mayhem():
    from paper.engine import Engine

    toks = {"M_ok": SimpleNamespace(mayhem=False, quote_mint=SOLQ, create_slot=10, creator="C"),
            "M_may": SimpleNamespace(mayhem=True, quote_mint=SOLQ, create_slot=10, creator="C")}
    pools = {"P_ok": SimpleNamespace(mayhem=False, mint="M_ok", coin_creator="CC", orientation="normal"),
             "P_may": SimpleNamespace(mayhem=True, mint="M_ok", coin_creator="CC", orientation="normal")}
    eng = SimpleNamespace(feed=SimpleNamespace(token=toks.get, pool=pools.get), burn_slot=0, dev=(0, 1_000))
    assert [Engine._universe(eng, "curve", k)[0] for k in ("M_ok", "M_may")] == [True, False]
    assert [Engine._universe(eng, "pool", k)[0] for k in ("P_ok", "P_may")] == [True, False]
