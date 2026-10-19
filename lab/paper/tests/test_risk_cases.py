"""The golden cases of M1 C3-2 (paper/tests/data/c3_risk_cases.json, research/m1-c3-risk-spec-2026-10-19.md §5): the
paper engine with risk on still makes exactly the stored journal writes on every case, and regenerating the file gives
the same file, so every case's own check of what it is for still holds on the unmodified engine. lab/live's paper_core
tests read the same file.

  C3_CASES_REGEN=1 pytest paper/tests/test_risk_cases.py      # rewrite the file from the engine first
"""

import json
import os

import pytest

from paper.tests import c3_risk_cases as G

if os.environ.get("C3_CASES_REGEN") == "1":
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


def test_regenerating_gives_the_same_file():
    assert G.C2.dumps(G.table()) == TEXT


def test_the_files_format():
    """The case keys, the risk versions (a first text at null, then slots that do not go back, every group of the groups
    text), and the groups naming the files."""
    assert DOC["format"] == G.FORMAT and [c["name"] for c in DOC["cases"]] == [f.__name__ for f in G.CASES]
    for c in DOC["cases"]:
        groups = json.loads(c["groups"])
        assert list(c["risk"]) == ["K3", "H"] and set(groups) == {"K3", "H"}, c["name"]
        for g, vs in c["risk"].items():
            assert groups[g]["risk"] == G.FILES[g], c["name"]
            assert vs[0][0] is None and all(isinstance(at, int) for at, _ in vs[1:]), c["name"]
            assert [at for at, _ in vs[1:]] == sorted(at for at, _ in vs[1:]), c["name"]
            assert all(t is None or isinstance(t, str) for _, t in vs), c["name"]
