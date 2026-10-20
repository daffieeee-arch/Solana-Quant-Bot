"""F7 cost-gap decomposition on dev (run with PYTHONPATH=lab, the legs dir as the argument), in points of the stake (additive waterfall per trigger; wipe-outs are -100 pt, not
-690 lp), E45 unit: per-pool mean of the pool's triggers, 0.5 % trimmed mean over pools, pool bootstrap (BOOT, seed 7).
mech - delay - fee_in - imp_in - fee_out - imp_out - network = net, exactly per trigger."""
import json, math, os, sys, time
import duckdb, numpy as np
from wallets.markout_report import trimmed_mean, day_ci, BOOT

LAB = os.environ.get("LAB_DATA", "/home/chupa/Solana-project/data-old-faithful-one/lab")
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
chk = con.execute("SELECT max(abs(mech - delay - fee_in - imp_in - fee_out - imp_out - network - net)) FROM w").fetchone()
con.execute("ALTER TABLE w ADD COLUMN live BOOLEAN; UPDATE w SET live = cls <> 'wiped'")
con.execute(f"ALTER TABLE w ADD COLUMN lg DOUBLE; UPDATE w SET lg = ln(greatest(1 + gross, 0.001))")


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


TERMS = ["mech", "delay", "fee_in", "imp_in", "fee_out", "imp_out", "network", "net", "gross", "lg", "net_10", "net_25"]
res = {"check": chk, "live": {}, "wiped": {}, "p1": {}, "depth": {}, "ladder": {}}
for h in HS:
    for d in DS:
        L = pm(f"d = {d} AND h = '{h}' AND live", TERMS)
        res["live"][f"d{d}_{h}"] = {c: stat(L[c], L["pool"], ci=(d == 2 and c in ("mech", "delay", "fee_in", "imp_in", "fee_out", "imp_out", "net", "net_10", "lg"))) for c in TERMS}
        res["live"][f"d{d}_{h}"]["per_trigger"] = {c: ptm(f"d = {d} AND h = '{h}' AND live", c) for c in TERMS}
        W = con.execute(f"""SELECT pool, avg((NOT live)::int) AS share, avg(CASE WHEN live THEN 0 ELSE net END) AS contrib, avg(net) AS net_all,
                            avg(CASE WHEN live THEN lg ELSE 0 END) AS lg_live_c, avg(CASE WHEN live THEN 0 ELSE lg END) AS lg_wiped_c, avg(lg) AS lg_all
                            FROM w WHERE d = {d} AND h = '{h}' GROUP BY pool""").fetchnumpy()
        res["wiped"][f"d{d}_{h}"] = {"share_pool": stat(W["share"], W["pool"], ci=(d == 2)), "share_trig": ptm(f"d = {d} AND h = '{h}'", "(NOT live)::int"),
                                     "contrib_pool": stat(W["contrib"], W["pool"], ci=(d == 2)), "net_all_pool": stat(W["net_all"], W["pool"], ci=(d == 2))}
        res["p1"][f"d{d}_{h}"] = {"lg_all": stat(W["lg_all"], W["pool"], ci=(d == 2)), "lg_live_contrib": stat(W["lg_live_c"], W["pool"], ci=(d == 2)),
                                  "lg_wiped_contrib": stat(W["lg_wiped_c"], W["pool"], ci=(d == 2)),
                                  "lg_wiped_within_trig": ptm(f"d = {d} AND h = '{h}' AND NOT live", "lg"), "gross_wiped_median": con.execute(f"SELECT median(gross) FROM w WHERE d = {d} AND h = '{h}' AND NOT live").fetchone()[0]}
BANDS = [(0, 100), (100, 150), (150, 200), (200, 300), (300, 500), (500, 1e9)]
for h in HS:
    for lo, hi in BANDS:
        wh = f"d = 2 AND h = '{h}' AND b_sol >= {lo} AND b_sol < {hi}"
        A = con.execute(f"SELECT pool, avg(net) AS net, avg(net_10) AS net_10, avg((NOT live)::int) AS wiped, avg(CASE WHEN live THEN lg END) AS lg_live, avg(CASE WHEN live THEN net END) AS net_live, count(*) AS n FROM w WHERE {wh} GROUP BY pool").fetchnumpy()
        blocks = {}
        for blk, bw in (("early", "day < '2026-09-20'"), ("binding", "day >= '2026-09-20' AND day <= '2026-09-30'")):
            B = con.execute(f"SELECT pool, avg(net) AS net FROM w WHERE {wh} AND {bw} GROUP BY pool").fetchnumpy()
            blocks[blk] = stat(B["net"], B["pool"], ci=(lo in (100, 150)))
        res["depth"][f"{h}_{lo}-{hi}"] = {"net_50": stat(A["net"], A["pool"]), "net_10": stat(A["net_10"], A["pool"], ci=False), "wiped_share": stat(A["wiped"], A["pool"], ci=False),
                                         "lg_live": stat(A["lg_live"], A["pool"], ci=False), "net_live": stat(A["net_live"], A["pool"], ci=False), "triggers": int(A["n"].sum()), "blocks": blocks}
res["seconds"] = time.time() - t0
json.dump(res, open(f"{SP}/costgap3.json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print(chk, f"{res['seconds']:.0f} s")
