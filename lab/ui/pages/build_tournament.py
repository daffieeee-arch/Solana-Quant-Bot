"""Build the "Toernooi v1" page from the strategy_* tables in analytics.duckdb.

    "$LAB_PY" lab/ui/pages/build_tournament.py [analytics.duckdb] [out.html]   # light, seconds

First load the finished development run with `lab/analytics/build.py --tournament <run>` (heavy job): that
re-settles every position like lab/backtest/costs.py and checks it against the run's own numbers. This
script only reads the result and writes $LAB_DATA_ROOT/ui-cache/pages/toernooi-v1.html.
"""
import datetime as dt
import json
import os
import sys

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
SUMMARY_COLS = ["family", "variant", "size_sol", "d", "tau", "scenario", "n", "filled", "mean_ret", "median_ret",
                "win_rate", "ci_lo", "ci_hi", "total_pnl_sol", "mean_pnl_sol", "n2_mean_ret", "edge_vs_n2",
                "hist_drift", "fail_rate", "stop_loss_share", "days"]
POINTS = 160


def main(db=os.path.join(DATA, "analytics", "analytics.duckdb"), out_name="toernooi-v1.html"):
    con = duckdb.connect(db, read_only=True, config={"memory_limit": "900MB", "threads": 2})
    run = con.execute("SELECT run_id, config_json FROM strategy_runs").fetchone()
    conf = json.loads(run[1])
    have = {r[0] for r in con.execute("DESCRIBE strategy_summary").fetchall()}
    cols = [c for c in SUMMARY_COLS if c in have]
    sel = ", ".join(c if c in have else f"NULL AS {c}" for c in SUMMARY_COLS)
    summary = con.execute(f"SELECT {sel} FROM strategy_summary ORDER BY ALL").fetchall()
    tau = "tau" if "tau" in {r[0] for r in con.execute("DESCRIBE strategy_curves").fetchall()} else "NULL"
    curves = {}
    for fam, var, size, d, t, scn, step, pnl in con.execute(f"""
        SELECT family, variant, size_sol, d, {tau}, scenario, step, round(cum_pnl_sol, 4)
        FROM strategy_curves WHERE step = 1 OR step = n OR step % greatest(1, n // {POINTS}) = 0
        ORDER BY family, variant, size_sol, d, scenario, step""").fetchall():
        # Key must match the page's JS: String(10) == "10", String(null) == "null".
        key = "|".join([fam, var, "%g" % size, str(d), "null" if t is None else str(t), scn])
        curves.setdefault(key, []).append([step, pnl])
    data = {
        "run": {k: conf.get(k) for k in ("run_id", "families", "sizes_sol", "delays", "tau", "q", "slippage_tol",
                                         "signals", "positions", "seconds")},
        "store": conf.get("store", {}),
        "columns": SUMMARY_COLS,
        "summary": [list(r) for r in summary],
        "curves": curves,
        "built_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    data["run"]["run_id"] = data["run"]["run_id"] or run[0]
    blob = json.dumps(data, ensure_ascii=False, default=str)
    blob = blob.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    tpl = open(os.path.join(HERE, "tournament.html")).read()
    out = os.path.join(DATA, "ui-cache", "pages", out_name)
    with open(out + ".tmp", "w") as f:
        f.write(tpl.replace("/*__DATA__*/null", blob, 1))
    os.replace(out + ".tmp", out)
    print(out, len(blob) // 1024, "KiB data;", len(cols), "summary columns present")


if __name__ == "__main__":
    main(*sys.argv[1:3])
