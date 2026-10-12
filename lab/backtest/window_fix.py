"""One-off correction of a finished run's positions to the window-end rule (replay.WINDOW_END).

Runs made before the rule let a position whose deadline lay after the store's end decide at that
deadline, on the last state of the window, and recorded the exit slots past the end (and S1's
"never" sentinel, entry + 1e9). Some S1 rebalance entries fell on or after the end. The rule:

- an entry at or after the window end is skipped ("after_window_end", no tokens, cost or proceeds);
- a position that would decide or fill at or after it closes on the window's last slot, W - 1
  ("window_end"); its proceeds stay those of the last state, as the old run computed them (no event
  of the token lies between the last state and any later slot inside the data).

The parts files that hold such rows are rewritten (originals kept in parts-before-window-fix/), and
window_fix.json records what changed. Rebuild the report afterwards (report.write_report).

  python -m backtest.window_fix <run_dir>            # dry: counts only
  python -m backtest.window_fix <run_dir> --apply
"""

import argparse
import glob
import json
import os
import shutil
from collections import Counter

import pyarrow as pa
import pyarrow.compute  # noqa: F401 (pa.compute)
import pyarrow.parquet as pq


def fix_rows(rows, w):
    """Correct a list of position dicts in place; returns Counter((family, change))."""
    changed = Counter()
    for r in rows:
        if r["skipped"]:
            continue
        if r["entry_slot"] is not None and r["entry_slot"] >= w:
            r.update(skipped=True, exit_reason="after_window_end", tokens=0, cost=0, proceeds=0, entry_failed=False,
                     exit_decision_slot=None, exit_slot=None, exit_attempts=0, cf_graduation=False, seed_pool=False,
                     vault_capped=False, reverted_direct=0, reverted_router=0, sells_dropped=0, sells_scaled=0,
                     hist_ret=None)
            changed[(r["family"], "entry after the window: skipped")] += 1
        elif (r["exit_slot"] is not None and r["exit_slot"] >= w) or (
                r["exit_decision_slot"] is not None and r["exit_decision_slot"] >= w):
            r["exit_decision_slot"] = min(r["exit_decision_slot"], w - 1) if r["exit_decision_slot"] is not None else w - 1
            r["exit_slot"] = w - 1
            r["exit_reason"] = "window_end"
            changed[(r["family"], "closed on the window's last slot")] += 1
    return changed


def run(run_dir, apply=False):
    with open(os.path.join(run_dir, "config.json")) as f:
        w = json.load(f)["store"]["slot_end_exclusive"]
    total, files = Counter(), []
    backup = os.path.join(run_dir, "parts-before-window-fix")
    for path in sorted(glob.glob(os.path.join(run_dir, "parts", "*.parquet"))):
        slots = pq.read_table(path, columns=["entry_slot", "exit_slot", "exit_decision_slot", "skipped"])
        ex = pa.compute
        late = ex.or_(ex.greater_equal(ex.fill_null(slots["entry_slot"], 0), w),
                      ex.or_(ex.greater_equal(ex.fill_null(slots["exit_slot"], 0), w),
                             ex.greater_equal(ex.fill_null(slots["exit_decision_slot"], 0), w)))
        if not ex.any(ex.and_(late, ex.invert(slots["skipped"]))).as_py():
            continue
        table = pq.read_table(path)
        cols = set(table.column_names)
        rows = table.to_pylist()
        changed = fix_rows(rows, w)
        if not changed:
            continue
        total.update(changed)
        files.append(os.path.basename(path))
        if apply:
            os.makedirs(backup, exist_ok=True)
            if not os.path.exists(os.path.join(backup, os.path.basename(path))):
                shutil.copy2(path, backup)
            tmp = path + ".tmp"
            pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), tmp)
            os.replace(tmp, path)
        assert cols == set(table.column_names)
    report = {"window_end": w, "applied": apply, "files": files,
              "changes": [{"family": f, "change": c, "rows": n} for (f, c), n in sorted(total.items())]}
    if apply:
        with open(os.path.join(run_dir, "window_fix.json"), "w") as f:
            json.dump(report, f, indent=1)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    print(json.dumps(run(a.run_dir, a.apply), indent=1))


if __name__ == "__main__":
    main()
