"""Build the tournament page ("Toernooi v1", "Toernooi v1.1", ...) from the strategy_* tables in analytics.duckdb.

    "$LAB_PY" lab/ui/pages/build_tournament.py [analytics.duckdb] [out.html]   # light, seconds

First load the finished development run with `lab/analytics/build.py --tournament <run>` (heavy job): that
re-settles every position like lab/backtest/costs.py and checks it against the run's own numbers. This
script only reads the result and writes $LAB_DATA_ROOT/ui-cache/pages/toernooi-<version>.html, where the
version is the run id up to "-dev" (v1-dev-1042-1046 -> toernooi-v1.html, page title "Toernooi v1").
"""
import datetime as dt
import html
import json
import os
import sys

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
SUMMARY_COLS = ["family", "variant", "size_sol", "d", "tau", "scenario", "n", "filled", "mean_ret", "median_ret",
                "win_rate", "ci_lo", "ci_hi", "total_pnl_sol", "mean_pnl_sol", "n2_mean_ret", "edge_vs_n2",
                "hist_drift", "fail_rate", "stop_loss_share", "days", "pnl_ci_lo", "pnl_ci_hi", "verdict",
                "edge_ci_lo", "edge_ci_hi"]
# Run keys that split a family x variant into separate groups (lab/analytics/strategies.py GROUP); the page shows
# one value per key at a time.
RUN_KEYS = ["q", "segment", "regime", "farm", "overlay"]
# Points per curve: at most 160, fewer when a run has many groups, so the page stays a few MB (artifact limit 16 MB).
POINTS, MIN_POINTS, POINT_BUDGET = 160, 24, 250_000


def js(v):
    """A key part exactly as the page's JS String() writes it: 10 -> "10", 0.5 -> "0.5", None -> "null"."""
    if v is None:
        return "null"
    return "%g" % v if isinstance(v, float) else str(v)


def main(db=os.path.join(DATA, "analytics", "analytics.duckdb"), out_name=None):
    con = duckdb.connect(db, read_only=True, config={"memory_limit": "900MB", "threads": 2})
    run = con.execute("SELECT run_id, config_json FROM strategy_runs").fetchone()
    conf = json.loads(run[1])
    have = {r[0] for r in con.execute("DESCRIBE strategy_summary").fetchall()}
    have_c = {r[0] for r in con.execute("DESCRIBE strategy_curves").fetchall()}
    keys = [k for k in RUN_KEYS if k in have and k in have_c]
    columns = SUMMARY_COLS + keys
    cols = [c for c in columns if c in have]
    sel = ", ".join(c if c in have else f"NULL AS {c}" for c in columns)
    summary = con.execute(f"SELECT {sel} FROM strategy_summary ORDER BY ALL").fetchall()
    tau = "tau" if "tau" in have_c else "NULL"
    k_sel = "".join(f", {k}" for k in keys)
    groups = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT family, variant, size_sol, d{k_sel}, scenario, {tau} "
                         "FROM strategy_curves)").fetchone()[0]
    points = max(MIN_POINTS, min(POINTS, POINT_BUDGET // max(1, groups)))
    curves = {}
    for fam, var, size, d, t, scn, step, pnl, *kv in con.execute(f"""
        SELECT family, variant, size_sol, d, {tau}, scenario, step, round(cum_pnl_sol, 4){k_sel}
        FROM strategy_curves WHERE step = 1 OR step = n OR step % greatest(1, n // {points}) = 0
        ORDER BY family, variant, size_sol, d{k_sel}, scenario, step""").fetchall():
        # Must match the page's curve key: family|variant|size|d|tau|<run keys>|scenario.
        key = "|".join([fam, var, js(float(size)), str(d), js(t), *map(js, kv), scn])
        curves.setdefault(key, []).append([step, pnl])
    data = {
        "run": {k: conf.get(k) for k in ("run_id", "families", "sizes_sol", "delays", "tau", "q", "slippage_tol",
                                         "signals", "positions", "seconds")},
        "store": conf.get("store", {}),
        "columns": columns,
        "keys": keys,
        "summary": [list(r) for r in summary],
        "curves": curves,
        "built_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    data["run"]["run_id"] = data["run"]["run_id"] or run[0]
    version = data["run"]["run_id"].split("-dev")[0]
    title = html.escape("Toernooi " + version)
    blob = json.dumps(data, ensure_ascii=False, default=str)
    blob = blob.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    tpl = open(os.path.join(HERE, "tournament.html")).read()
    tpl = tpl.replace("<title>Toernooi</title>", f"<title>{title}</title>", 1).replace("<h1>Toernooi</h1>", f"<h1>{title}</h1>", 1)
    out = os.path.join(DATA, "ui-cache", "pages", out_name or f"toernooi-{version}.html")
    with open(out + ".tmp", "w") as f:
        f.write(tpl.replace("/*__DATA__*/null", blob, 1))
    os.replace(out + ".tmp", out)
    print(out, len(blob) // 1024, "KiB data;", len(cols), "summary columns present; run keys", keys,
          f"; {groups} curves at <= {points} points")


if __name__ == "__main__":
    main(*sys.argv[1:3])
