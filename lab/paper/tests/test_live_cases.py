"""The golden cases of M3-08 (paper/tests/data/m3_live_cases.json, research/m3-08-core-live-api-spec-2026-10-19.md §3):
the paper engine on a feed that learns while it reads (and, M3-08b, takes backfills as history with the live farm-window
rule) still makes exactly the stored journal writes on every case, the file keeps its format for the Rust reader (lab/live
paper_core tests), regenerating it gives the same file, and each case depends on what is learned, when, or on the rule.

  M3_CASES_REGEN=1 pytest paper/tests/test_live_cases.py      # rewrite the file from the engine first
"""

import copy
import json
import os

import pytest

from paper.tests import c1b_signal_cases as C1
from paper.tests import m3_live_cases as G

if os.environ.get("M3_CASES_REGEN") == "1":
    G.generate()
with open(G.PATH) as f:
    TEXT = f.read()
DOC = json.loads(TEXT)
CASES = {c["name"]: c for c in DOC["cases"]}


@pytest.mark.parametrize("name", list(CASES))
def test_the_engine_makes_each_cases_writes(name):
    case = CASES[name]
    ops, eng = G.run(case)
    assert ops == case["expect_ops"], name
    assert not eng.open and not eng.pending and not eng.grad_wait, name


# Per case, whether its writes stay the same when nothing is learned, when everything is learned at the start, and with
# the other farm-window rule. Each case changes under at least one: it shows what the live API must carry. The expected
# sames: graduation_learned_after_the_entry's entry does not read its learn and completion_learned_too_late's learn comes
# too late (each the other's check); M3-08's cases have no group H row a window decides; window_rule_live learns
# nothing; backfill_as_history's backfill already comes with the first event.
SAME = {"pool_learned_mid_run": (False, False, True), "graduation_learned_after_the_entry": (False, True, True),
        "completion_learned_too_late": (True, False, True), "clock_learned_mid_run": (False, False, True),
        "window_rule_live": (True, True, False), "backfill_as_history": (False, True, False),
        "backfill_of_an_unknown_pool": (False, False, False)}


@pytest.mark.parametrize("name", list(CASES))
def test_each_case_depends_on_what_is_learned_and_when(name):
    case = CASES[name]
    nothing = copy.deepcopy(case)
    nothing["learn"] = []
    start = copy.deepcopy(case)
    start["learn"] = [{**x, "at_slot": 0} for x in case["learn"]]
    rule = copy.deepcopy(case)
    rule["window_rule"] = "live" if case["window_rule"] == "all_pools" else "all_pools"
    same = tuple(G.run(c)[0] == case["expect_ops"] for c in (nothing, start, rule))
    assert same == SAME[name] and not all(same), name


def test_the_files_format():
    """C2's format (tokens with their graduation fields, writes of the five tables) plus the learn list: in at_slot order,
    each item a token row, a pools row or an epoch's ms per slot."""
    assert DOC["format"] == G.FORMAT and [c["name"] for c in DOC["cases"]] == [f.__name__ for f in G.CASES]
    C1.check_ints(DOC)
    for case in DOC["cases"]:
        name = case["name"]
        assert list(case) == list(G.CASE), name
        orders = [(e["slot"], e["tx"], e["outer"], e["inner"]) for e in case["events"]]
        assert orders == sorted(orders) and len(set(orders)) == len(orders), name
        slots = [x["at_slot"] for x in case["learn"]]
        assert slots == sorted(slots) and case["window_rule"] in ("all_pools", "live"), name
        assert case["learn"] or case["window_rule"] == "live", name
        for x in case["learn"]:
            if "history" in x:
                h = x["history"]
                assert list(x) == ["at_slot", "history"] and list(h) == ["pool", "events"] and h["events"], name
                orders = [(e["slot"], e["tx"], e["outer"], e["inner"]) for e in h["events"]]
                assert orders == sorted(orders) and all(C1.fields(C1.event(e)) == e and e["key"] == h["pool"] for e in h["events"]), name
            elif "token" in x:
                assert list(x) == ["at_slot", "token"] and list(x["token"]) == ["mint", "create_slot", "creator", "mayhem",
                                                                                 "quote_mint", "complete_slot", "pool"], name
            elif "pool" in x:
                assert list(x) == ["at_slot", "pool"] and list(x["pool"]) == list(case["pools"][0] if case["pools"] else x["pool"]), name
            else:
                assert list(x) == ["at_slot", "epoch", "ms"] and isinstance(x["ms"], float), name


def test_regenerating_gives_the_same_file(tmp_path):
    path = str(tmp_path / "m3.json")
    G.generate(path)
    with open(path) as f:
        assert f.read() == TEXT
