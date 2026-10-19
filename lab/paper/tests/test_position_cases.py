"""The golden cases of M1 C2 (paper/tests/data/c2_position_cases.json, research/m1-c2-positionsim-spec-2026-10-19.md
§6.2): the paper engine still makes exactly the stored journal writes on every case, the file keeps its format for the
Rust reader (lab/live paper_core.rs), and regenerating it gives the same file, so every case's own check of what it is
for still holds on the unmodified engine.

  C2_CASES_REGEN=1 pytest paper/tests/test_position_cases.py      # rewrite the file from the engine first
"""

import json
import os

import pytest

from paper.journal import TABLES
from paper.tests import c1b_signal_cases as C1
from paper.tests import c2_position_cases as G

if os.environ.get("C2_CASES_REGEN") == "1":
    G.generate()
with open(G.PATH) as f:
    TEXT = f.read()
DOC = json.loads(TEXT)
CASES = {c["name"]: c for c in DOC["cases"]}
HEAD = ["venue", "key", "slot", "tx", "outer", "inner", "block_time"]


@pytest.mark.parametrize("name", list(CASES))
def test_the_engine_makes_each_cases_writes(name):
    case = CASES[name]
    ops, eng = G.run(case)
    assert ops == case["expect_ops"], name
    assert not eng.open and not eng.pending and not eng.grad_wait, name


def test_the_files_format():
    """What the Rust reader relies on (FORMAT): the case keys, chain order, legs that give their fields back, integers that
    fit i64, the tokens' graduation fields, the engine's own groups and cost scenario text, and writes of the five
    tables with every column, leaving the positions table empty."""
    assert DOC["format"] == G.FORMAT and [c["name"] for c in DOC["cases"]] == [f.__name__ for f in G.CASES]
    C1.check_ints(DOC)
    cols = {t: [c.split()[0] for c in TABLES[t][0].split(", ")] for t in G.FIVE}
    for case in DOC["cases"]:
        name = case["name"]
        assert list(case) == list(G.CASE), name
        orders = [(e["slot"], e["tx"], e["outer"], e["inner"]) for e in case["events"]]
        assert orders == sorted(orders) and len(set(orders)) == len(orders), name
        for e in case["events"]:
            assert list(e) == [*HEAD, *(C1.CURVE if e["venue"] == "curve" else C1.POOL)]
            assert C1.fields(C1.event(e)) == e
        for t in case["tokens"]:
            assert list(t) == ["mint", "create_slot", "creator", "mayhem", "quote_mint", "complete_slot", "pool"], name
        scn = json.loads(case["cost_scenario"])
        assert sorted(scn) == list(scn) and scn["name"] == "base", name
        open_positions = set()
        for op, table, row in case["expect_ops"]:
            assert table in G.FIVE, (name, table)
            if op == "put":
                assert list(row) == cols[table], (name, table)
                if table == "positions":
                    open_positions.add(tuple(row.values())[:4])
            else:
                assert (op, table, list(row)) == ("delete", "positions", ["family", "variant", "key", "t"]), name
                open_positions.discard(tuple(row.values()))
        assert not open_positions, name


def test_regenerating_gives_the_same_file(tmp_path):
    path = str(tmp_path / "c2.json")
    G.generate(path)
    with open(path) as f:
        assert f.read() == TEXT
