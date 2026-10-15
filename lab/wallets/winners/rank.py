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

# 3. store profile of the persistent traders (top 100 by net_out) + the top 100 persistent of every other group for the money map
con.execute("CREATE TABLE top AS SELECT wallet, grp FROM (SELECT wallet, grp, row_number() OVER (PARTITION BY grp ORDER BY net_out DESC) AS rn FROM g WHERE persistent) WHERE rn <= 100")
log("top set", con.execute("SELECT count(*) FROM top").fetchone()[0])
EPOCHS=[(446256000+432000*i, 446256000+432000*(i+1)) for i in range(14)]
first=True
for lo,hi in EPOCHS:
    con.execute(f"""CREATE OR REPLACE TABLE lg AS
SELECT c.trader AS wallet, 'curve' AS venue, c.mint, NULL::VARCHAR AS pool, c.slot, c.tx_index, c.is_buy, c.q AS sol, c.outer_program, c.priority_fee, c.jito_tip, c.tx_fee,
       c.fee_payer <> c.trader AS other_payer, m.create_slot, NULL::UBIGINT AS pool_create, FALSE AS farmer
FROM s.curve c JOIN top ON top.wallet = c.trader LEFT JOIN s.mints m USING (mint) WHERE c.slot >= {lo} AND c.slot < {hi}
UNION ALL
SELECT p.trader, CASE WHEN p.orientation = 'reversed' THEN 'pool_reversed' ELSE 'pool' END, po.mint, p.pool, p.slot, p.tx_index, p.token_buy, p.sol_amount, p.outer_program, p.priority_fee, p.jito_tip, p.tx_fee,
       p.fee_payer <> p.trader, m.create_slot, po.create_slot, p.farmer
FROM s.pool p JOIN top ON top.wallet = p.trader LEFT JOIN s.pools po USING (pool) LEFT JOIN s.mints m ON m.mint = po.mint
WHERE p.kind IN ('buy','sell') AND p.slot >= {lo} AND p.slot < {hi}""")
    if first: con.execute("CREATE TABLE legs AS SELECT * FROM lg"); first=False
    else: con.execute("INSERT INTO legs SELECT * FROM lg")
    log("epoch", lo, con.execute("SELECT count(*) FROM lg").fetchone()[0])
# same-slot round trips from the extracted legs; relabel arbs inside the profile set
con.execute("""CREATE TABLE rt AS SELECT wallet, count(*) AS n_rt FROM (
  SELECT wallet, COALESCE(pool, mint) AS k, slot FROM legs GROUP BY 1,2,3 HAVING bool_or(is_buy) AND bool_or(NOT is_buy)) GROUP BY 1""")
con.execute("""UPDATE top SET grp = 'same_slot_arb' FROM rt, (SELECT wallet, sum(is_buy::INT) AS nb FROM legs GROUP BY 1) q
  WHERE top.wallet = rt.wallet AND q.wallet = top.wallet AND rt.n_rt >= 0.3*q.nb AND top.grp = 'trader'""")
con.execute("UPDATE g SET grp = top.grp FROM top WHERE g.wallet = top.wallet")
log("same-slot arbs relabelled", con.execute("SELECT count(*) FROM top WHERE grp = 'same_slot_arb'").fetchone()[0])
con.execute(f"COPY legs TO '{OUT}/legs_wide.parquet' (FORMAT PARQUET)")
log("legs", con.execute("SELECT count(*), count(DISTINCT wallet) FROM legs").fetchone())
# per wallet x mint position: first buy, first sell, entry delay vs create / pool create
con.execute("""CREATE TABLE pos AS
SELECT wallet, mint, min(CASE WHEN is_buy THEN slot END) AS first_buy, min(CASE WHEN NOT is_buy THEN slot END) AS first_sell, max(CASE WHEN NOT is_buy THEN slot END) AS last_sell,
       sum(CASE WHEN is_buy THEN sol END) AS buy_sol, sum(CASE WHEN NOT is_buy THEN sol END) AS sell_sol, any_value(create_slot) AS create_slot, min(pool_create) AS pool_create,
       arg_min(venue, slot) AS entry_venue, count(*) AS n
FROM legs GROUP BY 1,2""")
con.execute("""CREATE TABLE prof AS
SELECT t.wallet, t.grp,
  count(*) AS n_pos, round(median(p.first_buy - p.create_slot)) AS med_slots_after_create, round(quantile_cont(p.first_buy - p.create_slot, 0.1)) AS p10_after_create,
  round(avg((p.first_buy - p.create_slot <= 3)::INT),2) AS sh_create_snipe, round(avg((p.first_buy - p.pool_create <= 3)::INT),2) AS sh_pool_snipe,
  round(avg((p.first_buy - p.create_slot > 60*1000/270)::INT),2) AS sh_after_1min,
  round(median(p.first_sell - p.first_buy)) AS med_hold_slots, round(median(p.buy_sol)/1e9,3) AS med_buy_sol,
  round(avg((p.entry_venue = 'curve')::INT),2) AS sh_curve, round(avg((p.entry_venue = 'pool_reversed')::INT),2) AS sh_reversed,
  round(avg((p.first_sell IS NULL)::INT),2) AS sh_never_sold
FROM pos p JOIN top t USING (wallet) GROUP BY 1,2""")
con.execute("""CREATE TABLE prof2 AS
SELECT wallet, round(median(priority_fee)/1e6,4) AS med_prio_msol, round(median(jito_tip)/1e6,4) AS med_tip_msol, round(avg((jito_tip > 0)::INT),2) AS sh_tip,
  round(avg(other_payer::INT),2) AS sh_other_payer, mode(outer_program) AS outer_program, round(avg(farmer::INT),2) AS sh_farmer,
  count(DISTINCT slot) AS n_slots, count(*) AS n_legs, count(DISTINCT mint) AS n_mints, count(DISTINCT (slot::BIGINT / 324000)) AS n_days_est
FROM legs GROUP BY 1""")
con.execute("CREATE TABLE P AS SELECT g.wallet, g.grp, g.net_lab, g.net_out, g.d_all, g.d_all_pos, g.n_created, g.n_lp, COALESCE(rt.n_rt,0) AS n_rt, prof.* EXCLUDE (wallet, grp), prof2.* EXCLUDE (wallet) FROM g JOIN prof USING (wallet) JOIN prof2 USING (wallet) LEFT JOIN rt USING (wallet)")
con.execute(f"COPY P TO '{OUT}/profiles_wide.parquet' (FORMAT PARQUET)")
print("\n== persistent TRADERS top 40 by net days 10-18 (SOL): wallet, net_lab, net_out, n_pos, med slots after create, p10, sh create-snipe, sh pool-snipe, sh >1min, med hold slots, med buy SOL, sh curve, sh reversed, never sold, prio mSOL, tip mSOL, sh tip, other payer, outer program, farmer, mints")
for r in con.execute("""SELECT left(wallet,10), round(net_lab/1e9), round(net_out/1e9), n_pos, med_slots_after_create, p10_after_create, sh_create_snipe, sh_pool_snipe, sh_after_1min, med_hold_slots, med_buy_sol, sh_curve, sh_reversed, sh_never_sold, med_prio_msol, med_tip_msol, sh_tip, sh_other_payer, left(outer_program,8), sh_farmer, n_mints
  FROM P WHERE grp = 'trader' ORDER BY net_out DESC LIMIT 40""").fetchall(): print(r)
print("\n== group medians (persistent top-100 per group)")
for r in con.execute("""SELECT grp, count(*), round(sum(net_out)/1e9), median(med_slots_after_create), round(avg(sh_create_snipe),2), round(avg(sh_pool_snipe),2), round(avg(sh_after_1min),2), median(med_hold_slots), median(med_buy_sol), round(avg(sh_curve),2), round(avg(sh_reversed),2), median(med_prio_msol), median(med_tip_msol), round(avg(sh_tip),2), round(avg(sh_other_payer),2)
  FROM P GROUP BY 1 ORDER BY 3 DESC""").fetchall(): print(r)
log("done")
