"""F7 paid-flow test on the clean data (C3 / E54; research/prereg-f7-betaalde-kopers-clean-2026-10-21.md): the exit-free NET
markout of the F7 pool_reversed triggers preceded by paid token buys, the POOL as the unit.

Strategy-search workflow 1 (E54) found on dev that the triggers whose pool saw, in the 60 s before the trigger, at least
MIN_PAID distinct buyers paying >= PAID_MIN_LAMPORTS of priority fee + Jito tip have a net 5 m markout of about +11.8 pt per
pool [+6.7; +17.2] against -7.2 for the rest (the report's reading: a bump or promotion campaign is running); the arm was
chosen after seeing dev, so it is a hypothesis for the unseen data. The count is markout's paid_buyers_60s (the window
through the Clock per epoch, the trigger slot included; fees are transaction-level, the payer is fee_payer else trader),
the net the markout run's SIZE round trip against the real reserves plus the base network costs (markout's net), at d = the
Clock slots of RULE delay_ms per epoch (the run JSON's delay_slots_per_epoch) and h = H.

Primary, pre-set: P1 = the net markout at (d, H) over the pools of the arm (per pool the mean over its arm triggers,
TRIM-trimmed mean over pools, 95 % pool bootstrap: BOOT draws, seed 7). Outcome: CONFIRMED when the lower bound > 0;
REJECTED when the trimmed mean <= 0; INCONCLUSIVE otherwise (= not shown, no follow-up). NOT JUDGED below MIN_POOLS
pools or MIN_DAYS UTC days with an arm trigger. Secondary, never changing the outcome: the same at CONSISTENCY horizons
(same sign as at H), the complement (triggers outside the arm), the payer-count classes at H, the 99.9 % lower bound.
Descriptive: the day blocks (first half of the days against the rest), the workflow's fee band on paid_max_60s (REPORT_BAND) against the arm, the arm and its complement per depth band
of the entry state (E49's bands, when depth_entry is present), per day, per farm, per regime, per creator class.

  python -m wallets.paidflow_report --markout DIR --run NAME [--boot 10000] [--out DIR/paidflow.md] [--d 2: dev descriptive only]
"""
import argparse
import json
import os
import sys
import time

import duckdb
import numpy as np

from .depthband_report import BANDS as DEPTH_BANDS, CREATOR_CLASS, band_case, band_label
from .markout_report import BOOT, TRIM, _pool, close_pool, fmt, workers
from .mechanism_report import _lower, delay_case, pool_stat  # noqa: F401

# The pre-registered rule, in one place; test_rule_is_frozen holds the values. The arm: at least min_paid_buyers distinct
# payers of token-buy transactions paying >= paid_min_lamports (priority fee + tip) in the paid_window_ms before the trigger.
RULE = {"delay_ms": 600, "h": "5m", "consistency": ("1m",), "min_paid_buyers": 2, "paid_min_lamports": 5_000, "paid_window_ms": 60_000, "size_sol": 0.5,
        "min_pools": 100, "min_days": 5, "boot": BOOT, "seed": 7, "trim": TRIM, "wiped": -0.999, "unit": "pool", "family": "F7", "segment": "pool_reversed"}
CLASSES = ((0, 0), (1, 1), (2, 3), (4, 8), (9, None))   # payer-count classes (descriptive; the arm = classes from min_paid_buyers up)
REPORT_BAND = (5_000, 500_000)                           # the workflow's band on the highest paid buy, lamports (descriptive)
H = RULE["h"]
CONSISTENCY = RULE["consistency"]
MIN_POOLS, MIN_DAYS = RULE["min_pools"], RULE["min_days"]
FAMILY, SEGMENT = RULE["family"], RULE["segment"]
MIN_PAID = RULE["min_paid_buyers"]
SOL = 10**9
LG = "ln(GREATEST(1 + gross, 0.001))"


def class_label(lo, hi):
    return f"{lo}" if lo == hi else (f"{lo}-{hi}" if hi is not None else f">={lo}")


def class_case():
    return "CASE " + " ".join(f"WHEN paid_buyers_60s >= {lo}{'' if hi is None else f' AND paid_buyers_60s <= {hi}'} THEN '{class_label(lo, hi)}'"
                             for lo, hi in CLASSES) + " ELSE NULL END"


def load(con, markout_dir, run, d=None):
    """The markout rows of the rule's d (per epoch from the run JSON, or the constant --d), F7 pool_reversed only, with the
    arm flag, the payer-count class, the workflow's band and the depth band; returns the d description."""
    path = os.path.join(markout_dir, f"markout-{run}.parquet")
    if not os.path.exists(path):
        raise SystemExit(f"no {path}")
    run_json = None
    jp = os.path.join(markout_dir, f"run-markout-{run}.json")
    if os.path.exists(jp):
        run_json = json.load(open(jp))
        if abs(float(run_json.get("size_sol", 0)) - RULE["size_sol"]) > 1e-12:
            raise SystemExit(f"the markout run was made at {run_json.get('size_sol')} SOL, the rule says {RULE['size_sol']}")
        for key in ("paid_window_ms", "paid_min_lamports"):
            if run_json.get(key) is not None and int(run_json[key]) != RULE[key]:
                raise SystemExit(f"the markout run counted paid buyers with {key} {run_json[key]}, the rule says {RULE[key]}")
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{path}'").fetchall()]
    if "paid_buyers_60s" not in cols:
        raise SystemExit("the markout run has no paid_buyers_60s column (markout before E54): rerun wallets.markout")
    d_sql, d_info = delay_case(run_json, d)
    sel = "*" + ("" if "creator_prior_pools" in cols else ", NULL::INTEGER AS creator_prior_pools") + ("" if "depth_entry" in cols else ", NULL::BIGINT AS depth_entry") \
        + ("" if "paid_max_60s" in cols else ", NULL::BIGINT AS paid_max_60s")
    con.execute(f"""
      CREATE OR REPLACE TABLE m AS SELECT {sel} FROM read_parquet('{path}')
      WHERE h <> '' AND d = ({d_sql}) AND family = '{FAMILY}' AND segment = '{SEGMENT}'
      QUALIFY row_number() OVER (PARTITION BY venue, mint, pool, t, d, h ORDER BY cut, net IS NULL) = 1""")
    con.execute(f"""
      CREATE OR REPLACE TABLE f AS
      SELECT pool, mint, t, day, regime, farm, d, h, cut, gross, {LG} AS lg, net, net <= {RULE['wiped']} AS wiped, paid_buyers_60s, paid_max_60s,
             paid_buyers_60s >= {MIN_PAID} AS in_arm, {class_case()} AS cls,
             paid_max_60s >= {REPORT_BAND[0]} AND paid_max_60s < {REPORT_BAND[1]} AS report_band,
             depth_entry / {SOL}.0 AS depth_sol, creator_prior_pools, {CREATOR_CLASS} AS creator_class
      FROM m WHERE net IS NOT NULL AND paid_buyers_60s IS NOT NULL""")
    con.execute(f"ALTER TABLE f ADD COLUMN band VARCHAR; UPDATE f SET band = {band_case()}")
    con.execute("""
      CREATE OR REPLACE TABLE p AS
      SELECT pool, h, in_arm, count(*) AS triggers, min(day) AS first_day, avg(net) AS net, avg(lg) AS lg, avg(gross) AS gross, avg(wiped::INT) AS wiped
      FROM f GROUP BY ALL""")
    return d_info


def _pool_table(con, name, where):
    """A per-pool table over the triggers of `where` (the pool's mean within that selection)."""
    con.execute(f"""CREATE OR REPLACE TABLE {name} AS SELECT pool, h, count(*) AS triggers, avg(net) AS net, avg(lg) AS lg, avg(wiped::INT) AS wiped
                    FROM f WHERE {where} GROUP BY ALL""")
    return name


def _three(con, h, boot, table):
    return {"net": pool_stat(con, h, "net", boot, table=table), "lg": pool_stat(con, h, "lg", boot, table=table), "wiped": pool_stat(con, h, "wiped", boot, table=table)}


def analyse(con, boot, d_info=None, log=lambda m: None):
    t0 = time.time()
    n_trig, n_pools, n_days = con.execute(f"SELECT count(*), count(DISTINCT pool), count(DISTINCT day) FROM f WHERE h = '{H}' AND in_arm").fetchone()
    a_trig, a_pools, a_days = con.execute(f"SELECT count(*), count(DISTINCT pool), count(DISTINCT day) FROM f WHERE h = '{H}'").fetchone()
    ds = dict(con.execute("SELECT d, count(*) FROM f WHERE h = ? GROUP BY 1 ORDER BY 1", [H]).fetchall())
    res = {"rule": {k: (list(v) if isinstance(v, tuple) else v) for k, v in RULE.items()} | {"boot": boot, "d": d_info, "d_rows": {str(k): v for k, v in ds.items()}},
           "input": {"arm_triggers": n_trig, "arm_pools": n_pools, "arm_days": n_days, "all_triggers": a_trig, "all_pools": a_pools, "all_days": a_days},
           "primary": {}, "secondary": {}, "classes": {}, "descriptive": {}}
    log(f"{n_trig:,} arm triggers (>= {MIN_PAID} paying buyers in the {RULE['paid_window_ms'] // 1000} s before) in {n_pools:,} pools over {n_days} days, "
        f"of {a_trig:,} F7 {SEGMENT} triggers in {a_pools:,} pools; d {ds}, h {H}")
    hs = (H,) + CONSISTENCY
    _pool_table(con, "pa", "in_arm")
    _pool_table(con, "pc", "NOT in_arm")
    for h in hs:
        res["primary"][h] = {"P1_net": pool_stat(con, h, "net", boot, table="pa")}
        res["secondary"][h] = {"arm_lg": pool_stat(con, h, "lg", boot, table="pa"), "arm_wiped": pool_stat(con, h, "wiped", boot, table="pa"),
                               "complement_net": pool_stat(con, h, "net", boot, table="pc"), "complement_lg": pool_stat(con, h, "lg", boot, table="pc"),
                               "complement_wiped": pool_stat(con, h, "wiped", boot, table="pc")}
        s = res["primary"][h]["P1_net"]
        log(f"h {h}: P1 net " + (f"{fmt(s['mean'] * 100, sign=True)} [{fmt(s['lo'] * 100, sign=True)}; {fmt(s['hi'] * 100, sign=True)}] over {s['pools']:,} pools" if s else "–")
            + "; complement net " + (f"{fmt(res['secondary'][h]['complement_net']['mean'] * 100, sign=True)}" if res["secondary"][h]["complement_net"] else "–"))
    p1 = res["primary"][H]["P1_net"]
    judged = n_pools >= MIN_POOLS and n_days >= MIN_DAYS and p1 is not None and p1["lo"] is not None
    if not judged:
        outcome = "NOT JUDGED"
    elif p1["lo"] > 0:
        outcome = "CONFIRMED"
    elif p1["mean"] <= 0:
        outcome = "REJECTED"
    else:
        outcome = "INCONCLUSIVE"
    cons = {}
    for h in CONSISTENCY:
        q = res["primary"][h]["P1_net"]
        cons[h] = None if not judged or q is None else bool(np.sign(q["mean"]) == np.sign(p1["mean"]))
    res["verdict"] = {"outcome": outcome, "judged": judged, "P1_lo_above_0": None if not judged else p1["lo"] > 0, "consistency_same_sign": cons}
    # the payer-count classes at H and the consistency horizons (descriptive; the arm = the classes from MIN_PAID up)
    for h in hs:
        res["classes"][h] = {}
        for lo, hi in CLASSES:
            lab = class_label(lo, hi)
            _pool_table(con, "pd", f"cls = '{lab}'")
            n = con.execute(f"SELECT count(*) FROM f WHERE h = '{h}' AND cls = '{lab}'").fetchone()[0]
            res["classes"][h][lab] = {"triggers": n} | _three(con, h, boot, "pd")
    # descriptive at H: per trigger, per day, farm, creator class, regime; the workflow's band against the arm; the depth bands
    pt = con.execute(f"SELECT count(*), avg(net), median(net), avg((net > 0)::INT), avg(wiped::INT) FROM f WHERE h = '{H}' AND in_arm").fetchone()
    res["descriptive"]["per_trigger"] = {"n": pt[0], "net_mean": pt[1], "net_median": pt[2], "share_pos": pt[3], "wiped": pt[4]}
    days = con.execute(f"SELECT day, count(DISTINCT pool), avg(net), avg(wiped::INT) FROM f WHERE h = '{H}' AND in_arm GROUP BY 1 ORDER BY 1").fetchall()
    res["descriptive"]["per_day"] = [{"day": r[0], "pools": r[1], "net": r[2], "wiped": r[3]} for r in days]
    if days:
        w = min(days, key=lambda r: r[2])
        res["descriptive"]["worst_day"] = {"day": w[0], "net": w[2]}
    for key, col in (("farm", "farm"), ("creator_class", "creator_class"), ("regime", "regime")):
        res["descriptive"][key] = {}
        for (val,) in con.execute(f"SELECT DISTINCT {col} FROM f WHERE in_arm ORDER BY 1").fetchall():
            _pool_table(con, "pd", f"in_arm AND {col} = '{val}'")
            res["descriptive"][key][str(val)] = {"net": pool_stat(con, H, "net", boot, table="pd"), "wiped": pool_stat(con, H, "wiped", boot, table="pd")}
    # day blocks (descriptive consistency, as E48's day blocks): the first half of the UTC days present against the rest
    days_all = [r[0] for r in con.execute(f"SELECT DISTINCT day FROM f WHERE h = '{H}' ORDER BY 1").fetchall()]
    res["descriptive"]["day_blocks"] = {}
    if len(days_all) >= 2:
        cut = (len(days_all) + 1) // 2
        for lab, block in (("first", days_all[:cut]), ("second", days_all[cut:])):
            lit = ", ".join(f"'{d}'" for d in block)
            out = {"days": block}
            for side, where in (("arm", f"in_arm AND day IN ({lit})"), ("complement", f"NOT in_arm AND day IN ({lit})")):
                _pool_table(con, "pd", where)
                n = con.execute(f"SELECT count(*) FROM f WHERE h = '{H}' AND {where}").fetchone()[0]
                out[side] = {"triggers": n, "net": pool_stat(con, H, "net", boot, table="pd"), "wiped": pool_stat(con, H, "wiped", boot, table="pd")}
            res["descriptive"]["day_blocks"][lab] = out
    res["descriptive"]["report_band"] = {}
    for lab, where in (("band & arm", "report_band AND in_arm"), ("band & not arm", "report_band AND NOT in_arm"), ("arm & not band", "in_arm AND NOT report_band"),
                       ("neither", "NOT report_band AND NOT in_arm")):
        _pool_table(con, "pd", where)
        n = con.execute(f"SELECT count(*) FROM f WHERE h = '{H}' AND {where}").fetchone()[0]
        res["descriptive"]["report_band"][lab] = {"triggers": n, "net": pool_stat(con, H, "net", boot, table="pd"), "wiped": pool_stat(con, H, "wiped", boot, table="pd")}
    res["descriptive"]["depth"] = {}
    if con.execute("SELECT count(depth_sol) FROM f").fetchone()[0]:
        for lo, hi in DEPTH_BANDS:
            lab = band_label(lo, hi)
            out = {}
            for side, where in (("arm", f"band = '{lab}' AND in_arm"), ("complement", f"band = '{lab}' AND NOT in_arm")):
                _pool_table(con, "pd", where)
                n = con.execute(f"SELECT count(*) FROM f WHERE h = '{H}' AND {where}").fetchone()[0]
                out[side] = {"triggers": n, "net": pool_stat(con, H, "net", boot, table="pd"), "wiped": pool_stat(con, H, "wiped", boot, table="pd")}
            res["descriptive"]["depth"][lab] = out
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _c(x):
    return fmt(None if x is None else x * 100, sign=True)


def _row(label, s, extra=""):
    if not s:
        return f"| {label} | – | – | – | – |{extra}"
    return f"| {label} | {s['pools']:,} | {_c(s['mean'])} | [{_c(s['lo'])}; {_c(s['hi'])}] | {_c(s['lo_999'])} |{extra}"


def _ci(n):
    return f"{_c(n['mean'])} [{_c(n['lo'])}; {_c(n['hi'])}] ({n['pools']:,} pools)" if n else "–"


def markdown(res, markout_dir):
    v, i, r = res["verdict"], res["input"], res["rule"]
    L = [f"## F7 paid-flow test ({os.path.basename(markout_dir.rstrip('/'))})", "",
         f"Markouts {markout_dir}; arm = at least {r['min_paid_buyers']} distinct buyers paying >= {r['paid_min_lamports']:,} lamports (priority fee + tip) on token buys "
         f"in the {r['paid_window_ms'] // 1000} s before the trigger (trigger slot included); {i['arm_triggers']:,} arm triggers in {i['arm_pools']:,} pools over "
         f"{i['arm_days']} UTC days (of {i['all_triggers']:,} F7 {SEGMENT} triggers in {i['all_pools']:,} pools); unit = pool (mean over the pool's arm triggers, "
         f"{r['trim'] * 100:.1f} % trimmed mean over pools, 95 % pool bootstrap {r['boot']:,} draws seed {r['seed']}); d = {r['delay_ms']} ms through the Clock per epoch "
         f"({r['d']}; rows per d {r['d_rows']}), h = {r['h']}; net = {r['size_sol']} SOL round trip against the real reserves plus the base network costs, in "
         f"percentage points; minimum {r['min_pools']} pools and {r['min_days']} days.", "",
         f"**Outcome: {v['outcome']}** — P1 (net in the arm at {r['h']}) lower bound > 0: {v['P1_lo_above_0']}; consistency same sign at "
         + ", ".join(f"{h}: {c}" for h, c in v["consistency_same_sign"].items()) + ".", "",
         "| measure | pools | trimmed mean (pt / lp) | 95 % pool CI | 99.9 % lower |", "|---|---|---|---|---|"]
    for h in (r["h"],) + tuple(r["consistency"]):
        pr, se = res["primary"][h], res["secondary"][h]
        L += [_row(f"P1 net in arm @ {h}", pr["P1_net"]), _row(f"log markout in arm @ {h}", se["arm_lg"]), _row(f"wiped share in arm @ {h}", se["arm_wiped"]),
              _row(f"net outside the arm @ {h}", se["complement_net"]), _row(f"log markout outside @ {h}", se["complement_lg"]), _row(f"wiped share outside @ {h}", se["complement_wiped"])]
    L += ["", f"Per payer-count class at {r['h']} (descriptive; the arm's classes in bold):", "", "| paying buyers | triggers | pools | net (pt) | 95 % pool CI | log (lp) | wiped |", "|---|---|---|---|---|---|---|"]
    for lab, b in res["classes"][r["h"]].items():
        n, w = b["net"], b["wiped"]
        lo = int(lab.split("-")[0].lstrip(">="))
        name = f"**{lab}**" if lo >= MIN_PAID else lab
        L.append(f"| {name} | {b['triggers']:,} | {n['pools'] if n else 0:,} | {_c(n['mean']) if n else '–'} | " + (f"[{_c(n['lo'])}; {_c(n['hi'])}]" if n else "–")
                 + f" | {_c(b['lg']['mean']) if b['lg'] else '–'} | {fmt(w['mean'] * 100) if w else '–'} % |")
    pt = res["descriptive"]["per_trigger"]
    L += ["", "Descriptive (not in the rule):", "",
          f"- per trigger (unweighted) in the arm at {r['h']}: n {pt['n']:,}, net mean {_c(pt['net_mean'])}, median {_c(pt['net_median'])}, {(pt['share_pos'] or 0) * 100:.0f} % > 0, wiped {(pt['wiped'] or 0) * 100:.1f} %"]
    if res["descriptive"].get("worst_day"):
        w = res["descriptive"]["worst_day"]
        L.append(f"- per day (pool means): {len(res['descriptive']['per_day'])} days; worst day {w['day']} net {_c(w['net'])}")
    for key in ("farm", "creator_class", "regime"):
        for val, st in res["descriptive"][key].items():
            n, w = st["net"], st["wiped"]
            if n:
                L.append(f"- {key} {val}: net {_ci(n)}, wiped {fmt(w['mean'] * 100) if w else '–'} %")
    for lab, st in res["descriptive"]["day_blocks"].items():
        L.append(f"- day block {lab} ({st['days'][0]} … {st['days'][-1]}, {len(st['days'])} days): arm {st['arm']['triggers']:,} triggers, net {_ci(st['arm']['net'])}; complement {st['complement']['triggers']:,} triggers, net {_ci(st['complement']['net'])}")
    for lab, st in res["descriptive"]["report_band"].items():
        L.append(f"- workflow band [{REPORT_BAND[0]:,}, {REPORT_BAND[1]:,}) on the highest paid buy, {lab}: {st['triggers']:,} triggers, net {_ci(st['net'])}")
    for lab, st in res["descriptive"]["depth"].items():
        L.append(f"- depth {lab} SOL at entry: arm {st['arm']['triggers']:,} triggers, net {_ci(st['arm']['net'])}; complement {st['complement']['triggers']:,} triggers, net {_ci(st['complement']['net'])}")
    L.append("")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--markout", required=True)
    ap.add_argument("--run", required=True, help="markout-<run>.parquet and run-markout-<run>.json in --markout")
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--out")
    ap.add_argument("--d", type=int, help="a constant d instead of the run JSON's per-epoch delay (dev descriptives only)")
    a = ap.parse_args()
    if workers() > 1:
        _pool(workers())
    try:
        con = duckdb.connect()
        con.execute(f"SET threads TO {int(os.environ.get('LAB_WALLETS_THREADS', '2'))}; SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1GB')}'")
        d_info = load(con, a.markout, a.run, a.d)
        res = analyse(con, a.boot, d_info, log=lambda m: print(m, file=sys.stderr, flush=True))
        out = a.out or os.path.join(a.markout, "paidflow.md")
        with open(out, "w") as f:
            f.write(markdown(res, a.markout))
        with open(os.path.splitext(out)[0] + ".json", "w") as f:
            json.dump(res, f, indent=1, default=float)
        print(json.dumps({"out": out, "outcome": res["verdict"]["outcome"], "arm_pools": res["input"]["arm_pools"], "arm_triggers": res["input"]["arm_triggers"],
                          "P1_net": res["primary"][H]["P1_net"], "seconds": res["seconds"]}, default=float))
    finally:
        close_pool()


if __name__ == "__main__":
    main()
