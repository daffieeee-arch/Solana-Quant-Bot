"""Two paper journals compared row for row: the Python engine against the Rust live route on the same input (M1 and
M3 of lab/docs/rust-live-route-design-2026-10-16.md §3), or any two runs of the same engine.

  python -m paper.journal_diff --journal-a <dir> --journal-b <dir> --out <dir> [--from-day D] [--to-day D]
                                [--new-table NAME ...] [--scope all|signals]
  python -m paper.parity --journal-a <dir> --journal-b <dir> --out <dir>          # the same

First the schema: every table's columns, types and primary key (PRAGMA table_info) must be the same in both, so
the Rust journal writer cannot drift from paper/journal.py. A table named with --new-table (one a newer engine adds,
such as `learned` in #331) may exist in only one of them: listed, not a difference (a regression of main against a PR
that adds a table; LAB review on #319). Its schema still counts where both have it. Then per table, keyed as the journal keys it
(paper/journal.py TABLES): rows only in A, rows only in B, and rows in
both that differ in a compared column. Compared are the decisions and their outcome: signal status, reason, farm
labels, entry, fills, exit, proceeds, P&L, the exit windows. Not compared are the measured arrival times (receive,
created_at, latency, segment lag), which two engines on one stream measure differently by design. Every
difference is unexplained until a cause is named (`causes`, empty now). Green: 0 unexplained. Writes
journal_diff.md and journal_diff.json; exit 1 when red.

`--scope signals` is M1 C1b's comparison (research/m1-c1b-signals-spec-2026-10-19.md §1.3, §6.3): B is a journal of
the Rust route that decides the signals only. Only the signals table counts, with the columns that follow from the
stream alone and the farm gate a row stopped at (`gate`). Busy and the risk limits need positions (C2) and risk state
(C3), so status and reason are not compared: a B row has a NULL status and reason, or is 'skipped' at a farm gate; any
other B row is decided beyond C1b and unexplained. A's status among its rows that passed the farm gates is reported as
decided in C2/C3. State 'groups' must be present and equal; 'cost_scenario' and 'feed' are only reported. Green: 0
unexplained, the same schema, the same groups, and rows in both journals.
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from types import SimpleNamespace

from backtest.costs import Scenario, settle

# Per table: the key columns and the compared columns (the journal schema of paper/journal.py).
TABLES = {
    "signals": (("family", "variant", "key", "t"),
                ("day", "venue", "orientation", "depth", "trigger_trader", "status", "reason", "farm", "block_time", "d_ms",
                 "farm_complete", "pool_seen_slot", "farm_window_complete")),
    "orders": (("family", "variant", "key", "t"), ("day", "entry_slot", "size", "status", "reason")),
    "fills": (("family", "variant", "key", "t", "side"), ("day", "slot", "tokens", "quote")),
    "exits": (("family", "variant", "key", "t"),
              ("day", "entry_slot", "exit_decision_slot", "exit_slot", "exit_reason", "tokens", "cost", "proceeds", "pnl", "ret",
               "exit_attempts", "cf_graduation", "seed_pool", "vault_capped", "reverted_direct", "reverted_router",
               "sells_dropped", "sells_scaled", "peak_mark", "peak_slot", "n_flow_slots", "cap_slots", "settle_rule")),
    "positions": (("family", "variant", "key", "t"), ("entry_slot", "cost", "tokens", "status")),
}
# State the two runs must share: the same groups and the same costs, or the comparison means nothing.
STATE_KEYS = ("groups", "cost_scenario", "feed", "settle_rule")
SHOWN = 50
# The one difference a freed pool may make to a signal both runs have: one saw the pool busy, the other entered.
FREED_SIGNAL = {("skipped", "busy"), ("entered", None)}
# The exits columns a write-off ends earlier than the run without it (the exit, its day and attempts, the P&L, and the
# diagnostics counted over the hold); entry, size, cost, cap and the flags must match.
WRITE_OFF_COLS = {"day", "exit_decision_slot", "exit_slot", "exit_reason", "pnl", "ret", "exit_attempts", "n_flow_slots",
                  "reverted_direct", "reverted_router", "sells_dropped", "sells_scaled", "peak_mark", "peak_slot"}
# The signals scope (C1b): the signal columns that do not depend on positions (block_time, d_ms and farm_complete are
# NULL in a replay, engine.py:656-659, 731-732), and the farm gates, which _signal checks before busy (engine.py:737-742).
SIGNAL_COLS = ("day", "venue", "orientation", "depth", "trigger_trader", "farm", "pool_seen_slot", "farm_window_complete",
               "block_time", "d_ms", "farm_complete")
FARM_GATES = ("farm flagged", "farm history incomplete", "farm window incomplete")


def gate(status, reason):
    """The farm gate a signal row stopped at, or 'pass': what comes after the gates (busy, risk) is C2/C3's."""
    return reason if status == "skipped" and reason in FARM_GATES else "pass"


def signal_row(row):
    """A signals row in the C1b projection: its key, SIGNAL_COLS and `gate`; status and reason ride along (reported)."""
    return {**{c: row.get(c) for c in (*TABLES["signals"][0], *SIGNAL_COLS)}, "gate": gate(row.get("status"), row.get("reason")),
            "status": row.get("status"), "reason": row.get("reason")}


def _beyond_c1b(row):
    """A Rust row decided further than C1b decides (spec D10): passed rows have a NULL status and reason, gated rows are
    'skipped' with the gate as reason. Anything else (entered, busy, a risk reason) belongs to C2/C3."""
    if row["status"] is None:
        return row["reason"] is not None
    return not (row["status"] == "skipped" and row["reason"] in FARM_GATES)


def _decided(row):
    """A's decision on a row that passed the farm gates, as the report counts it: entered, busy or risk: <reason>."""
    if row["status"] == "entered" or row["status"] is None:
        return row["status"] or "NULL"
    return "busy" if row["reason"] == "busy" else f"risk: {row['reason']}"


def _rows(path, table, keys, cols, lo_day, hi_day):
    """{key tuple: {col: value}} of one table, read-only; the columns a journal lacks (an older one) read as None."""
    db = sqlite3.connect(f"file:{os.path.join(path, 'journal.sqlite')}?mode=ro", uri=True)
    try:
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if not have:
            return {}
        sel = [c if c in have else f"NULL AS {c}" for c in (*keys, *cols)]
        if table == "exits" and "settle_rule" in cols:
            # A historical journal has one rule in state (absent means rule 1). New rows pin their own rule.
            old = db.execute("SELECT v FROM state WHERE k = 'settle_rule'").fetchone()
            old_rule = int(old[0]) if old else 1
            i = (*keys, *cols).index("settle_rule")
            sel[i] = f"COALESCE(settle_rule, {old_rule}) AS settle_rule" if "settle_rule" in have else f"{old_rule} AS settle_rule"
        where, params = [], []
        if "day" in have and lo_day:
            where.append("day >= ?")
            params.append(lo_day)
        if "day" in have and hi_day:
            where.append("day <= ?")
            params.append(hi_day)
        sql = f"SELECT {', '.join(sel)} FROM {table}" + (f" WHERE {' AND '.join(where)}" if where else "")
        out = {}
        for r in db.execute(sql, params):
            out[tuple(r[:len(keys)])] = dict(zip(cols, r[len(keys):]))
        return out
    finally:
        db.close()


def schema_of(path):
    """{table: [(column, type, notnull, default, pk position)]} of every table in the journal (PRAGMA table_info)."""
    db = sqlite3.connect(f"file:{os.path.join(path, 'journal.sqlite')}?mode=ro", uri=True)
    try:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {n: [tuple(r[1:]) for r in db.execute(f"PRAGMA table_info({n})")] for n in names}
    finally:
        db.close()


def schema_diff(path_a, path_b, new_tables=()):
    """The schema differences of two journals: tables only in one (except `new_tables`, see new_tables_only), and
    columns that differ (name, type, not null, default or primary key position)."""
    a, b = schema_of(path_a), schema_of(path_b)
    out = ([f"table {t} only in A" for t in sorted(set(a) - set(b) - set(new_tables))]
           + [f"table {t} only in B" for t in sorted(set(b) - set(a) - set(new_tables))])
    for t in sorted(set(a) & set(b)):
        if a[t] != b[t]:
            out.append(f"table {t}: A {a[t]} / B {b[t]}")
    return out


def new_tables_only(path_a, path_b, new_tables=()):
    """The tables of `new_tables` that only one journal has: expected, listed in the report."""
    a, b = schema_of(path_a), schema_of(path_b)
    return ([f"table {t} only in A" for t in sorted((set(a) - set(b)) & set(new_tables))]
            + [f"table {t} only in B" for t in sorted((set(b) - set(a)) & set(new_tables))])


def _state(path):
    db = sqlite3.connect(f"file:{os.path.join(path, 'journal.sqlite')}?mode=ro", uri=True)
    try:
        return {k: v for k, v in db.execute("SELECT k, v FROM state") if k in STATE_KEYS}
    finally:
        db.close()


def compare(path_a, path_b, lo_day=None, hi_day=None, causes=(), new_tables=()):
    """The comparison as a dict: per table the row counts, only-A, only-B and differing rows (the first SHOWN
    of each with their columns), the shared state, and `unexplained` (every difference no cause in `causes`
    explains; causes: callables (table, key, row_a, row_b) -> a cause name or None). new_tables: tables that may
    exist in one journal only (--new-table)."""
    res = {"a": path_a, "b": path_b, "days": [lo_day, hi_day], "tables": {}, "unexplained": 0, "explained": {},
           "schema_diff": schema_diff(path_a, path_b, new_tables), "new_tables": new_tables_only(path_a, path_b, new_tables)}
    sa, sb = _state(path_a), _state(path_b)
    res["state"] = {k: {"a": sa.get(k), "b": sb.get(k), "equal": sa.get(k) == sb.get(k)} for k in STATE_KEYS}
    # The feed may differ (a replay against a live run); groups and costs may not.
    res["state_mismatch"] = [k for k in ("groups", "cost_scenario") if not res["state"][k]["equal"]]
    for table, (keys, cols) in TABLES.items():
        ra, rb = _rows(path_a, table, keys, cols, lo_day, hi_day), _rows(path_b, table, keys, cols, lo_day, hi_day)
        t = res["tables"][table] = _table_diff(table, ra, rb, cols, causes)
        res["unexplained"] += t["unexplained"]
        for c, n in t["explained"].items():
            res["explained"][c] = res["explained"].get(c, 0) + n
    res["green"] = res["unexplained"] == 0 and not res["state_mismatch"] and not res["schema_diff"] and any(
        t["rows_a"] for t in res["tables"].values())
    return res


def _table_diff(table, ra, rb, cols, causes):
    """One table compared: {key: row} of A and B, rows only in one of them and rows that differ in `cols`; a difference
    a cause in `causes` names is explained, every other one unexplained."""
    only_a = sorted(k for k in ra if k not in rb)
    only_b = sorted(k for k in rb if k not in ra)
    diff = []
    for k in sorted(set(ra) & set(rb)):
        cols_diff = [c for c in cols if ra[k][c] != rb[k][c]]
        if cols_diff:
            diff.append((k, {c: [ra[k][c], rb[k][c]] for c in cols_diff}))
    explained, unexplained = {}, 0
    for k, row_a, row_b in ([(k, ra[k], None) for k in only_a] + [(k, None, rb[k]) for k in only_b]
                            + [(k, ra[k], rb[k]) for k, _ in diff]):
        cause = next((c for c in (f(table, k, row_a, row_b) for f in causes) if c), None)
        if cause:
            explained[cause] = explained.get(cause, 0) + 1
        else:
            unexplained += 1
    return {"rows_a": len(ra), "rows_b": len(rb), "only_a": len(only_a), "only_b": len(only_b),
            "different": len(diff), "unexplained": unexplained, "explained": explained,
            "only_a_first": [list(k) for k in only_a[:SHOWN]], "only_b_first": [list(k) for k in only_b[:SHOWN]],
            "different_first": [{"key": list(k), "columns": c} for k, c in diff[:SHOWN]]}


def compare_signals(path_a, path_b, lo_day=None, hi_day=None, causes=(), new_tables=()):
    """The signals scope (C1b) as a dict shaped like compare()'s: the signals table only, its SIGNAL_COLS and `gate`
    compared. Unexplained are the differences no cause explains and every B row decided beyond C1b (`decided_beyond_c1b`).
    `gates`: the rows per gate in A and B; `decided_in_c2_c3`: A's status among its rows that passed the gates."""
    keys = TABLES["signals"][0]
    res = {"scope": "signals", "a": path_a, "b": path_b, "days": [lo_day, hi_day], "tables": {}, "unexplained": 0,
           "explained": {}, "schema_diff": schema_diff(path_a, path_b, new_tables),
           "new_tables": new_tables_only(path_a, path_b, new_tables)}
    sa, sb = _state(path_a), _state(path_b)
    res["state"] = {k: {"a": sa.get(k), "b": sb.get(k), "equal": sa.get(k) == sb.get(k)} for k in STATE_KEYS}
    # The Rust route writes the groups it read (the Python journal's), verbatim; it has no costs (C2).
    res["state_mismatch"] = [] if sa.get("groups") is not None and res["state"]["groups"]["equal"] else ["groups"]
    ra, rb = ({k: signal_row(dict(zip(keys, k), **r)) for k, r in _rows(path, "signals", keys, (*SIGNAL_COLS, "status", "reason"),
                                                                         lo_day, hi_day).items()} for path in (path_a, path_b))
    t = res["tables"]["signals"] = _table_diff("signals", ra, rb, (*SIGNAL_COLS, "gate"), causes)
    beyond = sorted(k for k, r in rb.items() if _beyond_c1b(r))
    t["decided_beyond_c1b"] = len(beyond)
    t["decided_beyond_c1b_first"] = [{"key": list(k), "status": rb[k]["status"], "reason": rb[k]["reason"]} for k in beyond[:SHOWN]]
    t["unexplained"] += len(beyond)
    res["unexplained"], res["explained"] = t["unexplained"], dict(t["explained"])
    res["gates"] = {side: dict(sorted(Counter(r["gate"] for r in rows.values()).items())) for side, rows in (("a", ra), ("b", rb))}
    res["decided_in_c2_c3"] = dict(sorted(Counter(_decided(r) for r in ra.values() if r["gate"] == "pass").items()))
    res["green"] = (res["unexplained"] == 0 and not res["state_mismatch"] and not res["schema_diff"] and t["rows_a"] > 0
                    and t["rows_b"] > 0)
    return res


def report(res):
    lines = [f"# Journal diff: {res['a']} against {res['b']}", "",
             f"- days {res['days'][0] or 'all'} … {res['days'][1] or 'all'}; "
             f"**{'GREEN' if res['green'] else 'RED'}**: {res['unexplained']} unexplained"
             + (f", explained {res['explained']}" if res["explained"] else "")
             + (f"; state differs in {res['state_mismatch']} (groups and costs must be equal)" if res["state_mismatch"] else "")
             + (f"; **schema differs**: {res['schema_diff']}" if res["schema_diff"] else "")
             + (f"; expected new tables (--new-table): {res['new_tables']}" if res.get("new_tables") else ""),
             "", "| table | rows A | rows B | only A | only B | different | unexplained |", "|---|---|---|---|---|---|---|"]
    for t, r in res["tables"].items():
        lines.append(f"| {t} | {r['rows_a']} | {r['rows_b']} | {r['only_a']} | {r['only_b']} | {r['different']} | {r['unexplained']} |")
    for t, r in res["tables"].items():
        for d in r["different_first"][:10]:
            lines.append(f"- {t} {d['key']}: {d['columns']}")
        for k in r["only_a_first"][:5]:
            lines.append(f"- {t} only in A: {k}")
        for k in r["only_b_first"][:5]:
            lines.append(f"- {t} only in B: {k}")
    return lines


def report_signals(res):
    """The signals scope's report: the counts, the gates, A's decisions C1b leaves to C2/C3, and the first differences."""
    t, st = res["tables"]["signals"], res["state"]
    lines = [f"# Journal diff, scope signals (C1b): {res['a']} against {res['b']}", "",
             f"- days {res['days'][0] or 'all'} … {res['days'][1] or 'all'}; "
             f"**{'GREEN' if res['green'] else 'RED'}**: {res['unexplained']} unexplained"
             + (f", explained {res['explained']}" if res["explained"] else "")
             + ("; state groups differs or is missing (it must be present and equal)" if res["state_mismatch"] else "")
             + (f"; **schema differs**: {res['schema_diff']}" if res["schema_diff"] else "")
             + (f"; expected new tables (--new-table): {res['new_tables']}" if res.get("new_tables") else ""),
             f"- compared: the signals rows by (family, variant, key, t) on {', '.join(SIGNAL_COLS)} and gate; status and "
             "reason are not (busy and risk: C2/C3)",
             f"- rows per gate: A {res['gates']['a']}, B {res['gates']['b']}",
             f"- decided in C2/C3 (A's status among its 'pass' rows): {res['decided_in_c2_c3']}",
             f"- B rows decided beyond C1b (a status other than NULL or 'skipped' at a farm gate): {t['decided_beyond_c1b']}",
             f"- state (reported): cost_scenario {'equal' if st['cost_scenario']['equal'] else 'differs'}; "
             f"feed A {st['feed']['a']}, B {st['feed']['b']}",
             "", "| table | rows A | rows B | only A | only B | different | decided beyond C1b | unexplained |",
             "|---|---|---|---|---|---|---|---|",
             f"| signals | {t['rows_a']} | {t['rows_b']} | {t['only_a']} | {t['only_b']} | {t['different']} | "
             f"{t['decided_beyond_c1b']} | {t['unexplained']} |"]
    for d in t["different_first"][:10]:
        lines.append(f"- signals {d['key']}: {d['columns']}")
    for k in t["only_a_first"][:5]:
        lines.append(f"- signals only in A: {k}")
    for k in t["only_b_first"][:5]:
        lines.append(f"- signals only in B: {k}")
    for d in t["decided_beyond_c1b_first"][:5]:
        lines.append(f"- signals decided beyond C1b in B: {d['key']} status {d['status']}, reason {d['reason']}")
    return lines


def settlement_matches(row, scenario, rule, *, check_return=False):
    """Check the complete cost arithmetic before waiving a write-off versus late-sale difference.

    The scenario must be known, and absent values are not zero. This is stricter than checking a delta: two equally
    corrupted P&Ls must not explain each other. The existing settle function remains the only cost model.
    """
    if scenario is None or type(rule) is not int or rule not in (1, 2):
        return False
    if any(type(row.get(c)) is not int for c in ("cost", "proceeds", "exit_attempts", "pnl")):
        return False
    if min(row["cost"], row["proceeds"]) < 0 or row["exit_attempts"] < 1:
        return False
    if type(row.get("exit_slot")) is not int or not isinstance(row.get("exit_reason"), str):
        return False
    result = SimpleNamespace(entry_failed=False, cost=row["cost"], proceeds=row["proceeds"],
                             exit_attempts=row["exit_attempts"], exit_slot=row["exit_slot"], exit_reason=row["exit_reason"])
    pnl, ret, _ = settle(result, scenario, rule)
    return row["pnl"] == pnl and (not check_return or row.get("ret") == ret)


def writeoff_causes(path_a, path_b):
    """The causes of an engine without the write-off (A) against one with it (B, engine._write_off_if_stuck), both on the
    same input: `written_off`, B's exit 'written_off' (and its sell fill) where A's position never sold (exit_slot NULL,
    proceeds 0, e.g. closed by the data's end), with the same entry; B's exit at its last decision + d, that decision at or
    after the rule's deadline (entry_slot + cap_slots), the P&L apart by the failed attempts only, and no other exits
    column apart than those a write-off ends earlier (WRITE_OFF_COLS). `written_off_later_sold`: the same write-off where
    A's position sold later, after B's write-off (the pool got liquidity again: the late sale the write-off forgoes, by the
    owner's rule); listed apart. `busy_freed`: after a write-off freed the pool of a (family, variant, key), a signal both
    runs have whose only difference is busy on one side and entered on the other, and a row of that entered signal
    (order, fill, exit, position) the busy side has not. A signal only one side has, a row of a signal both entered, or a
    row both have that differs in anything else stays unexplained (Codex review #7)."""
    states = [_state(p) for p in (path_a, path_b)]
    exits = []
    for p, state in zip((path_a, path_b), states):
        rows = _raw(p, "exits")
        for r in rows:
            if r.get("settle_rule") is None:
                r["settle_rule"] = int(state.get("settle_rule") or 1)
        exits.append({tuple(r[c] for c in TABLES["exits"][0]): r for r in rows})
    wo = {k: r for k, r in exits[1].items() if r["exit_reason"] == "written_off"}
    costs = json.loads(states[1].get("cost_scenario") or "{}")
    failed_tx = costs.get("failed_tx")
    try:
        scenario = Scenario(**costs) if states[0].get("cost_scenario") == states[1].get("cost_scenario") else None
    except (TypeError, ValueError):
        scenario = None  # a missing/incomplete cost model cannot justify a later sale's P&L
    freed = {}
    for k, r in wo.items():
        freed.setdefault(k[:3], []).append(r["exit_slot"])
    sig = [_rows(p, "signals", *TABLES["signals"], None, None) for p in (path_a, path_b)]

    def written_off(table, key, a, b):
        if table not in ("exits", "fills") or a is None or b is None:
            return None
        x = wo.get(tuple(key[:4]))
        if x is None:
            return None
        if table == "fills":  # A fill is explained only through the matching, validated exit pair.
            if key[4] != "sell" or (b["slot"], b["quote"], a["tokens"]) != (x["exit_slot"], 0, b["tokens"]):
                return None
            parent_key = tuple(key[:4])
            parent_a = exits[0].get(parent_key)
            if parent_a is None or any((fill["slot"], fill["quote"], fill["tokens"])
                                      != (parent["exit_slot"], parent["proceeds"], parent["tokens"])
                                      for fill, parent in ((a, parent_a), (b, x))):
                return None
            return written_off("exits", parent_key, parent_a, x)
        differ = {c for c in TABLES["exits"][1] if a[c] != b[c]}
        d = x["entry_slot"] - key[3]  # x: B's exits row with every column (cap_slots is not compared)
        b_ok = ((a["entry_slot"], a["tokens"], a["cost"]) == (x["entry_slot"], x["tokens"], x["cost"])
                and x["exit_slot"] == x["exit_decision_slot"] + d and x["cap_slots"] is not None
                and x["exit_decision_slot"] >= x["entry_slot"] + x["cap_slots"] and not x["proceeds"])
        if not b_ok:
            return None
        if (a["exit_slot"] is None and not a["proceeds"] and failed_tx is not None and not differ - WRITE_OFF_COLS
                and x["pnl"] - a["pnl"] == (a["exit_attempts"] - x["exit_attempts"]) * failed_tx):
            return "written_off"
        if (a["exit_slot"] is not None and a["exit_slot"] > x["exit_slot"]
                and a["exit_reason"] not in ("written_off", "end_of_data", "window_end")
                and not differ - WRITE_OFF_COLS - {"proceeds", "vault_capped", "settle_rule"}
                and settlement_matches(a, scenario, a["settle_rule"], check_return=True)
                and settlement_matches(x, scenario, x["settle_rule"], check_return=True)):
            return "written_off_later_sold"
        return None

    def pair(k):
        """(A's, B's) (status, reason) of the signal k, or None when a side has no such signal."""
        s = [x.get(k) for x in sig]
        return None if None in s else tuple((r["status"], r["reason"]) for r in s)

    def busy_freed(table, key, a, b):
        slots = freed.get(tuple(key[:3]))
        if not slots or key[3] <= min(slots):
            return None
        p = pair(tuple(key[:4]))
        if p is None or set(p) != FREED_SIGNAL:
            return None  # a signal one side lacks, or not busy against entered
        if table == "signals":
            if a is None or b is None or {c for c in TABLES["signals"][1] if a[c] != b[c]} - {"status", "reason"}:
                return None
            return "busy_freed"
        entered = 0 if p[0] == ("entered", None) else 1  # the side whose signal entered has the rows, the busy side none
        return "busy_freed" if (a, b)[entered] is not None and (a, b)[1 - entered] is None else None

    return (written_off, busy_freed)


def _raw(path, table):
    db = sqlite3.connect(f"file:{path}/journal.sqlite?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(f"SELECT * FROM {table}")]
    finally:
        db.close()


def run(journal_a, journal_b, out, lo_day=None, hi_day=None, new_tables=(), scope="all", causes=()):
    if scope == "signals":
        res = compare_signals(journal_a, journal_b, lo_day, hi_day, causes=causes, new_tables=new_tables)
    else:
        res = compare(journal_a, journal_b, lo_day, hi_day, causes=causes, new_tables=new_tables)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "journal_diff.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    lines = report_signals(res) if scope == "signals" else report(res)
    with open(os.path.join(out, "journal_diff.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:3]))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal-a", required=True, help="a journal directory (with journal.sqlite): the reference")
    ap.add_argument("--journal-b", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--from-day", help="first UTC day (YYYY-MM-DD) compared")
    ap.add_argument("--to-day", help="last UTC day compared")
    ap.add_argument("--new-table", action="append", default=[], metavar="NAME",
                    help="a table a newer engine adds: in one journal only it is no schema difference (repeatable)")
    ap.add_argument("--scope", choices=("all", "signals"), default="all",
                    help="signals: M1 C1b's comparison, the signals table only and no status beyond the farm gates")
    ap.add_argument("--writeoff-causes", action="store_true",
                    help="A an engine without the write-off, B with it: name the causes written_off and busy_freed")
    a = ap.parse_args(argv)
    causes = writeoff_causes(a.journal_a, a.journal_b) if a.writeoff_causes else ()
    res = run(a.journal_a, a.journal_b, a.out, a.from_day, a.to_day, tuple(a.new_table), a.scope, causes)
    return 0 if res["green"] else 1


if __name__ == "__main__":
    sys.exit(main())
