"""Route (ii) report (research/spec-route-ii-sneller-instappen-2026-10-20.md v0.3 §3.5-§3.6): the tournament's parts of a
run with --entries (R, D1both, D1, E0, K1, K2, K3, K5), read only, dev 1033-1046.

Per group (K3 = tp4_T600 on every trigger; H5 = flow300_tp100_H6 organic; H4 = flow300_H6 organic, reported only), per
block (days 1-9, days 10-18, all days, per slot-time regime) and per entry code:
- Σ net SOL per day (the primary measure; settle_sql base scenario, a day without a position counts 0), its mean over the
  block's days with a 95 % day bootstrap (BOOT draws, seed 7), the worst day;
- Σ/day − R as a PAIRED day bootstrap of the difference: the same resampled days for both codes (one draw of day indices
  per block, shared by every series), so the interval is of the difference, not two separate intervals;
- rows, filled, entry_failed, busy-only (triggers present in R but not in the code, and the reverse: busy chains move),
  capped share and the median k_tx of the k-codes, the exit mix;
- net return per filled trade (mean, 1 % trimmed) with its day CI, and the PAIRED Δ return against R on the triggers filled
  in both codes (a selection; busy chains differ) with its day CI, plus 'same state' = the share of those pairs with the same
  fill as R (the same tokens for 0.5 SOL: the pool did not trade between the two entries);
- the k-curve: Σ/day − R for E0 (= K0), K1, K2, K3, K5 and D1, and the competition margin = the largest k whose paired
  interval lies above 0 with every smaller k's interval above 0 too (contiguous from k = 0; a lone positive k higher up does
  not count; stricter than the spec's literal "largest k").
Decision rule (§3.6, pre-set): positive when for K3 AND H5 on days 10-18 both E0 and K1 have Σ/day − R > 0 with the paired
interval above 0 and a paired Δ return ≥ +0.2 pt on the common triggers (secondary); consistency: days 1-9 same sign.
H4 is reported, not tested. Nothing here is a claim: dev is used data.

  python -m wallets.entry_report --parts <run>/parts [--cfg week2-defaults.yaml] [--boot 10000] [--out entry_report.md]
"""
import argparse
import glob
import json
import os
import sys
import time

import duckdb
import numpy as np

from backtest import costs
from .markout_report import BLOCKS, BOOT, day_ci, trimmed_mean as trimmed, workers, _pool, close_pool

GROUPS = (("K3", "tp4_T600", "all"), ("H5", "flow300_tp100_H6", "organic"), ("H4", "flow300_H6", "organic"))
TESTED = ("K3", "H5")
CODES = ("R", "D1both", "D1", "E0", "K1", "K2", "K3", "K5")
KCURVE = (("E0", 0), ("K1", 1), ("K2", 2), ("K3", 3), ("K5", 5))
BINDING = BLOCKS[1][0]
EARLY = BLOCKS[0][0]
MIN_DELTA_PT = 0.2  # paired Δ return per trade, percentage points (§3.6)
SEED = 7


def load(con, parts_dir, cfg_path):
    files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
    if not files:
        raise SystemExit(f"no parquet parts in {parts_dir}")
    import yaml
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    pnl, ret = costs.settle_sql(costs.Scenario.from_cfg("base", cfg))
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE p AS
      SELECT entry, variant, farm, regime, day, pool, t, entry_slot, exit_slot, exit_reason, skipped, entry_failed, capped, k_tx,
             cost, tokens, proceeds, ({pnl})::DOUBLE / 1e9 AS pnl, ({ret})::DOUBLE AS ret,
             NOT skipped AND NOT entry_failed AS filled
      FROM read_parquet({lit}, union_by_name = true)
      WHERE family = 'F7' AND segment = 'pool_reversed' AND size_sol = 0.5 AND entry IS NOT NULL""")
    n = con.execute("SELECT count(*) FROM p").fetchone()[0]
    codes = [r[0] for r in con.execute("SELECT DISTINCT entry FROM p ORDER BY 1").fetchall()]
    if "R" not in codes:
        raise SystemExit("the parts have no R rows (the reference)")
    return n, codes


def _where(variant, farm, block_sql):
    return f"variant = '{variant}' AND {block_sql}" + ("" if farm == "all" else f" AND farm = '{farm}'")


def daily_series(con, code, days):
    rows = dict(con.execute(f"SELECT day, sum(pnl) FROM g WHERE entry = '{code}' AND NOT skipped GROUP BY 1").fetchall())
    return np.array([float(rows.get(d, 0.0) or 0.0) for d in days])


def boot_idx(n_days, boot, seed=SEED):
    """One set of day resamples per block, shared by every series and difference in it (paired)."""
    return np.random.default_rng(seed).integers(0, n_days, size=(boot, n_days))


def mean_ci(x, idx):
    x = np.asarray(x, dtype=float)
    if len(x) < 2:
        return float(x.mean()) if len(x) else None, None, None
    m = x[idx].mean(1)
    return float(x.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def code_stats(con, code, days, idx, r_daily):
    """Everything the tables show for one code in the group-block table g (created by analyse)."""
    w = f"entry = '{code}'"
    rows, filled, failed, capped_n, capped_known, k_med = con.execute(f"""
      SELECT count(*), sum(filled::INT), sum(entry_failed::INT), sum(COALESCE(capped, false)::INT), sum((capped IS NOT NULL)::INT),
             quantile_cont(k_tx, 0.5) FROM g WHERE {w} AND NOT skipped""").fetchone()
    mix = dict(con.execute(f"SELECT exit_reason, count(*) FROM g WHERE {w} AND filled GROUP BY 1").fetchall())
    d = daily_series(con, code, days)
    mean, lo, hi = mean_ci(d, idx)
    dmean, dlo, dhi = mean_ci(d - r_daily, idx)
    out = {"rows": int(rows or 0), "filled": int(filled or 0), "entry_failed": int(failed or 0),
           "capped_share": (capped_n / capped_known) if capped_known else None, "k_tx_median": k_med,
           "sum_day": mean, "sum_day_lo": lo, "sum_day_hi": hi, "worst_day": float(d.min()) if len(d) else None,
           "diff_day": dmean, "diff_day_lo": dlo, "diff_day_hi": dhi,
           "exit_mix": {k: v / filled for k, v in mix.items()} if filled else {}}
    # net return per filled trade
    rr = con.execute(f"SELECT ret, day FROM g WHERE {w} AND filled").fetchnumpy()
    rets, rdays = rr["ret"].astype(float), rr["day"]
    out["ret_mean"] = float(rets.mean()) if len(rets) else None
    out["ret_trim"] = trimmed(rets) if len(rets) else None
    out["ret_lo"], out["ret_hi"] = day_ci(rets, rdays) if len(rets) else (None, None)
    # paired against R on the triggers filled in both (a selection); same state = the same fill (tokens for 0.5 SOL exact-in)
    pr = con.execute(f"""
      SELECT a.ret - b.ret AS dret, a.day, a.tokens = b.tokens AS same
      FROM g a JOIN g b ON b.pool = a.pool AND b.t = a.t AND b.entry = 'R'
      WHERE a.entry = '{code}' AND a.filled AND b.filled""").fetchnumpy()
    dret, pdays, same = pr["dret"].astype(float), pr["day"], pr["same"]
    out["pairs"] = int(len(dret))
    out["dret_mean"] = float(dret.mean()) if len(dret) else None
    out["dret_trim"] = trimmed(dret) if len(dret) else None
    out["dret_lo"], out["dret_hi"] = day_ci(dret, pdays) if len(dret) else (None, None)
    out["same_state"] = float(np.mean(same.astype(float))) if len(same) else None
    # busy chains: triggers present in R but not in the code, and the reverse
    only_r, only_c = con.execute(f"""
      WITH c AS (SELECT pool, t FROM g WHERE {w} AND NOT skipped), r AS (SELECT pool, t FROM g WHERE entry = 'R' AND NOT skipped)
      SELECT (SELECT count(*) FROM r ANTI JOIN c USING (pool, t)), (SELECT count(*) FROM c ANTI JOIN r USING (pool, t))""").fetchone()
    out["only_in_R"], out["only_in_code"] = int(only_r), int(only_c)
    return out


def analyse(con, codes, boot, log=print):
    regs = [r[0] for r in con.execute("SELECT DISTINCT regime FROM p ORDER BY 1").fetchall()]
    blocks = list(BLOCKS) + [(r, f"regime = '{r}'") for r in regs]
    out = {"groups": {}, "boot": boot, "seed": SEED, "codes": codes}
    for gname, variant, farm in GROUPS:
        g = {"variant": variant, "farm": farm, "blocks": {}}
        for bname, bsql in blocks:
            con.execute(f"CREATE OR REPLACE TEMP TABLE g AS SELECT * FROM p WHERE {_where(variant, farm, bsql)}")
            days = [r[0] for r in con.execute("SELECT DISTINCT day FROM g ORDER BY 1").fetchall()]
            if not days:
                continue
            idx = boot_idx(len(days), boot)
            r_daily = daily_series(con, "R", days)
            blk = {"days": len(days), "codes": {}}
            for code in codes:
                blk["codes"][code] = code_stats(con, code, days, idx, r_daily)
            # k-curve and the competition margin
            curve = [(code, k, blk["codes"][code]) for code, k in KCURVE if code in blk["codes"]]
            margin = None
            for code, k, c in curve:
                if c["diff_day_lo"] is not None and c["diff_day_lo"] > 0:
                    margin = k
                else:
                    break
            blk["competition_margin_k"] = margin
            g["blocks"][bname] = blk
            log(f"  {gname} {bname}: {len(days)} days, R Σ/day {r_daily.mean():+.3f} SOL")
        out["groups"][gname] = g
    # decision rule
    verdict = {"rule": "K3 AND H5, days 10-18: E0 and K1 both Σ/day − R > 0 with the paired day interval above 0 AND paired Δ return ≥ +0.2 pt; "
                       "consistency: days 1-9 same sign (not a condition on the interval)", "per_group": {}}
    positive = True
    for gname in TESTED:
        g = out["groups"].get(gname, {}).get("blocks", {})
        b, e = g.get(BINDING, {}).get("codes", {}), g.get(EARLY, {}).get("codes", {})
        per = {}
        for code in ("E0", "K1"):
            c, c1 = b.get(code), e.get(code)
            ok_sum = c is not None and c["diff_day_lo"] is not None and c["diff_day_lo"] > 0
            ok_d = c is not None and c["dret_trim"] is not None and c["dret_trim"] * 100 >= MIN_DELTA_PT
            same_sign = (c is not None and c1 is not None and c["diff_day"] is not None and c1["diff_day"] is not None
                         and np.sign(c["diff_day"]) == np.sign(c1["diff_day"]))
            per[code] = {"sum_day_diff_ci_above_0": ok_sum, "delta_ret_ge_0.2pt": ok_d, "days_1_9_same_sign": same_sign,
                         "diff_day": c["diff_day"] if c else None, "diff_day_lo": c["diff_day_lo"] if c else None,
                         "dret_trim_pt": c["dret_trim"] * 100 if c and c["dret_trim"] is not None else None}
            positive &= ok_sum and ok_d
        verdict["per_group"][gname] = per
    verdict["positive"] = bool(positive)
    out["verdict"] = verdict
    return out


# --- markdown ---------------------------------------------------------------------------------------

def _f(x, nd=3, sign=True):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def _pct(x, nd=2):
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{nd}f}"


def _share(x):
    return "–" if x is None else f"{x * 100:.0f}%"


def _ci(lo, hi, nd=3):
    return "–" if lo is None else f"{lo:+.{nd}f} … {hi:+.{nd}f}"


def markdown(res, parts_dir, n_rows, seconds):
    L = [f"## Route (ii) entry report ({os.path.basename(os.path.dirname(parts_dir.rstrip('/')))})", "",
         f"Parts {parts_dir}; {n_rows:,} F7 pool_reversed rows at 0.5 SOL; codes {', '.join(res['codes'])}; base costs (settle_sql); "
         f"Σ/day = net SOL per UTC day of the trigger (a day without a position counts 0), mean over the block's days with a 95 % day "
         f"bootstrap ({res['boot']:,} draws, seed {res['seed']}); **Σ/day − R** = the paired day bootstrap of the daily difference (the same "
         f"resampled days for both codes); ret = net return per filled trade (mean / 1 % trimmed, day CI); **Δ ret vs R** = paired per "
         f"trigger on the triggers filled in both codes (a selection: busy chains differ), 1 % trimmed with day CI; same state = share of "
         f"those pairs with R's fill (same tokens); only R / only code = triggers present in one of the two (busy chains); capped = share of the "
         f"k-code's rows whose entry slot had fewer token buys than k (then the slot end); k_tx = median pool transactions before our "
         f"order in the entry slot. Not a claim: dev 1033–1046 is used data. {seconds:.0f} s.", ""]
    for gname, g in res["groups"].items():
        L.append(f"### {gname} · {g['variant']} · farm {g['farm']}" + ("" if gname in TESTED else " (reported, not tested)"))
        L.append("")
        L.append("| block | code | rows | filled | failed | only R / only code | capped | k_tx | **Σ/day (SOL)** | 95% CI | **Σ/day − R** | 95% CI (paired) | worst day | ret mean | ret trim | 95% CI | pairs | **Δ ret vs R (trim, pt)** | 95% CI | same state | tp / sl / time / outflow |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for bname, blk in g["blocks"].items():
            for code, c in blk["codes"].items():
                mix = c["exit_mix"]
                mixs = " / ".join(_share(mix.get(k, 0.0)) for k in ("take_profit", "stop_loss", "time", "outflow"))
                L.append(f"| {bname} ({blk['days']} d) | {code} | {c['rows']:,} | {c['filled']:,} | {c['entry_failed']:,} | {c['only_in_R']:,} / {c['only_in_code']:,} | "
                         f"{_share(c['capped_share'])} | {_f(c['k_tx_median'], 1, False)} | **{_f(c['sum_day'])}** | {_ci(c['sum_day_lo'], c['sum_day_hi'])} | "
                         f"**{_f(c['diff_day'])}** | {_ci(c['diff_day_lo'], c['diff_day_hi'])} | {_f(c['worst_day'], 2)} | {_pct(c['ret_mean'])} | {_pct(c['ret_trim'])} | "
                         f"{_ci(c['ret_lo'] * 100 if c['ret_lo'] is not None else None, c['ret_hi'] * 100 if c['ret_hi'] is not None else None, 2)} | "
                         f"{c['pairs']:,} | **{_pct(c['dret_trim'])}** | {_ci(c['dret_lo'] * 100 if c['dret_lo'] is not None else None, c['dret_hi'] * 100 if c['dret_hi'] is not None else None, 2)} | "
                         f"{_share(c['same_state'])} | {mixs} |")
            m = blk["competition_margin_k"]
            L.append(f"| {bname} | k-curve | | | | | | | | | | | | | | | | | | | competition margin: "
                     + ("none (E0's paired interval is not above 0)" if m is None else f"k = {m} (largest k with Σ/day − R above 0, contiguous from k = 0)") + " |")
        L.append("")
    v = res["verdict"]
    L.append(f"### Decision rule (§3.6): **{'POSITIVE' if v['positive'] else 'NOT positive'}**")
    L.append("")
    L.append(v["rule"])
    L.append("")
    L.append("| group | code | Σ/day − R (days 10-18) | paired CI lower | above 0 | Δ ret trim (pt) | ≥ 0.2 pt | days 1-9 same sign |")
    L.append("|---|---|---|---|---|---|---|---|")
    for gname, per in v["per_group"].items():
        for code, c in per.items():
            L.append(f"| {gname} | {code} | {_f(c['diff_day'])} | {_f(c['diff_day_lo'])} | {'yes' if c['sum_day_diff_ci_above_0'] else 'no'} | "
                     f"{_f(c['dret_trim_pt'], 2)} | {'yes' if c['delta_ret_ge_0.2pt'] else 'no'} | {'yes' if c['days_1_9_same_sign'] else 'no'} |")
    L.append("")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parts", required=True)
    ap.add_argument("--cfg", default=os.environ.get("LAB_DEFAULTS", "/home/chupa/Solana-project/data-old-faithful-one/lab/research/week2-defaults.yaml"))
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--out", help="markdown path (default <run>/entry_report.md); the json goes next to it")
    a = ap.parse_args()
    t0 = time.time()
    if workers() > 1:
        _pool(workers())
    try:
        con = duckdb.connect()
        con.execute(f"SET threads TO {int(os.environ.get('LAB_WALLETS_THREADS', '2'))}; SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1GB')}'")
        n, codes = load(con, a.parts, a.cfg)
        print(f"{n:,} rows, codes {codes}", file=sys.stderr, flush=True)
        res = analyse(con, codes, a.boot, log=lambda m: print(m, file=sys.stderr, flush=True))
        out = a.out or os.path.join(os.path.dirname(a.parts.rstrip("/")), "entry_report.md")
        with open(out, "w") as f:
            f.write(markdown(res, a.parts, n, time.time() - t0))
        with open(os.path.splitext(out)[0] + ".json", "w") as f:
            json.dump(res, f, indent=1, default=float)
        print(json.dumps({"out": out, "positive": res["verdict"]["positive"], "seconds": round(time.time() - t0, 1)}))
    finally:
        close_pool()


if __name__ == "__main__":
    main()
