"""PositionSim fed slot by slot with advance() in quiet slots (the paper engine) equals the batch replay, also for a
position that cannot sell at its deadline (a drained pool): job 90 (paper arm parity, 2c3e5fe) found the streaming
end() adding an end-of-data decision and a second attempt after advance() had taken the deadline."""

import os

import pytest

from backtest import replay, streams
from backtest.costs import Scenario
from backtest.store import load_defaults
from backtest.strategies import F7_LONG_GRID, Clock, Signal
from backtest.tournament import delay_slots, exit_rule_for

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")
POOL, T = "4cXkvtBTBSr2rs5ntkgqT22ywyKytWowBxX29q7L5ZmA", 450210851  # drained after the entry: proceeds 0
FIELDS = ("entry_slot", "tokens", "cost", "exit_decision_slot", "exit_slot", "exit_reason", "proceeds", "exit_attempts",
          "entry_failed", "skipped", "marks")


def _setup():
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    con = streams.open_store(STORE, threads=2, memory="1GB")
    clock = Clock.from_store(con)
    st = streams.load_pool_streams(con, {POOL: (T - 2_000, T + 90_000)})[POOL]
    con.close()
    params = F7_LONG_GRID[4]  # flow300_H6
    sig = Signal("F7", "flow300_H6", st.mint, T, "pool", 0, params, pool=POOL, orientation=st.orientation)
    d = delay_slots(600, T, clock)
    return st, clock, sig, d, Scenario.from_cfg("base", load_defaults())


def _streamed(st, clock, sig, d, scn, tail):
    legs = st.pool_legs
    rule = exit_rule_for(sig, scn, d, clock, st)
    r, sim, i = replay.open_pool_position(st, T, int(0.5e9), d, 0.5, rule, 0.05, replay.Tau("empirical"))
    slots = sorted({l.slot for l in legs if l.slot >= T + d})
    by = {s: [l for l in legs if l.slot == s] for s in slots}
    entry = T + d
    if entry in by:
        a = next(k for k, l in enumerate(legs) if l.slot == entry)
        sim.on_slot(entry, legs[a:a + len(by[entry])], start=i - a)
    for s in range(entry + 1, tail):
        if sim.done:
            break
        if s in by:
            sim.on_slot(s, by[s])
        else:
            sim.advance(s)  # the engine's step 5: no event of the token in this slot
    if not sim.done:
        sim.end()
    return r


def test_a_drained_position_streamed_with_advance_equals_the_batch_replay():
    st, clock, sig, d, scn = _setup()
    batch = replay.simulate_pool_position(st, T, int(0.5e9), d, 0.5, exit_rule_for(sig, scn, d, clock, st), 0.05,
                                          replay.Tau("empirical"))
    assert batch.proceeds == 0 and batch.exit_slot is None and batch.exit_attempts == 1  # the case: it never sells
    deadline = batch.exit_decision_slot
    for tail in (deadline + d + 5, deadline + 50_000):  # data ends soon after the deadline, or long after it
        streamed = _streamed(st, clock, sig, d, scn, tail)
        assert {f: getattr(streamed, f) for f in FIELDS} == {f: getattr(batch, f) for f in FIELDS}
