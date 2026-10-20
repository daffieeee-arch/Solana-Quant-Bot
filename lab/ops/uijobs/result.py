"""A UI job's result (spec v0.3 §5): the chosen F7 variant and K3 (tp4_T600) on the same run, settled under the
base cost scenario (backtest.costs.settle_sql, as backtest.report), with the stop rule's measure: net P&L per trade
after all costs (failed entries' costs included) and its 95 % interval from the 10,000-draw day bootstrap of
paper/stoprule.py, plus Σ per day with its interval. Run by the template on the NAS (lab venv); prints JSON.

  python result.py --run <run dir> --variant V --k3 tp4_T600 --segment S --farm all|organic --size 0.5 --id <ULID>
"""

import argparse
import glob
import importlib.util
import json
import os

import duckdb


def _stoprule(lab):
    spec = importlib.util.spec_from_file_location("lab_paper_stoprule", os.path.join(lab, "paper", "stoprule.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def summarize(con, run, variants, segment, farm, size, stoprule):
    from backtest.costs import Scenario, settle_sql
    from backtest.store import load_defaults

    pnl, ret = settle_sql(Scenario.from_cfg("base", load_defaults()))  # the cost scenarios the tournament's report uses
    parts = sorted(glob.glob(os.path.join(run, "parts", "*.parquet")))
    if not parts:
        raise SystemExit(f"no parts in {run}")
    rows = con.execute(f"""
        WITH v AS (SELECT *, {pnl} AS pnl, {ret} AS ret FROM read_parquet(?, union_by_name = true)
                   WHERE family = 'F7' AND list_contains(?, variant) AND segment = ? AND size_sol = ?
                         AND (? = 'all' OR farm = ?) AND NOT skipped)
        SELECT variant, day, COALESCE(sum(pnl), 0), count(pnl), count(ret), max(ret), min(ret) FROM v GROUP BY ALL ORDER BY ALL""",
                       [parts, list(variants), segment, size, farm, farm]).fetchall()
    out = {}
    for v in variants:
        days = [r for r in rows if r[0] == v]
        sums, cnts = [r[2] for r in days], [r[3] for r in days]
        n, filled = sum(cnts), sum(r[4] for r in days)
        lo, hi = stoprule.boot_ci(sums, cnts) if len(days) >= 2 else (None, None)
        dlo, dhi = stoprule.boot_ci(sums, [1] * len(sums)) if len(days) >= 2 else (None, None)
        sol = lambda x: None if x is None else x / 1e9  # noqa: E731
        out[v] = {"trades": n, "filled": filled, "failed_entries": n - filled, "days": len(days),
                  "total_sol": sol(sum(sums)), "mean_sol_per_trade": sol(sum(sums) / n) if n else None,
                  "ci95_sol_per_trade": [sol(lo), sol(hi)] if lo is not None else None,
                  "sum_sol_per_day": sol(sum(sums) / len(days)) if days else None,
                  "ci95_sol_per_day": [sol(dlo), sol(dhi)] if dlo is not None else None,
                  "best_ret": max((r[5] for r in days if r[5] is not None), default=None),
                  "worst_ret": min((r[6] for r in days if r[6] is not None), default=None)}
    return out


def main():
    ap = argparse.ArgumentParser()
    for a in ("--run", "--variant", "--k3", "--segment", "--farm", "--size", "--id"):
        ap.add_argument(a, required=True)
    a = ap.parse_args()
    lab = os.environ["LAB_CODE"] + "/lab"
    con = duckdb.connect(":memory:", config={"memory_limit": "2GB", "threads": 2})
    variants = [a.variant] if a.variant == a.k3 else [a.variant, a.k3]
    res = summarize(con, a.run, variants, a.segment, a.farm, float(a.size), _stoprule(lab))
    print(json.dumps({"id": a.id, "scenario": "base", "draws": 10_000, "variant": a.variant, "k3": a.k3,
                      "segment": a.segment, "farm": a.farm, "size_sol": float(a.size), "results": res,
                      "note": "verkennend: een van vele pogingen, geen bewijs"}, sort_keys=True))


if __name__ == "__main__":
    main()
