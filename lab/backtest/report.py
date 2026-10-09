"""Summarize a tournament run: settle each position per cost scenario and aggregate."""

import json
import os

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .costs import LAMPORTS, Scenario, settle
from .replay import Result

KEYS = ["family", "variant", "size_sol", "d", "tau"]


def _settled(rows, scn):
    out = []
    for r in rows:
        res = Result(r["mint"], r["t"], size=int(r["size_sol"] * LAMPORTS), entry_failed=r["entry_failed"],
                     cost=r["cost"] or 0, proceeds=r["proceeds"] or 0)
        pnl, ret, _ = settle(res, scn)
        out.append((r, pnl, ret))
    return out


def _day_bootstrap(rets, days, n=1000, seed=7):
    if len(rets) < 2:
        return (None, None)
    by_day = {}
    for x, d in zip(rets, days):
        by_day.setdefault(d, []).append(x)
    keys = list(by_day)
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n):
        pick = rng.choice(len(keys), size=len(keys), replace=True)
        vals = [v for i in pick for v in by_day[keys[i]]]
        means.append(float(np.mean(vals)))
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def summarize(rows, cfg):
    groups = {}
    skipped = {}
    for r in rows:
        key = tuple(r[k] for k in KEYS)
        if r.get("skipped"):
            skipped[key] = skipped.get(key, 0) + 1
            continue
        groups.setdefault(key, []).append(r)
    out = []
    for name in ("optimistic", "base", "pessimistic"):
        scn = Scenario.from_cfg(name, cfg)
        for key, rs in groups.items():
            st = _settled(rs, scn)
            filled = [(r, p, x) for r, p, x in st if x is not None]
            rets = [x for _, _, x in filled]
            pnls = [p for _, p, _ in st]
            lo, hi = _day_bootstrap(rets, [r["day"] for r, _, _ in filled])
            out.append(dict(zip(KEYS, key), scenario=name, n=len(rs), skipped=skipped.get(key, 0), filled=len(filled),
                            fail_rate=1 - len(filled) / len(rs) if rs else None,
                            mean_ret=float(np.mean(rets)) if rets else None,
                            median_ret=float(np.median(rets)) if rets else None,
                            win_rate=float(np.mean([x > 0 for x in rets])) if rets else None,
                            mean_pnl_sol=float(np.mean(pnls)) / LAMPORTS if pnls else None,
                            total_pnl_sol=float(np.sum(pnls)) / LAMPORTS,
                            ci_lo=lo, ci_hi=hi, days=len({r["day"] for r in rs}),
                            cf_graduation=float(np.mean([r["cf_graduation"] for r in rs]))))
    # N2 comparison: same variant/size/d/tau/scenario.
    idx = {(o["family"], o["variant"], o["size_sol"], o["d"], o["tau"], o["scenario"]): o for o in out}
    for o in out:
        n2 = idx.get(("N2_" + o["family"], o["variant"], o["size_sol"], o["d"], o["tau"], o["scenario"]))
        o["n2_mean_ret"] = n2["mean_ret"] if n2 else None
        o["edge_vs_n2"] = (o["mean_ret"] - n2["mean_ret"]) if (n2 and o["mean_ret"] is not None and n2["mean_ret"] is not None) else None
    return out


def _pct(x):
    return "" if x is None else f"{x*100:+.2f}%"


def write_report(run_dir, cfg):
    rows = pq.read_table(os.path.join(run_dir, "results.parquet")).to_pylist()
    summary = summarize(rows, cfg)
    pq.write_table(pa.Table.from_pylist(summary), os.path.join(run_dir, "summary.parquet"))
    with open(os.path.join(run_dir, "config.json")) as f:
        config = json.load(f)
    base = sorted((s for s in summary if s["scenario"] == "base"),
                  key=lambda s: (s["family"], s["variant"], s["size_sol"], s["d"], s["tau"]))
    lines = [f"# Backtest {config['run_id']}", "",
             f"Store: slots {config['store']['slot_start']}–{config['store']['slot_end_exclusive']} (dev only). "
             f"Signals {config['signals']}, positions {config['positions']}, {config['seconds']} s.",
             f"Entry/exit delay d, intra-slot position q={config['q']}, own slippage tolerance {config['slippage_tol']}.",
             "", "## Base cost scenario", "",
             "| family | variant | size SOL | d | tau | n | filled | mean | median | win | 95% CI (day bootstrap) | mean PnL SOL | total PnL SOL | N2 mean | edge vs N2 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in base:
        ci = f"{_pct(s['ci_lo'])} … {_pct(s['ci_hi'])}" if s["ci_lo"] is not None else ""
        win = "" if s["win_rate"] is None else f"{s['win_rate'] * 100:.0f}%"
        mean_pnl = "" if s["mean_pnl_sol"] is None else f"{s['mean_pnl_sol']:+.4f}"
        lines.append(f"| {s['family']} | {s['variant']} | {s['size_sol']} | {s['d']} | {s['tau']} | {s['n']} | {s['filled']} | "
                     f"{_pct(s['mean_ret'])} | {_pct(s['median_ret'])} | {win} | {ci} | {mean_pnl} | "
                     f"{s['total_pnl_sol']:+.2f} | {_pct(s['n2_mean_ret'])} | {_pct(s['edge_vs_n2'])} |")
    with open(os.path.join(run_dir, "report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return summary
