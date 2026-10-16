"""Two paper journals compared row for row: the Python engine against the Rust live route on the same input (M1 and
M3 of lab/docs/rust-live-route-design-2026-10-16.md §3), or any two runs of the same engine.

  python -m paper.journal_diff --journal-a <dir> --journal-b <dir> --out <dir> [--from-day D] [--to-day D]
  python -m paper.parity --journal-a <dir> --journal-b <dir> --out <dir>          # the same

Per table, keyed as the journal keys it (paper/journal.py TABLES): rows only in A, rows only in B, and rows in
both that differ in a compared column. Compared are the decisions and their outcome: signal status, reason, farm
labels, entry, fills, exit, proceeds, P&L, the exit windows. Not compared are the measured arrival times (receive,
created_at, latency, segment lag), which two engines on one stream measure differently by design. Every
difference is unexplained until a cause is named (`causes`, empty now). Green: 0 unexplained. Writes
journal_diff.md and journal_diff.json; exit 1 when red.
"""

import argparse
import json
import os
import sqlite3
import sys

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
               "sells_dropped", "sells_scaled", "peak_mark", "peak_slot", "n_flow_slots", "cap_slots")),
    "positions": (("family", "variant", "key", "t"), ("entry_slot", "cost", "tokens", "status")),
}
# State the two runs must share: the same groups and the same costs, or the comparison means nothing.
STATE_KEYS = ("groups", "cost_scenario", "feed")
SHOWN = 50


def _rows(path, table, keys, cols, lo_day, hi_day):
    """{key tuple: {col: value}} of one table, read-only; the columns a journal lacks (an older one) read as None."""
    db = sqlite3.connect(f"file:{os.path.join(path, 'journal.sqlite')}?mode=ro", uri=True)
    try:
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if not have:
            return {}
        sel = [c if c in have else f"NULL AS {c}" for c in (*keys, *cols)]
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


def _state(path):
    db = sqlite3.connect(f"file:{os.path.join(path, 'journal.sqlite')}?mode=ro", uri=True)
    try:
        return {k: v for k, v in db.execute("SELECT k, v FROM state") if k in STATE_KEYS}
    finally:
        db.close()


def compare(path_a, path_b, lo_day=None, hi_day=None, causes=()):
    """The comparison as a dict: per table the row counts, only-A, only-B and differing rows (the first SHOWN
    of each with their columns), the shared state, and `unexplained` (every difference no cause in `causes`
    explains; causes: callables (table, key, row_a, row_b) -> a cause name or None)."""
    res = {"a": path_a, "b": path_b, "days": [lo_day, hi_day], "tables": {}, "unexplained": 0, "explained": {}}
    sa, sb = _state(path_a), _state(path_b)
    res["state"] = {k: {"a": sa.get(k), "b": sb.get(k), "equal": sa.get(k) == sb.get(k)} for k in STATE_KEYS}
    # The feed may differ (a replay against a live run); groups and costs may not.
    res["state_mismatch"] = [k for k in ("groups", "cost_scenario") if not res["state"][k]["equal"]]
    for table, (keys, cols) in TABLES.items():
        ra, rb = _rows(path_a, table, keys, cols, lo_day, hi_day), _rows(path_b, table, keys, cols, lo_day, hi_day)
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
        res["tables"][table] = {"rows_a": len(ra), "rows_b": len(rb), "only_a": len(only_a), "only_b": len(only_b),
                                "different": len(diff), "unexplained": unexplained, "explained": explained,
                                "only_a_first": [list(k) for k in only_a[:SHOWN]], "only_b_first": [list(k) for k in only_b[:SHOWN]],
                                "different_first": [{"key": list(k), "columns": c} for k, c in diff[:SHOWN]]}
        res["unexplained"] += unexplained
        for c, n in explained.items():
            res["explained"][c] = res["explained"].get(c, 0) + n
    res["green"] = res["unexplained"] == 0 and not res["state_mismatch"] and any(
        t["rows_a"] for t in res["tables"].values())
    return res


def report(res):
    lines = [f"# Journal diff: {res['a']} against {res['b']}", "",
             f"- days {res['days'][0] or 'all'} … {res['days'][1] or 'all'}; "
             f"**{'GREEN' if res['green'] else 'RED'}**: {res['unexplained']} unexplained"
             + (f", explained {res['explained']}" if res["explained"] else "")
             + (f"; state differs in {res['state_mismatch']} (groups and costs must be equal)" if res["state_mismatch"] else ""),
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


def run(journal_a, journal_b, out, lo_day=None, hi_day=None):
    res = compare(journal_a, journal_b, lo_day, hi_day)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "journal_diff.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    lines = report(res)
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
    a = ap.parse_args(argv)
    res = run(a.journal_a, a.journal_b, a.out, a.from_day, a.to_day)
    return 0 if res["green"] else 1


if __name__ == "__main__":
    sys.exit(main())
