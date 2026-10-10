"""Summarize a tournament run: settle each position per cost scenario and aggregate.

  python -m backtest.report <run_dir>      # (re)writes summary.parquet, report.md, trials.parquet

Runs as DuckDB SQL over results.parquet, so millions of positions fit in bounded memory. The
settlement is costs.settle_sql, the SQL twin of costs.settle (tests/test_settle_sql.py).
"""

import json
import os
import sys

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .costs import Scenario, settle_sql
from .streams import SPILL_DIR

TRIALS = os.environ.get("LAB_TRIALS", "/home/chupa/Solana-project/data-old-faithful-one/lab/trials")

KEYS = ["family", "variant", "size_sol", "d", "tau"]
K = ", ".join(KEYS)
SCENARIOS = ("optimistic", "base", "pessimistic")
FAMILIES = ("F7", "F1")  # families with an N2 control; N1 is a control itself


def _connect():
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '1GB'; SET threads TO 2; SET temp_directory = '{SPILL_DIR}'")
    return con


def _boot(sums, cnts, n=1000, seed=7):
    """Day-clustered bootstrap of a mean from per-day sums and counts: (lo, hi) 95%."""
    if cnts.sum() < 2 or len(cnts) < 2:
        return None, None
    pick = np.random.default_rng(seed).integers(0, len(cnts), size=(n, len(cnts)))
    with np.errstate(invalid="ignore", divide="ignore"):
        means = sums[pick].sum(1) / cnts[pick].sum(1)
    return float(np.nanpercentile(means, 2.5)), float(np.nanpercentile(means, 97.5))


def _boot_diff(a, b, n=1000, seed=7):
    """Bootstrap over days of mean(a) - mean(b); a, b: {day: (sum, count)}."""
    days = sorted(set(a) | set(b))
    if len(days) < 2:
        return None, None
    sa, ca, sb, cb = (np.array([x.get(d, (0.0, 0))[i] for d in days], dtype=float) for x, i in
                      ((a, 0), (a, 1), (b, 0), (b, 1)))
    pick = np.random.default_rng(seed).integers(0, len(days), size=(n, len(days)))
    with np.errstate(invalid="ignore", divide="ignore"):
        diff = sa[pick].sum(1) / ca[pick].sum(1) - sb[pick].sum(1) / cb[pick].sum(1)
    if np.isnan(diff).all():
        return None, None
    return float(np.nanpercentile(diff, 2.5)), float(np.nanpercentile(diff, 97.5))


MIN_TRADES = 200  # below this: report, no verdict (stop rule)
# Historical mid moves above this factor are degenerate pool states (near-empty reserves), not
# market moves: roughly the largest real move, curve start (vSOL 30) to pool open (vSOL 115).
MAX_JUMP = 16
LIVE_MIN_SOL = 5  # sizes below this are informative only (live size is > 10 SOL)


def verdict(o):
    """Stop-rule verdict on net P&L per trade: GO if the 95% CI is above 0, ADJUST if the mean is."""
    if o["mean_pnl_sol"] is None or (o["filled"] or 0) < MIN_TRADES:
        return "too few trades"
    if o["pnl_ci_lo"] is not None and o["pnl_ci_lo"] > 0:
        return "GO"
    return "ADJUST" if o["mean_pnl_sol"] > 0 else "STOP"


def summarize(results, cfg, con=None):
    con = con or _connect()
    src = f"read_parquet('{results}')"
    out, daily, daily_pnl = [], {}, {}
    for name in SCENARIOS:
        pnl, ret = settle_sql(Scenario.from_cfg(name, cfg))
        con.execute(f"CREATE OR REPLACE TEMP VIEW v AS SELECT *, {pnl} AS pnl, {ret} AS ret FROM {src}")
        cols = ["skipped", "n", "filled", "mean_ret", "median_ret", "win_rate", "mean_pnl_sol", "total_pnl_sol",
                "hist_drift", "drift_excluded", "median_cost", "stop_loss_share", "stop_loss_mean", "reverted_router", "reverted_direct",
                "sells_dropped", "seed_pool", "cf_graduation", "days"]
        rows = con.execute(f"""
            SELECT {K},
              count(*) FILTER (WHERE skipped), count(*) FILTER (WHERE NOT skipped), count(ret),
              avg(ret), median(ret), avg((ret > 0)::INT), avg(pnl) / 1e9, COALESCE(sum(pnl), 0) / 1e9,
              avg(hist_ret) FILTER (WHERE ret IS NOT NULL AND hist_ret <= {MAX_JUMP} - 1),
              (count(*) FILTER (WHERE ret IS NOT NULL AND hist_ret > {MAX_JUMP} - 1))::DOUBLE
                / NULLIF(count(hist_ret) FILTER (WHERE ret IS NOT NULL), 0),
              median(ret - hist_ret) FILTER (WHERE ret IS NOT NULL AND hist_ret <= {MAX_JUMP} - 1),
              (count(*) FILTER (WHERE ret IS NOT NULL AND exit_reason = 'stop_loss'))::DOUBLE / NULLIF(count(ret), 0),
              avg(ret) FILTER (WHERE exit_reason = 'stop_loss'),
              avg(reverted_router) FILTER (WHERE NOT skipped), avg(reverted_direct) FILTER (WHERE NOT skipped),
              avg(sells_dropped) FILTER (WHERE NOT skipped), avg(seed_pool::INT) FILTER (WHERE NOT skipped),
              avg(cf_graduation::INT) FILTER (WHERE NOT skipped), count(DISTINCT day) FILTER (WHERE NOT skipped)
            FROM v GROUP BY ALL""").fetchall()
        robust = {tuple(r[:5]): r[5:] for r in con.execute(f"""
            SELECT {K}, avg(ret) FILTER (WHERE rk > 3), avg(ret) FILTER (WHERE rk > ceil(0.01 * cnt))
            FROM (SELECT {K}, ret, row_number() OVER (PARTITION BY {K} ORDER BY ret DESC) AS rk,
                         count(*) OVER (PARTITION BY {K}) AS cnt FROM v WHERE ret IS NOT NULL)
            GROUP BY ALL""").fetchall()}
        for r in con.execute(f"SELECT {K}, day, sum(ret), count(ret), sum(pnl), count(pnl) FROM v "
                             "WHERE pnl IS NOT NULL GROUP BY ALL").fetchall():
            if r[7]:
                daily.setdefault((name, *r[:5]), {})[r[5]] = (r[6], r[7])
            daily_pnl.setdefault((name, *r[:5]), {})[r[5]] = (r[8] / 1e9, r[9])
        for r in rows:
            key = tuple(r[:5])
            o = dict(zip(KEYS, key), scenario=name, **dict(zip(cols, r[5:])))
            o["fail_rate"] = 1 - o["filled"] / o["n"] if o["n"] else None
            o["mean_ex_top3"], o["mean_ex_top1pct"] = robust.get(key, (None, None))
            d = daily.get((name, *key), {})
            days = sorted(d)
            o["ci_lo"], o["ci_hi"] = _boot(np.array([d[x][0] for x in days], dtype=float),
                                           np.array([d[x][1] for x in days], dtype=float))
            # Stop-rule measure: net P&L per trade in SOL after all costs (failed entries included).
            dp = daily_pnl.get((name, *key), {})
            o["pnl_ci_lo"], o["pnl_ci_hi"] = _boot(np.array([dp[x][0] for x in sorted(dp)], dtype=float),
                                                   np.array([dp[x][1] for x in sorted(dp)], dtype=float))
            o["verdict"] = verdict(o)
            out.append(o)
    # N2 comparison: same variant / size / d / tau / scenario, CI by resampling days jointly.
    idx = {(o["scenario"], o["family"], o["variant"], o["size_sol"], o["d"], o["tau"]): o for o in out}
    for o in out:
        k2 = (o["scenario"], "N2_" + o["family"], o["variant"], o["size_sol"], o["d"], o["tau"])
        n2 = idx.get(k2)
        o["n2_n"] = n2["filled"] if n2 else None
        o["n2_mean_ret"] = n2["mean_ret"] if n2 else None
        o["edge_vs_n2"] = (o["mean_ret"] - n2["mean_ret"]) if (n2 and o["mean_ret"] is not None and n2["mean_ret"] is not None) else None
        o["edge_ci_lo"], o["edge_ci_hi"] = (_boot_diff(daily.get((o["scenario"], *[o[k] for k in KEYS]), {}),
                                                       daily.get(k2, {})) if n2 else (None, None))
    return out


def trials(summary, config):
    """One row per tested family variant (plan: trials.parquet): spec, params, window, commit, outcome.

    Gate (batch 1, binding): base costs, edge vs N2 > 0 at a size >= 5 SOL for every delay d."""
    base = [s for s in summary if s["scenario"] == "base" and s["family"] in FAMILIES]
    rows = []
    for fam, var in sorted({(s["family"], s["variant"]) for s in base}):
        mine = [s for s in base if s["family"] == fam and s["variant"] == var]
        by_size = {}
        for s in mine:
            by_size.setdefault(s["size_sol"], []).append(s)
        gate = any(sz >= 5 and all(x["edge_vs_n2"] is not None and x["edge_vs_n2"] > 0 for x in xs)
                   for sz, xs in by_size.items())
        rows.append({
            "run_id": config["run_id"], "batch": "tournament_v1", "family": fam, "variant": var,
            "spec": "week2-strategy-specs.md", "engine_commit": config.get("engine_commit"),
            "slot_start": config["store"]["slot_start"], "slot_end_exclusive": config["store"]["slot_end_exclusive"],
            "sizes_sol": json.dumps(config["sizes_sol"]), "delays": json.dumps(config["delays"]),
            "tau": json.dumps(config["tau"]), "n2_k": config.get("n2_k"),
            "outcome_base": json.dumps([{k: s[k] for k in ("size_sol", "d", "tau", "filled", "mean_ret", "edge_vs_n2",
                                                            "edge_ci_lo", "edge_ci_hi")} for s in mine]),
            "gate_pass": gate,
        })
    return rows


def _pct(x):
    return "" if x is None else f"{x*100:+.2f}%"


def _num(x, fmt):
    return "" if x is None else format(x, fmt)


def _sol(x):
    return "" if x is None else f"{x:+.4f}"


DEVIATIONS = [
    "N2_F1: the F1 exit reference (real SOL at the decision) is the matched token's own depth, not the trigger's.",
    "N2: the random entry slot is one of the matched token's own events in that hour (a slot with activity), "
    "not a uniform slot of the hour.",
]


def _report_commit():
    import subprocess

    here = os.path.dirname(os.path.abspath(__file__))
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=here).stdout.strip()
    return sha or os.environ.get("LAB_ENGINE_COMMIT", "unknown")


def write_report(run_dir, cfg):
    results = os.path.join(run_dir, "results.parquet")
    summary = summarize(results, cfg)
    pq.write_table(pa.Table.from_pylist(summary), os.path.join(run_dir, "summary.parquet"))
    with open(os.path.join(run_dir, "config.json")) as f:
        config = json.load(f)
    tr = trials(summary, config)
    if tr:
        # The run's rows, and the same file in the shared trials/ dataset (one file per run).
        table = pa.Table.from_pylist(tr)
        pq.write_table(table, os.path.join(run_dir, "trials.parquet"))
        os.makedirs(TRIALS, exist_ok=True)
        pq.write_table(table, os.path.join(TRIALS, f"{config['run_id']}.parquet"))
    base = sorted((s for s in summary if s["scenario"] == "base"),
                  key=lambda s: (s["family"], s["variant"], s["size_sol"], s["d"], s["tau"]))
    lines = [f"# Backtest {config['run_id']}", "",
             f"Store: slots {config['store']['slot_start']}–{config['store']['slot_end_exclusive']} (dev only). "
             f"Engine {config.get('engine_commit', 'unknown')}. Signals {config['signals']}, positions {config['positions']}, "
             f"{config['seconds']} s.",
             f"Entry/exit delay d, intra-slot position q={config['q']}, own slippage tolerance {config['slippage_tol']}, "
             f"N2: {config.get('n2_k', 5)} matched random entries per trigger. Report generated by {_report_commit()}.",
             "", "## Deviations from the spec", ""] + [f"- {x}" for x in DEVIATIONS] + [
             "", "## Gate (batch 1): base costs, above N2 at ≥5 SOL for every d", ""]
    for t in tr:
        lines.append(f"- {t['family']} {t['variant']}: {'PASS' if t['gate_pass'] else 'no'}")
    fam = sorted((s for s in base if s["family"] in FAMILIES), key=lambda s: (s["family"], s["variant"], s["size_sol"], s["d"]))
    lines += ["", "## Stop-rule measure per strategy (base costs)", "",
              f"Net P&L per trade in SOL after all costs (failed entries included), 95% CI by day bootstrap. "
              f"GO: CI above 0; ADJUST: mean above 0; STOP: mean ≤ 0; fewer than {MIN_TRADES} trades: no verdict. "
              f"Sizes below {LIVE_MIN_SOL} SOL are informative only (under the live size).",
              "",
              "| family | variant | size SOL | d | trades | days | net SOL/trade | 95% CI | verdict | mean % | edge vs N2 | edge 95% CI | note |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in fam:
        ci = f"{_sol(s['pnl_ci_lo'])} … {_sol(s['pnl_ci_hi'])}" if s["pnl_ci_lo"] is not None else ""
        eci = f"{_pct(s['edge_ci_lo'])} … {_pct(s['edge_ci_hi'])}" if s["edge_ci_lo"] is not None else ""
        note = "informative (under live size)" if s["size_sol"] < LIVE_MIN_SOL else ""
        lines.append(f"| {s['family']} | {s['variant']} | {s['size_sol']} | {s['d']} | {s['filled']} | {s['days']} | "
                     f"{_sol(s['mean_pnl_sol'])} | {ci} | {s['verdict']} | {_pct(s['mean_ret'])} | "
                     f"{_pct(s['edge_vs_n2'])} | {eci} | {note} |")
    lines += ["", "## Base cost scenario", "",
              "Mean = mean return per filled trade after all costs. Drift = mean historical mid move entry→exit without us, "
              f"excluding degenerate states (mid move above ×{MAX_JUMP}: near-empty pool reserves; share shown); "
              "mean − drift ≈ our costs and impact; the median of (return − drift) per trade is shown as well. SL = stop-loss exits (share, realized mean). "
              "CIs: 95%, bootstrap over days. Ex-top: mean without the best 3 trades / best 1%.",
              "",
              "| family | variant | size SOL | d | tau | n | filled | mean | median | win | 95% CI | ex-top3 / ex-top1% | "
              "drift (excluded) | median ret − drift | "
              "SL share / mean | total PnL SOL | N2 mean | edge vs N2 | edge 95% CI |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in base:
        ci = f"{_pct(s['ci_lo'])} … {_pct(s['ci_hi'])}" if s["ci_lo"] is not None else ""
        eci = f"{_pct(s['edge_ci_lo'])} … {_pct(s['edge_ci_hi'])}" if s["edge_ci_lo"] is not None else ""
        win = "" if s["win_rate"] is None else f"{s['win_rate'] * 100:.0f}%"
        sl = "" if s["stop_loss_share"] is None else f"{s['stop_loss_share']*100:.0f}% / {_pct(s['stop_loss_mean'])}"
        lines.append(f"| {s['family']} | {s['variant']} | {s['size_sol']} | {s['d']} | {s['tau']} | {s['n']} | {s['filled']} | "
                     f"{_pct(s['mean_ret'])} | {_pct(s['median_ret'])} | {win} | {ci} | "
                     f"{_pct(s['mean_ex_top3'])} / {_pct(s['mean_ex_top1pct'])} | "
                     f"{_pct(s['hist_drift'])} ({_num(s['drift_excluded'] and s['drift_excluded'] * 100, '.2f')}%) | "
                     f"{_pct(s['median_cost'])} | {sl} | "
                     f"{s['total_pnl_sol']:+.2f} | {_pct(s['n2_mean_ret'])} | {_pct(s['edge_vs_n2'])} | {eci} |")
    diag = sorted((s for s in base if s["tau"] == base[0]["tau"]), key=lambda s: (s["family"], s["variant"], s["size_sol"], s["d"]))
    lines += ["", "## Replay diagnostics (per position, base scenario)", "",
              "| family | variant | size SOL | d | reverted router txs | reverted direct txs | dropped sells | "
              "counterfactual graduation | seed-pool exits |",
              "|---|---|---|---|---|---|---|---|---|"]
    for s in diag:
        lines.append(f"| {s['family']} | {s['variant']} | {s['size_sol']} | {s['d']} | {_num(s['reverted_router'], '.2f')} | "
                     f"{_num(s['reverted_direct'], '.2f')} | {_num(s['sells_dropped'], '.2f')} | "
                     f"{_num(s['cf_graduation'] and s['cf_graduation'] * 100, '.1f')}% | "
                     f"{_num(s['seed_pool'] and s['seed_pool'] * 100, '.1f')}% |")
    with open(os.path.join(run_dir, "report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return summary


if __name__ == "__main__":
    from .store import load_defaults

    write_report(sys.argv[1], load_defaults())
