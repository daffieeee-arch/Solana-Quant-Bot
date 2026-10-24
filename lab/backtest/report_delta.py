"""Old and new report JSON side by side (Codex review #5: E63 / E65 / E70 recomputed from their existing parts with the
daily loss limit on the exit day). Every scalar leaf that differs (path, old, new, delta) and, for a list of numbers
(a day series), its largest difference; a path only one side has is listed too. Gate: no leaf under a key in
UNCHANGED may differ (the fix moves only the limited simulation: the rows without limits and the d 0 expectation stay).

  python -m backtest.report_delta OLD.json NEW.json --label E63 --out DIR    (delta-<label>.md and .json; rc 1 on RED)
"""

import argparse
import json
import math
import os
import sys

UNCHANGED = ("unlimited", "expected")
SKIP = ("provenance", "plan", "plan_sha256", "gates", "caveats")  # inputs and gate texts: reported by the runs themselves


def leaves(x, path=()):
    """(path, value) for every scalar leaf; a list of numbers is one leaf (a series)."""
    if isinstance(x, dict):
        for k, v in x.items():
            if not path and k in SKIP:
                continue
            yield from leaves(v, path + (str(k),))
    elif isinstance(x, list) and x and all(isinstance(e, (int, float)) and not isinstance(e, bool) or e is None for e in x):
        yield path, x
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from leaves(v, path + (_name(v, i),))
    else:
        yield path, x


def _name(v, i):
    """A list element's name: its identifying fields where it has them (a cell, a limited row), else its index."""
    if isinstance(v, dict):
        ids = [f"{k}={v[k]}" for k in ("candidate", "variant", "farm", "size", "d", "scenario", "tol", "mapping", "unsold", "capital")
               if k in v and not isinstance(v[k], (dict, list))]
        if ids:
            return "[" + ",".join(ids) + "]"
    return f"[{i}]"


def same(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (math.isnan(a) and math.isnan(b)) or abs(a - b) <= 1e-12 * max(1.0, abs(a), abs(b))
    return a == b


def diff(old, new):
    o, n = dict(leaves(old)), dict(leaves(new))
    rows, red = [], []
    for p in sorted(set(o) | set(n)):
        a, b = o.get(p, "<missing>"), n.get(p, "<missing>")
        if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            d = max((abs((x or 0) - (y or 0)) for x, y in zip(a, b)), default=0.0)
            if d > 1e-12:
                rows.append((p, f"series[{len(a)}]", f"series[{len(b)}]", f"max |d| {d:.6g}"))
            else:
                continue
        elif same(a, b):
            continue
        else:
            dd = (b - a) if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool) else ""
            rows.append((p, a, b, dd))
        if any(k in UNCHANGED for k in p):
            red.append("/".join(p))
    return {"compared": len(set(o) | set(n)), "changed": len(rows), "rows": rows, "red_unchanged": red}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    with open(a.old) as f, open(a.new) as g:
        res = diff(json.load(f), json.load(g))
    os.makedirs(a.out, exist_ok=True)
    status = "RED" if res["red_unchanged"] else "GREEN"
    with open(os.path.join(a.out, f"delta-{a.label}.json"), "w") as f:
        json.dump({"label": a.label, "old": a.old, "new": a.new, "status": status, **res}, f, indent=1, default=str)
    lines = [f"# {a.label}: old and new side by side", "",
             f"Old: `{a.old}`; new: `{a.new}`. {res['compared']} leaves compared, {res['changed']} differ. "
             f"Gate (nothing under {', '.join(UNCHANGED)} differs): **{status}**"
             + (f" ({len(res['red_unchanged'])}: {res['red_unchanged'][:5]})" if res["red_unchanged"] else "") + ".", "",
             "| path | old | new | delta |", "|---|---|---|---|"]
    cell = lambda x: str(x).replace("|", "\\|")  # noqa: E731  (E65's 'F|miss' keys)
    lines += [f"| {cell('/'.join(p))} | {cell(o)} | {cell(n)} | {cell(d)} |" for p, o, n, d in res["rows"]]
    with open(os.path.join(a.out, f"delta-{a.label}.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{a.label}: {res['changed']} of {res['compared']} leaves differ; gate {status} (delta-{a.label}.md)")
    return 1 if status == "RED" else 0


if __name__ == "__main__":
    sys.exit(main())
