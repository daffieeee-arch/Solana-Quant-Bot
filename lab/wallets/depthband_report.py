"""F7 depth-band test on the clean data (E49; research/prereg-f7-dieptebande-clean-2026-10-22.md): the exit-free NET
markout of the F7 pool_reversed triggers whose pool holds DEPTH_BAND SOL at our entry, the POOL as the unit.

E48 (lab/reports/2026-10-21-f7-kostenkloof.md §4) found on dev that the triggers entered with 100-150 SOL in the pool (the
trigger sale and what followed pushed the pool under the 150 SOL trigger threshold) have a net 5 m markout of +8.28 pt
per pool [+4.64; +11.83] while all triggers have -4.38; the band was chosen after seeing dev, so it is a hypothesis for
the unseen data. The depth is the entry state's SOL side (markout's depth_entry: b of a reversed pool), the net the
markout run's SIZE round trip against the real reserves plus the base network costs (markout's net), at d = the Clock
slots of RULE delay_ms per epoch (the run JSON's delay_slots_per_epoch) and h = H.

Primary, pre-set: P1 = the net markout at (d, H) over the pools of the band (per pool the mean over its band triggers,
TRIM-trimmed mean over pools, 95 % pool bootstrap: BOOT draws, seed 7). Outcome: CONFIRMED when the lower bound > 0;
REJECTED when the trimmed mean <= 0; INCONCLUSIVE otherwise (= not shown, no follow-up). NOT JUDGED below MIN_POOLS
pools or MIN_DAYS UTC days with a band trigger. Secondary, never changing the outcome: the same at CONSISTENCY horizons
(same sign as at H), the complement (triggers outside the band) and the other bands at H, the 99.9 % lower bound.
Descriptive: per band the log markout, the wiped share (net <= WIPED: the pool pays nothing), per day, per farm, per
creator class (markout's creator_prior_pools: E41's point-in-time class).

  python -m wallets.depthband_report --markout DIR --run NAME [--boot 10000] [--out DIR/depthband.md] [--d 2: dev descriptive only]
"""
import argparse
import json
import os
import sys
import time

import duckdb
import numpy as np

from .markout_report import BOOT, TRIM, _pool, close_pool, fmt, workers
from .mechanism_report import _lower, delay_case, pool_stat, trigger_stat  # noqa: F401 (trigger_stat reads table f)

# The pre-registered rule, in one place; test_rule_is_frozen holds the values. The band bounds are SOL in the pool at the
# entry state, lower bound inclusive, upper exclusive.
RULE = {"delay_ms": 600, "h": "5m", "consistency": ("1m",), "depth_band_sol": (100, 150), "size_sol": 0.5, "min_pools": 100, "min_days": 5,
        "boot": BOOT, "seed": 7, "trim": TRIM, "wiped": -0.999, "unit": "pool", "family": "F7", "segment": "pool_reversed"}
BANDS = ((0, 100), (100, 150), (150, 200), (200, 300), (300, 500), (500, None))
H = RULE["h"]
CONSISTENCY = RULE["consistency"]
MIN_POOLS, MIN_DAYS = RULE["min_pools"], RULE["min_days"]
FAMILY, SEGMENT = RULE["family"], RULE["segment"]
LO_SOL, HI_SOL = RULE["depth_band_sol"]
SOL = 10**9
LG = "ln(GREATEST(1 + gross, 0.001))"
CREATOR_CLASS = """CASE WHEN creator_prior_pools IS NULL THEN 'pool niet in de store' WHEN creator_prior_pools < 0 THEN 'creator onbekend (system-program)'
                        WHEN creator_prior_pools = 0 THEN 'eerste pool van de creator' WHEN creator_prior_pools < 10 THEN 'creator met 1-9 eerdere pools'
                        WHEN creator_prior_pools < 100 THEN 'creator met 10-99 eerdere pools' ELSE 'creator met >= 100 eerdere pools' END"""


def band_label(lo, hi):
    return f"{lo}-{hi}" if hi is not None else f">={lo}"


def band_case():
    return "CASE " + " ".join(f"WHEN depth_sol >= {lo}{'' if hi is None else f' AND depth_sol < {hi}'} THEN '{band_label(lo, hi)}'"
                             for lo, hi in BANDS) + " ELSE NULL END"


def load(con, markout_dir, run, d=None):
    """The markout rows of the rule's d (per epoch from the run JSON, or the constant --d), F7 pool_reversed only, with the
    depth band of the entry state; returns the d description."""
    path = os.path.join(markout_dir, f"markout-{run}.parquet")
    if not os.path.exists(path):
        raise SystemExit(f"no {path}")
    run_json = None
    jp = os.path.join(markout_dir, f"run-markout-{run}.json")
    if os.path.exists(jp):
        run_json = json.load(open(jp))
        if abs(float(run_json.get("size_sol", 0)) - RULE["size_sol"]) > 1e-12:
            raise SystemExit(f"the markout run was made at {run_json.get('size_sol')} SOL, the rule says {RULE['size_sol']}")
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{path}'").fetchall()]
    if "depth_entry" not in cols:
        raise SystemExit("the markout run has no depth_entry column (markout before E49): rerun wallets.markout")
    d_sql, d_info = delay_case(run_json, d)
    sel = "*" if "creator_prior_pools" in cols else "*, NULL::INTEGER AS creator_prior_pools"
    con.execute(f"""
      CREATE OR REPLACE TABLE m AS SELECT {sel} FROM read_parquet('{path}')
      WHERE h <> '' AND d = ({d_sql}) AND family = '{FAMILY}' AND segment = '{SEGMENT}'
      QUALIFY row_number() OVER (PARTITION BY venue, mint, pool, t, d, h ORDER BY cut, net IS NULL) = 1""")
    con.execute(f"""
      CREATE OR REPLACE TABLE f AS
      SELECT pool, mint, t, day, regime, farm, d, h, cut, gross, {LG} AS lg, net, net <= {RULE['wiped']} AS wiped, depth_entry / {SOL}.0 AS depth_sol,
             fee_bps, creator_prior_pools, {CREATOR_CLASS} AS creator_class
      FROM m WHERE net IS NOT NULL AND depth_entry IS NOT NULL""")
    con.execute(f"ALTER TABLE f ADD COLUMN band VARCHAR; UPDATE f SET band = {band_case()}")
    con.execute(f"ALTER TABLE f ADD COLUMN in_band BOOLEAN; UPDATE f SET in_band = depth_sol >= {LO_SOL} AND depth_sol < {HI_SOL}")
    con.execute("""
      CREATE OR REPLACE TABLE p AS
      SELECT pool, h, band, count(*) AS triggers, min(day) AS first_day, avg(net) AS net, avg(lg) AS lg, avg(gross) AS gross, avg(wiped::INT) AS wiped
      FROM f GROUP BY ALL""")
    return d_info


def _pool_table(con, name, where):
    """A per-pool table over the triggers of `where` (the pool's mean within that selection)."""
    con.execute(f"""CREATE OR REPLACE TABLE {name} AS SELECT pool, h, count(*) AS triggers, avg(net) AS net, avg(lg) AS lg, avg(wiped::INT) AS wiped
                    FROM f WHERE {where} GROUP BY ALL""")
    return name


def analyse(con, boot, d_info=None, log=lambda m: None):
    t0 = time.time()
    n_trig, n_pools, n_days = con.execute(f"SELECT count(*), count(DISTINCT pool), count(DISTINCT day) FROM f WHERE h = '{H}' AND in_band").fetchone()
    a_trig, a_pools, a_days = con.execute(f"SELECT count(*), count(DISTINCT pool), count(DISTINCT day) FROM f WHERE h = '{H}'").fetchone()
    ds = dict(con.execute("SELECT d, count(*) FROM f WHERE h = ? GROUP BY 1 ORDER BY 1", [H]).fetchall())
    res = {"rule": {k: (list(v) if isinstance(v, tuple) else v) for k, v in RULE.items()} | {"boot": boot, "d": d_info, "d_rows": {str(k): v for k, v in ds.items()}},
           "input": {"band_triggers": n_trig, "band_pools": n_pools, "band_days": n_days, "all_triggers": a_trig, "all_pools": a_pools, "all_days": a_days},
           "primary": {}, "secondary": {}, "bands": {}, "descriptive": {}}
    log(f"{n_trig:,} band triggers ({LO_SOL}-{HI_SOL} SOL at entry) in {n_pools:,} pools over {n_days} days, of {a_trig:,} F7 {SEGMENT} triggers in {a_pools:,} pools; d {ds}, h {H}")
    hs = (H,) + CONSISTENCY
    _pool_table(con, "pb", "in_band")
    _pool_table(con, "pc", "NOT in_band")
    for h in hs:
        res["primary"][h] = {"P1_net": pool_stat(con, h, "net", boot, table="pb")}
        res["secondary"][h] = {"band_lg": pool_stat(con, h, "lg", boot, table="pb"), "band_wiped": pool_stat(con, h, "wiped", boot, table="pb"),
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
    # every band at H and the consistency horizons (descriptive; the rule's band among them)
    for h in hs:
        res["bands"][h] = {}
        for lo, hi in BANDS:
            lab = band_label(lo, hi)
            _pool_table(con, "pd", f"band = '{lab}'")
            n = con.execute(f"SELECT count(*) FROM f WHERE h = '{h}' AND band = '{lab}'").fetchone()[0]
            res["bands"][h][lab] = {"triggers": n, "net": pool_stat(con, h, "net", boot, table="pd"), "lg": pool_stat(con, h, "lg", boot, table="pd"),
                                    "wiped": pool_stat(con, h, "wiped", boot, table="pd")}
    # descriptive within the band at H: per trigger, per day, farm, creator class
    pt = con.execute(f"SELECT count(*), avg(net), median(net), avg((net > 0)::INT), avg(wiped::INT) FROM f WHERE h = '{H}' AND in_band").fetchone()
    res["descriptive"]["per_trigger"] = {"n": pt[0], "net_mean": pt[1], "net_median": pt[2], "share_pos": pt[3], "wiped": pt[4]}
    days = con.execute(f"SELECT day, count(DISTINCT pool), avg(net), avg(wiped::INT) FROM f WHERE h = '{H}' AND in_band GROUP BY 1 ORDER BY 1").fetchall()
    res["descriptive"]["per_day"] = [{"day": r[0], "pools": r[1], "net": r[2], "wiped": r[3]} for r in days]
    if days:
        w = min(days, key=lambda r: r[2])
        res["descriptive"]["worst_day"] = {"day": w[0], "net": w[2]}
    for key, col in (("farm", "farm"), ("creator_class", "creator_class"), ("regime", "regime")):
        res["descriptive"][key] = {}
        for (val,) in con.execute(f"SELECT DISTINCT {col} FROM f WHERE in_band ORDER BY 1").fetchall():
            _pool_table(con, "pd", f"in_band AND {col} = '{val}'")
            res["descriptive"][key][str(val)] = {"net": pool_stat(con, H, "net", boot, table="pd"), "wiped": pool_stat(con, H, "wiped", boot, table="pd")}
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _c(x):
    return fmt(None if x is None else x * 100, sign=True)


def _row(label, s, extra=""):
    if not s:
        return f"| {label} | – | – | – | – |{extra}"
    return f"| {label} | {s['pools']:,} | {_c(s['mean'])} | [{_c(s['lo'])}; {_c(s['hi'])}] | {_c(s['lo_999'])} |{extra}"


def markdown(res, markout_dir):
    v, i, r = res["verdict"], res["input"], res["rule"]
    L = [f"## F7 depth-band test ({os.path.basename(markout_dir.rstrip('/'))})", "",
         f"Markouts {markout_dir}; band = {LO_SOL}-{HI_SOL} SOL in the pool at the entry state; {i['band_triggers']:,} band triggers in {i['band_pools']:,} pools over "
         f"{i['band_days']} UTC days (of {i['all_triggers']:,} F7 {SEGMENT} triggers in {i['all_pools']:,} pools); unit = pool (mean over the pool's band triggers, "
         f"{r['trim'] * 100:.1f} % trimmed mean over pools, 95 % pool bootstrap {r['boot']:,} draws seed {r['seed']}); d = {r['delay_ms']} ms through the Clock per epoch "
         f"({r['d']}; rows per d {r['d_rows']}), h = {r['h']}; net = {r['size_sol']} SOL round trip against the real reserves plus the base network costs, in "
         f"percentage points; minimum {r['min_pools']} pools and {r['min_days']} days.", "",
         f"**Outcome: {v['outcome']}** — P1 (net in the band at {r['h']}) lower bound > 0: {v['P1_lo_above_0']}; consistency same sign at "
         + ", ".join(f"{h}: {c}" for h, c in v["consistency_same_sign"].items()) + ".", "",
         "| measure | pools | trimmed mean (pt / lp) | 95 % pool CI | 99.9 % lower |", "|---|---|---|---|---|"]
    for h in (r["h"],) + tuple(r["consistency"]):
        pr, se = res["primary"][h], res["secondary"][h]
        L += [_row(f"P1 net in band @ {h}", pr["P1_net"]), _row(f"log markout in band @ {h}", se["band_lg"]), _row(f"wiped share in band @ {h}", se["band_wiped"]),
              _row(f"net outside the band @ {h}", se["complement_net"]), _row(f"log markout outside @ {h}", se["complement_lg"]), _row(f"wiped share outside @ {h}", se["complement_wiped"])]
    L += ["", f"Per band at {r['h']} (descriptive; the rule's band in bold):", "", "| band (SOL at entry) | triggers | pools | net (pt) | 95 % pool CI | log (lp) | wiped |", "|---|---|---|---|---|---|---|"]
    for lab, b in res["bands"][r["h"]].items():
        n, w = b["net"], b["wiped"]
        name = f"**{lab}**" if lab == band_label(LO_SOL, HI_SOL) else lab
        L.append(f"| {name} | {b['triggers']:,} | {n['pools'] if n else 0:,} | {_c(n['mean']) if n else '–'} | " + (f"[{_c(n['lo'])}; {_c(n['hi'])}]" if n else "–")
                 + f" | {_c(b['lg']['mean']) if b['lg'] else '–'} | {fmt(w['mean'] * 100) if w else '–'} % |")
    pt = res["descriptive"]["per_trigger"]
    L += ["", "Descriptive within the band (not in the rule):", "",
          f"- per trigger (unweighted) at {r['h']}: n {pt['n']:,}, net mean {_c(pt['net_mean'])}, median {_c(pt['net_median'])}, {(pt['share_pos'] or 0) * 100:.0f} % > 0, wiped {(pt['wiped'] or 0) * 100:.1f} %"]
    if res["descriptive"].get("worst_day"):
        w = res["descriptive"]["worst_day"]
        L.append(f"- per day (pool means): {len(res['descriptive']['per_day'])} days; worst day {w['day']} net {_c(w['net'])}")
    for key in ("farm", "creator_class", "regime"):
        for val, st in res["descriptive"][key].items():
            n, w = st["net"], st["wiped"]
            if n:
                L.append(f"- {key} {val}: net {_c(n['mean'])} [{_c(n['lo'])}; {_c(n['hi'])}] ({n['pools']:,} pools), wiped {fmt(w['mean'] * 100) if w else '–'} %")
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
        out = a.out or os.path.join(a.markout, "depthband.md")
        with open(out, "w") as f:
            f.write(markdown(res, a.markout))
        with open(os.path.splitext(out)[0] + ".json", "w") as f:
            json.dump(res, f, indent=1, default=float)
        print(json.dumps({"out": out, "outcome": res["verdict"]["outcome"], "band_pools": res["input"]["band_pools"], "band_triggers": res["input"]["band_triggers"],
                          "P1_net": res["primary"][H]["P1_net"], "seconds": res["seconds"]}, default=float))
    finally:
        close_pool()


if __name__ == "__main__":
    main()
