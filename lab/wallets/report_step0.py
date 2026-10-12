"""Markdown tables for walvis stap 0 from clusters.py and veto.py output.

  python -m wallets.report_step0 --clusters DIR [--veto DIR ...]
"""

import argparse
import glob
import json
import os

import duckdb

from .common import STORE


def cluster_tables(cdir):
    con = duckdb.connect()
    con.execute("SET memory_limit = '1GB'; SET threads TO 2")
    st = f"read_parquet('{os.path.join(cdir, 'stats-*.parquet')}')"
    days = con.execute(f"SELECT (max(window_slot) - min(window_slot)) FROM {st}").fetchone()[0]
    windows = con.execute(f"SELECT count(DISTINCT window_slot) FROM {st}").fetchone()[0]
    out = [f"Windows: {windows} × 20 min.", "",
           "| kind | venue | create slot | link | events | SOL bought | sold ≤3 s | ≤40 s | ≤10 min | ≥50% sold ≤10 min |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    rows = con.execute(f"""
      SELECT kind, CASE WHEN venue = 'curve' THEN 'curve' ELSE 'pool ' || orientation END AS v, create_slot,
             CASE WHEN kind = 'solo' THEN '' ELSE link END AS l,
             sum(events), sum(sol), sum(share_sum_3s) / sum(events), sum(share_sum_40s) / sum(events),
             sum(share_sum_10m) / sum(events), sum(half_10m) / sum(events)
      FROM {st} GROUP BY ALL ORDER BY v, create_slot, kind, l""").fetchall()
    for kind, v, cs, link, n, sol, a, b, c, h in rows:
        out.append(f"| {kind} | {v} | {'yes' if cs else 'no'} | {link} | {int(n):,} | {sol:,.0f} | {a:.0%} | {b:.0%} | "
                   f"{c:.0%} | {h:.0%} |")
    out += ["", "Same, clusters only, split by token: graduated in its own create slot (never tradable) or not.", "",
            "| venue | token graduated in create slot | cluster events | SOL bought | sold ≤40 s | ≤10 min |",
            "|---|---|---|---|---|---|"]
    ev = f"read_parquet('{os.path.join(cdir, 'events-*.parquet')}')"
    con.execute(f"ATTACH '{STORE}' AS s (READ_ONLY)")
    rows = con.execute(f"""
      SELECT CASE WHEN venue = 'curve' THEN 'curve' ELSE 'pool ' || orientation END AS v,
             COALESCE(m.complete_slot = m.create_slot, false) AS g, count(*), sum(sol) / 1e9,
             avg(least(1.0, sold_40s::DOUBLE / tokens)), avg(least(1.0, sold_10m::DOUBLE / tokens))
      FROM {ev} e LEFT JOIN s.mints m ON m.mint = e.mint WHERE tokens > 0 GROUP BY ALL ORDER BY ALL""").fetchall()
    for v, g, n, sol, b, c in rows:
        out.append(f"| {v} | {'yes' if g else 'no'} | {n:,} | {sol:,.0f} | {b:.0%} | {c:.0%} |")
    return "\n".join(out), days


def veto_tables(vdirs):
    out = ["| veto | family | segment | farm | size SOL | d | positions | vetoed | net/trade base | Δ net/trade | Δ per day |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for vdir in vdirs:
        for f in sorted(glob.glob(os.path.join(vdir, "summary-*.json"))):
            for s in json.load(open(f))["summary"]:
                if "grad_create_slot" in s or s["size_sol"] not in (0.5, 1.0):
                    continue
                per_day = " / ".join(f"{x:+.4f}" for x in s["per_day"])
                out.append(f"| {s['veto']} | {s['family']} | {s['segment']} | {s['farm']} | {s['size_sol']} | {s['d']} | "
                           f"{s['positions']:,} | {s['veto_share']:.1%} | {s['base_sol']:+.4f} | {s['delta_sol']:+.5f} | {per_day} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clusters", required=True)
    ap.add_argument("--veto", nargs="*", default=[])
    a = ap.parse_args()
    print(cluster_tables(a.clusters)[0])
    if a.veto:
        print()
        print(veto_tables(a.veto))


if __name__ == "__main__":
    main()
