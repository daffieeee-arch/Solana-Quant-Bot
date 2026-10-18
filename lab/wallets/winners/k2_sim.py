"""K2 step 2 (lab/reports/2026-10-17-k2-fabriekspools.md; run after k2_pools.py): a point-in-time rule (no cluster
knowledge), its precision/recall against the factory label, and a 0.5 SOL simulation on every rule match with entries at
minute 1 and 2 and exits 30 s .. 5 min later (d = 2 / 3), drains included. Output k2_sim.parquet.

Codex round 2 (2026-10-18), point 1 (confirmed): the one rule R3 used the 2-minute features (traders, inflow, tips,
creator trades over [ft, ft + 2 min)) for the entry at minute 1 as well, a look-ahead of up to a minute. Now each entry
has its own rule on the information known at that entry: `rule_1m` (the `*_1m` columns of k2_pools, window
[ft, ft + 1 min)) for the minute-1 entry and `rule` (2 min) for the minute-2 entry; the thresholds are the same.

  PYTHONPATH=lab python -m wallets.winners.k2_sim [--smoke]   # --smoke: epochs 1040-1041 only (after k2_pools --smoke)
"""
import argparse, duckdb, time, os, sys
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb")
W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
MEM = os.environ.get("LAB_WALLETS_MEMORY", "1400MB")
t0=time.time(); log=lambda *a: print(round(time.time()-t0,1), *a, flush=True)
# the features per window: what k2_pools logged over [ft, ft + 2 min) and over [ft, ft + 1 min)
WINDOWS = {"2m": dict(traders="n_traders", inflow="inflow", tips="tip_share", ctr="creator_trades"),
           "1m": dict(traders="n_traders_1m", inflow="inflow_1m", tips="tip_share_1m", ctr="creator_trades_1m")}
ENTRY_WINDOW = {60: "1m", 120: "2m"}  # the entry (seconds after the first trade) and the window its rule may use


def rules_for(window):
    """R0..R5 on the features of `window` ('1m' or '2m'); R0/R1/R4 use creation-time information only."""
    c = WINDOWS[window]
    r0 = "creator_prior_pools = 0 AND NOT COALESCE(creator_traded_before, false)"
    r2 = f"{c['traders']} >= 40 AND {c['inflow']} >= 30e9"
    return {
        "R0 one-off creator, never traded before the creation": r0,
        "R1 R0 + b0 >= 300 SOL": f"{r0} AND b0_sol >= 300",
        f"R2 R1 + >= 40 traders + inflow >= 30 SOL in {window}": f"{r0} AND b0_sol >= 300 AND {r2}",
        f"R3 R2 + no tips, no creator trades in {window}": f"{r0} AND b0_sol >= 300 AND {r2} AND COALESCE({c['tips']},0) = 0 AND COALESCE({c['ctr']},0) = 0",
        "R4 b0 >= 300 only": "b0_sol >= 300",
        f"R5 >= 40 traders + inflow >= 30 SOL in {window} (any creator)": r2,
    }


def r3(window):
    return next(v for k, v in rules_for(window).items() if k.startswith("R3 "))


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--smoke", action="store_true"); a = ap.parse_args(argv)
    os.makedirs(f"{DATA}/tmp/winners-v1", exist_ok=True)
    con=duckdb.connect(); con.execute(f"SET memory_limit='{MEM}'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='20GB'")
    con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
    con.execute(f"""CREATE TABLE p AS SELECT *, buy_sol - sell_sol AS inflow, COALESCE(buy_sol_1m, 0) - COALESCE(sell_sol_1m, 0) AS inflow_1m, b0/1e9 AS b0_sol, day >= '2026-09-20' AS out_block FROM '{W}/k2_pools.parquet' WHERE ft IS NOT NULL""")
    nfac = con.execute("SELECT sum(factory::INT) FROM p").fetchone()[0]
    for window in ("2m", "1m"):
        print(f"== rules on the {window} features: matches | factory among them (precision) | recall of the {nfac} | matches per day block (1-9, 10-18)")
        for k, w in rules_for(window).items():
            r = con.execute(f"SELECT count(*), sum(factory::INT), round(sum(factory::INT)/{nfac},3), sum((NOT out_block)::INT), sum(out_block::INT) FROM p WHERE {w}").fetchone()
            print(k, r)
    RULE, RULE1 = r3("2m"), r3("1m")
    con.execute(f"CREATE TABLE sel AS SELECT pool, creator, create_slot, ft, factory, day, out_block, ms, COALESCE(bps,30) AS fb, ({RULE}) AS rule, ({RULE1}) AS rule_1m FROM p WHERE ({RULE}) OR ({RULE1}) OR factory")
    log("selected", con.execute("SELECT count(*), sum(rule::INT), sum(rule_1m::INT), sum((rule AND rule_1m)::INT), sum(factory::INT), sum((rule AND factory)::INT), sum((rule_1m AND factory)::INT) FROM sel").fetchone())
    EP=[(446256000+432000*i, 446256000+432000*(i+1)) for i in (range(7, 9) if a.smoke else range(14))]
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
    rc = {60: "rule_1m", 120: "rule"}  # the rule a row is judged on at each entry: minute 1 on the 1m rule, minute 2 on the 2m rule
    for e in (60, 120):
        for d in (2, 3):
            show(f"entry minute {e//60} (rule on the {ENTRY_WINDOW[e]} features), d={d}: group | block | n | per hold 30/60/120/180/300 s: mean SOL per pool (share>0)",
              f"""SELECT CASE WHEN {rc[e]} AND factory THEN 'rule&factory' WHEN {rc[e]} THEN 'rule only' ELSE 'factory only' END AS g, out_block, count(*),
                {', '.join(f"round(avg(net_{e}_{h}_{d})/1e9,4) || ' (' || round(avg((net_{e}_{h}_{d}>0)::INT),2) || ')'" for h in (30,60,120,180,300))}
                FROM sim WHERE {rc[e]} OR factory GROUP BY 1,2 ORDER BY 1,2""")
    show("rule matches: NULL nets (no state) per column (1m rule for minute 1, 2m rule for minute 2)", "SELECT count(*) FILTER (WHERE rule_1m), count(net_60_30_3) FILTER (WHERE rule_1m), count(net_60_120_3) FILTER (WHERE rule_1m), count(*) FILTER (WHERE rule), count(net_120_60_3) FILTER (WHERE rule), count(net_120_300_3) FILTER (WHERE rule) FROM sim")
    show("rule_1m matches: block | n | Σ per hold (entry min 1, d=3) 30/60/120/180/300 | drained share at 30/120/300",
      """SELECT out_block, count(*), round(sum(net_60_30_3)/1e9,1), round(sum(net_60_60_3)/1e9,1), round(sum(net_60_120_3)/1e9,1), round(sum(net_60_180_3)/1e9,1), round(sum(net_60_300_3)/1e9,1),
         round(avg(drained_60_30_3::INT),2), round(avg(drained_60_120_3::INT),2), round(avg(drained_60_300_3::INT),2) FROM sim WHERE rule_1m GROUP BY 1 ORDER BY 1""")
    show("rule (2m) matches: block | n | Σ per hold (entry min 2, d=3) 30/60/120/180/300 | share from factory pools at 60 s",
      """SELECT out_block, count(*), round(sum(net_120_30_3)/1e9,1), round(sum(net_120_60_3)/1e9,1), round(sum(net_120_120_3)/1e9,1), round(sum(net_120_180_3)/1e9,1), round(sum(net_120_300_3)/1e9,1),
         round(sum(CASE WHEN factory THEN net_120_60_3 END)/sum(net_120_60_3),2) FROM sim WHERE rule GROUP BY 1 ORDER BY 1""")
    show("rule_1m matches, entry minute 1, d=3, hold 30 s: per day | n | Σ SOL | share>0 | Σ factory pools | drained share",
      """SELECT day, count(*), round(sum(net_60_30_3)/1e9,2), round(avg((net_60_30_3>0)::INT),2), round(sum(CASE WHEN factory THEN net_60_30_3 END)/1e9,2), round(avg(drained_60_30_3::INT),2) FROM sim WHERE rule_1m GROUP BY 1 ORDER BY 1""")
    show("rule_1m matches: p5 / median / p95 (entry 1, hold 30, d=3) and (entry 1, hold 60, d=3)",
      """SELECT round(quantile_cont(net_60_30_3,0.05)/1e9,3), round(median(net_60_30_3)/1e9,3), round(quantile_cont(net_60_30_3,0.95)/1e9,3), round(quantile_cont(net_60_60_3,0.05)/1e9,3), round(median(net_60_60_3)/1e9,3), round(quantile_cont(net_60_60_3,0.95)/1e9,3) FROM sim WHERE rule_1m""")
    show("drain timing of rule_1m matches: share drained within 1/2/3/5/7 min after the first trade",
      """SELECT round(avg((first_withdraw < ft + round(60*1000/ms))::INT),3), round(avg((first_withdraw < ft + round(120*1000/ms))::INT),3), round(avg((first_withdraw < ft + round(180*1000/ms))::INT),3), round(avg((first_withdraw < ft + round(300*1000/ms))::INT),3), round(avg((first_withdraw < ft + round(420*1000/ms))::INT),3) FROM sim WHERE rule_1m""")
    show("old reading for comparison: the 2m rule judged at the minute-1 entry (look-ahead; d=3, hold 30/60 s): block | n | mean | Σ",
      """SELECT out_block, count(*), round(avg(net_60_30_3)/1e9,4), round(sum(net_60_30_3)/1e9,1), round(avg(net_60_60_3)/1e9,4), round(sum(net_60_60_3)/1e9,1) FROM sim WHERE rule GROUP BY 1 ORDER BY 1""")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
