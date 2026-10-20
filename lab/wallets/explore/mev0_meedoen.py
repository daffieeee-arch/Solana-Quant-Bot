"""MEV phase 0 follow-up (lab/reports/2026-10-20-mev0-meedoen.md, E43): what taking part would need, read from the
existing E17 output (wallets/mev0-rerun/mev0-v1, NAS job 96 on a33446b). Read-only DuckDB over the parquet files;
nothing is sent or traded. 'explained' = net <= |legs_sol| (the 2026-10-15 note: the rest are LP withdrawals).

  python -I lab/wallets/explore/mev0_meedoen.py [DIR]   (in a 3G scope; ~250 MB of parquet)
"""
import os
import sys

import duckdb

D = sys.argv[1] if len(sys.argv) > 1 else "/home/chupa/Solana-project/data-old-faithful-one/lab/wallets/mev0-rerun/mev0-v1"
TMP = os.environ.get("LAB_SPILL_DIR", "/home/chupa/Solana-project/data-old-faithful-one/lab/tmp") + "/mev-join"
con = duckdb.connect()
con.execute(f"SET memory_limit='1400MB'; SET threads=2; SET temp_directory='{TMP}'")
con.execute(f"CREATE TABLE arbs AS SELECT *, net <= abs(legs_sol) AS explained FROM read_parquet(['{D}/arbs-1040.parquet','{D}/arbs-1045.parquet']) WHERE closed_loop")
con.execute(f"CREATE TABLE att AS SELECT * FROM read_parquet(['{D}/attempts-1040.parquet','{D}/attempts-1045.parquet'])")
# signers-<epoch> has one row per fee_payer per epoch: take the earliest first day per payer, else the join doubles arbs
con.execute(f"CREATE TABLE sg AS SELECT fee_payer, min(first_day) AS first_day, max(last_day) AS last_day, sum(txs) AS txs "
            f"FROM read_parquet(['{D}/signers-1040.parquet','{D}/signers-1045.parquet']) GROUP BY 1")
con.execute(f"CREATE TABLE days AS SELECT * FROM read_parquet(['{D}/days-1040.parquet','{D}/days-1045.parquet'])")
P = lambda title, q: print(f"== {title}\n" + con.execute(q).df().to_string() + "\n")  # noqa: E731

P("days (coverage: slots per day against ~322k)", "SELECT * FROM days ORDER BY day")
P("arbs per day: all / explained / reachable & explained, net SOL, fee payers", """
 SELECT day, count(*) n, sum(explained::INT) n_expl, sum((reachable AND explained)::INT) n_reach_expl, round(sum(net)/1e9,1) net_sol,
   round(sum(net) FILTER (WHERE explained)/1e9,1) net_expl, round(sum(net) FILTER (WHERE reachable AND explained)/1e9,1) net_reach_expl,
   count(DISTINCT fee_payer) payers, count(DISTINCT fee_payer) FILTER (WHERE reachable AND explained) payers_reach FROM arbs GROUP BY 1 ORDER BY 1""")
P("(1) speed: distance opportunity -> arb (explained backruns), by day", """
 SELECT day, count(*) n, round(quantile_cont(dist_slots,0.5),2) med_slots, round(quantile_cont(dist_slots,0.9),2) p90_slots,
   sum((dist_slots=0)::INT) same_slot, sum((dist_slots=1)::INT) next_slot, sum((dist_slots>=2)::INT) reach,
   round(sum(net) FILTER (WHERE dist_slots=0)/1e9,1) net_same, round(sum(net) FILTER (WHERE dist_slots=1)/1e9,1) net_next,
   round(sum(net) FILTER (WHERE dist_slots>=2)/1e9,1) net_reach FROM arbs WHERE backrun AND explained GROUP BY 1 ORDER BY 1""")
P("(1b) same-slot backruns: block-position distance and tipping", """
 SELECT day, count(*) n, round(quantile_cont(dist_tx,0.5),1) med_dtx, round(quantile_cont(dist_tx,0.9),1) p90_dtx,
   round(avg((jito_tip>0)::INT),3) tipped, round(avg((priority_fee>0)::INT),3) prio FROM arbs WHERE backrun AND explained AND dist_slots=0 GROUP BY 1 ORDER BY 1""")
con.execute("CREATE TABLE top AS SELECT fee_payer FROM arbs WHERE day='2026-09-29' AND explained AND reachable GROUP BY 1 ORDER BY sum(net) DESC LIMIT 3")
P("(1c) top-3 payers (ranked on reachable explained net) vs newcomers, 09-29 explained backruns, all distances", """
 SELECT CASE WHEN a.fee_payer IN (SELECT fee_payer FROM top) THEN 'top3' WHEN sg.first_day >= '2026-09-28' THEN 'newcomer' ELSE 'other' END grp,
   count(*) n, round(sum(a.net)/1e9,2) net_sol, round(quantile_cont(a.dist_slots,0.5),1) med_slots, round(avg((a.jito_tip>0)::INT),3) tipped,
   round(quantile_cont(a.jito_tip,0.5)/1e9,6) med_tip, round(quantile_cont(a.priority_fee,0.5)/1e9,6) med_prio
 FROM arbs a LEFT JOIN sg USING (fee_payer) WHERE a.day='2026-09-29' AND a.explained AND a.backrun GROUP BY 1 ORDER BY 1""")
P("(2) tips and fees as share of gross (explained arbs), per day", """
 SELECT day, count(*) n, round(sum(gross)/1e9,1) gross_sol, round(sum(net)/1e9,1) net_sol, round(sum(jito_tip)/1e9,2) tips_sol,
   round(sum(priority_fee)/1e9,3) prio_sol, round(sum(tx_fee)/1e9,3) txfee_sol, round(sum(jito_tip)/sum(gross),3) tip_share,
   round(sum(priority_fee+tx_fee)/sum(gross),3) fee_share, round(avg((jito_tip>0)::INT),3) tipped, round(quantile_cont(net,0.5)/1e9,5) med_net
 FROM arbs WHERE explained AND gross > 0 GROUP BY 1 ORDER BY 1""")
P("(2b) reachable explained 09-29: net per opportunity", """
 SELECT count(*) n, round(quantile_cont(net,0.5)/1e9,5) p50, round(quantile_cont(net,0.9)/1e9,4) p90, round(quantile_cont(net,0.99)/1e9,3) p99,
   round(max(net)/1e9,2) mx, round(sum(net)/1e9,2) total, sum((net>=1e7)::INT) n_ge_001, round(sum(net) FILTER (WHERE net>=1e7)/1e9,2) sol_ge_001
 FROM arbs WHERE day='2026-09-29' AND explained AND reachable""")
P("(3) failed attempts per day (upper bound)", """
 SELECT day, count(*) n, sum(by_winner::INT) by_winner, round(sum(cost)/1e9,2) cost_sol, count(DISTINCT fee_payer) payers,
   round(quantile_cont(cost,0.5)/1e9,6) med_cost FROM att GROUP BY 1 ORDER BY 1""")
P("(3b) 09-29 top-10 winners: arbs, net, tips, failed attempts", """
 WITH w AS (SELECT fee_payer, count(*) arbs, round(sum(net)/1e9,2) net_sol, round(sum(jito_tip)/1e9,3) tips FROM arbs WHERE day='2026-09-29' AND explained GROUP BY 1),
   f AS (SELECT fee_payer, count(*) fails, round(sum(cost)/1e9,3) fail_cost FROM att WHERE day='2026-09-29' GROUP BY 1)
 SELECT left(w.fee_payer,8) payer, w.arbs, w.net_sol, w.tips, COALESCE(f.fails,0) fails, COALESCE(f.fail_cost,0) fail_cost,
   round(COALESCE(f.fails,0)/(w.arbs+COALESCE(f.fails,0)),3) fail_share FROM w LEFT JOIN f USING (fee_payer) ORDER BY w.net_sol DESC LIMIT 10""")
P("(3c) 09-29 all winners: fail share and net after fail cost", """
 WITH w AS (SELECT fee_payer, count(*) arbs, sum(net) net FROM arbs WHERE day='2026-09-29' AND explained GROUP BY 1),
   f AS (SELECT fee_payer, count(*) fails, sum(cost) fcost FROM att WHERE day='2026-09-29' GROUP BY 1)
 SELECT count(*) winners, sum((COALESCE(f.fails,0)>0)::INT) with_fails, round(quantile_cont(COALESCE(f.fails,0)/(w.arbs+COALESCE(f.fails,0)),0.5),3) med_fail_share,
   round(sum(w.net)/1e9,1) net_sol, round(sum(COALESCE(f.fcost,0))/1e9,2) fail_cost_sol, sum((w.net - COALESCE(f.fcost,0) > 0)::INT) positive_after
 FROM w LEFT JOIN f USING (fee_payer)""")
P("(4) reachable explained backruns 09-29: arbs per trigger", """
 SELECT n_arbs, count(*) triggers, round(sum(net1)/1e9,2) net_first FROM (SELECT prior_slot, prior_tx, count(*) n_arbs, max(net) net1
   FROM arbs WHERE day='2026-09-29' AND explained AND backrun AND reachable GROUP BY 1,2) GROUP BY 1 ORDER BY 1""")
P("(4b) cumulative share of explained backrun net (09-29) by distance in slots", """
 SELECT dist_slots, count(*) n, round(sum(net)/1e9,2) net_sol, round(sum(sum(net)) OVER (ORDER BY dist_slots)/sum(sum(net)) OVER (),3) cum_share
 FROM arbs WHERE day='2026-09-29' AND explained AND backrun GROUP BY 1 ORDER BY 1 LIMIT 12""")
P("(4c) competing same-slot backruns on one trigger (all five days): tip vs block position", """
 WITH g AS (SELECT prior_slot, prior_tx, tx_index, jito_tip, priority_fee, count(*) OVER (PARTITION BY prior_slot, prior_tx) n
   FROM arbs WHERE explained AND backrun AND dist_slots=0)
 SELECT count(*) n_arbs, count(DISTINCT (prior_slot, prior_tx)) triggers, round(avg((jito_tip>0)::INT),3) tipped,
   corr(tx_index, jito_tip) corr_pos_tip, corr(tx_index, priority_fee) corr_pos_prio FROM g WHERE n > 1""")
