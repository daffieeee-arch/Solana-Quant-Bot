"""Multi-hop swaps and exact-in curve variants on real rows (fixture from the epoch-1054 smoke extract).

pump multi_hop_curve_swap and PumpSwap multi_hop_swap name no pool or mint account (the hops are
remaining accounts), so the extractor leaves ix_pool / ix_base_mint NULL for their rows; the store
reads pool, mint and trader from the events. These tests run the store's row SQL on the fixture,
turn the rows into legs and re-execute them:

* every buy instruction in the IDLs is classified the way its arguments say (a quote budget:
  exact-in; a token amount with a cost cap: exact-out);
* on its logged pre-state every hop reproduces its logged fill: the hop budget is q + fee, not
  the instruction's amount_in, which is the route input shared by all hops;
* after a counterfactual buy moved the price, exact-in hops and buy_exact_quote_in_v3 buys spend
  the same budget for fewer tokens, while buy_v3 (exact-out) keeps its tokens and pays more.
"""

import json
import os
from collections import Counter
from dataclasses import replace

import pytest

from backtest import data, replay, store, streams
from backtest import venues as V
from backtest.replay import Holdings, Tau

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "smoke1054_multihop")
IDL = os.path.join(HERE, "..", "..", "extractor", "idl")
CHUNK = (455328000, 455329000, FIX)
POOL_ROW = """kind, slot, tx_index, outer_ix, inner_ix, parent_ix_disc, b, vault, vq, vault + vq AS e, lp_bps, protocol_bps,
              creator_bps, cashback_bps, base, e_delta, quote_user, limit_quote, limit_base, NULL AS boost_left,
              NULL AS lp_amount, NULL AS lp_supply, trader, outer_program, false AS farmer"""


@pytest.fixture(scope="module")
def con():
    c = data.connect(threads=1, memory="512MB")
    yield c
    c.close()


def curve_legs(con):
    sql = store._curve_trades(store.Chunk(CHUNK), mints=None)
    return streams._curve_legs(con.execute(f"SELECT {streams.CURVE_COLS} FROM ({sql}) ORDER BY slot, tx_index, outer_ix, inner_ix")
                               .fetchall())


def pool_legs(con):
    sql = store._pool_trades(store.Chunk(CHUNK))
    return streams._pool_legs(con.execute(f"SELECT {POOL_ROW} FROM ({sql}) ORDER BY pool, slot, tx_index, outer_ix, inner_ix")
                              .fetchall())


def rerun(fn, state, leg):
    return fn(state, leg, Tau("inf"), Holdings(True), {"sells_dropped": 0, "sells_scaled": 0})


def idl_buys(name):
    with open(os.path.join(IDL, name)) as f:
        ixs = json.load(f)["instructions"]
    return {bytes(i["discriminator"]).hex(): (i["name"], {a["name"] for a in i["args"]})
            for i in ixs if i["name"].startswith(("buy", "multi_hop"))}


def test_buy_instructions_are_classified_by_their_arguments():
    curve = idl_buys("pump.json")
    assert {"buy_exact_quote_in_v3", "multi_hop_curve_swap"} <= {n for n, _ in curve.values()}
    for disc, (name, args) in curve.items():
        assert data.PUMP_IX.get(disc) == name, name
        assert (name in replay.EXACT_IN_CURVE) == ("max_sol_cost" not in args), name
    for disc, (name, args) in idl_buys("pump_amm.json").items():
        assert (disc in data.POOL_EXACT_OUT) == ("base_amount_out" in args), name


def test_route_amount_in_is_not_a_hop_budget(con):
    # One multi_hop_curve_swap with two curve hops: both events carry the route's amount_in; the
    # second hop spends what the first one bought.
    rows = con.execute(f"""
      SELECT slot, tx_index, outer_ix, token_amount, COALESCE(quote_amount, sol_amount) + fee + creator_fee AS spent,
             TRY_CAST(json_extract_string(parent_ix_args, '$.amount_in') AS HUGEINT) AS amount_in
      FROM {data.parquet('pump/TradeEvent', [CHUNK])}
      WHERE parent_ix_name = 'multi_hop_curve_swap' AND is_buy ORDER BY slot, tx_index, outer_ix, inner_ix""").fetchall()
    by_ix = {}
    for r in rows:
        by_ix.setdefault(r[:3], []).append(r[3:])
    two = [hops for hops in by_ix.values() if len(hops) == 2]
    assert len(two) == 1
    (t1, s1, a1), (_, s2, a2) = two[0]
    assert a1 == a2 == s1 and s2 == t1 and s2 != a2
    assert all(leg.budget is None for leg in curve_legs(con) if leg.variant == "multi_hop_curve_swap")


def test_curve_legs_reproduce_their_fill_and_keep_their_budget(con):
    legs = curve_legs(con)
    n = Counter((l.variant, l.is_buy) for l in legs)
    assert n[("multi_hop_curve_swap", True)] == 12 and n[("multi_hop_curve_swap", False)] == 11
    assert n[("buy_exact_quote_in_v3", True)] == 20 and n[("buy_v3", True)] == 10
    for leg in legs:
        state, delta = rerun(replay._rerun_curve, leg.pre, leg)
        assert delta == (leg.t if leg.is_buy else -leg.t), leg.order
        assert (state.vt, state.vq, state.rt, state.rq) == (leg.post.vt, leg.post.vq, leg.post.rt, leg.post.rq), leg.order
        if not leg.is_buy:
            continue
        # A counterfactual buy of 2% of the virtual quote first; the trade's own limits are left
        # out (direct=False with tau inf), so only the exact-in / exact-out rule decides.
        moved = V.curve_buy_exact_in(leg.pre, leg.pre.vq // 50).state
        state, tokens = rerun(replay._rerun_curve, moved, replace(leg, direct=False))
        spent = state.vq - moved.vq
        if leg.variant == "buy_v3":
            assert tokens == leg.t and spent > leg.q, leg.order
        else:
            assert spent == leg.q and tokens < leg.t, leg.order


def test_pool_hops_are_exact_in_with_the_hop_input(con):
    legs = pool_legs(con)
    assert Counter(l.kind for l in legs) == {"buy": 20, "sell": 17}
    # Every route in the fixture has min_amount_out = 1: it binds the last hop only, earlier hops log 0.
    route_min = con.execute(f"""SELECT DISTINCT json_extract_string(parent_ix_args, '$.min_amount_out')
                                FROM {data.parquet('pump_amm/BuyEvent', [CHUNK])} UNION
                                SELECT DISTINCT json_extract_string(parent_ix_args, '$.min_amount_out')
                                FROM {data.parquet('pump_amm/SellEvent', [CHUNK])}""").fetchall()
    assert route_min == [("1",)]
    for leg in legs:
        assert not leg.exact_out
        if leg.kind == "buy":
            # The hop's quote limit is its own input (max_quote_amount_in = quote_amount_in).
            assert leg.limit_quote == leg.quote_net and leg.limit_base in (0, 1), leg.order
        else:
            assert leg.limit_quote in (0, 1), leg.order
        state, delta = rerun(replay._rerun_pool, leg.pre, leg)
        assert delta == (leg.base if leg.kind == "buy" else -leg.base), leg.order
        assert (state.b, state.e) == (leg.post.b, leg.post.e), leg.order
        if leg.kind != "buy":
            continue
        moved = V.pool_buy_exact_in(leg.pre, leg.pre.e // 50).state
        state, tokens = rerun(replay._rerun_pool, moved, replace(leg, direct=False))
        assert state.e - moved.e == leg.post.e - leg.pre.e and tokens < leg.base, leg.order
