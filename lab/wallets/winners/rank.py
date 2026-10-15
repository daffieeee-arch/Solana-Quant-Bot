"""Prototype of the winners analysis (lab/reports/2026-10-15-winnaars.md, exploratory). DuckDB <= 1.5 GB, per epoch, so it
runs in a 3G scope; store read-only. Paths: LAB_DATA_ROOT (default /home/chupa/Solana-project/data-old-faithful-one/lab),
outputs under LAB_WALLETS_OUT/winners-v1 (default $LAB_DATA_ROOT/wallets/winners-v1). Run order: rank, closed, profile, feasibility."""
import os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
OUT = os.path.join(os.environ.get("LAB_WALLETS_OUT", os.path.join(DATA, "wallets")), "winners-v1")
os.makedirs(OUT, exist_ok=True)
SPILL = os.environ.get("LAB_SPILL_DIR", os.path.join(DATA, "tmp", "winners-v1")); os.makedirs(SPILL, exist_ok=True)
STORE = os.environ.get("LAB_STORE", os.path.join(DATA, "store", "dev2.duckdb"))
MV = os.path.join(DATA, "wallets", "money-v1")
import duckdb, time, glob
t0=time.time()
con=duckdb.connect(); con.execute(f"SET memory_limit='1500MB'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{SPILL}'; SET max_temp_directory_size='4GB'")
con.execute(f"ATTACH '{STORE}' AS s (READ_ONLY)")
def log(*a): print(round(time.time()-t0,1), *a, flush=True)

# 1. per wallet: label window days 1-9 (2026-09-11..09-19), outcome window days 10-18 (09-20..09-30); bucket files = one wallet per file
first=True
for f in sorted(glob.glob(f"{MV}/wallet-day-*.parquet")):
    con.execute(f"""
    CREATE OR REPLACE TABLE b AS
    SELECT wallet,
      sum((day < '2026-09-20')::INT) AS d_lab, sum((day < '2026-09-20' AND cash - fees > 0)::INT) AS d_lab_pos,
      sum(CASE WHEN day < '2026-09-20' THEN cash - fees END) AS net_lab, sum(CASE WHEN day < '2026-09-20' THEN buy_sol END) AS buy_lab,
      sum((day >= '2026-09-20' AND day <= '2026-09-30')::INT) AS d_out, sum((day >= '2026-09-20' AND day <= '2026-09-30' AND cash - fees > 0)::INT) AS d_out_pos,
      sum(CASE WHEN day >= '2026-09-20' AND day <= '2026-09-30' THEN cash - fees END) AS net_out,
      count(*) AS d_all, sum((cash - fees > 0)::INT) AS d_all_pos, sum(cash - fees) AS net_all, sum(cash) AS cash, sum(fees) AS fees, sum(dvalue) AS dvalue,
      sum(buy_sol) AS buy_sol, sum(sell_sol) AS sell_sol, sum(n_legs) AS n_legs, sum(n_buys) AS n_buys, sum(n_pool_legs) AS n_pool_legs, sum(n_farmer_legs) AS n_farmer_legs,
      sum(n_mints) AS n_mints, sum(n_opened) AS n_opened, sum(n_snipe) AS n_snipe, avg(median_buy_sol) AS med_buy, sum(n_closed) AS n_closed, avg(median_hold_slots) AS med_hold,
      sum(paid_by_other) AS paid_by_other
    FROM read_parquet('{f}') WHERE n_legs > 0 GROUP BY wallet HAVING count(*) >= 5""")
    if first: con.execute("CREATE TABLE w AS SELECT * FROM b"); first=False
    else: con.execute("INSERT INTO w SELECT * FROM b")
log("wallets >=5 days", con.execute("SELECT count(*) FROM w").fetchone()[0])
# candidates for the store-side groups: day 1-9 winners (the coin-flip control uses the wallet-day table only)
con.execute("CREATE TABLE cand AS SELECT wallet FROM w WHERE d_lab >= 5 AND d_lab_pos >= 0.6*d_lab AND net_lab > 0")
log("day 1-9 winners", con.execute("SELECT count(*) FROM cand").fetchone()[0])

# 2. groups that we cannot copy: creators, LP of reversed pools (store pool kind deposit/withdraw), same-slot arbs, wash farmers; seller-only (transfer-fed)
con.execute(f"CREATE TABLE creators AS SELECT wallet, count(*) AS n_created FROM read_parquet('{MV}/creators.parquet') GROUP BY 1")
con.execute("""CREATE TABLE lps AS SELECT trader AS wallet, count(*) AS n_lp, sum(CASE WHEN kind = 'withdraw' THEN 1 ELSE 0 END) AS n_withdraw
               FROM s.pool WHERE kind IN ('deposit','withdraw') AND trader IN (SELECT wallet FROM cand) GROUP BY 1""")
log("creators", con.execute("SELECT count(*) FROM creators").fetchone()[0], "lps", con.execute("SELECT count(*) FROM lps").fetchone()[0])
con.execute("""CREATE TABLE g AS
  SELECT w.*, COALESCE(c.n_created,0) AS n_created, COALESCE(l.n_lp,0) AS n_lp, COALESCE(l.n_withdraw,0) AS n_withdraw, COALESCE(r.n_rt,0) AS n_rt,
    CASE WHEN buy_sol < 0.2*sell_sol THEN 'seller_only'
         WHEN COALESCE(l.n_withdraw,0) >= 3 THEN 'lp'
         WHEN COALESCE(c.n_created,0) >= 3 THEN 'creator'
         WHEN n_farmer_legs >= 0.3*n_legs THEN 'wash_farmer'
         WHEN paid_by_other >= 0.9*n_legs THEN 'service_payer_bot'
         ELSE 'trader' END AS grp,
    d_lab >= 5 AND d_lab_pos >= 0.6*d_lab AND net_lab > 0 AS lab_winner,
    d_lab >= 5 AND d_lab_pos >= 0.6*d_lab AND net_lab > 0 AND d_out >= 3 AND net_out > 0 AND d_out_pos >= 0.6*d_out AS persistent
  FROM w LEFT JOIN creators c USING (wallet) LEFT JOIN lps l USING (wallet) LEFT JOIN (SELECT NULL::VARCHAR AS wallet, NULL::BIGINT AS n_rt) r USING (wallet)""")
con.execute(f"COPY g TO '{OUT}/groups.parquet' (FORMAT PARQUET)")
print("\n== groups: n, lab winners, persistent (lab winner AND days 10-18 net>0 with >=60% pos days), Σ net days 10-18 of persistent (SOL), Σ net all of group")
for r in con.execute("""SELECT grp, count(*), sum(lab_winner::INT), sum(persistent::INT), round(sum(CASE WHEN persistent THEN net_out END)/1e9), round(sum(net_all)/1e9),
   round(100.0*sum(persistent::INT)/greatest(sum(lab_winner::INT),1),1) AS pct_persist
   FROM g GROUP BY 1 ORDER BY 2 DESC""").fetchall(): print(r)
print("\n== lab winners: do they persist? by group: share with net_out > 0 among lab winners with d_out >= 3; vs. lab losers (same)")
for r in con.execute("""SELECT grp, sum((lab_winner AND d_out>=3)::INT) AS n_w, round(avg(CASE WHEN lab_winner AND d_out>=3 THEN (net_out>0)::INT END),2) AS p_win_pos,
   round(avg(CASE WHEN NOT lab_winner AND d_lab>=5 AND d_out>=3 THEN (net_out>0)::INT END),2) AS p_loser_pos,
   round(median(CASE WHEN lab_winner AND d_out>=3 THEN net_out END)/1e9,2) AS med_out_w FROM g GROUP BY 1 ORDER BY 2 DESC""").fetchall(): print(r)

log("done: groups.parquet (the store profile of the top wallets is profile.py)")
