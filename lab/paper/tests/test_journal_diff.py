"""Two paper journals compared row for row (paper/journal_diff.py): the tool M1 and M3 of the Rust live route use to
prove the Rust engine's journal equals the Python engine's (lab/docs/rust-live-route-design-2026-10-16.md §3)."""

import json
import os

import pytest

from paper import journal_diff
from paper.journal import Journal

SOL = 1_000_000_000
GROUPS = json.dumps({"K3": {"primary": True, "variants": ["tp4_T600"]}})
COSTS = json.dumps({"name": "base", "fee_per_tx": 605_000})


def _journal(path, pnl=SOL // 10, extra_signal=False, latency=12, groups=GROUPS):
    j = Journal(str(path))
    j.state("groups", groups)
    j.state("cost_scenario", COSTS)
    j.state("feed", json.dumps({"kind": "replay"}))
    for t, day in ((100, "2026-10-26"), (200, "2026-10-27")):
        j.put("signals", family="F7", variant="tp4_T600", key=f"P{t}", t=t, day=day, venue="pool", orientation="reversed",
              depth=200 * SOL, status="entered", farm="organic", latency_ms=latency, received_unix_ns=latency * 7)
        j.put("orders", family="F7", variant="tp4_T600", key=f"P{t}", t=t, day=day, entry_slot=t + 3, size=SOL // 2, status="filled")
        j.put("exits", family="F7", variant="tp4_T600", key=f"P{t}", t=t, day=day, entry_slot=t + 3, exit_slot=t + 50,
              exit_reason="time", tokens=5, cost=SOL // 2, proceeds=SOL // 2 + pnl, pnl=pnl, ret=pnl / (SOL // 2), exit_attempts=1)
    if extra_signal:
        j.put("signals", family="F7", variant="tp4_T600", key="P300", t=300, day="2026-10-27", status="skipped", reason="busy")
    j.commit()
    j.close()
    return str(path)


def test_two_equal_journals_are_green_and_arrival_times_are_not_compared(tmp_path):
    a, b = _journal(tmp_path / "a"), _journal(tmp_path / "b", latency=99)  # measured latency differs by design
    res = journal_diff.compare(a, b)
    assert res["green"] and res["unexplained"] == 0
    assert res["tables"]["exits"]["rows_a"] == res["tables"]["exits"]["rows_b"] == 2


def test_every_difference_is_counted_and_shown(tmp_path):
    a = _journal(tmp_path / "a")
    b = _journal(tmp_path / "b", pnl=SOL // 10 + 1, extra_signal=True)
    res = journal_diff.compare(a, b)
    assert not res["green"]
    ex, sig = res["tables"]["exits"], res["tables"]["signals"]
    assert (ex["different"], ex["unexplained"]) == (2, 2)
    assert ex["different_first"][0]["columns"] == {"proceeds": [SOL // 2 + SOL // 10, SOL // 2 + SOL // 10 + 1],
                                                   "pnl": [SOL // 10, SOL // 10 + 1], "ret": [0.2, (SOL // 10 + 1) / (SOL // 2)]}
    assert (sig["only_b"], sig["only_b_first"]) == (1, [["F7", "tp4_T600", "P300", 300]])
    assert res["unexplained"] == 3
    # one day only: the 26th has the one differing exit
    assert journal_diff.compare(a, b, "2026-10-26", "2026-10-26")["unexplained"] == 1


def test_different_groups_or_costs_are_red_and_a_cause_explains(tmp_path):
    a = _journal(tmp_path / "a")
    b = _journal(tmp_path / "b", groups=json.dumps({"K3": {"primary": True, "variants": ["tp6_T600"]}}))
    res = journal_diff.compare(a, b)
    assert res["unexplained"] == 0 and res["state_mismatch"] == ["groups"] and not res["green"]
    c = _journal(tmp_path / "c", extra_signal=True)
    res = journal_diff.compare(a, c, causes=[lambda t, k, ra, rb: "rust_saw_more" if ra is None and t == "signals" else None])
    assert res["green"] and res["explained"] == {"rust_saw_more": 1}


def test_a_schema_difference_is_red(tmp_path):
    import sqlite3

    a, b = _journal(tmp_path / "a"), _journal(tmp_path / "b")
    assert journal_diff.schema_diff(a, b) == []
    db = sqlite3.connect(os.path.join(b, "journal.sqlite"))
    db.execute("ALTER TABLE exits ADD COLUMN extra INTEGER")
    db.execute("CREATE TABLE extra_table (x INTEGER)")
    db.commit()
    db.close()
    res = journal_diff.compare(a, b)
    assert not res["green"] and res["unexplained"] == 0
    assert res["schema_diff"][0] == "table extra_table only in B" and res["schema_diff"][1].startswith("table exits:")


def test_the_command_writes_its_report_and_exits_by_colour(tmp_path):
    a, b = _journal(tmp_path / "a"), _journal(tmp_path / "b", pnl=0)
    assert journal_diff.main(["--journal-a", a, "--journal-b", a, "--out", str(tmp_path / "o1")]) == 0
    assert journal_diff.main(["--journal-a", a, "--journal-b", b, "--out", str(tmp_path / "o2")]) == 1
    md = open(tmp_path / "o2" / "journal_diff.md").read()
    assert "**RED**: 2 unexplained" in md and "| exits | 2 | 2 | 0 | 0 | 2 | 2 |" in md
    assert json.load(open(tmp_path / "o2" / "journal_diff.json"))["tables"]["exits"]["different"] == 2


STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")


def test_two_runs_of_the_python_engine_on_one_slice_are_identical(tmp_path):
    """The reference must be deterministic: the same slice twice gives a green diff (M1 compares Rust to it)."""
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    from paper import engine

    here = os.path.dirname(engine.__file__)
    lo = 450_130_000
    for run in ("a", "b"):
        engine.main(["--from", str(lo), "--to", str(lo + 4_000), "--journal", str(tmp_path / run), "--store", STORE,
                     "--config", os.path.join(here, "k3-paper-2026-10-12.yaml"), "--groups", os.path.join(here, "h-arms-2026-10-16.yaml"),
                     "--no-backup"])
    res = journal_diff.compare(str(tmp_path / "a"), str(tmp_path / "b"))
    assert res["green"], journal_diff.report(res)
    assert res["tables"]["signals"]["rows_a"] > 0
