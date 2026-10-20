"""Route (ii)'s entry position apart from the exit's (research/spec-route-ii-sneller-instappen-2026-10-20.md v0.3 §3.2,
§4): replay._entry_index on a synthetic slot (k token-buying transactions, a round trip counting once, a sell not at
all, capped past the slot's buys), and on a dev2 pool the properties the smoke checks on a day: no entry and the same
entry as (d, q) give one position; E0 (= K0, after no transaction of t + 1) fills on the state (d_e 0, q 1.0) fills on,
one entry slot later; K past the entry slot's buys is D1 at q 1.0; the exit keeps the paper's (d, q 0.5)."""

import dataclasses
import os
from types import SimpleNamespace

import pytest

from backtest import replay, streams
from backtest.costs import Scenario
from backtest.store import load_defaults
from backtest.strategies import Clock, Signal
from backtest.tournament import ENTRIES, delay_slots, entry_code, exit_rule_for

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")
POOL, T = "4cXkvtBTBSr2rs5ntkgqT22ywyKytWowBxX29q7L5ZmA", 450210851  # test_stream_end's reversed pool
FIELDS = ("entry_slot", "tokens", "cost", "exit_decision_slot", "exit_slot", "exit_reason", "proceeds", "exit_attempts",
          "entry_failed", "skipped", "marks")


def _slot(normal):
    """Slot 101 of a pool: A [buy], B [buy, sell] (a round trip), C [sell], D [buy, buy]; one leg before and after.
    The kinds are the pool's: a token buy is a pool buy in a normal pool and a pool sell in a reversed one."""
    b, s = ("buy", "sell") if normal else ("sell", "buy")
    leg = lambda slot, tx, kind: SimpleNamespace(slot=slot, tx=tx, kind=kind)  # noqa: E731
    return [leg(100, 5, b), leg(101, 0, b), leg(101, 1, b), leg(101, 1, s), leg(101, 2, s), leg(101, 3, b), leg(101, 3, b),
            leg(102, 0, s)]


@pytest.mark.parametrize("orientation", ["normal", "reversed"])
def test_k_counts_token_buying_transactions(orientation):
    legs = _slot(orientation == "normal")
    got = {k: replay._entry_index(legs, 101, ("k", k), orientation) for k in range(5)}
    # k 0 before A; k 1 after A; k 2 after the round trip B (both its legs; C's sell does not count); k 3 after D,
    # the slot end with 3 token-buying transactions; k 4: more than the slot has, its end, capped
    assert got == {0: (1, False, 0), 1: (2, False, 1), 2: (4, False, 2), 3: (7, False, 4), 4: (7, True, 4)}
    # ("q", q) places as _insertion_index: q 0.5 of 4 transactions is before the third (C)
    assert replay._entry_index(legs, 101, ("q", 0.5), orientation) == (replay._insertion_index(legs, 101, 0.5), None, 2) == (4, None, 2)
    assert replay._entry_index(legs, 101, ("q", 1.0), orientation) == (7, None, 4)
    # an empty slot: k 0 is its start, any k > 0 its end, capped
    assert replay._entry_index(legs, 103, ("k", 0), orientation)[:2] == (8, False)
    assert replay._entry_index(legs, 103, ("k", 1), orientation) == (8, True, 0)
    with pytest.raises(ValueError):
        replay._entry_index(legs, 101, ("x", 1), orientation)


def test_the_entry_codes():
    assert entry_code("E0") == (1, ("k", 0), None) == entry_code("K0")
    assert entry_code("K5") == (1, ("k", 5), None)
    assert (entry_code("R"), entry_code("D1both"), entry_code("D1")) == ((2, ("q", 0.5), None), (1, ("q", 0.5), 1), (1, ("q", 0.5), None))
    assert set(ENTRIES) >= {"R", "D1both", "D1", "E0", "D1Q1", "D0Q1"}
    with pytest.raises(ValueError):
        entry_code("K")


def _setup():
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    con = streams.open_store(STORE, threads=2, memory="1GB")
    clock = Clock.from_store(con)
    st = streams.load_pool_streams(con, {POOL: (T - 2_000, T + 90_000)})[POOL]
    con.close()
    sig = Signal("F7", "tp4_T600", st.mint, T, "pool", 0, (0.04, 600), pool=POOL, orientation=st.orientation)
    d = delay_slots(600, T, clock)
    rule = lambda: exit_rule_for(sig, Scenario.from_cfg("base", load_defaults()), d, clock, st)  # noqa: E731
    return st, d, rule


def _sim(st, d, rule, entry=None):
    return replay.simulate_pool_position(st, T, int(0.5e9), d, 0.5, rule(), 0.05, replay.Tau("empirical"), entry=entry)


def _fields(r):
    return {f: getattr(r, f) for f in FIELDS}


def test_no_entry_and_the_same_entry_give_one_position():
    st, d, rule = _setup()
    plain, same = _sim(st, d, rule), _sim(st, d, rule, entry=(d, ("q", 0.5)))
    assert _fields(plain) == _fields(same) and plain.capped is None and plain.k_tx is None and same.k_tx is not None
    assert plain.tokens > 0


def test_e0_fills_on_the_state_d0_at_q_one_fills_on_one_slot_later():
    st, d, rule = _setup()
    e0, d0 = _sim(st, d, rule, entry=entry_code("E0")[:2]), _sim(st, d, rule, entry=entry_code("D0Q1")[:2])
    assert (e0.cost, e0.tokens) == (d0.cost, d0.tokens) and e0.tokens > 0
    assert (e0.entry_slot, d0.entry_slot) == (T + 1, T) and e0.capped is False and e0.k_tx == 0


def test_k_past_the_slots_buys_is_d1_at_q_one():
    st, d, rule = _setup()
    k, q1 = _sim(st, d, rule, entry=(1, ("k", 10**6))), _sim(st, d, rule, entry=entry_code("D1Q1")[:2])
    assert _fields(k) == _fields(q1) and k.capped is True and q1.capped is None and k.k_tx == q1.k_tx


def test_the_exit_keeps_the_papers_position():
    st, d, rule = _setup()
    r, sim, i = replay.open_pool_position(st, T, int(0.5e9), d, 0.5, rule(), 0.05, replay.Tau("empirical"), entry=(1, ("k", 1)))
    assert sim is not None and (sim.d, sim.q_frac, r.entry_slot) == (d, 0.5, T + 1)
    assert dataclasses.is_dataclass(r) and r.k_tx is not None
