"""Build the "Toernooi v1" page from a finished development tournament run.

    "$LAB_PY" lab/ui/pages/build_tournament.py "$LAB_DATA_ROOT/backtests/<run>"   # light, < 1 min

Settles every position per cost scenario exactly like lab/backtest/costs.py (lab/analytics/strategies.py,
checked against the run's summary.parquet) and writes $LAB_DATA_ROOT/ui-cache/pages/toernooi-v1.html.
"""
import datetime as dt
import json
import os
import sys

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "analytics"))
import strategies  # noqa: E402

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
POINTS = 160


def main(run_dir, out_name="toernooi-v1.html"):
    con = duckdb.connect(config={"memory_limit": "900MB", "threads": 2})
    conf = strategies.load(con, run_dir)
    summary = con.execute("""
        SELECT family, variant, size_sol, d, tau, scenario, n, filled, mean_ret, median_ret, win_rate, ci_lo, ci_hi,
               total_pnl_sol, mean_pnl_sol, n2_mean_ret, edge_vs_n2, hist_drift, fail_rate, stop_loss_share, days
        FROM strategy_summary ORDER BY family, variant, size_sol, d, tau, scenario""").fetchall()
    cols = ["family", "variant", "size_sol", "d", "tau", "scenario", "n", "filled", "mean_ret", "median_ret",
            "win_rate", "ci_lo", "ci_hi", "total_pnl_sol", "mean_pnl_sol", "n2_mean_ret", "edge_vs_n2", "hist_drift",
            "fail_rate", "stop_loss_share", "days"]
    curves = {}
    for fam, var, size, d, tau, scn, step, pnl in con.execute(f"""
        WITH c AS (SELECT *, count(*) OVER (PARTITION BY family, variant, size_sol, d, tau, scenario) AS n
                   FROM strategy_curves)
        SELECT family, variant, size_sol, d, tau, scenario, step, round(cum_pnl_sol, 4)
        FROM c WHERE step = 1 OR step = n OR step % greatest(1, n // {POINTS}) = 0
        ORDER BY family, variant, size_sol, d, tau, scenario, step""").fetchall():
        curves.setdefault(f"{fam}|{var}|{size}|{d}|{tau}|{scn}", []).append([step, pnl])
    data = {
        "run": {k: conf.get(k) for k in ("run_id", "families", "sizes_sol", "delays", "tau", "q", "slippage_tol",
                                         "signals", "positions", "seconds")},
        "store": conf.get("store", {}),
        "columns": cols,
        "summary": [list(r) for r in summary],
        "curves": curves,
        "built_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    blob = json.dumps(data, ensure_ascii=False, default=str)
    blob = blob.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    tpl = open(os.path.join(HERE, "tournament.html")).read()
    out = os.path.join(DATA, "ui-cache", "pages", out_name)
    with open(out + ".tmp", "w") as f:
        f.write(tpl.replace("/*__DATA__*/null", blob, 1))
    os.replace(out + ".tmp", out)
    print(out, len(blob) // 1024, "KiB data")


if __name__ == "__main__":
    main(sys.argv[1], *(sys.argv[2:3]))
