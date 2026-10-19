"""The golden table of M1 C3-1 (paper/tests/data/c3_risk_load_cases.json; research/m1-c3-risk-spec-2026-10-19.md D1,
D2): the unmodified risk.load still gives every stored result, and regenerating the file gives the same file. lab-live's
risk reader (lab/live/src/risk.rs) reads the same file in its tests.

  C3_RISK_CASES_REGEN=1 pytest paper/tests/test_risk_load_cases.py      # rewrite the file from risk.load first
"""

import json
import os

from paper.tests import c3_risk_load_cases as G

if os.environ.get("C3_RISK_CASES_REGEN") == "1":
    G.generate()
with open(G.PATH) as f:
    TEXT = f.read()
DOC = json.loads(TEXT)


def test_regenerating_gives_the_same_file():
    assert G.text() == TEXT


def test_the_files_format():
    """What the Rust reader relies on: the format line, unique names in the generator's order, a text exactly for the
    text cases, a Risk or a reason for every 'same' case and a record for every 'refused' one, and the paper start's
    risk files among the cases as they are committed."""
    assert DOC["format"] == G.FORMAT and DOC["own_reason_prefix"] == G.OWN
    assert [c["name"] for c in DOC["cases"]] == [n for n, *_ in G.cases()]
    for c in DOC["cases"]:
        assert ("text" in c) == (c["kind"] == "text"), c["name"]
        assert c["rust"] in ("same", "refused"), c["name"]
        want = {"risk"} if "risk" in c["python"] else {"reason"} if c["rust"] == "same" else {"record"}
        assert set(c["python"]) == want, c["name"]
    by = {c["name"]: c for c in DOC["cases"]}
    for name, file in (("risk_yaml", "risk.yaml"), ("risk_h_yaml", "risk-h.yaml")):
        with open(os.path.join(G.PAPER, file)) as f:
            assert by[name]["text"] == f.read()
        assert by[name]["python"]["risk"]["size_lamports"] == 500_000_000
    assert sum(c["rust"] == "refused" for c in DOC["cases"]) >= 20
