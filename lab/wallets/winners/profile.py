"""Prototype of the winners analysis (lab/reports/2026-10-15-winnaars.md, exploratory). DuckDB <= 1.5 GB, per epoch, so it
runs in a 3G scope; store read-only. Paths: LAB_DATA_ROOT (default /home/chupa/Solana-project/data-old-faithful-one/lab),
outputs under LAB_WALLETS_OUT/winners-v1 (default $LAB_DATA_ROOT/wallets/winners-v1). Run order: rank, closed, profile, feasibility."""
# no-quote-filter: consumed data, conclusions audited 2026-10-18 (lab/reports/2026-10-18-audit-niet-sol-quote.md)
import os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
OUT = os.path.join(os.environ.get("LAB_WALLETS_OUT", os.path.join(DATA, "wallets")), "winners-v1")
os.makedirs(OUT, exist_ok=True)
SPILL = os.environ.get("LAB_SPILL_DIR", os.path.join(DATA, "tmp", "winners-v1")); os.makedirs(SPILL, exist_ok=True)
STORE = os.environ.get("LAB_STORE", os.path.join(DATA, "store", "dev2.duckdb"))
MV = os.path.join(DATA, "wallets", "money-v1")
import duckdb, time
t0=time.time()
def log(*a): print(round(time.time()-t0,1), *a, flush=True)
con=duckdb.connect(); con.execute(f"SET memory_limit='1500MB'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{SPILL}'; SET max_temp_directory_size='6GB'")
con.execute(f"ATTACH '{STORE}' AS s (READ_ONLY)")
con.execute(f"CREATE TABLE cw AS SELECT * FROM '{OUT}/closed_wallets.parquet'")
con.execute(f"CREATE TABLE wm AS SELECT wallet, mint, kind, net FROM (SELECT wallet, mint, CASE WHEN sum(tok_in) > 0 AND abs(sum(tok_out) - sum(tok_in)) <= 0.05 * sum(tok_in) THEN 'closed' WHEN sum(tok_out) > 1.05 * sum(tok_in) THEN 'tokens_in' ELSE 'open' END AS kind, sum(cash) - sum(fees) AS net FROM '{OUT}/closed_wmd.parquet' GROUP BY 1,2)")
con.execute("""CREATE TABLE top AS SELECT wallet, 'C' AS tier FROM (SELECT wallet, row_number() OVER (ORDER BY net_out_c DESC) rn FROM cw WHERE d_lab_c >= 7 AND d_lab_c_pos >= 7 AND net_lab_c > 10e9 AND d_out_c >= 3 AND net_out_c > 0) WHERE rn <= 100""")
log("top", con.execute("SELECT count(*) FROM top").fetchone()[0])
EPOCHS=[(446256000+432000*i, 446256000+432000*(i+1)) for i in range(14)]
first=True
for lo,hi in EPOCHS:
    con.execute(f"""CREATE OR REPLACE TABLE lg AS
SELECT c.trader AS wallet, 'curve' AS venue, c.mint, NULL::VARCHAR AS pool, c.slot::BIGINT AS slot, c.tx_index, c.is_buy, c.q AS sol, c.t AS tok, c.outer_program, c.priority_fee, c.jito_tip, c.tx_fee,
       c.fee_payer <> c.trader AS other_payer, m.create_slot::BIGINT AS create_slot, NULL::BIGINT AS pool_create, FALSE AS farmer
FROM s.curve c JOIN top ON top.wallet = c.trader LEFT JOIN s.mints m USING (mint) WHERE c.slot >= {lo} AND c.slot < {hi}
UNION ALL
SELECT p.trader, CASE WHEN p.orientation = 'reversed' THEN 'pool_reversed' ELSE 'pool' END, po.mint, p.pool, p.slot::BIGINT, p.tx_index, p.token_buy, p.sol_amount, p.token_amount, p.outer_program, p.priority_fee, p.jito_tip, p.tx_fee,
       p.fee_payer <> p.trader, m.create_slot::BIGINT, po.create_slot::BIGINT, p.farmer
FROM s.pool p JOIN top ON top.wallet = p.trader LEFT JOIN s.pools po USING (pool) LEFT JOIN s.mints m ON m.mint = po.mint
WHERE p.kind IN ('buy','sell') AND p.slot >= {lo} AND p.slot < {hi}""")
    if first: con.execute("CREATE TABLE legs AS SELECT * FROM lg"); first=False
    else: con.execute("INSERT INTO legs SELECT * FROM lg")
log("legs", con.execute("SELECT count(*), count(DISTINCT wallet) FROM legs").fetchone())
con.execute(f"COPY legs TO '{OUT}/legs.parquet' (FORMAT PARQUET)")
con.execute("""CREATE TABLE pos AS
SELECT wallet, mint, min(CASE WHEN is_buy THEN slot END) AS first_buy, min(CASE WHEN NOT is_buy THEN slot END) AS first_sell,
       sum(CASE WHEN is_buy THEN sol END) AS buy_sol, sum(CASE WHEN NOT is_buy THEN sol END) AS sell_sol, any_value(create_slot) AS create_slot, min(pool_create) AS pool_create,
       arg_min(venue, slot) AS entry_venue, count(*) AS n, sum(CASE WHEN is_buy THEN sol ELSE -sol END) * -1 AS net_sol
FROM legs GROUP BY 1,2""")
con.execute("""CREATE TABLE prof AS
SELECT p.wallet, count(*) AS n_pos, round(median(p.first_buy - p.create_slot)) AS med_delay, round(quantile_cont(p.first_buy - p.create_slot, 0.1)) AS p10_delay,
  round(avg((p.first_buy - p.create_slot <= 3)::INT),2) AS sh_create_snipe, round(avg((p.first_buy - p.pool_create <= 3)::INT),2) AS sh_pool_snipe,
  round(median(p.first_sell - p.first_buy)) AS med_hold, round(median(p.buy_sol)/1e9,3) AS med_buy_sol,
  round(avg((p.entry_venue = 'curve')::INT),2) AS sh_curve, round(avg((p.entry_venue = 'pool_reversed')::INT),2) AS sh_reversed,
  round(avg((COALESCE(m.kind,'open') = 'closed')::INT),2) AS sh_closed, round(avg((COALESCE(m.kind,'open') = 'tokens_in')::INT),2) AS sh_tokens_in,
  round(sum(CASE WHEN m.kind = 'closed' THEN m.net END)/1e9) AS net_closed, round(sum(CASE WHEN m.kind = 'tokens_in' THEN m.net END)/1e9) AS net_tokens_in
FROM pos p LEFT JOIN wm m USING (wallet, mint) GROUP BY 1""")
con.execute("""CREATE TABLE prof2 AS
SELECT wallet, round(median(priority_fee)/1e6,4) AS prio_msol, round(median(jito_tip)/1e6,4) AS tip_msol, round(avg((jito_tip > 0)::INT),2) AS sh_tip,
  mode(outer_program) AS outer_program, round(avg(farmer::INT),2) AS sh_farmer, count(*) AS n_legs, count(DISTINCT mint) AS n_mints,
  count(*) FILTER (WHERE other_payer) AS n_other_payer
FROM legs GROUP BY 1""")
con.execute("""CREATE TABLE rt AS SELECT wallet, count(*) AS n_rt FROM (SELECT wallet, COALESCE(pool, mint) k, slot FROM legs GROUP BY 1,2,3 HAVING bool_or(is_buy) AND bool_or(NOT is_buy)) GROUP BY 1""")
con.execute("""CREATE TABLE P AS SELECT cw.wallet, round(cw.net_lab_c/1e9) AS net_lab_c, round(cw.net_out_c/1e9) AS net_out_c, cw.d_lab_c, cw.d_out_c, prof.* EXCLUDE (wallet), prof2.* EXCLUDE (wallet), COALESCE(rt.n_rt,0) AS n_rt,
  CASE WHEN COALESCE(rt.n_rt,0) >= 0.3*n_pos THEN 'A0 same-slot round-trip (arb/MEV)'
  WHEN sh_curve >= 0.6 AND sh_create_snipe >= 0.5 THEN 'A1 curve create-sniper (<= 3 slots after create)'
  WHEN sh_curve >= 0.6 AND med_delay <= 700 AND med_hold <= 400 THEN 'A2 curve scalper (entry <= 3 min after create, hold <= 2 min)'
  WHEN sh_curve >= 0.6 THEN 'A3 curve, later entry or longer hold'
  WHEN sh_pool_snipe >= 0.5 AND med_buy_sol >= 20 THEN 'A4 migration-sniper, large size (>= 20 SOL)'
  WHEN sh_pool_snipe >= 0.5 THEN 'A5 migration-sniper, small size'
  WHEN sh_reversed >= 0.3 THEN 'A6 reversed-pool trader'
  WHEN med_delay > 1000 AND med_hold > 1000 THEN 'A7 pool swing (entry > 5 min, hold > 5 min)'
  WHEN med_buy_sol >= 20 THEN 'A8 pool early, large size'
  ELSE 'A9 pool, other' END AS arch
  FROM top JOIN cw USING (wallet) JOIN prof USING (wallet) JOIN prof2 USING (wallet) LEFT JOIN rt USING (wallet)""")
con.execute(f"COPY P TO '{OUT}/profiles.parquet' (FORMAT PARQUET)")
print("\n== archetypes of the top-100 closed-round persistent traders: arch | n | Σ net_out_c | median | med delay | med hold | med buy | med positions | closed share | tokens-in share | Σ net tokens-in | curve share | prio mSOL | tip share | n other-payer legs")
for r in con.execute("""SELECT arch, count(*), sum(net_out_c), median(net_out_c), median(med_delay), median(med_hold), round(median(med_buy_sol),2), median(n_pos), round(avg(sh_closed),2), round(avg(sh_tokens_in),2), sum(net_tokens_in), round(avg(sh_curve),2), median(prio_msol), round(avg(sh_tip),2), sum(n_other_payer)
  FROM P GROUP BY 1 ORDER BY 1""").fetchall(): print(r)
print("\n== all 100 wallets: wallet | arch | net_lab_c | net_out_c | n_pos | med delay | p10 | hold | buy SOL | curve | closed | tokens-in | prio | tip share | outer | mints")
for r in con.execute("SELECT left(wallet,12), left(arch,2), net_lab_c, net_out_c, n_pos, med_delay, p10_delay, med_hold, med_buy_sol, sh_curve, sh_closed, sh_tokens_in, prio_msol, sh_tip, left(outer_program,8), n_mints FROM P ORDER BY arch, net_out_c DESC").fetchall(): print(r)
print("\n== examples: per archetype the top-2 wallets with their best closed mint (net SOL), entry delay and hold")
for r in con.execute("""SELECT a.arch, a.wallet, pm.mint, round(pm.net/1e9,1), pm.delay, pm.hold FROM (SELECT arch, wallet, row_number() OVER (PARTITION BY arch ORDER BY net_out_c DESC) rn FROM P) a
  JOIN (SELECT p.wallet, p.mint, m.net, p.first_buy - p.create_slot AS delay, p.first_sell - p.first_buy AS hold, row_number() OVER (PARTITION BY p.wallet ORDER BY m.net DESC) rn FROM pos p JOIN wm m USING (wallet, mint) WHERE m.kind = 'closed') pm ON pm.wallet = a.wallet AND pm.rn = 1 WHERE a.rn <= 2 ORDER BY 1""").fetchall(): print(r)
print("\n== per archetype, closed positions only: n, win share, median net, mean, top-1 % share of positive sum")
for r in con.execute("""WITH c AS (SELECT P.arch, m.net, row_number() OVER (PARTITION BY P.arch ORDER BY m.net DESC) rn, count(*) OVER (PARTITION BY P.arch) cnt FROM wm m JOIN P USING (wallet) WHERE m.kind = 'closed')
  SELECT arch, count(*), round(avg((net>0)::INT),2), round(median(net)/1e9,3), round(avg(net)/1e9,3), round(sum(CASE WHEN rn <= cnt*0.01 THEN net END)/sum(CASE WHEN net>0 THEN net END),2) FROM c GROUP BY 1 ORDER BY 1""").fetchall(): print(r)
log("done")
