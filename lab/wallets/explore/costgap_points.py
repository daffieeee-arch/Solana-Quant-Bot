"""F7 cost-gap decomposition on dev, in points of the stake (additive waterfall per trigger; wipe-outs are -100 pt, not
-690 lp), E45 unit: per-pool mean of the pool's triggers, 0.5 % trimmed mean over pools, pool bootstrap (BOOT, seed 7).
mech - delay - fee_in - imp_in - fee_out - imp_out - network = net, exactly per trigger."""
import json, math, os, sys, time
import duckdb, numpy as np
sys.path.insert(0, "/home/chupa/Solana-project/worktrees/lab-research/lab")
from wallets.markout_report import trimmed_mean, day_ci, BOOT

LAB = "/home/chupa/Solana-project/data-old-faithful-one/lab"
SP = sys.argv[1]  # the directory with legs-<epoch>.parquet (costgap_legs.py); the json lands there too
MK = f"{LAB}/wallets/markout-v1-txdelay-v4/markout-f7rev-txdelay.parquet"
RJ = json.load(open(f"{LAB}/wallets/markout-v1-txdelay-v4/run-markout-f7rev-txdelay.json"))
ROUTE = f"{LAB}/backtests/route-ii-full-547f422/entry-rows/part-000000.parquet"
SOL = 10**9
S = RJ["size_sol"]          # SOL
C = RJ["per_order_lamports"] / SOL
TIP = 0.0001
DS = [0, 1, 2, 3, 2001]
HS = ["1m", "5m"]
SIZES = [0.1, 0.25, 0.5]
boot = int(os.environ.get("BOOT", BOOT))
t0 = time.time()
con = duckdb.connect()
con.execute("SET memory_limit='1500MB'; SET threads=2")
con.execute(f"""CREATE TABLE mk AS SELECT pool, t, day, d, h, mid, gross, net, impact FROM '{MK}'
                WHERE family = 'F7' AND segment = 'pool_reversed' AND d IN (0, 1, 2, 3, 1000, 2001) AND h IN ('', '1m', '5m')""")
con.execute("CREATE TABLE ent AS SELECT pool, t, day, d, mid, impact FROM mk WHERE h = ''")
con.execute("CREATE TABLE ref AS SELECT pool, t, max(CASE WHEN d = 1000 THEN mid END) AS mid_trig, max(CASE WHEN d = 0 THEN mid END) AS mid_0 FROM ent GROUP BY ALL")
legs = f"{SP}/legs-*.parquet"
con.execute(f"""CREATE TABLE fee AS SELECT pool, slot AS t, arg_min(lp_bps + protocol_bps + creator_bps + COALESCE(cashback_bps, 0), tx_index) AS bps FROM '{legs}' GROUP BY ALL""")
con.execute(f"""CREATE TABLE eng AS SELECT pool, t, bool_or(entry_failed) AS failed FROM '{ROUTE}' WHERE entry = 'R' AND variant = 'tp4_T600' GROUP BY ALL""")
con.execute(f"""CREATE TABLE tr AS
  SELECT e.pool, e.t, m.day, e.d, m.h, f.bps / 10000.0 AS phi, g.failed,
         m.mid / r.mid_trig - 1 AS mech, m.gross AS gross, m.net AS net, e.impact AS impact,
         ln(e.mid / r.mid_trig) AS dl_trig, ln(e.mid / r.mid_0) AS dl_0,
         {S} / ((1 + e.impact) * (1 - f.bps / 10000.0) - 1) AS b_sol
  FROM ent e JOIN mk m ON m.pool = e.pool AND m.t = e.t AND m.d = e.d AND m.h <> ''
  JOIN ref r ON r.pool = e.pool AND r.t = e.t JOIN fee f ON f.pool = e.pool AND f.t = e.t
  LEFT JOIN eng g ON g.pool = e.pool AND g.t = e.t
  WHERE e.d IN (0, 1, 2, 3, 2001) AND e.mid > 0 AND r.mid_trig > 0 AND r.mid_0 > 0 AND m.mid > 0 AND m.gross IS NOT NULL AND m.net IS NOT NULL AND e.impact IS NOT NULL""")
con.execute(f"""CREATE TABLE w AS SELECT *,
    mech - gross AS delay,
    (1 + gross) * phi AS fee_in,
    (1 + gross) * (1 - phi) * (1 - 1 / (1 + {S} / b_sol)) AS imp_in,
    (1 + gross) / (1 + impact) AS v_in,
    (1 + gross) / (1 + impact) * phi / (1 + phi) AS fee_out,
    (1 + net) * (1 + 2 * {C} / {S}) AS back_s,
    (1 + gross) / (1 + impact) / (1 + phi) - (1 + net) * (1 + 2 * {C} / {S}) AS imp_out,
    (1 + net) * 2 * {C} / {S} AS network,
    CASE WHEN net <= -0.999 THEN 'wiped' WHEN net < -0.5 AND gross > -0.5 THEN 'exit_dry' WHEN net < -0.5 THEN 'price_crash' ELSE 'normal' END AS cls
  FROM tr""")
for s in SIZES:
    c = int(s * 100)
    con.execute(f"""ALTER TABLE w ADD COLUMN net_{c} DOUBLE; UPDATE w SET net_{c} = CASE WHEN back_s <= 0 THEN -1 ELSE
        ((1 + gross) * (1 - phi) / (1 + {s} / b_sol)) / ((1 + phi) * (1 + (v_in / ((1 + phi) * back_s) - 1) * ({s} / {S}))) / (1 + 2 * {C} / {s}) - 1 END""")
chk = con.execute("SELECT max(abs(mech - delay - fee_in - imp_in - fee_out - imp_out - network - net)), max(abs(net_50 - net)) FROM w").fetchone()


def pm(where, cols):
    return con.execute(f"SELECT pool, {', '.join(f'avg({c}) AS {c}' for c in cols)} FROM w WHERE {where} GROUP BY pool").fetchnumpy()


def stat(arr, pools, ci=True):
    x = np.asarray(arr, dtype=float); ok = ~np.isnan(x); x, p = x[ok], np.asarray(pools)[ok]
    if len(x) == 0:
        return None
    out = {"pools": int(len(x)), "mean": float(trimmed_mean(x))}
    if ci:
        out["lo"], out["hi"] = day_ci(x, p, boot, seed=7)
    return out


def ptm(where, col):
    return con.execute(f"SELECT avg({col}) FROM w WHERE {where}").fetchone()[0]


TERMS = ["mech", "delay", "fee_in", "imp_in", "fee_out", "imp_out", "network", "net", "gross", "dl_trig", "dl_0", "b_sol", "net_10", "net_25", "net_50"]
res = {"check_identity_max": chk, "n": con.execute("SELECT count(*), count(DISTINCT pool), count(DISTINCT day) FROM w WHERE d = 2 AND h = '5m'").fetchone(),
       "K3_R": con.execute("SELECT count(*), sum(failed::int) FROM eng").fetchone(), "decomp": {}, "classes": {}, "depth": {}, "failed": {}, "guard": {}}
for h in HS:
    for d in DS:
        P = pm(f"d = {d} AND h = '{h}'", TERMS)
        res["decomp"][f"d{d}_{h}"] = {c: stat(P[c], P["pool"], ci=(d == 2 or c in ("delay", "net", "mech"))) for c in TERMS}
        res["decomp"][f"d{d}_{h}"]["per_trigger"] = {c: ptm(f"d = {d} AND h = '{h}'", c) for c in TERMS}
    # exit classes at d 2: share of triggers, and each class's contribution to the pool-unit net and imp_out
    for cls in ("normal", "exit_dry", "price_crash", "wiped"):
        P = con.execute(f"""SELECT pool, avg(CASE WHEN cls = '{cls}' THEN net ELSE 0 END) AS net_c, avg(CASE WHEN cls = '{cls}' THEN imp_out ELSE 0 END) AS imp_c,
                            avg((cls = '{cls}')::int) AS share FROM w WHERE d = 2 AND h = '{h}' GROUP BY pool""").fetchnumpy()
        res["classes"][f"{h}_{cls}"] = {"share_triggers": ptm(f"d = 2 AND h = '{h}'", f"(cls = '{cls}')::int"), "share_pool": stat(P["share"], P["pool"], ci=False),
                                        "net_contrib": stat(P["net_c"], P["pool"], ci=False), "imp_out_contrib": stat(P["imp_c"], P["pool"], ci=False),
                                        "net_within": ptm(f"d = 2 AND h = '{h}' AND cls = '{cls}'", "net"), "gross_within": ptm(f"d = 2 AND h = '{h}' AND cls = '{cls}'", "gross")}
BANDS = [(0, 100), (100, 150), (150, 200), (200, 300), (300, 500), (500, 1e9)]
for h in HS:
    for lo, hi in BANDS:
        P = pm(f"d = 2 AND h = '{h}' AND b_sol >= {lo} AND b_sol < {hi}", ["net_10", "net_25", "net_50", "gross", "mech", "delay", "imp_out"])
        res["depth"][f"{h}_{lo}-{hi}"] = {c: stat(P[c], P["pool"], ci=(c in ("net_10", "net_50"))) for c in ["net_10", "net_25", "net_50", "gross", "mech", "delay", "imp_out"]}
    res["depth"][f"{h}_quantiles"] = con.execute(f"SELECT quantile_cont(b_sol, [0.1, 0.25, 0.5, 0.75, 0.9]) FROM w WHERE d = 2 AND h = '{h}'").fetchone()[0]
for h in HS:
    for lab, where in (("filled", "failed = false"), ("failed", "failed = true")):
        P = pm(f"d = 2 AND h = '{h}' AND {where}", ["net", "gross", "mech", "delay", "dl_0", "net_10"])
        res["failed"][f"{h}_{lab}"] = {c: stat(P[c], P["pool"], ci=(c in ("net", "dl_0"))) for c in ["net", "gross", "mech", "delay", "dl_0", "net_10"]}
    P = pm(f"d = 2 AND h = '{h}' AND failed IS NOT NULL", ["net"])
    res["failed"][f"{h}_all_K3"] = stat(P["net"], P["pool"])
res["failed"]["share"] = con.execute("SELECT avg(failed::int) FROM w WHERE d = 2 AND h = '5m' AND failed IS NOT NULL").fetchone()[0]
for h in HS:
    for refc in ("dl_trig", "dl_0"):
        for X in (0.005, 0.01, 0.02, 0.03, 0.05):
            for d in (2, 3):
                P = pm(f"d = {d} AND h = '{h}' AND {refc} <= {math.log(1 + X)}", ["net", "net_10"])
                kept = con.execute(f"SELECT avg(({refc} <= {math.log(1 + X)})::int) FROM w WHERE d = {d} AND h = '{h}'").fetchone()[0]
                res["guard"][f"{h}_{refc}_X{X}_d{d}"] = {"kept_share": kept, "net_kept": stat(P["net"], P["pool"], ci=(X in (0.01, 0.05))), "net_10_kept": stat(P["net_10"], P["pool"], ci=False),
                                                       "revert_cost_pt": (1 - kept) * TIP / S * 100}
res["seconds"] = time.time() - t0
json.dump(res, open(f"{SP}/costgap2.json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print(res["check_identity_max"], res["n"], res["K3_R"], f"{res['seconds']:.0f} s")
