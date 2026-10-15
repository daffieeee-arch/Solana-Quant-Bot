"""Spec E §2 (week3-specs-exits-2026-10-15.md, sha256 7372ba01...): the exit grid of F7 pool_reversed (trail / hold /
flow, each with and without the -8 % stop) against tp4_T600, from the replay's positions (parts). Exploratory on dev.

Per variant x d x farm, days 10-18 binding (days 1-9 and all days beside, regimes as rows of their own):
  net per filled trade (0.5 SOL, q 0.5, scenario base: costs.settle_sql, as the tournaments), 1 %-trimmed mean and
  plain mean, 95 % CI of the trimmed mean over a day bootstrap; the paired Δ against the baseline per trigger on
  the COMMON triggers only (busy_until skips later triggers of a pool while a position is open, so a longer hold has
  fewer positions: the dropped triggers are counted per variant, and the baseline's own net on the common set is
  shown next to the variant's); the total net P&L in SOL per day of the block (days without a trade count 0: a longer
  hold fills fewer triggers per pool, so a higher net per trade can still sum lower per day); the empty share
  (net <= -99 %: sold into a drained pool); the tail (p5, p95, worst day, share > 0, payoff); the exit reasons; the
  median hold in seconds. The parts carry farm = 'flagged' and 'organic' (K3's filter); farm 'all' is their union
  (K3's farm 'alle'), added by load() when the parts do not carry it, and paired as a group of its own.
Bar (spec §2, fixed): Δ > 0 with CI > 0 at d = 1 AND d = 2 on days 10-18, farm all, and the empty share at most the
baseline's + 2 points. DSR / PBO over all trials: wallets.overfit on the same parts (nas_exits.sh runs it).

Spec F §3 (week3-specs-runners-2026-10-15.md, sha256 2a8d96fa...): the same report over the long-exit grid (--families
F7,M1,M1E,M1P): the reference per family is tp4_T600 for F7 and <prefix>_trail20 for the M1 families (k2_trail30 ->
k2_trail20, m1_flow300 -> m1_trail20, k15_flow300 -> k15_trail20); when the parts carry peak_mark (engine fc4173c+) the
rows add the runners: a position whose net peak (settle_sql with peak_mark in place of proceeds) is >= x5 / x10 of the
entry total, the share of them captured (net at exit >= 50 % of the net peak), and the net SOL per day from runners,
drains (net <= -99 %), collapses (net <= -50 %, not a drain) and the rest. Parts without peak_mark render exactly as before.

  python -m wallets.exits_report --parts DIR [--families F7] [--baseline tp4_T600] [--cfg week2-defaults.yaml] [--boot 10000] --out FILE
"""

import argparse
import glob
import json
import os
import sys
import time

import numpy as np

from .markout_report import BOOT, TRIM, day_ci, fmt, trimmed_mean

FAMILY, SEGMENT = "F7", "pool_reversed"
FAMILY_SEGMENT = {"F7": "pool_reversed", "M1": "pool_normal", "M1P": "pool_normal", "M1E": "pool_normal"}
BASELINE = "tp4_T600"
RUNNER5, RUNNER10, CAPTURED = 4.0, 9.0, 0.5   # net peak / entry total - 1 >= 4 (x5) / 9 (x10); captured: net >= 50 % of the net peak
COLLAPSE = -0.5
STATE = {"has_peak": False}   # set by load(): whether the parts carry peak_mark


def reference(family, variant, baseline=BASELINE):
    """The reference variant of a family: tp4_T600 for F7, <prefix>_trail20 for the M1 families (the existing M1Exit)."""
    return baseline if family == "F7" else variant.split("_", 1)[0] + "_trail20"
BLOCKS = (("days 10-18", "day >= '2026-09-20' AND day <= '2026-09-30'"), ("days 1-9", "day < '2026-09-20'"), ("all days", "TRUE"))
EMPTY = -0.99
EMPTY_TOL = 0.02       # the variant's empty share may exceed the baseline's by at most 2 points
DEFAULT_CFG = os.environ.get("LAB_DEFAULTS", "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml")


def _costs():
    """costs.settle_sql: the frozen engine's under LAB_ENGINE when set (the tournaments' own), else the repo's copy."""
    if os.environ.get("LAB_ENGINE"):
        from .markout import engine
        return engine()[1]
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest", "costs.py")
    spec = importlib.util.spec_from_file_location("lab_repo_costs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load(con, parts_dir, cfg_path, baseline=BASELINE, families=(FAMILY,)):
    import yaml
    costs = _costs()
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    pnl, ret = costs.settle_sql(costs.Scenario.from_cfg("base", cfg))
    files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
    if not files:
        raise SystemExit(f"no parquet parts in {parts_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    cols = [c[0] for c in con.execute(f"SELECT * FROM read_parquet({lit}, union_by_name = true) LIMIT 0").description]
    overlay = "AND overlay = 'none'" if "overlay" in cols else ""
    farm_col = "COALESCE(farm, 'organic')" if "farm" in cols else "'organic'"
    has_peak = "peak_mark" in cols
    ret_peak = ret.replace("proceeds", "peak_mark") if has_peak else "NULL::DOUBLE"
    fam = ", ".join(f"('{f}', '{FAMILY_SEGMENT[f]}')" for f in families)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE pos AS
      SELECT family, variant, d, {farm_col} AS farm, day, regime, mint, t, pool, exit_reason, {ret} AS ret, {pnl} AS pnl, {ret_peak} AS ret_peak,
             (exit_slot - entry_slot) AS hold_slots, CASE WHEN d > 0 THEN d_ms / d END AS ms_per_slot
      FROM read_parquet({lit}, union_by_name = true)
      WHERE (family, segment) IN ({fam}) AND NOT skipped AND NOT entry_failed AND size_sol = 0.5 AND q = 0.5 {overlay}""")
    n = con.execute("SELECT count(*), count(DISTINCT variant) FROM pos").fetchone()   # the parts' positions, before the union
    # farm 'all' = the union of the farm groups (a trigger belongs to one pool, so to one group): K3's farm 'alle'
    if not con.execute("SELECT count(*) FROM pos WHERE farm = 'all'").fetchone()[0]:
        con.execute("INSERT INTO pos SELECT family, variant, d, 'all', day, regime, mint, t, pool, exit_reason, ret, pnl, ret_peak, hold_slots, ms_per_slot FROM pos")
    # the reference per (family, variant); its positions joined per trigger (mint, t, d, farm): the common set and the dropped triggers
    con.execute("CREATE OR REPLACE TEMP TABLE ref (family VARCHAR, variant VARCHAR, ref VARCHAR)")
    for f, v in con.execute("SELECT DISTINCT family, variant FROM pos").fetchall():
        con.execute("INSERT INTO ref VALUES (?, ?, ?)", [f, v, reference(f, v, baseline)])
    con.execute("""
      CREATE OR REPLACE TEMP TABLE pair AS
      SELECT v.family, v.variant, v.d, v.farm, v.day, v.regime, v.mint, v.t, v.ret AS ret_v, b.ret AS ret_b, v.ret - b.ret AS delta
      FROM pos v JOIN ref r ON r.family = v.family AND r.variant = v.variant
      JOIN pos b ON b.family = v.family AND b.variant = r.ref AND b.mint = v.mint AND b.t = v.t AND b.d = v.d AND b.farm = v.farm
      WHERE v.variant <> r.ref""")
    STATE["has_peak"] = has_peak
    print(f"positions {n[0]:,}, variants {n[1]}, paired rows {con.execute('SELECT count(*) FROM pair').fetchone()[0]:,}", file=sys.stderr, flush=True)
    return n


def stats(con, variant, d, farm, where, boot, family=FAMILY):
    rows = con.execute(f"SELECT ret, day, hold_slots * ms_per_slot / 1000.0, pnl, ret_peak FROM pos WHERE family = ? AND variant = ? AND d = ? AND farm = ? AND {where}", [family, variant, d, farm]).fetchall()
    if not rows:
        return None
    block_days = con.execute(f"SELECT count(DISTINCT day) FROM pos WHERE {where}").fetchone()[0]
    r = np.array([x[0] for x in rows]); days = np.array([x[1] for x in rows]); hold = np.array([x[2] for x in rows], dtype=float)
    pnl = np.array([x[3] for x in rows], dtype=float) / 1e9
    sol_day = pnl.sum() / block_days
    runners = None
    if STATE["has_peak"]:
        pk = np.array([x[4] if x[4] is not None else np.nan for x in rows], dtype=float)
        is5, is10 = pk >= RUNNER5, pk >= RUNNER10
        drain, collapse = r <= EMPTY, (r <= COLLAPSE) & (r > EMPTY)
        cap = r >= CAPTURED * pk
        runners = {"r5": float(is5.mean()), "c5": float(cap[is5].mean()) if is5.any() else None, "r10": float(is10.mean()), "c10": float(cap[is10].mean()) if is10.any() else None,
                   "sol_runners": pnl[is5].sum() / block_days, "sol_drains": pnl[drain].sum() / block_days, "sol_collapses": pnl[collapse].sum() / block_days,
                   "sol_rest": pnl[~is5 & ~drain & ~collapse].sum() / block_days}
    lo, hi = day_ci(r, days, boot)
    per_day = {}
    for v, dd in zip(r, days):
        per_day[dd] = per_day.get(dd, 0.0) + v
    wins, losses = r[r > 0], r[r <= 0]
    return {"n": len(r), "trim": trimmed_mean(r), "mean": float(r.mean()), "lo": lo, "hi": hi, "sol_day": sol_day, "empty": float((r <= EMPTY).mean()),
            "p5": float(np.percentile(r, 5)), "p95": float(np.percentile(r, 95)), "worst_day": min(per_day.values()), "pos_share": float((r > 0).mean()),
            "payoff": (float(wins.mean() / -losses.mean()) if len(wins) and len(losses) and losses.mean() < 0 else None),
            "hold_s": float(np.median(hold[~np.isnan(hold)])) if np.isfinite(hold).any() else None, "days": len(per_day), "runners": runners}


def paired(con, variant, d, farm, where, boot, baseline=BASELINE, family=FAMILY):
    ref = reference(family, variant, baseline)
    rows = con.execute(f"SELECT delta, ret_v, ret_b, day FROM pair WHERE family = ? AND variant = ? AND d = ? AND farm = ? AND {where}", [family, variant, d, farm]).fetchall()
    nb = con.execute(f"SELECT count(*) FROM pos WHERE family = ? AND variant = ? AND d = ? AND farm = ? AND {where}", [family, ref, d, farm]).fetchone()[0]
    nv = con.execute(f"SELECT count(*) FROM pos WHERE family = ? AND variant = ? AND d = ? AND farm = ? AND {where}", [family, variant, d, farm]).fetchone()[0]
    if not rows:
        return {"n": 0, "dropped_b": nb, "dropped_v": nv}
    dl = np.array([x[0] for x in rows]); rv = np.array([x[1] for x in rows]); rb = np.array([x[2] for x in rows]); days = np.array([x[3] for x in rows])
    lo, hi = day_ci(dl, days, boot)
    return {"n": len(dl), "delta": trimmed_mean(dl), "lo": lo, "hi": hi, "v_common": trimmed_mean(rv), "b_common": trimmed_mean(rb),
            "empty_v": float((rv <= EMPTY).mean()), "empty_b": float((rb <= EMPTY).mean()), "dropped_b": nb - len(dl), "dropped_v": nv - len(dl)}


def ci(lo, hi):
    return "–" if lo is None else f"{fmt(lo, 1, True, True)} … {fmt(hi, 1, True, True)}"


def _runner_cells(s):
    k = s["runners"]
    cap = lambda x: fmt(x, 0, True) if x is not None else "–"
    return (f" {fmt(k['r5'], 1, True)} / {cap(k['c5'])} | {fmt(k['r10'], 1, True)} / {cap(k['c10'])} | "
            f"{fmt(k['sol_runners'], 2, False, True)} / {fmt(k['sol_drains'], 2, False, True)} / {fmt(k['sol_collapses'], 2, False, True)} / {fmt(k['sol_rest'], 2, False, True)} |")


def render(con, info, boot, baseline=BASELINE, families=(FAMILY,)):
    has_peak = STATE["has_peak"]
    ds = [r[0] for r in con.execute("SELECT DISTINCT d FROM pos ORDER BY 1").fetchall()]
    farms = [r[0] for r in con.execute("SELECT DISTINCT farm FROM pos ORDER BY (farm = 'organic'), 1").fetchall()]
    regimes = [r[0] for r in con.execute("SELECT DISTINCT regime FROM pos WHERE regime IS NOT NULL ORDER BY 1").fetchall()]
    multi = tuple(families) != (FAMILY,)
    L = [f"## Exit grid (spec E §2, exploratory): {FAMILY} {SEGMENT}, 0.5 SOL, q 0.5, base costs, against {baseline}\n" if not multi else
         f"## Exit grid (spec E §2 + spec F §3, exploratory): {', '.join(f'{f} {FAMILY_SEGMENT[f]}' for f in families)}, 0.5 SOL, q 0.5, base costs, against the reference of each family\n",
         f"Positions {info['positions']:,} over {info['variants']} variants (filled, overlay none). Net per trade = costs.settle_sql (the tournaments' return); "
         f"trimmed = {TRIM:.0%} at each end with a 95 % CI over a day bootstrap ({boot:,}×); the paired Δ against {baseline} per trigger on the "
         f"**common** triggers only (busy_until: a longer hold skips later triggers of a pool while a position is open; the dropped triggers of each "
         f"side are counted), with both sides' net on that common set; empty = net ≤ {EMPTY:.0%} (sold into a drained pool); Σ SOL / day = the total net P&L in SOL over the block divided by its days (days without a trade count 0). "
         f"Farm all = flagged ∪ organic (K3's farm 'alle'). Bar (spec §2): Δ > 0 with CI > 0 at d = 1 and d = 2 on days 10-18, farm all, and the empty share at most {baseline}'s + {EMPTY_TOL:.0%}. "
         "Days 10-18 are consumed dev data: nothing here is a candidate; DSR / PBO over every trial are in the overfit section.\n"]
    if multi:
        L.append(f"Reference per family: F7 → {baseline}; M1 / M1E / M1P → the variant's prefix + _trail20 (the existing M1Exit: trail 20 %, hard stop −25 %, 6 h, O7). "
                 "Spec F sets no bar for the long exits; the spec E bar applies to F7 only.\n")
    if has_peak:
        L.append(f"Runners (spec F §3): a position whose net peak (settle_sql with peak_mark in place of proceeds) is ≥ ×{RUNNER5 + 1:.0f} / ×{RUNNER10 + 1:.0f} of the entry total; "
                 f"captured = net at exit ≥ {CAPTURED:.0%} of the net peak; the net SOL / day split into runners (×5), drains (net ≤ {EMPTY:.0%}), collapses (net ≤ {COLLAPSE:.0%}, not a drain) and the rest.\n")
    verdict = {}
    for family in families:
        variants = [r[0] for r in con.execute("SELECT variant FROM pos WHERE family = ? GROUP BY 1 ORDER BY (variant = ?) DESC, 1", [family, reference(family, "x", baseline) if family == FAMILY else ""]).fetchall()]
        if not variants:
            continue
        fam_hdr = f" ({family} {FAMILY_SEGMENT[family]})" if multi else ""
        for blk, where in BLOCKS + tuple((f"regime {r}", f"regime = '{r}'") for r in regimes):
            L.append(f"### {blk}{fam_hdr}\n")
            L.append(f"| variant | d | farm | n | net trim | 95 % CI (days) | mean | Σ SOL / day | empty | > 0 | p5 / p95 | worst day | payoff | median hold | common n | Δ vs {baseline if not multi else 'ref'} (trim) | 95 % CI | variant / {baseline if not multi else 'ref'} on common | empty common v / b | dropped v / b |"
                     + (" runners ×5 / captured | ×10 / captured | Σ SOL / day runners / drains / collapses / rest |" if has_peak else ""))
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|" + ("---|---|---|" if has_peak else ""))
            for farm in farms:
                for d in ds:
                    for v in variants:
                        s = stats(con, v, d, farm, where, boot, family)
                        if s is None:
                            continue
                        row = (f"| {v} | {d} | {farm} | {s['n']:,} | **{fmt(s['trim'], 1, True, True)}** | {ci(s['lo'], s['hi'])} | {fmt(s['mean'], 1, True, True)} | "
                               f"{fmt(s['sol_day'], 3, False, True)} | {fmt(s['empty'], 1, True)} | {fmt(s['pos_share'], 0, True)} | {fmt(s['p5'], 1, True, True)} / {fmt(s['p95'], 1, True, True)} | "
                               f"{fmt(s['worst_day'], 2, True, True)} | {fmt(s['payoff'], 2) if s['payoff'] is not None else '–'} | {fmt(s['hold_s'], 1) if s['hold_s'] is not None else '–'} s |")
                        tail = _runner_cells(s) if has_peak else ""
                        if v == reference(family, v, baseline):
                            L.append(row + " – | – | – | – | – | – |" + tail)
                            continue
                        p = paired(con, v, d, farm, where, boot, baseline, family)
                        if p["n"] == 0:
                            L.append(row + f" 0 | – | – | – | – | {p['dropped_v']:,} / {p['dropped_b']:,} |" + tail)
                            continue
                        ok = p["lo"] is not None and p["lo"] > 0 and p["empty_v"] <= p["empty_b"] + EMPTY_TOL
                        if blk == "days 10-18" and farm == "all" and family == FAMILY:
                            verdict.setdefault(v, {})[d] = ok
                        L.append(row + f" {p['n']:,} | **{fmt(p['delta'], 1, True, True)}** | {ci(p['lo'], p['hi'])} | {fmt(p['v_common'], 1, True, True)} / {fmt(p['b_common'], 1, True, True)} | "
                                 f"{fmt(p['empty_v'], 1, True)} / {fmt(p['empty_b'], 1, True)} | {p['dropped_v']:,} / {p['dropped_b']:,} |" + tail)
            L.append("")
        L.append(f"### Exit reasons (days 10-18, farm all, d = 1){fam_hdr}\n")
        reasons = [r[0] for r in con.execute("SELECT DISTINCT exit_reason FROM pos WHERE family = ? ORDER BY 1", [family]).fetchall()]
        L.append("| variant | n | " + " | ".join(str(r) for r in reasons) + " |")
        L.append("|---|---|" + "---|" * len(reasons))
        for v in variants:
            r = con.execute(f"SELECT count(*), {', '.join(f'avg((exit_reason = ?)::INT)' for _ in reasons)} FROM pos WHERE variant = ? AND family = ? AND d = 1 AND farm = 'all' AND {BLOCKS[0][1]}",
                            [*reasons, v, family]).fetchone()
            if r[0]:
                L.append(f"| {v} | {r[0]:,} | " + " | ".join(fmt(x, 0, True) for x in r[1:]) + " |")
        L.append("")
    passed = []
    if FAMILY in families:
        variants = [r[0] for r in con.execute("SELECT variant FROM pos WHERE family = ? GROUP BY 1 ORDER BY (variant = ?) DESC, 1", [FAMILY, baseline]).fetchall()]
        L.append(f"### Verdict against the bar (days 10-18, farm all{', F7 only' if multi else ''})\n")
        L.append(f"| variant | d = 1 | d = 2 | bar |")
        L.append("|---|---|---|---|")
        for v in variants:
            if v == baseline:
                continue
            vd = verdict.get(v, {})
            both = all(vd.get(d, False) for d in (1, 2)) and {1, 2} <= set(vd)
            if both:
                passed.append(v)
            L.append(f"| {v} | {'yes' if vd.get(1) else 'no'} | {'yes' if vd.get(2) else 'no'} | {'**clearly better on dev**' if both else 'no'} |")
        L.append("\nA variant that passes the bar is 'better on dev' only; DSR / PBO over all trials (overfit section) and the hold-out diagnosis decide whether it becomes a second hypothesis before clean-v1 is read.\n")
    L.append(f"Run: commit {info['commit']}; parts {info['parts']}.")
    info["passed"] = passed
    return "\n".join(L)


def main(argv=None):
    from .common import git_commit
    import duckdb
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", required=True)
    ap.add_argument("--baseline", default=BASELINE)
    ap.add_argument("--families", default=FAMILY, help="comma-separated: F7 (spec E) or F7,M1,M1E,M1P (spec F long grid)")
    ap.add_argument("--cfg", default=DEFAULT_CFG)
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    t0 = time.time()
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1500MB')}'; SET threads TO {os.environ.get('LAB_WALLETS_THREADS', '2')}")
    families = tuple(x for x in a.families.split(",") if x)
    n = load(con, a.parts, a.cfg, a.baseline, families)
    info = {"parts": a.parts, "baseline": a.baseline, "families": list(families), "cfg": a.cfg, "boot": a.boot, "positions": n[0], "variants": n[1], "commit": git_commit(), "seconds": None}
    md = render(con, info, a.boot, a.baseline, families)
    info["seconds"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        f.write(md + "\n")
    with open(os.path.splitext(a.out)[0] + ".json", "w") as f:
        json.dump(info, f, indent=1, default=str)
    print(json.dumps(info, default=str), file=sys.stderr)


if __name__ == "__main__":
    main()
