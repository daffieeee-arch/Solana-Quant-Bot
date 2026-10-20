"""A UI job's result (spec v0.3 §5): the chosen F7 variant and K3 (tp4_T600) on the same run, with exactly the
canonical tournament report's numbers. The rows come from backtest.report.summarize itself (base cost scenario,
the report's defaults, its day-clustered bootstrap), over the run's parts filtered to these two variants, so a
UI job and a tournament report can never disagree: one row per (variant, d, regime) as in summary.parquet, for
the job's segment, farm and size. Run by the template on the NAS (lab venv); prints JSON.

Before 2026-10-20 this file pooled every d and regime of a run (LAB review found K3 +0.0058 SOL/trade on
long-dev-1033-1046, a number in no report: d 1 270 ms is +0.0071, d 2 +0.0029); test_uijobs reproduces a report
row exactly.

  python result.py --run <run dir> --variant V --k3 tp4_T600 --segment S --farm all|organic --size 0.5 --id <ULID>
"""

import argparse
import glob
import json
import os
import tempfile

KEEP = ("variant", "d", "regime", "n", "filled", "days", "mean_pnl_sol", "total_pnl_sol", "pnl_ci_lo", "pnl_ci_hi",
        "mean_ret", "win_rate", "verdict")


def rows(run, variants, segment, farm, size, con=None):
    """The report's base-scenario rows of `variants` for this segment, farm and size: [{KEEP...}], by variant, d,
    regime. The parts are first narrowed to F7 and these variants (summarize's statistics are per row group, so
    the other variants do not change them)."""
    from backtest.report import _connect, summarize
    from backtest.store import load_defaults

    parts = sorted(glob.glob(os.path.join(run, "parts", "*.parquet")))
    if not parts:
        raise SystemExit(f"no parts in {run}")
    con = con or _connect()
    with tempfile.TemporaryDirectory() as tmp:
        narrowed = os.path.join(tmp, "parts.parquet")
        con.execute(f"COPY (SELECT * FROM read_parquet(?, union_by_name = true) WHERE family = 'F7' AND list_contains(?, variant)) "
                    f"TO '{narrowed}' (FORMAT parquet)", [parts, list(variants)])
        out = summarize(narrowed, load_defaults(), con)
    keep = [o for o in out if o["scenario"] == "base" and o["family"] == "F7" and o["variant"] in variants
            and o["segment"] == segment and o["farm"] == farm and float(o["size_sol"]) == float(size)]
    res = []
    for o in sorted(keep, key=lambda o: (list(variants).index(o["variant"]), o["d"], str(o["regime"]))):
        r = {k: o.get(k) for k in KEEP}
        r["failed_entries"] = (o["n"] or 0) - (o["filled"] or 0)
        r["sum_sol_per_day"] = o["total_pnl_sol"] / o["days"] if o.get("days") else None
        res.append(r)
    return res


def main():
    ap = argparse.ArgumentParser()
    for a in ("--run", "--variant", "--k3", "--segment", "--farm", "--size", "--id"):
        ap.add_argument(a, required=True)
    a = ap.parse_args()
    variants = [a.variant] if a.variant == a.k3 else [a.variant, a.k3]
    res = rows(a.run, variants, a.segment, a.farm, float(a.size))
    print(json.dumps({"id": a.id, "scenario": "base", "source": "backtest.report.summarize", "bootstrap": "report (day-clustered, 1,000 draws)",
                      "variant": a.variant, "k3": a.k3, "segment": a.segment, "farm": a.farm, "size_sol": float(a.size),
                      "rows": res, "note": "verkennend: een van vele pogingen, geen bewijs"}, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
