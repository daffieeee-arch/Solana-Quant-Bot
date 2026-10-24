"""Entry tolerance sweep on dev (research/tol-sweep-plan-2026-10-24.md, sha256 35ca706b; E70; exploratory and in-sample,
no new rule): H5 and C3/hold300 at d 3, 0.5 and 1.25 SOL under E63's capital rules, at the engine's tolerance 0.02 /
0.05 / 0.10 / 0.15. The engine has one tolerance for both legs (an entry fails below the expected tokens x (1 - tol), an
exit whose sale would come below the mark x (1 - tol) is retried), as paper's Engine (tol=0.05); the sweep moves both.

  python -m backtest.tol_sweep --parts RUN_DIR [RUN_DIR ...] --e63-json JSON --c3-keys PARQUET --store dev2.duckdb --out DIR

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
import hashlib
import json
import os
import subprocess
import sys

import duckdb

from . import racing_cost as rc
from .capital_ceiling import (DEV, SCENARIOS, Refused, boot_days, cell_rows, check_run, ci, fmt, limited, require_exit_day, slot_days,
                              unlimited)
from .costs import Scenario, settle_sql
from .store import DEFAULTS, load_defaults

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
# T0 (LAB review of the plan, should-fix 1): the simulation code and the cost defaults are byte for byte those of E63's
# run (d15ac45), so its tol 0.05 parts are the same engine as the new runs, not only the same report code.
ENGINE_SHA256 = {
    "replay.py": "1d84263c08d2ca889827bc1b8010d69dca96cb9b26c36b1c275b97dfb6b55414",
    "tournament.py": "a57e93ee2db6319abed8395878f6e0afa4e58d4067c6f67fe71faa28296090ca",
    "streams.py": "201412f735647789a37a617adfe8e8b18a6ada088832c3bb371bfe0ba152467e",
    "venues.py": "8cec356ceb2e9ed416f3de3635728c1138e77c6bc5ddd507e92e304c2d8d5db3",
    "costs.py": "2fa4ce196051aaf5d1dcb4d3421e1e7dbf15c66828f7d419d43d28b9f4aa37d9",
    "data.py": "d54cf131a26233c5d02a23ed10af9137cf20fd0f11d0423f2fcde9d9e3ed08fd",
    "strategies.py": "c9744bda08ec678a90cf4751985543fe695f89d1b0fb511ed74d4ee88a7e3277",
    "store.py": "c06bfc1f994edaf3ba5dafd4925050a3d6589d62e4c0d153b93cbd24c2dbb04a",
}
DEFAULTS_SHA256 = "1e8aedddd2a179a6131e8487a986e82877d98a58bf00bab2e7ae3d4c58d89243"  # research/week2-defaults.yaml (9 Oct)
E70_COMMIT = "e3bef48"  # E70's own run (NAS 99zzzzz60): its engine files are the pins above
E70_TAG = "e70-2026-10-24"  # the tag on e3bef48 (PR #545's head; main has its squash 8584c5d), so every clone has it
# T0 on a later tree (Codex review #8, 2026-10-24): an engine file may differ from the pins only by a change listed here
# with its exact sha256 and the proof that the dev results are equal; anything else is RED. A new hash is no proof by itself.
ALLOWED_DRIFT = {
    "store.py": {"17bcaa21bde0d401ae55af27a68fb32700fb3ed3416b948fb08845fcc016f5d9": (
        "#555 (737a932): a pool with a u64 amount outside BIGINT leaves the store; with no such pool the build's SQL text is "
        "unchanged. Proof: store-u64-proof-be47fc4 (VPS) and NAS 98-clean-0-u64-proof-be47fc4: dev 1043 built by 46a0236 "
        "and by be47fc4, every table equal, 0 pools excluded.")},
}
LOOKS = len(TOLS) * len(LEADS) * len(SIZES) * len(SCENARIOS)  # 32 cells, 24 new (3 tols); 12 primary (base, with limits)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def engine_check(here=os.path.dirname(os.path.abspath(__file__)), defaults=DEFAULTS, commit=None):
    """T0: the engine files and the cost defaults against E63's (d15ac45; E70 ran on e3bef48 with the same files).

    commit: the files of that commit (git show; E70_COMMIT: the experiment's own identity), nothing allowed to differ.
    Without: this tree's files (here); a file that differs passes only with its exact sha256 in ALLOWED_DRIFT, reported
    under drift with its proof; any other difference is RED."""
    if commit:
        prefix = subprocess.run(["git", "-C", here, "rev-parse", "--show-prefix"], capture_output=True, text=True).stdout.strip()

        def read(n):
            p = subprocess.run(["git", "-C", here, "show", f"{commit}:{prefix}{n}"], capture_output=True)
            if p.returncode:
                raise Refused(f"T0: {commit}:{prefix}{n} is not in this checkout's history")
            return p.stdout
    else:
        def read(n):
            with open(os.path.join(here, n), "rb") as f:
                return f.read()
    bad, drift = {}, {}
    for n, h in ENGINE_SHA256.items():
        got = _sha(read(n))
        if got == h:
            continue
        why = None if commit else ALLOWED_DRIFT.get(n, {}).get(got)
        if why:
            drift[n] = {"sha256": got[:12], "why": why}
        else:
            bad[n] = got[:12]
    with open(defaults, "rb") as f:
        if _sha(f.read()) != DEFAULTS_SHA256:
            bad["week2-defaults.yaml"] = _sha(open(defaults, "rb").read())[:12]
    return {"files": sorted(ENGINE_SHA256) + ["week2-defaults.yaml"], "commit": commit, "differ": bad, "drift": drift,
            "status": "RED" if bad else "GREEN"}


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


def split(rows, ref, ndays):
    """Without limits, the difference against tol 0.05 by leg (LAB review of the plan, should-fix 2), on (pool, t):
    new = filled at this tol, not filled at 0.05 (refused there or not in its busy chain); lost = the reverse; common =
    filled at both (their entry fills agree when the state does: the difference is mostly the exit leg). SOL/day sums
    over the window's days; the rest of the difference is the failed entries' fees (refused rows)."""
    def filled(rs):
        return {(r[8], r[0]): r for r in rs if not r[6]}
    a, b = filled(rows), filled(ref)
    new = [a[k] for k in a.keys() - b.keys()]
    lost = [b[k] for k in b.keys() - a.keys()]
    common = sorted(a.keys() & b.keys())

    def part(rs):
        return {"n": len(rs), "sol_day": sum((r[5] or 0) for r in rs) / 1e9 / ndays,
                "drain_pct": round(100 * sum(1 for r in rs if (r[9] or 0) < 0.1 * r[4]) / len(rs), 2) if rs else None}

    d_pnl = sum((a[k][5] or 0) - (b[k][5] or 0) for k in common) / 1e9 / ndays
    d_cost = sum(1 for k in common if a[k][4] != b[k][4])
    d_proc = [((a[k][9] or 0) - (b[k][9] or 0)) / b[k][9] for k in common if b[k][9]]
    total = (sum((r[5] or 0) for r in rows) - sum((r[5] or 0) for r in ref)) / 1e9 / ndays
    out = {"new": part(new), "lost": part(lost),
           "common": {"n": len(common), "d_sol_day": d_pnl, "entry_cost_differs": d_cost,
                      "d_proceeds_mean_pct": round(100 * sum(d_proc) / len(d_proc), 3) if d_proc else None},
           "total_d_sol_day": total}
    out["failed_fees_d_sol_day"] = total - out["new"]["sol_day"] + out["lost"]["sol_day"] - d_pnl
    return out


def compute(con):
    day_of = slot_days(con)  # the daily loss limit's day: each exit slot's UTC day (the attached store's blocks)
    days = [r[0] for r in con.execute("SELECT DISTINCT day FROM rall ORDER BY 1").fetchall()]
    leads, t1 = {}, {}
    for name, variant, farm, arm in LEADS:
        L = {"variant": variant, "farm": farm or "all", "c3_arm": arm, "cells": []}
        for size in SIZES:
            capital = round(size / SHARE, 6)
            for sc in SCENARIOS:
                series, cells_rows = {}, {}
                for tol in TOLS:
                    _view(con, tol)
                    rows = cell_rows(con, variant, farm, arm, size, D, sc)
                    u, x = unlimited(rows, days), limited(rows, days, capital, size, "deadline", day_of)
                    if tol == REF_TOL:
                        t1[(name, size, D, sc)] = (u, x)
                    cells_rows[tol] = rows
                    filled = [r for r in rows if not r[6]]
                    drain = sum(1 for r in filled if (r[9] or 0) < 0.1 * r[4])
                    join = "SEMI JOIN c3 USING (pool, t)" if arm else ""
                    where = f"r.variant = '{variant}' AND r.size_sol = {size} AND r.d = {D} AND NOT r.entry_failed" + (f" AND r.farm = '{farm}'" if farm else "")
                    n_f, retried = con.execute(f"SELECT count(*), count(*) FILTER (WHERE exit_attempts > 1) FROM (SELECT * FROM r {join} WHERE {where})").fetchone()
                    series[tol] = rc.run_days(rc.trades_of(rows), days, capital, size, day_of)[0]
                    L["cells"].append({"tol": tol, "size": size, "capital": capital, "scenario": sc, "unlimited": u, "limited": x,
                                       "drain_pct": round(100 * drain / len(filled), 2) if filled else None,
                                       "exit_retry_pct": round(100 * retried / n_f, 2) if n_f else None})
                for c in L["cells"]:
                    if c["size"] == size and c["scenario"] == sc and c["tol"] != REF_TOL:
                        m, lo, hi = boot_days([a - b for a, b in zip(series[c["tol"]], series[REF_TOL])])
                        c["vs_005"] = {"sol_day": m, "lo": lo, "hi": hi}
                        c["split"] = split(cells_rows[c["tol"]], cells_rows[REF_TOL], len(days))
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
            L += ["", "Without limits, the difference against tol 0.05 by leg (pool, t): new = filled only at this tol, lost = filled only "
                  "at 0.05, common = filled at both (mostly the exit leg; entry cost differs on 'cost≠'):", "",
                  "| size | tol | total Δ SOL/day | new: n, SOL/day, drain % | lost: n, SOL/day, drain % | common: n, Δ SOL/day, Δ proceeds % (cost≠) | failed fees Δ |",
                  "|---|---|---|---|---|---|---|"]
            for c in sorted((c for c in Ld["cells"] if c["scenario"] == sc and c.get("split")), key=lambda c: (c["size"], c["tol"])):
                x = c["split"]
                L.append(f"| {c['size']:g} | {c['tol']:g} | {fmt(x['total_d_sol_day'])} | {x['new']['n']}, {fmt(x['new']['sol_day'])}, "
                         f"{fmt(x['new']['drain_pct'], 1, False)} | {x['lost']['n']}, {fmt(x['lost']['sol_day'])}, {fmt(x['lost']['drain_pct'], 1, False)} | "
                         f"{x['common']['n']}, {fmt(x['common']['d_sol_day'])}, {fmt(x['common']['d_proceeds_mean_pct'], 3)} ({x['common']['entry_cost_differs']}) | "
                         f"{fmt(x['failed_fees_d_sol_day'])} |")
            L.append("")
    L += [f"Looks: {LOOKS} cells ({len(TOLS)} tols x {len(LEADS)} leads x {len(SIZES)} sizes x {len(SCENARIOS)} cost scenarios), of which "
          f"{(len(TOLS) - 1) * len(LEADS) * len(SIZES) * len(SCENARIOS)} new (tol 0.05 is E63's) and "
          f"{(len(TOLS) - 1) * len(LEADS) * len(SIZES)} primary (base, with limits).", "",
          "## Caveats", "", "In-sample (dev looked at > 1,300 times; H5 and the arm were chosen on it). A looser tolerance accepts worse fills: "
          "more sandwich room (not modelled). The tolerance changes which positions exist, so the busy chains differ per tol. "
          "One tolerance for both legs, as paper: an entry-only change is not computed; a looser tol also lets a take-profit sale fill "
          "further below its mark (H5's tp exits can change their outcome). A gain at tol 0.15 is an upper bound (E63's sandwich room). "
          "K3 is not part of this task. Live trigger "
          "supply is ~20-29 % of dev's. Pre-Alpenglow slots.", "", "## Provenance", ""]
    L += [f"- {r['run']}: slippage_tol {r['slippage_tol']}, engine {r['engine_commit']}" + (f", DRY RUN range {r['slot_range']}" if r["slot_range"] else "")
          for r in out["gates"]["T2"]["runs"]]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check-engine", action="store_true", help="T0 only: the engine files and cost defaults against E63's")
    ap.add_argument("--engine-commit", help=f"T0 at a commit's own files (git show), e.g. E70's {E70_COMMIT}; default: this tree")
    ap.add_argument("--parts", nargs="+")
    ap.add_argument("--e63-json")
    ap.add_argument("--c3-keys")
    ap.add_argument("--out")
    ap.add_argument("--store", help="dev2.duckdb: the UTC day of each exit slot (the daily loss limit's day)")
    ap.add_argument("--memory", default=os.environ.get("LAB_DUCKDB_MEM", "2GB"))
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args(argv)
    t0 = engine_check(commit=a.engine_commit)
    if a.check_engine:
        where = a.engine_commit or "this tree"
        print(f"T0 {t0['status']} ({where}): " + (str(t0["differ"]) if t0["differ"] else "the engine files and week2-defaults.yaml are "
              "those of d15ac45") + (f"; allowed drift: {t0['drift']}" if t0["drift"] else ""))
        return 0 if t0["status"] == "GREEN" else 1
    if not (a.parts and a.e63_json and a.c3_keys and a.out and a.store):
        ap.error("--parts, --e63-json, --c3-keys, --out and --store are required")
    require_exit_day(a.e63_json)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{a.memory}'; SET threads = {a.threads}")
    tmp = os.environ.get("LAB_SPILL_DIR")
    if tmp:
        con.execute(f"SET temp_directory = '{tmp}'")
    info = load(con, a.parts, a.c3_keys)
    con.execute(f"ATTACH '{a.store}' AS st (READ_ONLY)")
    m = json.loads(con.execute("SELECT json FROM st.meta").fetchone()[0])
    if (m.get("slot_start"), m.get("slot_end_exclusive")) != DEV:
        raise Refused(f"{a.store}: window [{m.get('slot_start')}, {m.get('slot_end_exclusive')}) is not dev2's")
    days, leads, t1 = compute(con)
    with open(a.e63_json) as f:
        e63 = json.load(f)
    gates = {"T0": t0, "T2": {"status": info["status"], "runs": info["runs"]}, "T3": {"status": "GREEN"}, "T1": rc.gate_g4(e63["cells"], t1)}
    if e63["days"] != days:
        gates["T1"] = {"status": "RED", "why": f"days differ from E63's ({len(e63['days'])} vs {len(days)})"}
    out = {"plan": PLAN, "plan_sha256": PLAN_SHA, "looks": LOOKS, "days": days, "tols": TOLS, "sizes": SIZES, "d": D, "c3_keys": info["c3_keys"],
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
