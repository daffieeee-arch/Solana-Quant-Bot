"""Agenda idea 1, first step (lab/reports/2026-10-17-verkenning-drains-whale.md, E34): the K2 pool features (point-in-time at the trigger slot t) joined to the H4/H5 dev positions
(organic, d 1/2): which share of positions, drains and P&L sits in pools a factory-style veto would hit."""
import duckdb, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from backtest import costs
from backtest.store import load_defaults
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab"); W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
pnl, ret = costs.settle_sql(costs.Scenario.from_cfg("base", load_defaults()))
con=duckdb.connect(); con.execute(f"SET memory_limit='1000MB'; SET threads TO 2; SET temp_directory='{DATA}/tmp/winners-v1'")
con.execute(f"""CREATE TABLE pos AS
  SELECT 'H5' AS h, variant, d, pool, t, entry_slot, exit_slot, exit_reason, proceeds, cost, tokens, day, regime, {pnl} AS pnl FROM '{DATA}/backtests/bigtp-dev-1033-1046-4dbd34b/parts/*.parquet' WHERE variant = 'flow300_tp100_H6' AND farm = 'organic' AND NOT COALESCE(skipped, false)
  UNION ALL
  SELECT 'H4', variant, d, pool, t, entry_slot, exit_slot, exit_reason, proceeds, cost, tokens, day, regime, {pnl} FROM '{DATA}/backtests/long-dev-1033-1046-fc4173c/parts/*.parquet' WHERE variant = 'flow300_H6' AND farm = 'organic' AND NOT COALESCE(skipped, false)""")
print(con.execute("SELECT h, d, count(*), round(sum(pnl)/1e9,1) FROM pos GROUP BY 1,2 ORDER BY 1,2").fetchall())
print("exit reasons H5 d1:", con.execute("SELECT exit_reason, count(*), round(sum(pnl)/1e9,1) FROM pos WHERE h='H5' AND d=1 GROUP BY 1 ORDER BY 2 DESC").fetchall())
con.execute(f"""CREATE TABLE k AS SELECT pool, creator, create_slot, b0/1e9 AS b0_sol, creator_prior_pools, creator_n_trades IS NULL AS creator_never_traded, ft, ms, n_traders, buy_sol - sell_sol AS inflow, max_buy, tip_share, factory, first_withdraw FROM '{W}/k2_pools.parquet'""")
# point-in-time: the 2-minute features only when t >= ft + 2 min; creator features are known at the create
con.execute("""CREATE TABLE j AS SELECT p.*, k.pool IS NOT NULL AS in_dev_pools, k.factory, k.b0_sol, k.creator_prior_pools, k.creator_never_traded,
  CASE WHEN p.t >= k.ft + round(120000/k.ms) THEN k.n_traders END AS n_traders2, CASE WHEN p.t >= k.ft + round(120000/k.ms) THEN k.inflow END AS inflow2,
  (p.t - k.create_slot) * k.ms / 60000 AS age_min, p.proceeds = 0 OR p.exit_reason ILIKE '%drain%' OR p.exit_reason ILIKE '%unsold%' OR p.exit_slot IS NULL AS lost_all,
  p.day >= '2026-09-20' AS out_block
  FROM pos p LEFT JOIN k USING (pool)""")
def show(t, q):
    print("\n== "+t)
    for r in con.execute(q).fetchall(): print(r)
show("coverage: h | d | positions | pool created in dev | factory label | age at t median (min)", "SELECT h, d, count(*), sum(in_dev_pools::INT), sum(COALESCE(factory,false)::INT), round(median(age_min),1) FROM j GROUP BY 1,2 ORDER BY 1,2")
show("outcome by pool class (H5 d=1): class | n | share of positions | Σ pnl | share of Σ | lost-all share | mean pnl",
 """SELECT CASE WHEN NOT in_dev_pools THEN 'pool older than dev' WHEN factory THEN 'factory (cluster-traded)' WHEN creator_prior_pools = 0 AND creator_never_traded AND b0_sol >= 300 THEN 'R1 one-off creator, b0>=300' WHEN creator_prior_pools = 0 AND creator_never_traded THEN 'one-off creator, small b0' WHEN creator_prior_pools >= 100 THEN 'serial creator >=100 pools' ELSE 'other' END AS cls,
   count(*), round(count(*)::DOUBLE/(SELECT count(*) FROM j WHERE h='H5' AND d=1),3), round(sum(pnl)/1e9,1), round(sum(pnl)/(SELECT sum(pnl) FROM j WHERE h='H5' AND d=1),2), round(avg(lost_all::INT),3), round(avg(pnl)/1e9,4)
   FROM j WHERE h='H5' AND d=1 GROUP BY 1 ORDER BY 2 DESC""")
show("same for H4 d=1", """SELECT CASE WHEN NOT in_dev_pools THEN 'pool older than dev' WHEN factory THEN 'factory (cluster-traded)' WHEN creator_prior_pools = 0 AND creator_never_traded AND b0_sol >= 300 THEN 'R1 one-off creator, b0>=300' WHEN creator_prior_pools = 0 AND creator_never_traded THEN 'one-off creator, small b0' WHEN creator_prior_pools >= 100 THEN 'serial creator >=100 pools' ELSE 'other' END AS cls,
   count(*), round(count(*)::DOUBLE/(SELECT count(*) FROM j WHERE h='H4' AND d=1),3), round(sum(pnl)/1e9,1), round(sum(pnl)/(SELECT sum(pnl) FROM j WHERE h='H4' AND d=1),2), round(avg(lost_all::INT),3), round(avg(pnl)/1e9,4)
   FROM j WHERE h='H4' AND d=1 GROUP BY 1 ORDER BY 2 DESC""")
show("by pool age at t (H5 d=1): age | n | Σ pnl | lost-all share | mean", "SELECT CASE WHEN age_min IS NULL THEN 'older than dev' WHEN age_min < 5 THEN '<5 min' WHEN age_min < 30 THEN '5-30 min' WHEN age_min < 180 THEN '0.5-3 h' WHEN age_min < 1440 THEN '3-24 h' ELSE '>1 d' END, count(*), round(sum(pnl)/1e9,1), round(avg(lost_all::INT),3), round(avg(pnl)/1e9,4) FROM j WHERE h='H5' AND d=1 GROUP BY 1 ORDER BY 1")
show("by creator prior pools (H5 d=1)", "SELECT CASE WHEN creator_prior_pools IS NULL THEN 'older than dev' WHEN creator_prior_pools = 0 THEN '0' WHEN creator_prior_pools < 10 THEN '1-9' WHEN creator_prior_pools < 100 THEN '10-99' ELSE '>=100' END, count(*), round(sum(pnl)/1e9,1), round(avg(lost_all::INT),3), round(avg(pnl)/1e9,4) FROM j WHERE h='H5' AND d=1 GROUP BY 1 ORDER BY 1")
show("by b0 (H5 d=1)", "SELECT CASE WHEN b0_sol IS NULL THEN 'older than dev' WHEN b0_sol < 100 THEN '<100' WHEN b0_sol < 300 THEN '100-300' ELSE '>=300' END, count(*), round(sum(pnl)/1e9,1), round(avg(lost_all::INT),3), round(avg(pnl)/1e9,4) FROM j WHERE h='H5' AND d=1 GROUP BY 1 ORDER BY 1")
show("by 2-min traders when known at t (H5 d=1)", "SELECT CASE WHEN n_traders2 IS NULL THEN 'unknown at t' WHEN n_traders2 < 20 THEN '<20' WHEN n_traders2 < 40 THEN '20-39' WHEN n_traders2 < 100 THEN '40-99' ELSE '>=100' END, count(*), round(sum(pnl)/1e9,1), round(avg(lost_all::INT),3), round(avg(pnl)/1e9,4) FROM j WHERE h='H5' AND d=1 GROUP BY 1 ORDER BY 1")
show("veto R1 effect per h, d, block: with | Σ with | without | Σ without | lost-all with/without | worst day with/without",
 """WITH v AS (SELECT *, COALESCE(creator_prior_pools = 0 AND creator_never_traded AND b0_sol >= 300, false) AS veto FROM j),
   dd AS (SELECT h, d, out_block, day, sum(pnl) AS s_all, sum(CASE WHEN NOT veto THEN pnl END) AS s_keep FROM v GROUP BY 1,2,3,4)
   SELECT v.h, v.d, v.out_block, count(*), round(sum(pnl)/1e9,1), count(*) FILTER (WHERE NOT veto), round(sum(CASE WHEN NOT veto THEN pnl END)/1e9,1), round(avg(lost_all::INT),3), round(avg(lost_all::INT) FILTER (WHERE NOT veto),3),
     (SELECT round(min(s_all)/1e9,1) FROM dd WHERE dd.h=v.h AND dd.d=v.d AND dd.out_block=v.out_block), (SELECT round(min(s_keep)/1e9,1) FROM dd WHERE dd.h=v.h AND dd.d=v.d AND dd.out_block=v.out_block)
   FROM v GROUP BY 1,2,3 ORDER BY 1,2,3""")
con.execute(f"COPY j TO '{W}/d1_h45_pools.parquet' (FORMAT PARQUET)")
