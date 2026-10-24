"""Entry tolerance sweep on dev (research/tol-sweep-plan-2026-10-24.md, sha256 35ca706b; E70; exploratory and in-sample,
no new rule): H5 and C3/hold300 at d 3, 0.5 and 1.25 SOL under E63's capital rules, at the engine's tolerance 0.02 /
0.05 / 0.10 / 0.15. The engine has one tolerance for both legs (an entry fails below the expected tokens x (1 - tol), an
exit whose sale would come below the mark x (1 - tol) is retried), as paper's Engine (tol=0.05); the sweep moves both.

  python -m backtest.tol_sweep --parts RUN_DIR [RUN_DIR ...] --e63-json JSON --c3-keys PARQUET --out DIR

- Rows: each run's F7 pool_reversed positions of flow300_tp100_H6 and hold300 at d 3, q 0.5, 0.5 / 1.25 SOL, overlay
  none, not skipped, tagged with the run's slippage_tol. E63's run (capital-ceiling-d15ac45) is the tol 0.05 run and
  comes last in --parts.
- T2: every run's config equals E63's (the last run) on the racing_cost.SAME fields but slippage_tol, the store commit
  and q; it has d 3 and the leads' variants and sizes; the slippage_tols are exactly TOLS, one run each. T3: dev only
  (capital_ceiling.check_run, rows inside dev). T1: the tol 0.05 cells reproduce E63's ceiling.json (unlimited, and
  with limits at the primary mapping, unsold freed at the deadline) exactly. Any RED: exit 1.
- Per lead x size x tol, per scenario: E63's unlimited() and limited() (a view r per tol, so capital_ceiling's own
  cell_rows, unlimited and limited run unchanged); entries taken per day; the failed-entry share; the drain share
  (filled positions with proceeds < 10 % of cost, E63's total-loss rule); the exit-retry share (exit_attempts > 1); and
  the paired difference against tol 0.05 (day bootstrap of the daily difference, with limits).

Writes tol.json and tol.md.
"""

import argparse
import json
import os
import sys

import duckdb

from . import racing_cost as rc
from .capital_ceiling import DEV, SCENARIOS, Refused, boot_days, cell_rows, check_run, ci, fmt, limited, unlimited
from .costs import Scenario, settle_sql
from .store import load_defaults

PLAN = "research/tol-sweep-plan-2026-10-24.md"
PLAN_SHA = "35ca706b8ae3312a6f1de77d6e1aa33da0f1c472e1433190af41b060e0af5999"
TOLS = (0.02, 0.05, 0.10, 0.15)
REF_TOL = 0.05  # E63's run
SIZES = (0.5, 1.25)
D = 3
SHARE = 0.05
LEADS = rc.LEADS  # (name, variant, farm, C3 arm): H5 and C3/hold300
VARIANTS = rc.VARIANTS
SAME = tuple(f for f in rc.SAME if f != "slippage_tol") + ("q",)


def grid(runs):
    """T2 (see the docstring): the runs' configs and their slippage_tol, E63's run last."""
    cfgs = []
    for run in runs:
        check_run(run)
        with open(os.path.join(run, "config.json")) as f:
            cfgs.append(json.load(f))
    ref = cfgs[-1]
    bad, tols = [], []
    for run, c in zip(runs, cfgs):
        diff = [k for k in SAME if c.get(k) != ref.get(k)]
        if (c.get("store") or {}).get("git_commit") != (ref.get("store") or {}).get("git_commit"):
            diff.append("store.git_commit")
        if D not in (c.get("delays") or []):
            diff.append(f"delays {c.get('delays')}")
        if not set(VARIANTS) <= set(c.get("variants") or []):
            diff.append(f"variants {c.get('variants')}")
        if not set(SIZES) <= set(c.get("sizes_sol") or []):
            diff.append(f"sizes {c.get('sizes_sol')}")
        if diff:
            bad.append(f"{run}: {diff}")
        tols.append(c.get("slippage_tol"))
    if bad:
        raise Refused(f"T2 RED: the runs' grids differ from E63's (the last run): {bad}")
    if sorted(tols) != sorted(TOLS) or len(set(tols)) != len(tols) or tols[-1] != REF_TOL:
        raise Refused(f"T2 RED: slippage_tol of the runs {tols}, not {list(TOLS)} once each with E63's {REF_TOL} last")
    return {"runs": [{"run": r, "slippage_tol": t, "engine_commit": c.get("engine_commit"), "slot_range": c.get("slot_range")}
                     for r, t, c in zip(runs, tols, cfgs)], "status": "GREEN"}


def load(con, runs, c3_keys):
    cfg = load_defaults()
    pnl = {s: settle_sql(Scenario.from_cfg(s, cfg))[0] for s in SCENARIOS}
    info = grid(runs)
    # E63's r columns plus tol and exit_attempts (capital_ceiling.cell_rows reads a table or view named r).
    con.execute("""CREATE TABLE rall (tol DOUBLE, src INTEGER, variant VARCHAR, size_sol DOUBLE, d INTEGER, t UBIGINT, day VARCHAR,
                   pool VARCHAR, farm VARCHAR, entry_failed BOOLEAN, cost BIGINT, proceeds BIGINT, exit_slot UBIGINT,
                   exit_decision_slot UBIGINT, d_ms DOUBLE, depth DOUBLE, exit_attempts BIGINT, pnl_base BIGINT, pnl_pessimistic BIGINT)""")
    vl = ", ".join(f"'{v}'" for v in VARIANTS)
    sl = ", ".join(repr(s) for s in SIZES)
    for i, (run, meta) in enumerate(zip(runs, info["runs"])):
        src = f"read_parquet('{os.path.join(run, 'parts', '*.parquet')}', union_by_name = true)"
        cols = {c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
        if "entry" in cols and con.execute(f"SELECT count(*) FROM {src} WHERE entry IS NOT NULL").fetchone()[0]:
            raise Refused(f"{run}: route-(ii) entry rows")
        overlay = "COALESCE(overlay, 'none') = 'none'" if "overlay" in cols else "true"
        con.execute(f"""INSERT INTO rall SELECT {meta['slippage_tol']}, {i}, variant, size_sol, d, t, day, pool, farm, entry_failed, cost,
                          proceeds, exit_slot, exit_decision_slot, d_ms, depth, exit_attempts, {pnl['base']}, {pnl['pessimistic']}
                        FROM {src} WHERE family = 'F7' AND segment = 'pool_reversed' AND {overlay} AND q = 0.5 AND d = {D}
                          AND variant IN ({vl}) AND size_sol IN ({sl}) AND NOT skipped""")
    dup = con.execute("SELECT tol, variant, size_sol, pool, t, count(*) FROM rall GROUP BY ALL HAVING count(*) > 1 LIMIT 3").fetchall()
    if dup:
        raise Refused(f"duplicate positions (tol, variant, size, pool, t), e.g. {dup}")
    lo, hi = con.execute("SELECT min(t), max(greatest(t, COALESCE(exit_slot, 0), COALESCE(exit_decision_slot, 0))) FROM rall").fetchone()
    if lo is None or lo < DEV[0] or hi >= DEV[1]:
        raise Refused(f"T3 RED: rows reach outside dev: [{lo}, {hi}]")
    con.execute(f"""CREATE TABLE c3 AS SELECT DISTINCT pool, t FROM read_parquet('{c3_keys}')
                    WHERE d = 2 AND h = '5m' AND paid_buyers_60s >= 2""")
    klo, khi, nk = con.execute("SELECT min(t), max(t), count(*) FROM c3").fetchone()
    if not nk or klo < DEV[0] or khi >= DEV[1]:
        raise Refused(f"C3 keys: {nk} keys in [{klo}, {khi}], not dev")
    info["c3_keys"] = nk
    return info


def _view(con, tol):
    con.execute(f"CREATE OR REPLACE TEMP VIEW r AS SELECT * FROM rall WHERE tol = {tol}")


def compute(con):
    days = [r[0] for r in con.execute("SELECT DISTINCT day FROM rall ORDER BY 1").fetchall()]
    leads, t1 = {}, {}
    for name, variant, farm, arm in LEADS:
        L = {"variant": variant, "farm": farm or "all", "c3_arm": arm, "cells": []}
        for size in SIZES:
            capital = round(size / SHARE, 6)
            for sc in SCENARIOS:
                series = {}
                for tol in TOLS:
                    _view(con, tol)
                    rows = cell_rows(con, variant, farm, arm, size, D, sc)
                    u, x = unlimited(rows, days), limited(rows, days, capital, size, "deadline")
                    if tol == REF_TOL:
                        t1[(name, size, D, sc)] = (u, x)
                    filled = [r for r in rows if not r[6]]
                    drain = sum(1 for r in filled if (r[9] or 0) < 0.1 * r[4])
                    join = "SEMI JOIN c3 USING (pool, t)" if arm else ""
                    where = f"r.variant = '{variant}' AND r.size_sol = {size} AND r.d = {D} AND NOT r.entry_failed" + (f" AND r.farm = '{farm}'" if farm else "")
                    n_f, retried = con.execute(f"SELECT count(*), count(*) FILTER (WHERE exit_attempts > 1) FROM (SELECT * FROM r {join} WHERE {where})").fetchone()
                    series[tol] = rc.run_days(rc.trades_of(rows), days, capital, size)[0]
                    L["cells"].append({"tol": tol, "size": size, "capital": capital, "scenario": sc, "unlimited": u, "limited": x,
                                       "drain_pct": round(100 * drain / len(filled), 2) if filled else None,
                                       "exit_retry_pct": round(100 * retried / n_f, 2) if n_f else None})
                for c in L["cells"]:
                    if c["size"] == size and c["scenario"] == sc and c["tol"] != REF_TOL:
                        m, lo, hi = boot_days([a - b for a, b in zip(series[c["tol"]], series[REF_TOL])])
                        c["vs_005"] = {"sol_day": m, "lo": lo, "hi": hi}
        leads[name] = L
    return days, leads, t1


def report_md(out):
    L = [f"# Entry tolerance sweep on dev (E70; exploratory, in-sample; plan {PLAN}, sha256 {PLAN_SHA[:16]}…)"
         + (" — DRY RUN, not evidence" if out["dry_run"] else ""), "",
         f"Dev only, {len(out['days'])} UTC days. d {D}, q 0.5; E63's capital rules (size 5 % of C: 0.5 SOL = C 10, 1.25 SOL = C 25; "
         "at most 5 open, daily loss 10 % of C, 24 h cooldown, one open per pool, unsold freed at the deadline). The engine's "
         "tolerance is one parameter for the entry (fails below the expected tokens x (1 - tol)) and the exit (retried below the "
         "mark x (1 - tol)), as paper's Engine: the sweep moves both legs. tol 0.05 = E63's run.", "",
         "Gates: " + "; ".join(f"{k} {v['status']}" for k, v in out["gates"].items()), ""]
    for name, Ld in out["leads"].items():
        for sc in ("base", "pessimistic"):
            L += [f"## {name} ({Ld['variant']}, farm {Ld['farm']}{', E56 arm keys' if Ld['c3_arm'] else ''}), costs {sc}", "",
                  "| size (C) | tol | with limits SOL/day | vs tol 0.05 (paired) | taken/day | entry failed % | drain % | exit retried % | "
                  "net per filled (% of cost) | without limits SOL/day |", "|---|---|---|---|---|---|---|---|---|---|"]
            for c in sorted((c for c in Ld["cells"] if c["scenario"] == sc), key=lambda c: (c["size"], c["tol"])):
                u, x, v = c["unlimited"], c["limited"], c.get("vs_005")
                L.append(f"| {c['size']:g} ({c['capital']:g}) | {c['tol']:g} | {ci(x['sol_day'], x['lo'], x['hi'])}{' RUINED' if x['ruined'] else ''} | "
                         f"{'–' if v is None else ci(v['sol_day'], v['lo'], v['hi'])} | {x['taken_per_day']:g} | {fmt(u['entry_failed_pct'], 1, False)} | "
                         f"{fmt(c['drain_pct'], 1, False)} | {fmt(c['exit_retry_pct'], 1, False)} | {fmt(u['net_per_filled_pct_of_cost'], 2)} | "
                         f"{ci(u['sol_day'], u['lo'], u['hi'])} |")
            L.append("")
    L += ["## Caveats", "", "In-sample (dev looked at > 1,300 times; H5 and the arm were chosen on it). A looser tolerance accepts worse fills: "
          "more sandwich room (not modelled). The tolerance changes which positions exist, so the busy chains differ per tol. "
          "One tolerance for both legs, as paper: an entry-only change is not computed. K3 is not part of this task. Live trigger "
          "supply is ~20-29 % of dev's. Pre-Alpenglow slots.", "", "## Provenance", ""]
    L += [f"- {r['run']}: slippage_tol {r['slippage_tol']}, engine {r['engine_commit']}" + (f", DRY RUN range {r['slot_range']}" if r["slot_range"] else "")
          for r in out["gates"]["T2"]["runs"]]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parts", nargs="+", required=True, help="the tol runs, E63's (tol 0.05) last")
    ap.add_argument("--e63-json", required=True)
    ap.add_argument("--c3-keys", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--memory", default=os.environ.get("LAB_DUCKDB_MEM", "2GB"))
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args(argv)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{a.memory}'; SET threads = {a.threads}")
    tmp = os.environ.get("LAB_SPILL_DIR")
    if tmp:
        con.execute(f"SET temp_directory = '{tmp}'")
    info = load(con, a.parts, a.c3_keys)
    days, leads, t1 = compute(con)
    with open(a.e63_json) as f:
        e63 = json.load(f)
    gates = {"T2": {"status": info["status"], "runs": info["runs"]}, "T3": {"status": "GREEN"}, "T1": rc.gate_g4(e63["cells"], t1)}
    if e63["days"] != days:
        gates["T1"] = {"status": "RED", "why": f"days differ from E63's ({len(e63['days'])} vs {len(days)})"}
    out = {"plan": PLAN, "plan_sha256": PLAN_SHA, "days": days, "tols": TOLS, "sizes": SIZES, "d": D, "c3_keys": info["c3_keys"],
           "gates": gates, "leads": leads, "dry_run": any(r["slot_range"] for r in info["runs"])}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "tol.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    with open(os.path.join(a.out, "tol.md"), "w") as f:
        f.write(report_md(out))
    print(f"{len(leads)} leads x {len(SIZES)} sizes x {len(TOLS)} tols, {len(days)} days; gates "
          + ", ".join(f"{k} {v['status']}" for k, v in sorted(gates.items())) + " (tol.json, tol.md)")
    return 1 if any(v["status"] == "RED" for v in gates.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
