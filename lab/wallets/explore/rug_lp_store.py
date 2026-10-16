"""Agenda idea 4 / rug-pull check, step 1 (lab/reports/2026-10-17-rugpull-check-stap1.md, E36), store only: LP events of every reversed pool created in dev (deposits, withdraws, partial vs full,
lp_supply path, burn evidence = lp_supply drop without a withdraw), and the H4/H5 dev positions split by what the pool's LP
did during dev (diagnostic, not point-in-time)."""
import duckdb, time, os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab"); S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb"); W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
t0=time.time(); log=lambda *a: print(round(time.time()-t0,1), *a, flush=True)
con=duckdb.connect(); con.execute(f"SET memory_limit='1400MB'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='20GB'")
con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
con.execute(f"CREATE TABLE k AS SELECT pool, creator, create_slot, ft, ms, day, factory, b0 FROM '{W}/k2_pools.parquet'")
# every LP event of those pools over all of dev (one scan of the pool table)
con.execute("""CREATE TABLE lp AS SELECT p.pool, p.slot::BIGINT AS slot, p.tx_index, p.kind, p.trader, p.base::DOUBLE AS base, p.b::DOUBLE AS b, p.lp_amount::DOUBLE AS lp_amount, p.lp_supply::DOUBLE AS lp_supply,
     CASE WHEN p.kind = 'withdraw' THEN p.base::DOUBLE / nullif(p.b, 0) END AS frac
   FROM s.pool p JOIN k USING (pool) WHERE p.kind IN ('deposit', 'withdraw')""")
log("lp events", con.execute("SELECT kind, count(*), count(DISTINCT pool) FROM lp GROUP BY 1").fetchall())
con.execute("""CREATE TABLE per AS SELECT k.pool, k.factory, k.day, k.ft, k.ms, k.creator, k.b0,
     count(*) FILTER (WHERE kind = 'deposit') AS deposits, count(*) FILTER (WHERE kind = 'withdraw') AS withdraws,
     min(slot) FILTER (WHERE kind = 'withdraw') AS first_wd, max(frac) FILTER (WHERE kind = 'withdraw') AS max_frac,
     min(frac) FILTER (WHERE kind = 'withdraw') AS min_frac, arg_min(trader, slot) FILTER (WHERE kind = 'withdraw') AS first_wd_trader,
     arg_min(lp_supply, slot) FILTER (WHERE kind = 'deposit') AS lp0, arg_max(lp_supply, slot) AS lp_last,
     count(DISTINCT trader) FILTER (WHERE kind = 'withdraw') AS n_withdrawers
   FROM k LEFT JOIN lp USING (pool) GROUP BY ALL""")
def show(t, q):
    print("\n== "+t)
    for r in con.execute(q).fetchall(): print(r)
show("reversed pools in dev: withdraws during dev | pools | share | median b0 | median minutes first trade -> first withdraw | max frac median | first withdrawer = creator share",
 """SELECT CASE WHEN withdraws = 0 THEN 'never withdrawn in dev' WHEN max_frac >= 0.9 THEN 'full withdraw' WHEN max_frac >= 0.5 THEN 'partial >= 50 %' ELSE 'partial < 50 %' END, count(*), round(count(*)::DOUBLE/(SELECT count(*) FROM per),3), round(median(b0)/1e9,1),
    round(median((first_wd - ft) * ms / 60000),1), round(median(max_frac),2), round(avg((first_wd_trader = creator)::INT),3) FROM per GROUP BY 1 ORDER BY 2 DESC""")
show("never withdrawn: by creation day (censoring: late pools have less time) | pools | share never withdrawn", "SELECT day, count(*), round(avg((withdraws = 0)::INT),3) FROM per GROUP BY 1 ORDER BY 1")
show("lp_supply evidence: pools with >= 2 LP events | lp_last < lp0 without a withdraw (burn-like) | deposits > 1", "SELECT count(*) FILTER (WHERE deposits + withdraws >= 2), count(*) FILTER (WHERE withdraws = 0 AND lp_last < lp0), count(*) FILTER (WHERE deposits > 1) FROM per")
show("first withdrawer: creator | other; by class", "SELECT first_wd_trader = creator, count(*), round(avg(max_frac),2) FROM per WHERE withdraws > 0 GROUP BY 1")
# H4/H5 positions: diagnostic split by the pool's LP fate in dev (NOT point-in-time)
con.execute(f"CREATE TABLE j AS SELECT j.*, per.withdraws, per.first_wd, per.max_frac, per.b0 FROM '{W}/d1_h45_pools.parquet' j LEFT JOIN per USING (pool)")
show("H4/H5 positions by the pool's LP fate in dev (diagnostic, future info): fate | h | d | block | n | Σ | lost-all | mean",
 """SELECT CASE WHEN withdraws = 0 THEN 'never withdrawn in dev' WHEN first_wd > exit_slot THEN 'withdrawn after our exit' WHEN first_wd > t THEN 'withdrawn during the position' ELSE 'withdrawn (partly) before the trigger' END AS fate, h, d, out_block, count(*), round(sum(pnl)/1e9,1), round(avg(lost_all::INT),3), round(avg(pnl)/1e9,4)
    FROM j GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""")
show("point-in-time at t: a withdraw already happened before t (partial) — h | d | block | had_wd_before | n | Σ | lost-all | mean",
 """SELECT h, d, out_block, first_wd <= t AS had_wd_before, count(*), round(sum(pnl)/1e9,1), round(avg(lost_all::INT),3), round(avg(pnl)/1e9,4) FROM j GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""")
show("b0 (initial SOL, known at creation) vs fate (H5 d1): b0 bucket | n | Σ | lost-all | never withdrawn share",
 "SELECT CASE WHEN b0 < 50e9 THEN '<50' WHEN b0 < 100e9 THEN '50-100' WHEN b0 < 200e9 THEN '100-200' ELSE '>=200' END, count(*), round(sum(pnl)/1e9,1), round(avg(lost_all::INT),3), round(avg((withdraws=0)::INT),3) FROM j WHERE h='H5' AND d=1 GROUP BY 1 ORDER BY 1")
con.execute(f"COPY per TO '{W}/rug_lp_per_pool.parquet' (FORMAT PARQUET)")
log("done")
