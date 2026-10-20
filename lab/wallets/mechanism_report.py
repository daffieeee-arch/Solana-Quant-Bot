"""F7 mechanism test on the clean data (research/prereg-f7-mechanism-clean-2026-10-21.md): exit-free markouts of the F7
pool_reversed triggers of E20's run against their N2 twins (wallets.markout --clean-lock), the POOL as the unit.

Per pool and (d, h): the mean over its triggers of the LOG markout lg = ln(mid at the horizon / mid at the entry) (the
gross ratio has a heavy right tail on tiny pools: on dev the per-pool mean of the gross ratio runs into +600 % while its
median is +3 %; the log is symmetric and bounded, floored at ln 0.001), of the net markout (0.5 SOL round trip against
the real reserves, base costs; a return, not a log) and of the paired log difference to the N2 twins (per trigger the
mean over its twins' logs; triggers without a twin are left out of the pair). The statistic over pools is the
TRIM-trimmed mean with a 95 % POOL bootstrap (BOOT draws, seed 7; pools resampled, every pool one weight whatever its
trigger count: day_ci with the pool as the label).

Primary, pre-set, at d = D and h = H:
  P1  log markout of F7 over pools: the direction of the price after the trigger;
  P2  paired log markout F7 − N2 over pools: the bounce beyond what matched pools do in the same hour.
Outcome: CONFIRMED when both lower bounds > 0; REJECTED when either trimmed mean <= 0; INCONCLUSIVE otherwise (= not
shown, no follow-up). NOT JUDGED (no outcome) below MIN_POOLS pools or MIN_DAYS UTC days with a trigger.
Secondary, reported beside the outcome, never changing it:
  tradable  the unpaired NET F7 markout at (D, H) over pools: 'yes' when its lower bound > 0, else 'no';
  consistency  P1 and P2 at CONSISTENCY horizons: the same sign as at H (descriptive).
Descriptive: per trigger (unweighted; the gross ratio too, and the share of triggers with a rising mid: the direction),
per UTC day (pool means per day, the worst day), per regime and farm, the twins' own markout, the lower bound at 99.9 %
(a multiple-testing view beside the 95 % the rule uses).

  python -m wallets.mechanism_report --markout DIR [--run NAME] [--boot 10000] [--out DIR/mechanism.md]
"""
import argparse
import glob
import json
import os
import sys
import time

import duckdb
import numpy as np

from .markout_report import BOOT, TRIM, _pool, close_pool, day_ci, fmt, trimmed_mean, workers

D = 2            # the paper's d_ms 600 in whole slots at the clean epochs' measured ms per slot (pre-Alpenglow)
H = "1m"
CONSISTENCY = ("5m", "15m")
MIN_POOLS = 100
MIN_DAYS = 5
SEED = 7
FAMILY, SEGMENT = "F7", "pool_reversed"
LG = "ln(GREATEST(1 + gross, 0.001))"  # the log markout, floored at ln 0.001 (a mid at zero)


def load(con, markout_dir, run=None):
    pat = f"markout-{run}.parquet" if run else "markout-*.parquet"
    files = sorted(glob.glob(os.path.join(markout_dir, pat)))
    if not files:
        raise SystemExit(f"no {pat} in {markout_dir}")
    lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
    con.execute(f"""
      CREATE OR REPLACE TABLE m AS SELECT * FROM read_parquet({lit}, union_by_name = true) WHERE h <> ''
      QUALIFY row_number() OVER (PARTITION BY family, segment, venue, mint, pool, t, d, h ORDER BY cut, net IS NULL) = 1""")
    con.execute(f"""
      CREATE OR REPLACE TABLE f AS
      SELECT t.pool, t.mint, t.t, t.day, t.regime, t.farm, t.d, t.h, t.cut, t.gross, {LG} AS lg, t.net, n.n2_gross, n.n2_lg, n.n2_net, n.n_n2
      FROM m t LEFT JOIN (
        SELECT ref_mint, ref_t, d, h, avg(gross) AS n2_gross, avg({LG}) AS n2_lg, avg(net) AS n2_net, count(*) AS n_n2
        FROM m WHERE family = 'N2_{FAMILY}' AND segment = '{SEGMENT}' AND ref_mint IS NOT NULL AND gross IS NOT NULL GROUP BY ALL) n
      ON (n.ref_mint = t.mint OR n.ref_mint = t.pool) AND n.ref_t = t.t AND n.d = t.d AND n.h = t.h
      WHERE t.family = '{FAMILY}' AND t.segment = '{SEGMENT}'""")
    con.execute("""
      CREATE OR REPLACE TABLE p AS
      SELECT pool, d, h, count(*) AS triggers, count(gross) AS n_gross, count(n2_gross) AS n_paired, min(day) AS first_day,
             avg(lg) AS lg, avg(gross) AS gross, avg(net) AS net, avg(lg - n2_lg) AS diff, avg(n2_lg) AS n2_lg, avg(n2_net) AS n2_net
      FROM f GROUP BY ALL""")
    return len(files)


def pool_stat(con, d, h, col, boot, where="TRUE"):
    """The trimmed mean over pools of a per-pool mean, its 95 % pool bootstrap and the 99.9 % lower bound."""
    rows = con.execute(f"SELECT {col}, pool FROM p WHERE d = {d} AND h = '{h}' AND {col} IS NOT NULL AND {where}").fetchall()
    if not rows:
        return None
    x = np.array([r[0] for r in rows], dtype=float)
    pools = np.array([r[1] for r in rows])
    lo, hi = day_ci(x, pools, boot, seed=SEED)
    lo999 = _lower(x, pools, boot, 0.001)
    return {"pools": len(x), "mean": float(trimmed_mean(x)), "lo": lo, "hi": hi, "lo_999": lo999}


def _lower(x, labels, boot, alpha):
    """The alpha lower bound of the trimmed mean over a label bootstrap (same draws as day_ci: seed, multinomial)."""
    uniq, inv = np.unique(labels, return_inverse=True)
    if len(uniq) < 2:
        return None
    rng = np.random.default_rng(SEED)
    counts = rng.multinomial(len(uniq), np.full(len(uniq), 1 / len(uniq)), size=boot)
    order = np.argsort(x, kind="stable")
    xs, li = x[order], inv[order]
    from .markout_report import weighted_trimmed_mean
    out = np.array([weighted_trimmed_mean(xs, counts[b][li].astype(float)) for b in range(boot)])
    out = out[~np.isnan(out)]
    return None if len(out) == 0 else float(np.percentile(out, 100 * alpha))


def trigger_stat(con, d, h, col, where="TRUE"):
    rows = con.execute(f"SELECT {col} FROM f WHERE d = {d} AND h = '{h}' AND {col} IS NOT NULL AND {where}").fetchall()
    if not rows:
        return None
    x = np.array([r[0] for r in rows], dtype=float)
    return {"n": len(x), "mean": float(trimmed_mean(x)), "share_pos": float(np.mean(x > 0))}


def analyse(con, boot, log=lambda m: None):
    t0 = time.time()
    n_trig, n_pools, n_days, n_paired = con.execute(f"""SELECT count(*), count(DISTINCT pool), count(DISTINCT day), count(n2_gross)
        FROM f WHERE d = {D} AND h = '{H}'""").fetchone()
    res = {"rule": {"d": D, "h": H, "consistency": list(CONSISTENCY), "min_pools": MIN_POOLS, "min_days": MIN_DAYS, "boot": boot, "seed": SEED,
                    "trim": TRIM, "unit": "pool"},
           "input": {"triggers": n_trig, "pools": n_pools, "days": n_days, "paired_triggers": n_paired}, "primary": {}, "secondary": {},
           "descriptive": {}}
    log(f"{n_trig:,} F7 {SEGMENT} triggers in {n_pools:,} pools over {n_days} days at d {D}, h {H}; {n_paired:,} with an N2 twin")
    hs = (H,) + CONSISTENCY
    for h in hs:
        res["primary"][h] = {"P1_lg": pool_stat(con, D, h, "lg", boot), "P2_diff": pool_stat(con, D, h, "diff", boot)}
        res["secondary"][h] = {"net": pool_stat(con, D, h, "net", boot), "n2_lg": pool_stat(con, D, h, "n2_lg", boot),
                               "n2_net": pool_stat(con, D, h, "n2_net", boot)}
        log(f"h {h}: " + ", ".join(f"{k} {fmt(v['mean'] * 100, sign=True)} [{fmt(v['lo'] * 100, sign=True)}; {fmt(v['hi'] * 100, sign=True)}]"
                                  for k, v in ((k, v) for k, v in (res["primary"][h] | res["secondary"][h]).items() if v)))
    p1, p2 = res["primary"][H]["P1_lg"], res["primary"][H]["P2_diff"]
    net = res["secondary"][H]["net"]
    judged = n_pools >= MIN_POOLS and n_days >= MIN_DAYS and p1 is not None and p2 is not None and p1["lo"] is not None and p2["lo"] is not None
    if not judged:
        outcome = "NOT JUDGED"
    elif p1["lo"] > 0 and p2["lo"] > 0:
        outcome = "CONFIRMED"
    elif p1["mean"] <= 0 or p2["mean"] <= 0:
        outcome = "REJECTED"
    else:
        outcome = "INCONCLUSIVE"
    tradable = None if net is None or net["lo"] is None else ("yes" if net["lo"] > 0 else "no")
    cons = {}
    for h in CONSISTENCY:
        q1, q2 = res["primary"][h]["P1_lg"], res["primary"][h]["P2_diff"]
        cons[h] = None if not judged or q1 is None or q2 is None else bool(np.sign(q1["mean"]) == np.sign(p1["mean"]) and np.sign(q2["mean"]) == np.sign(p2["mean"]))
    res["verdict"] = {"outcome": outcome, "judged": judged, "P1_lo_above_0": None if not judged else p1["lo"] > 0,
                      "P2_lo_above_0": None if not judged else p2["lo"] > 0, "tradable": tradable, "consistency_same_sign": cons}
    # descriptive: per trigger, per day (pool means per day), regime, farm
    for h in hs:
        res["descriptive"][h] = {"per_trigger": {c: trigger_stat(con, D, h, c) for c in ("lg", "gross", "net", "lg - n2_lg", "n2_lg")}}
    days = con.execute(f"""SELECT day, count(DISTINCT pool), avg(lg), avg(net) FROM f WHERE d = {D} AND h = '{H}' AND lg IS NOT NULL
                            GROUP BY 1 ORDER BY 1""").fetchall()
    res["descriptive"]["per_day"] = [{"day": r[0], "pools": r[1], "lg": r[2], "net": r[3]} for r in days]
    if days:
        w = min(days, key=lambda r: r[2])
        res["descriptive"]["worst_day"] = {"day": w[0], "lg": w[2]}
    for key, col in (("regime", "regime"), ("farm", "farm")):
        res["descriptive"][key] = {}
        for (val,) in con.execute(f"SELECT DISTINCT {col} FROM f ORDER BY 1").fetchall():
            # a pool's triggers of one regime / farm label: the per-pool mean within that label
            con.execute(f"""CREATE OR REPLACE TABLE p AS SELECT pool, d, h, avg(lg) AS lg, avg(net) AS net, avg(lg - n2_lg) AS diff,
                            avg(n2_lg) AS n2_lg, avg(n2_net) AS n2_net FROM f WHERE {col} = '{val}' GROUP BY ALL""")
            res["descriptive"][key][str(val)] = {c: pool_stat(con, D, H, c, boot) for c in ("lg", "diff", "net")}
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _row(label, s):
    if not s:
        return f"| {label} | – | – | – | – |"
    return f"| {label} | {s['pools']:,} | {_c(s['mean'])} | [{_c(s['lo'])}; {_c(s['hi'])}] | {_c(s['lo_999'])} |"


def _c(x):
    """log markouts and returns both × 100: log points / percentage points, two decimals, signed."""
    return fmt(None if x is None else x * 100, sign=True)


def markdown(res, markout_dir):
    v, i, r = res["verdict"], res["input"], res["rule"]
    L = [f"## F7 mechanism test ({os.path.basename(markout_dir.rstrip('/'))})", "",
         f"Markouts {markout_dir}; F7 {SEGMENT} triggers {i['triggers']:,} in {i['pools']:,} pools over {i['days']} UTC days; {i['paired_triggers']:,} with an N2 twin; "
         f"unit = pool (mean over the pool's triggers, {r['trim'] * 100:.1f} % trimmed mean over pools, 95 % pool bootstrap {r['boot']:,} draws seed {r['seed']}); "
         f"d = {r['d']} slots, h = {r['h']}; minimum {r['min_pools']} pools and {r['min_days']} days. Log markouts in log points × 100 (lp), "
         "net returns in percentage points.", "",
         f"**Outcome: {v['outcome']}** — P1 lower bound > 0: {v['P1_lo_above_0']}; P2 lower bound > 0: {v['P2_lo_above_0']}; "
         f"tradable (net 0.5 SOL at {r['h']}, lower bound > 0): **{v['tradable']}**; consistency same sign at "
         + ", ".join(f"{h}: {c}" for h, c in v["consistency_same_sign"].items()) + ".", "",
         "| measure | pools | trimmed mean (lp / pt) | 95 % pool CI | 99.9 % lower |", "|---|---|---|---|---|"]
    for h in (r["h"],) + tuple(r["consistency"]):
        pr, se = res["primary"][h], res["secondary"][h]
        L += [_row(f"P1 log F7 @ {h}", pr["P1_lg"]), _row(f"P2 log F7 − N2 @ {h}", pr["P2_diff"]), _row(f"net F7 (0.5 SOL) @ {h}", se["net"]),
              _row(f"N2 log @ {h}", se["n2_lg"]), _row(f"N2 net @ {h}", se["n2_net"])]
    L += ["", "Descriptive (not in the rule):", ""]
    pt = res["descriptive"][r["h"]]["per_trigger"]
    L.append("- per trigger (unweighted, trimmed; log × 100 or pt) at " + r["h"] + ": " + "; ".join(
        f"{k} {_c(s['mean'])} (n {s['n']:,}, {s['share_pos'] * 100:.0f} % > 0)" for k, s in pt.items() if s))
    if res["descriptive"].get("worst_day"):
        w = res["descriptive"]["worst_day"]
        L.append(f"- per day (pool means): {len(res['descriptive']['per_day'])} days; worst day {w['day']} log {_c(w['lg'])}")
    for key in ("regime", "farm"):
        for val, st in res["descriptive"][key].items():
            L.append(f"- {key} {val}: " + "; ".join(f"{c} {_c(s['mean'])} [{_c(s['lo'])}; {_c(s['hi'])}] ({s['pools']:,} pools)"
                                                     for c, s in st.items() if s))
    L.append("")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--markout", required=True)
    ap.add_argument("--run", help="markout-<run>.parquet only (default: every markout-*.parquet in the directory)")
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--out")
    a = ap.parse_args()
    if workers() > 1:
        _pool(workers())
    try:
        con = duckdb.connect()
        con.execute(f"SET threads TO {int(os.environ.get('LAB_WALLETS_THREADS', '2'))}; SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1GB')}'")
        load(con, a.markout, a.run)
        res = analyse(con, a.boot, log=lambda m: print(m, file=sys.stderr, flush=True))
        out = a.out or os.path.join(a.markout, "mechanism.md")
        with open(out, "w") as f:
            f.write(markdown(res, a.markout))
        with open(os.path.splitext(out)[0] + ".json", "w") as f:
            json.dump(res, f, indent=1, default=float)
        print(json.dumps({"out": out, "outcome": res["verdict"]["outcome"], "tradable": res["verdict"]["tradable"], "seconds": res["seconds"]}))
    finally:
        close_pool()


if __name__ == "__main__":
    main()
