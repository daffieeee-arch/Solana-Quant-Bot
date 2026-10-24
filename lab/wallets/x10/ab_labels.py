"""A/B of two x10 labels files for the same window (counts only): rows matched on (key, moment, t), identical entries
(tokens, cost), hit / stop_first counts per moment, means of the exit multiples and peaks, and the direction of change.
Used for the mark-cap fix proof (spec-runners-mark-cap-2026-10-24.md): old = the archived run, new = the fixed harness.

  python -m wallets.x10.ab_labels --old OLD.parquet --new NEW.parquet --out DIR [--tag s0.5]
Writes ab-<tag>.json and ab-<tag>.md. Fail-closed (x10-hertelling-plan-2026-10-24.md section 2): exit status 1, with the
reason in the json ("red"), when the row counts differ, an entry differs (tokens / cost) or any peak_24 went down; the
NAS job turns that into RED before stage B.
"""

import argparse
import json
import os
import sys

import duckdb

HITS = ("hit_2_24", "hit_5_24", "hit_10_24", "hit_2_72", "hit_5_72", "hit_10_72", "stop_first_24", "stop_first_72")
MEANS = ("x_tp2_24", "x_trail30_24", "x_tp5_24", "peak_24", "end_24")
TIE_TOL = 0.0015  # one fee-tier step (5-10 bps on the gross) on a peak mark, as a relative drop of the net peak; plan v0.5


def compare(old, new):
    con = duckdb.connect()
    con.execute("SET memory_limit='1GB'")
    con.execute(f"CREATE VIEW o AS SELECT * FROM read_parquet('{old}')")
    con.execute(f"CREATE VIEW n AS SELECT * FROM read_parquet('{new}')")
    cols_o = {r[0] for r in con.execute("DESCRIBE o").fetchall()}
    cols_n = {r[0] for r in con.execute("DESCRIBE n").fetchall()}
    res = {"old": old, "new": new,
           "rows": con.execute("SELECT (SELECT count(*) FROM o), (SELECT count(*) FROM n), (SELECT count(*) FROM o JOIN n USING (key, moment, t))").fetchone(),
           "entries_identical": con.execute("SELECT count(*) FROM o JOIN n USING (key, moment, t) WHERE o.tokens = n.tokens AND o.cost_sol = n.cost_sol").fetchone()[0],
           "by_moment": {}, "means": {}, "direction": {}}
    moments = [r[0] for r in con.execute("SELECT DISTINCT moment FROM o ORDER BY 1").fetchall()]
    for m in moments:
        d = {"n": con.execute(f"SELECT count(*) FROM o JOIN n USING (key, moment, t) WHERE o.moment = '{m}'").fetchone()[0]}
        for h in HITS:
            if h in cols_o and h in cols_n:
                a, b = con.execute(f"SELECT sum(o.{h}::INT), sum(n.{h}::INT) FROM o JOIN n USING (key, moment, t) WHERE o.moment = '{m}'").fetchone()
                d[h] = {"old": int(a or 0), "new": int(b or 0)}
        thin = con.execute(f"SELECT round(avg((o.depth_sol < o.cost_sol)::INT), 4) FROM o WHERE o.moment = '{m}'").fetchone()[0] if "depth_sol" in cols_o else None
        d["share_depth_below_cost"] = thin
        res["by_moment"][m] = d
    for c in MEANS:
        if c in cols_o and c in cols_n:
            a, b = con.execute(f"SELECT avg(o.{c}), avg(n.{c}) FROM o JOIN n USING (key, moment, t)").fetchone()
            res["means"][c] = {"old": a, "new": b}
    changed = con.execute("SELECT count(*) FROM o JOIN n USING (key, moment, t) WHERE " + " OR ".join(f"o.{h} <> n.{h}" for h in HITS if h in cols_o and h in cols_n)).fetchone()[0]
    res["rows_with_a_changed_label"] = changed
    if "peak_24" in cols_o:
        up, down = con.execute("SELECT sum((n.peak_24 > o.peak_24)::INT), sum((n.peak_24 < o.peak_24)::INT) FROM o JOIN n USING (key, moment, t)").fetchone()
        res["direction"] = {"peak_24_up": int(up or 0), "peak_24_down": int(down or 0)}
        # every peak_24 drop, with the new run's peak-row kind and mixed-tier flag when the new file carries them (labels since
        # 2026-10-24); the tolerance of main() reads these: a drop is explained by the pre-fix slot-ASOF tie only when the new
        # peak row is a boost / withdraw / deposit row in a slot with more than one fee tier, and is at most one tier step (TIE_TOL)
        has_kind = "peak_kind_24" in cols_n and "peak_mixed_24" in cols_n
        kind_cols = ", n.peak_kind_24, n.peak_mixed_24" if has_kind else ", NULL, NULL"
        res["peak_24_drops"] = [{"key": r[0], "moment": r[1], "t": r[2], "old": r[3], "new": r[4], "rel": (r[3] - r[4]) / r[3] if r[3] else None,
                                 "peak_kind_24": r[5], "peak_mixed_24": r[6]}
                                for r in con.execute(f"SELECT o.key, o.moment, o.t, o.peak_24, n.peak_24{kind_cols} FROM o JOIN n USING (key, moment, t) WHERE n.peak_24 < o.peak_24 ORDER BY 1, 2, 3").fetchall()]
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default="ab")
    ap.add_argument("--allow-tier-tie-drops", action="store_true",
                    help="x10-hertelling-plan v0.5: a peak_24 drop is tolerated (listed, not RED) only when it is at most TIE_TOL relative and the new "
                         "run's peak row is a boost / withdraw / deposit row in a slot with more than one fee tier (the pre-2026-10-24 slot-ASOF tie); "
                         "every other drop stays RED")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    res = compare(a.old, a.new)
    red = []
    if not (res["rows"][0] == res["rows"][1] == res["rows"][2]):
        red.append(f"rows differ old/new/matched {res['rows']}")
    if res["entries_identical"] != res["rows"][2]:
        red.append(f"entries not identical: {res['entries_identical']} of {res['rows'][2]}")
    drops = res.get("peak_24_drops", [])
    if a.allow_tier_tie_drops:
        allowed = [d for d in drops if d["rel"] is not None and d["rel"] <= TIE_TOL and d["peak_kind_24"] in ("boost", "withdraw", "deposit") and d["peak_mixed_24"]]
        res["peak_24_drops_allowed"] = allowed
        res["peak_24_drops_red"] = [d for d in drops if d not in allowed]
        if res["peak_24_drops_red"]:
            red.append(f"peak_24 down on {len(res['peak_24_drops_red'])} rows outside the tier-tie tolerance ({len(allowed)} allowed)")
    elif res["direction"].get("peak_24_down", 0) > 0:
        red.append(f"peak_24 down on {res['direction']['peak_24_down']} rows")
    res["red"] = red
    with open(os.path.join(a.out, f"ab-{a.tag}.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    lines = [f"# A/B labels {a.tag}: rows old/new/matched {res['rows']}, identical entries {res['entries_identical']}, rows with a changed label {res['rows_with_a_changed_label']}", "",
             "| moment | n | " + " | ".join(f"{h} old→new" for h in HITS) + " | depth < cost |", "|---|---|" + "---|" * (len(HITS) + 1)]
    for m, d in res["by_moment"].items():
        lines.append(f"| {m} | {d['n']} | " + " | ".join(f"{d[h]['old']}→{d[h]['new']}" if h in d else "–" for h in HITS) + f" | {d['share_depth_below_cost']} |")
    lines += ["", "means old → new: " + ", ".join(f"{c} {v['old']:.3f}→{v['new']:.3f}" for c, v in res["means"].items() if v["old"] is not None), f"direction: {res['direction']}",
              *[f"- peak_24 down: {d['key']} {d['moment']} {d['t']}: {d['old']:.6f} -> {d['new']:.6f} (rel {d['rel']:.6f}), peak row {d['peak_kind_24']} mixed {d['peak_mixed_24']}"
                + (" ALLOWED" if d in res.get("peak_24_drops_allowed", []) else "") for d in res.get("peak_24_drops", [])[:200]],
              # the verdict is always the last line (the NAS job and the tests read it)
              ("RED: " + "; ".join(red)) if red else ("GREEN: rows equal, entries identical, no peak_24 down" if not res.get("peak_24_drops_allowed")
                                                      else f"GREEN: rows equal, entries identical, {len(res['peak_24_drops_allowed'])} peak_24 drops within the tier-tie tolerance (listed)")]
    with open(os.path.join(a.out, f"ab-{a.tag}.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
