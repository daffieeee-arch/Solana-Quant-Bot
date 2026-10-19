"""The shared golden cases of M1 C1b (paper/tests/data/c1b_signal_cases.json, research/m1-c1b-signals-spec-2026-10-19.md
§6.2): the paper engine still writes exactly the stored signal rows and signal state on every case, the file keeps its
format for the Rust reader, and the stored rows show what each case is for (the four required tests: mint absent,
quote_mint NULL, T1, T2; and N2, the hour rollover, the H gates and the 1041/1042 clock change).

  C1B_CASES_REGEN=1 pytest paper/tests/test_signal_cases.py      # rewrite the file from the engine first
"""

import json
import os

import pytest

from paper.journal import Journal
from paper.journal_diff import FARM_GATES, signal_row
from paper.tests import c1b_signal_cases as G

if os.environ.get("C1B_CASES_REGEN") == "1":
    G.generate()
with open(G.PATH) as f:
    DOC = json.load(f)
CASES = {c["name"]: c for c in DOC["cases"]}
SOL = 1_000_000_000
HEAD = ["venue", "key", "slot", "tx", "outer", "inner", "block_time"]


@pytest.mark.parametrize("name", list(CASES))
def test_the_engine_writes_each_cases_expectations(name):
    case = CASES[name]
    rows, state = G.run(case)
    assert rows == case["expect"], name
    assert state == case["expect_state"], name


def test_the_files_format(tmp_path):
    """What the Rust reader relies on (FORMAT): the format text, the keys of a case, chain order, legs that give their
    fields back, integers that fit i64 and u64 fields that are never negative, the engine's own groups text, and rows in
    the projection's columns, sorted by key."""
    assert DOC["format"] == G.FORMAT and len(CASES) == len(DOC["cases"])
    G.check_ints(DOC)
    cols = list(signal_row({}))
    u64 = {"slot", "tx", "outer", "inner", "t", "q", "fee", "vt", "vq", "rt", "rq", "fee_bps", "creator_bps", "budget", "max_cost",
           "min_tokens", "min_out", "limit_quote", "limit_base", "boost_left", "lp_amount", "lp_supply", "lp_bps", "protocol_bps",
           "cashback_bps"}
    for case in DOC["cases"]:
        assert list(case) == list(G.CASE), case["name"]
        assert all(isinstance(k, str) and isinstance(v, float) for k, v in case["clock"].items())
        orders = [(e["slot"], e["tx"], e["outer"], e["inner"]) for e in case["events"]]
        assert orders == sorted(orders) and len(set(orders)) == len(orders), case["name"]
        for e in case["events"]:
            assert list(e) == [*HEAD, *(G.CURVE if e["venue"] == "curve" else G.POOL)]
            assert G.fields(G.event(e)) == e
            assert all(e[k] is None or e[k] >= 0 for k in u64 if k in e), e
        for t in case["tokens"]:
            assert list(t) == ["mint", "create_slot", "creator", "mayhem", "quote_mint"]
        for p in case["pools"]:
            assert list(p) == ["pool", "mint", "orientation", "quote_class", "mayhem", "coin_creator", "create_slot"]
        j = Journal(str(tmp_path / case["name"]))
        G.engine(case, j)  # raises when groups is not what the engine journals
        j.close()
        assert all(list(r) == cols for r in case["expect"])
        assert case["expect"] == sorted(case["expect"], key=lambda r: (r["family"], r["variant"], r["key"], r["t"]))
        st = case["expect_state"]  # [key, slot, depth]; [slot, tx, outer, inner, amount, farm]; [first, last]
        assert list(st) == ["first_slot", "hour", "hour_events", "farm_win", "pool_seen"]
        assert list(st["hour_events"]) == ["curve", "pool_normal", "pool_reversed"]
        assert all(len(x) == 3 and isinstance(x[0], str) for xs in st["hour_events"].values() for x in xs)
        assert all(len(x) == 6 and all(type(v) is int for v in x) for xs in st["farm_win"].values() for x in xs)
        assert all(len(x) == 2 and x[0] <= x[1] for x in st["pool_seen"].values())


def _rows(name, family=None, variant="tp4_T600"):
    """A case's stored rows by label: {(family, label, t): row} (K3's variant unless another is given)."""
    case = CASES[name]
    return {(r["family"], case["labels"].get(r["key"], r["key"]), r["t"]): r for r in case["expect"]
            if (family is None or r["family"] == family) and (variant is None or r["variant"] == variant)}


def _labels(name, family, variant="tp4_T600"):
    return {k[1] for k in _rows(name, family, variant)}


def test_t1_quote_null_and_mint_absent():
    """T1's legs trigger in raw units (the controls), but PUMP, a NULL quote_mint, an absent mint and mayhem keep the
    curve out of the universe: no row and no N2 candidacy."""
    for name in ("t1_quote_default", "t1_quote_wsol"):
        (key, row), = _rows(name).items()
        assert key == ("F7", "T1", 450_197_213) and (row["depth"], row["farm"], row["gate"]) == (53_657_221_584, "n/a", "pass")
        assert row["trigger_trader"] == "6Ljyr7EoLpPyvbWUZ6Urh126cYLxDXVsRoHve1WWxok9"
        assert len(CASES[name]["expect_state"]["hour_events"]["curve"]) == 8
    for name in ("t1_as_stored", "t1_quote_null", "t1_token_absent", "t1_mayhem"):
        assert CASES[name]["expect"] == [] and CASES[name]["expect_state"]["hour_events"]["curve"] == [], name
    assert CASES["t1_quote_null"]["tokens"][0]["quote_mint"] is None and CASES["t1_token_absent"]["tokens"] == []
    assert len(CASES["t1_as_stored"]["events"]) == 13 and CASES["t1_as_stored"]["tokens"][0]["quote_mint"] == G.PUMP_QUOTE


def test_t2_and_pools_without_a_token():
    """T2's pool is no SOL market: unknown to the engine. The same legs in a 'sol' pool signal (its token is PUMP-quoted).
    'Mint absent' is for curves only: a pool without a token signals, its creator being its coin_creator."""
    other = CASES["t2_other_pool"]
    assert other["expect"] == [] and other["expect_state"]["farm_win"] == {}
    assert not any(other["expect_state"]["hour_events"].values())
    (key, row), = _rows("t2_control_sol").items()
    assert key == ("F7", "T2 pool", 450_145_128) and (row["depth"], row["farm"]) == (2_564_283_573_690, "organic")
    assert _labels("pool_mint_absent", "F7") == {"PA", "PC", "PD"}  # PB: its coin_creator sold within the creator window


def test_n2_picks_bands_and_collisions():
    for name, k in (("n2_k1", 1), ("n2_k3", 3)):
        n2 = _rows(name, "N2_F7")
        assert len(n2) == k and {key[1] for key in n2} <= {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6"}
        assert {r["depth"] for r in n2.values()} == {1_000 * SOL} and {r["trigger_trader"] for r in n2.values()} == {None}
        seen = {s[0] for p, s in CASES[name]["expect_state"]["pool_seen"].items() if CASES[name]["labels"][p][0] == "Q"}
        assert {r["pool_seen_slot"] for r in n2.values()} <= seen  # the N2 key's own first slot
    assert _labels("n2_k1", "N2_F7") < _labels("n2_k3", "N2_F7")
    assert _labels("n2_band_edges_above_2_53", "N2_F7") == {"E_lo", "E_hi"}
    assert _labels("n2_curve_universe", "N2_F7") == {"C_hi", "C_lo", "C_old"}
    assert _labels("n2_curve_universe", "F7") == {"CT"}
    (row,) = _rows("n2_collision", "N2_F7").values()  # Q, written by P1 and then by P2: P2's depth and farm
    assert (row["depth"], row["farm"], row["gate"]) == (1_180 * SOL, "organic", "pass")


def test_the_hour_rolls_over_after_the_first_keys_f7():
    n2 = {(k[1], k[2]): r["depth"] for k, r in _rows("hour_rollover_order", "N2_F7").items()}
    assert n2 == {("Q", 450_179_413): 950 * SOL, ("P2", 450_179_413): 950 * SOL, ("P1", 450_179_413): 1_050 * SOL}
    n2 = {(k[1], k[2]) for k in _rows("clock_1041_1042_mid_hour", "N2_F7")}
    assert n2 == {("Q", 450_144_010), ("P1", 450_144_100)}  # P2 no longer sees Q: the grid moved at 450144000


def test_the_creator_window_follows_the_clock():
    # CA (1041: 201 slots) and CC (its creator sold exactly 200 slots before) are blocked.
    assert _labels("clock_1041_1042_creator_window", "F7") == {"CB"}


def test_the_h_gates():
    """H's gates in the engine's order: PFW is flagged with its window incomplete, and the flag decides."""
    h = {k[1]: r for k, r in _rows("h_gates", "F7", "flow300_H6").items()}
    assert {p: r["gate"] for p, r in h.items()} == {"PW": "farm window incomplete", "PFW": "farm flagged",
                                                     "PE0": "farm window incomplete", "PE1": "pass", "PF": "farm flagged",
                                                     "PO": "pass"}
    assert [(h[p]["farm"], h[p]["farm_window_complete"]) for p in ("PFW", "PF")] == [("flagged", 0), ("flagged", 1)]
    k3 = _rows("h_gates", "F7")
    assert {r["gate"] for r in k3.values()} == {"pass"} and k3[("F7", "PF", 450_203_600)]["farm"] == "flagged"
    assert [k3[("F7", p, t)]["farm_window_complete"] for p, t in (("PE0", 450_203_440), ("PE1", 450_203_441))] == [0, 1]


def test_the_decision_bounds_and_the_curve_age():
    assert _labels("bounds_and_age", "F7") == {"P_dlo", "P_dhi", "A_burn"}  # not dlo - 1, dhi + 1, created before burn


def test_replay_rows_have_no_live_columns_and_pass_rows_are_c2s():
    for case in DOC["cases"]:
        for r in case["expect"]:
            assert (r["block_time"], r["d_ms"], r["farm_complete"]) == (None, None, None)
            if r["gate"] == "pass":
                assert (r["status"], r["reason"]) in (("entered", None), ("skipped", "busy"))
            else:
                assert (r["status"], r["reason"]) == ("skipped", r["gate"]) and r["gate"] in FARM_GATES


def test_t1s_legs_are_dev2s():
    """The stored T1 legs are dev2's own (the generator's query); skipped without the store."""
    if not os.path.exists(G.STORE):
        pytest.skip(f"no dev2 store at {G.STORE}")
    tok, events = G.read_t1()
    assert [tok] == CASES["t1_as_stored"]["tokens"] and events == CASES["t1_as_stored"]["events"]
