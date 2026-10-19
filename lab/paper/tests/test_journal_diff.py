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


def test_an_expected_new_table_is_listed_not_a_difference(tmp_path):
    """LAB review on #319: a regression of main against a PR that adds a journal table (as #331 added `learned`) is
    not red on that table alone. Named with --new-table, a table in one journal only is listed; a column difference
    in a shared table stays red, and so does the new table's schema where both have it."""
    import sqlite3

    a, b = _journal(tmp_path / "a"), _journal(tmp_path / "b")
    db = sqlite3.connect(os.path.join(b, "journal.sqlite"))
    db.execute("CREATE TABLE newer (x INTEGER)")
    db.commit()
    db.close()
    assert not journal_diff.compare(a, b)["green"]
    res = journal_diff.compare(a, b, new_tables=("newer",))
    assert res["green"] and res["schema_diff"] == [] and res["new_tables"] == ["table newer only in B"]
    assert journal_diff.main(["--journal-a", a, "--journal-b", b, "--out", str(tmp_path / "o"), "--new-table", "newer"]) == 0
    assert "expected new tables (--new-table): ['table newer only in B']" in open(tmp_path / "o" / "journal_diff.md").read()
    db = sqlite3.connect(os.path.join(b, "journal.sqlite"))
    db.execute("ALTER TABLE exits ADD COLUMN extra INTEGER")
    db.commit()
    db.close()
    assert not journal_diff.compare(a, b, new_tables=("newer",))["green"]
    c = _journal(tmp_path / "c")
    for path, col in ((a, "x INTEGER"), (c, "x TEXT")):  # both have it: its schema counts
        db = sqlite3.connect(os.path.join(path, "journal.sqlite"))
        db.execute(f"CREATE TABLE newer ({col})")
        db.commit()
        db.close()
    assert journal_diff.compare(a, c, new_tables=("newer",))["schema_diff"][0].startswith("table newer:")


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


# The signals scope (M1 C1b, research/m1-c1b-signals-spec-2026-10-19.md §6.3): A is the Python engine's journal, B the
# Rust route's, which decides the signals only (no busy, no risk, no positions, no costs).
FEED = json.dumps({"kind": "replay", "from": 450128159, "to": 450380000})
PASSED = [dict(key="P1", trigger_trader="X", status="entered", latency_ms=5), dict(key="P2", status="skipped", reason="busy"),
          dict(key="P3", status="skipped", reason="max open positions"),
          dict(key="P4", variant="flow300_H6", farm="flagged", status="skipped", reason="farm flagged")]


def _c1b(path, rows, groups=GROUPS, costs=COSTS):
    """A journal with these signal rows (pool_reversed at t 100 unless given), its groups, costs and feed state, and an
    order and exit per entered row (the Python engine's; the Rust route has no costs and no orders: costs=None)."""
    j = Journal(str(path))
    for k, v in (("groups", groups), ("cost_scenario", costs), ("feed", FEED)):
        if v is not None:
            j.state(k, v)
    for r in rows:
        row = {"family": "F7", "variant": "tp4_T600", "t": 100, "day": "2026-10-26", "venue": "pool", "orientation": "reversed",
               "depth": 200 * SOL, "farm": "organic", "pool_seen_slot": 90, "farm_window_complete": 1, **r}
        j.put("signals", **row)
        if row.get("status") == "entered":
            j.put("orders", family="F7", variant=row["variant"], key=row["key"], t=row["t"], day=row["day"], entry_slot=103,
                  size=SOL // 2, status="filled")
            j.put("exits", family="F7", variant=row["variant"], key=row["key"], t=row["t"], day=row["day"], entry_slot=103,
                  exit_slot=150, exit_reason="time", tokens=5, cost=SOL // 2, proceeds=SOL // 2, pnl=0, ret=0.0, exit_attempts=1)
    j.commit()
    j.close()
    return str(path)


def _rust(rows):
    """B's rows: a row that passed the farm gates has a NULL status and reason (spec D10), a gated row keeps its gate."""
    return [{**r, "status": None, "reason": None, "latency_ms": None} if r.get("reason") not in journal_diff.FARM_GATES else r
            for r in rows]


def test_the_signals_scope_compares_the_c1b_projection(tmp_path):
    """Only the signals table, on the stream's columns and the farm gate: A's busy and risk rows against B's NULL ones,
    A's orders and exits and costs against none, and arrival times are no difference."""
    assert journal_diff.gate("skipped", "farm window incomplete") == "farm window incomplete"
    assert [journal_diff.gate(s, r) for s, r in (("entered", None), ("skipped", "busy"), ("skipped", "kill switch"), (None, None))] \
        == ["pass"] * 4
    row = journal_diff.signal_row({"family": "F7", "variant": "v", "key": "P", "t": 1, "status": "skipped", "reason": "busy",
                                   "depth": 7, "latency_ms": 3})
    assert list(row) == ["family", "variant", "key", "t", *journal_diff.SIGNAL_COLS, "gate", "status", "reason"]
    assert (row["depth"], row["gate"], row["status"], row["reason"]) == (7, "pass", "skipped", "busy") and "latency_ms" not in row
    a = _c1b(tmp_path / "a", PASSED)
    b = _c1b(tmp_path / "b", _rust(PASSED), costs=None)
    res = journal_diff.compare_signals(a, b)
    assert res["green"], journal_diff.report_signals(res)
    t = res["tables"]["signals"]
    assert (t["rows_a"], t["rows_b"], t["different"], t["decided_beyond_c1b"]) == (4, 4, 0, 0) and list(res["tables"]) == ["signals"]
    assert res["decided_in_c2_c3"] == {"busy": 1, "entered": 1, "risk: max open positions": 1}
    assert res["gates"] == {"a": {"farm flagged": 1, "pass": 3}, "b": {"farm flagged": 1, "pass": 3}}
    assert res["state"]["cost_scenario"] == {"a": COSTS, "b": None, "equal": False} and res["state_mismatch"] == []
    # The full comparison of the same pair is red: status, reason, orders, exits and costs (spec §8.2 risk 1).
    full = journal_diff.compare(a, b)
    assert not full["green"] and full["state_mismatch"] == ["cost_scenario"] and full["tables"]["signals"]["different"] == 3


def test_a_rust_row_decided_beyond_c1b_is_red(tmp_path):
    """C1b decides no entry, busy or risk: a B row with such a status, or a reason without a status, is unexplained
    even where A agrees."""
    a = _c1b(tmp_path / "a", PASSED)
    for i, (status, reason) in enumerate((("entered", None), ("skipped", "busy"), (None, "busy"), ("skipped", None))):
        b = _c1b(tmp_path / f"b{i}", [{**r, "status": status, "reason": reason} if r["key"] == "P1" else r for r in _rust(PASSED)],
                 costs=None)
        res = journal_diff.compare_signals(a, b)
        t = res["tables"]["signals"]
        assert not res["green"] and t["decided_beyond_c1b"] == 1 and res["unexplained"] >= 1
        assert t["decided_beyond_c1b_first"] == [{"key": ["F7", "tp4_T600", "P1", 100], "status": status, "reason": reason}]
    assert (t["different"], res["unexplained"]) == (0, 1)  # ('skipped', None) is gate 'pass' as A's 'entered'


def test_busy_against_null_is_green(tmp_path):
    a = _c1b(tmp_path / "a", [dict(key="P2", status="skipped", reason="busy")])
    b = _c1b(tmp_path / "b", [dict(key="P2")], costs=None)
    res = journal_diff.compare_signals(a, b)
    assert res["green"] and res["decided_in_c2_c3"] == {"busy": 1}


def test_a_farm_gate_or_column_mismatch_is_red(tmp_path):
    a = _c1b(tmp_path / "a", PASSED)
    for i, (change, cols) in enumerate(((dict(status=None, reason=None), {"gate": ["farm flagged", "pass"]}),
                                        (dict(reason="farm window incomplete"), {"gate": ["farm flagged", "farm window incomplete"]}),
                                        (dict(farm="organic"), {"farm": ["flagged", "organic"]}),
                                        (dict(pool_seen_slot=91), {"pool_seen_slot": [90, 91]}))):
        rows = [{**r, **change} if r["key"] == "P4" else r for r in _rust(PASSED)]
        res = journal_diff.compare_signals(a, _c1b(tmp_path / f"b{i}", rows, costs=None))
        t = res["tables"]["signals"]
        assert not res["green"] and t["different_first"] == [{"key": ["F7", "flow300_H6", "P4", 100], "columns": cols}]
        assert res["unexplained"] == 1


def test_groups_must_be_present_and_equal(tmp_path):
    a = _c1b(tmp_path / "a", PASSED)
    b = _c1b(tmp_path / "b", _rust(PASSED), groups=json.dumps({"K3": {"primary": True, "variants": ["tp6_T600"]}}), costs=None)
    res = journal_diff.compare_signals(a, b)
    assert not res["green"] and res["unexplained"] == 0 and res["state_mismatch"] == ["groups"]
    c, d = _c1b(tmp_path / "c", PASSED, groups=None), _c1b(tmp_path / "d", _rust(PASSED), groups=None, costs=None)
    assert journal_diff.compare_signals(c, d)["state_mismatch"] == ["groups"]  # absent on both sides is no proof


def test_an_empty_side_is_red(tmp_path):
    a, b = _c1b(tmp_path / "a", PASSED), _c1b(tmp_path / "b", [], costs=None)
    res = journal_diff.compare_signals(a, b)
    assert not res["green"] and res["tables"]["signals"]["only_a"] == 4
    empty = _c1b(tmp_path / "c", [])
    res = journal_diff.compare_signals(empty, b)
    assert not res["green"] and res["unexplained"] == 0  # nothing differs, but nothing was compared


def test_the_signals_scope_command(tmp_path):
    a = _c1b(tmp_path / "a", PASSED)
    b = _c1b(tmp_path / "b", _rust(PASSED), costs=None)
    assert journal_diff.main(["--journal-a", a, "--journal-b", b, "--out", str(tmp_path / "o1"), "--scope", "signals"]) == 0
    md = open(tmp_path / "o1" / "journal_diff.md").read()
    assert "**GREEN**: 0 unexplained" in md and "| signals | 4 | 4 | 0 | 0 | 0 | 0 | 0 |" in md
    assert "- decided in C2/C3 (A's status among its 'pass' rows): {'busy': 1, 'entered': 1, 'risk: max open positions': 1}" in md
    assert json.load(open(tmp_path / "o1" / "journal_diff.json"))["scope"] == "signals"
    assert journal_diff.main(["--journal-a", a, "--journal-b", b, "--out", str(tmp_path / "o2")]) == 1  # the default scope
    c = _c1b(tmp_path / "c", [{**r, "status": "entered"} if r["key"] == "P2" else r for r in _rust(PASSED)], costs=None)
    assert journal_diff.main(["--journal-a", a, "--journal-b", c, "--out", str(tmp_path / "o3"), "--scope", "signals"]) == 1
    assert "- signals decided beyond C1b in B: ['F7', 'tp4_T600', 'P2', 100] status entered, reason None" in open(
        tmp_path / "o3" / "journal_diff.md").read()


def test_the_signals_scope_on_the_engines_own_journal(tmp_path):
    """The engine's journal of a golden case (h_gates: farm gates, entries; paper/tests/c1b_signal_cases.py) against what
    the Rust route writes for it: the same rows with a NULL status and reason past the gates, its groups, no costs and
    no orders. Green in the signals scope, red in the full one."""
    from paper.tests import c1b_signal_cases as G

    with open(G.PATH) as f:
        case = next(c for c in json.load(f)["cases"] if c["name"] == "h_gates")
    a, b = Journal(str(tmp_path / "a")), Journal(str(tmp_path / "b"))
    G.engine(case, a).run()
    for j, states in ((a, (("groups", case["groups"]), ("feed", FEED), ("cost_scenario", COSTS))),
                      (b, (("groups", case["groups"]), ("feed", FEED)))):
        for k, v in states:
            j.state(k, v)
    for r in a.rows("signals"):
        b.put("signals", **(r if journal_diff.gate(r["status"], r["reason"]) != "pass" else {**r, "status": None, "reason": None}))
    for j in (a, b):
        j.close()
    res = journal_diff.compare_signals(a.dir, b.dir)
    assert res["green"], journal_diff.report_signals(res)
    assert res["gates"]["a"] == {"farm flagged": 4, "farm window incomplete": 4, "pass": 10} == res["gates"]["b"]
    assert res["decided_in_c2_c3"] == {"entered": 10}
    assert not journal_diff.compare(a.dir, b.dir)["green"]
