"""K2 step 2 (lab/reports/2026-10-17-k2-fabriekspools.md; run after k2_pools.py): a point-in-time rule at minute 2 (no cluster knowledge), its precision/recall against the factory label, and a
0.5 SOL simulation on every rule match with entries at minute 1 and 2 and exits 30 s .. 5 min later (d = 2 / 3), drains
included. Output k2_sim.parquet."""
import duckdb, time, os
import os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb")
W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
t0=time.time(); log=lambda *a: print(round(time.time()-t0,1), *a, flush=True)
con=duckdb.connect(); con.execute(f"SET memory_limit='1400MB'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='20GB'")
con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
con.execute(f"""CREATE TABLE p AS SELECT *, buy_sol - sell_sol AS inflow, b0/1e9 AS b0_sol, day >= '2026-09-20' AS out_block FROM '{W}/k2_pools.parquet' WHERE ft IS NOT NULL""")
rules = {
 "R0 one-off creator, never traded": "creator_prior_pools = 0 AND creator_n_trades IS NULL",
 "R1 R0 + b0 >= 300 SOL": "creator_prior_pools = 0 AND creator_n_trades IS NULL AND b0_sol >= 300",
 "R2 R1 + >= 40 traders in 2 min + inflow >= 30 SOL": "creator_prior_pools = 0 AND creator_n_trades IS NULL AND b0_sol >= 300 AND n_traders >= 40 AND inflow >= 30e9",
 "R3 R2 + no tips, no creator trades": "creator_prior_pools = 0 AND creator_n_trades IS NULL AND b0_sol >= 300 AND n_traders >= 40 AND inflow >= 30e9 AND COALESCE(tip_share,0) = 0 AND COALESCE(creator_trades,0) = 0",
 "R4 b0 >= 300 only": "b0_sol >= 300",
 "R5 >= 40 traders + inflow >= 30 SOL in 2 min (any creator)": "n_traders >= 40 AND inflow >= 30e9",
}
print("== rules: matches | factory among them (precision) | recall of the 3,312 | matches per day block (1-9, 10-18)")
for k, w in rules.items():
    r = con.execute(f"SELECT count(*), sum(factory::INT), round(sum(factory::INT)/3312.0,3), sum((NOT out_block)::INT), sum(out_block::INT) FROM p WHERE {w}").fetchone()
    print(k, r)
RULE = rules["R3 R2 + no tips, no creator trades"]
con.execute(f"CREATE TABLE sel AS SELECT pool, creator, create_slot, ft, factory, day, out_block, ms, COALESCE(bps,30) AS fb, ({RULE}) AS rule FROM p WHERE ({RULE}) OR factory")
log("selected", con.execute("SELECT count(*), sum(rule::INT), sum(factory::INT), sum((rule AND factory)::INT) FROM sel").fetchone())
EP=[(446256000+432000*i, 446256000+432000*(i+1)) for i in range(14)]
first=True
for lo,hi in EP:
    ms = 316.4 if lo < 446256000+432000*4 else 267.1
    sl = lambda s: round(s*1000/ms)
    con.execute(f"CREATE OR REPLACE TABLE se AS SELECT * FROM sel WHERE create_slot >= {lo} AND create_slot < {hi}")
    con.execute(f"""CREATE OR REPLACE TABLE r AS SELECT se.pool, p.slot::BIGINT AS slot, p.tx_index, p.kind, p.b::DOUBLE AS b, p.e::DOUBLE AS e
      FROM s.pool p JOIN se ON se.pool = p.pool WHERE p.slot >= se.ft AND p.slot < se.ft + {sl(120+300)+10}""")
    cols = []
    for ent_s in (60, 120):
        for d in (2, 3):
            cols.append(f"arg_max(struct_pack(q := b, t := e), (slot, tx_index)) FILTER (WHERE kind IN ('buy','sell') AND slot < se.ft + {sl(ent_s)} + {d}) AS e{ent_s}_{d}")
            for hold_s in (30, 60, 120, 180, 300):
                cols.append(f"arg_max(struct_pack(q := b, t := e), (slot, tx_index)) FILTER (WHERE kind IN ('buy','sell') AND slot < se.ft + {sl(ent_s)} + {sl(hold_s)} + {d}) AS x{ent_s}_{hold_s}_{d}")
                cols.append(f"bool_or(kind = 'withdraw' AND slot < se.ft + {sl(ent_s)} + {sl(hold_s)} + {d}) AS w{ent_s}_{hold_s}_{d}")
    con.execute(f"""CREATE OR REPLACE TABLE st AS SELECT se.pool, min(slot) FILTER (WHERE kind = 'withdraw') AS first_withdraw, {', '.join(cols)}
      FROM r JOIN se USING (pool) GROUP BY 1""")
    con.execute("CREATE OR REPLACE TABLE e AS SELECT se.*, st.* EXCLUDE (pool) FROM se LEFT JOIN st USING (pool)")
    if first: con.execute("CREATE TABLE allp AS SELECT * FROM e"); first=False
    else: con.execute("INSERT INTO allp SELECT * FROM e")
    log("epoch", lo, con.execute("SELECT count(*) FROM e").fetchone()[0])
con.execute("CREATE MACRO buy_tok(st, sol, f) AS CASE WHEN st IS NULL OR st.q <= 0 OR st.t <= 0 THEN NULL ELSE st.t - st.t * st.q / (st.q + sol * (1 - f/10000.0)) END")
con.execute("CREATE MACRO sell_sol(st, tok, f) AS CASE WHEN st IS NULL OR tok IS NULL OR st.q <= 0 OR st.t <= 0 THEN 0 ELSE (st.q - st.q * st.t / (st.t + tok)) * (1 - f/10000.0) END")
COST=2*705000
# a withdraw before the exit = the LP drain (D note: the first withdraw is the drain in 92 %): the position is written off
nets = [f"CASE WHEN COALESCE(w{e}_{h}_{d}, false) THEN -5e8 - {COST} ELSE sell_sol(x{e}_{h}_{d}, buy_tok(e{e}_{d}, 5e8, fb), fb) - 5e8 - {COST} END AS net_{e}_{h}_{d}" for e in (60,120) for d in (2,3) for h in (30,60,120,180,300)]
nets += [f"COALESCE(w{e}_{h}_{d}, false) AS drained_{e}_{h}_{d}" for e in (60,120) for d in (2,3) for h in (30,60,120,180,300)]
con.execute(f"CREATE TABLE sim AS SELECT * EXCLUDE ({', '.join(c for c in [x[0] for x in con.execute('DESCRIBE allp').fetchall()] if c.startswith(('e60_','e120_','x60_','x120_','w60_','w120_')))}), {', '.join(nets)} FROM allp")
con.execute(f"COPY sim TO '{W}/k2_sim.parquet' (FORMAT PARQUET)")
def show(t, q):
    print("\n== "+t)
    for r in con.execute(q).fetchall(): print(r)
for e in (60, 120):
    for d in (2, 3):
        show(f"entry minute {e//60}, d={d}: group | block | n | per hold 30/60/120/180/300 s: mean SOL per pool (share>0)",
          f"""SELECT CASE WHEN rule AND factory THEN 'rule&factory' WHEN rule THEN 'rule only' ELSE 'factory only' END AS g, out_block, count(*),
            {', '.join(f"round(avg(net_{e}_{h}_{d})/1e9,4) || ' (' || round(avg((net_{e}_{h}_{d}>0)::INT),2) || ')'" for h in (30,60,120,180,300))}
            FROM sim GROUP BY 1,2 ORDER BY 1,2""")
show("rule matches: NULL nets (no state) per column", "SELECT count(*), count(net_60_30_3), count(net_60_120_3), count(net_120_60_3), count(net_120_300_3) FROM sim WHERE rule")
show("rule matches: block | n | Σ per hold (entry min 1, d=3) 30/60/120/180/300 | drained share at 30/120/300",
  """SELECT out_block, count(*), round(sum(net_60_30_3)/1e9,1), round(sum(net_60_60_3)/1e9,1), round(sum(net_60_120_3)/1e9,1), round(sum(net_60_180_3)/1e9,1), round(sum(net_60_300_3)/1e9,1),
     round(avg(drained_60_30_3::INT),2), round(avg(drained_60_120_3::INT),2), round(avg(drained_60_300_3::INT),2) FROM sim WHERE rule GROUP BY 1 ORDER BY 1""")
show("rule matches: block | n | Σ per hold (entry min 2, d=3) 30/60/120/180/300 | share from factory pools at 60 s",
  """SELECT out_block, count(*), round(sum(net_120_30_3)/1e9,1), round(sum(net_120_60_3)/1e9,1), round(sum(net_120_120_3)/1e9,1), round(sum(net_120_180_3)/1e9,1), round(sum(net_120_300_3)/1e9,1),
     round(sum(CASE WHEN factory THEN net_120_60_3 END)/sum(net_120_60_3),2) FROM sim WHERE rule GROUP BY 1 ORDER BY 1""")
show("rule matches, entry minute 1, d=3, hold 30 s: per day | n | Σ SOL | share>0 | Σ factory pools | drained share",
  """SELECT day, count(*), round(sum(net_60_30_3)/1e9,2), round(avg((net_60_30_3>0)::INT),2), round(sum(CASE WHEN factory THEN net_60_30_3 END)/1e9,2), round(avg(drained_60_30_3::INT),2) FROM sim WHERE rule GROUP BY 1 ORDER BY 1""")
show("rule matches: p5 / median / p95 (entry 1, hold 30, d=3) and (entry 1, hold 60, d=3)",
  """SELECT round(quantile_cont(net_60_30_3,0.05)/1e9,3), round(median(net_60_30_3)/1e9,3), round(quantile_cont(net_60_30_3,0.95)/1e9,3), round(quantile_cont(net_60_60_3,0.05)/1e9,3), round(median(net_60_60_3)/1e9,3), round(quantile_cont(net_60_60_3,0.95)/1e9,3) FROM sim WHERE rule""")
show("drain timing of rule matches: share drained within 1/2/3/5/7 min after the first trade",
  """SELECT round(avg((first_withdraw < ft + round(60*1000/ms))::INT),3), round(avg((first_withdraw < ft + round(120*1000/ms))::INT),3), round(avg((first_withdraw < ft + round(180*1000/ms))::INT),3), round(avg((first_withdraw < ft + round(300*1000/ms))::INT),3), round(avg((first_withdraw < ft + round(420*1000/ms))::INT),3) FROM sim WHERE rule""")
log("done")
