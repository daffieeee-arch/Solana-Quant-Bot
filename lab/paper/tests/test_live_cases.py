"""The golden cases of M3-08 (paper/tests/data/m3_live_cases.json, research/m3-08-core-live-api-spec-2026-10-19.md §3):
the paper engine on a feed that learns while it reads still makes exactly the stored journal writes on every case, the
file keeps its format for the Rust reader (lab/live paper_core tests), regenerating it gives the same file, and each case
depends on what is learned and when.

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


@pytest.mark.parametrize("name", list(CASES))
def test_each_case_depends_on_what_is_learned_and_when(name):
    """Learning nothing, or everything at the start, changes the writes: the case shows what the live API must carry
    (graduation_learned_after_the_entry learns what the entry does not read, completion_learned_too_late what comes too
    late to count, so each is the other's check)."""
    case = CASES[name]
    nothing = copy.deepcopy(case)
    nothing["learn"] = []
    start = copy.deepcopy(case)
    start["learn"] = [{**x, "at_slot": 0} for x in case["learn"]]
    same = [G.run(c)[0] == case["expect_ops"] for c in (nothing, start)]
    want = {"graduation_learned_after_the_entry": [False, True], "completion_learned_too_late": [True, False]}
    assert same == want.get(name, [False, False]), name


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
        assert case["learn"] and slots == sorted(slots), name
        for x in case["learn"]:
            if "token" in x:
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
