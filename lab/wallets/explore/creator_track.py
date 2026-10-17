"""Agenda idea 3 (lab/reports/2026-10-17-creator-trackrecord.md, E38): the creator's track record as a point-in-time feature at the create of a token, on the runners-v2 entries
(E27 method: per moment c1/c5/c15/f7, buckets of the feature, runner shares and simple-exit nets per 0.5 SOL, per day block)."""
import duckdb, time, os, glob
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab"); S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb"); W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
R=sorted(glob.glob(f"{DATA}/wallets/runners-v2/entries-*.parquet"))
t0=time.time(); log=lambda *a: print(round(time.time()-t0,1), *a, flush=True)
con=duckdb.connect(); con.execute(f"SET memory_limit='1400MB'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='20GB'")
con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
con.execute(f"CREATE TABLE e AS SELECT * FROM read_parquet({R!r}, union_by_name=true) WHERE NOT COALESCE(mayhem, false)")
log("entries", con.execute("SELECT count(*), count(DISTINCT mint) FROM e").fetchone())
# creator history from the mints table (all pump mints in dev): per mint the creator's earlier mints, their migration and their c1 runner outcome
con.execute("CREATE TABLE m AS SELECT mint, creator, create_slot::BIGINT AS create_slot, complete_slot::BIGINT AS complete_slot, migrate_slot::BIGINT AS migrate_slot FROM s.mints WHERE NOT COALESCE(mayhem, false)")
log("mints", con.execute("SELECT count(*), count(DISTINCT creator) FROM m").fetchone())
# c1 outcome per mint (one entry per mint for moment c1), with the slot at which the 6 h window ended (point-in-time usable after that)
con.execute("CREATE TABLE c1 AS SELECT mint, entry_slot + h6_slots AS known_slot, mult_6h >= 2 AS runner2, mult_6h >= 5 AS runner5, end_6h, x_tp2 FROM e WHERE moment = 'c1'")
con.execute("""CREATE TABLE hist AS SELECT a.mint, a.creator, a.create_slot,
    count(b.mint) AS prior_mints,
    count(b.mint) FILTER (WHERE b.complete_slot IS NOT NULL AND b.complete_slot < a.create_slot) AS prior_completed,
    count(c.mint) FILTER (WHERE c.known_slot < a.create_slot) AS prior_known,
    count(c.mint) FILTER (WHERE c.known_slot < a.create_slot AND c.runner2) AS prior_runner2,
    count(c.mint) FILTER (WHERE c.known_slot < a.create_slot AND c.runner5) AS prior_runner5,
    avg(c.end_6h) FILTER (WHERE c.known_slot < a.create_slot) AS prior_end6h_mean,
    min(b.create_slot) AS creator_first_create
  FROM m a LEFT JOIN m b ON b.creator = a.creator AND b.create_slot < a.create_slot LEFT JOIN c1 c ON c.mint = b.mint
  GROUP BY 1,2,3""")
log("hist", con.execute("SELECT count(*), round(avg((prior_mints>0)::INT),3), round(avg((prior_known>0)::INT),3) FROM hist").fetchone())
con.execute(f"COPY hist TO '{W}/creator_hist.parquet' (FORMAT PARQUET)")
con.execute("""CREATE TABLE x AS SELECT e.*, h.prior_mints, h.prior_completed, h.prior_known, h.prior_runner2, h.prior_runner5, h.prior_end6h_mean, (e.create_slot - h.creator_first_create) AS creator_age_slots,
    e.day >= '2026-09-20' AS out_block,
    CASE WHEN h.prior_mints = 0 THEN 'A first token' WHEN h.prior_known = 0 THEN 'B prior tokens, none with a known 6 h outcome' WHEN h.prior_runner2 > 0 THEN 'D prior token was a x2 runner' WHEN h.prior_completed > 0 THEN 'C prior token completed (migrated), no runner' ELSE 'E prior tokens, none completed, none runner' END AS cls
  FROM e JOIN hist h USING (mint)""")
def show(t, q):
    print("\n== "+t)
    for r in con.execute(q).fetchall(): print(r)
show("creator class share per moment: moment | cls | n | share", "SELECT moment, cls, count(*), round(count(*)::DOUBLE / sum(count(*)) OVER (PARTITION BY moment),3) FROM x GROUP BY 1,2 ORDER BY 1,2")
show("E27 table per moment x class x block: moment | cls | out | n | share x2 (6 h) | share x5 | mean end_6h | mean net SOL per entry: hold 6 h / tp x2 / trail30 / trail50 (x - 1)*cost",
 """SELECT moment, cls, out_block, count(*), round(avg((mult_6h>=2)::INT),3), round(avg((mult_6h>=5)::INT),3), round(avg(end_6h),3),
    round(avg((end_6h-1)*cost_sol),4), round(avg((x_tp2-1)*cost_sol),4), round(avg((x_tr30-1)*cost_sol),4), round(avg((x_tr50-1)*cost_sol),4)
    FROM x WHERE moment IN ('c1','c5','c15','f7') GROUP BY 1,2,3 ORDER BY 1,2,3""")
show("prior_mints buckets (c1): bucket | out | n | share x2 | mean net tp x2 / trail30", 
 """SELECT CASE WHEN prior_mints = 0 THEN '0' WHEN prior_mints < 3 THEN '1-2' WHEN prior_mints < 10 THEN '3-9' WHEN prior_mints < 50 THEN '10-49' ELSE '>=50' END, out_block, count(*), round(avg((mult_6h>=2)::INT),3), round(avg((x_tp2-1)*cost_sol),4), round(avg((x_tr30-1)*cost_sol),4)
    FROM x WHERE moment='c1' GROUP BY 1,2 ORDER BY 1,2""")
show("prior runner share (c1, creators with >= 3 known prior outcomes): share bucket | out | n | share x2 | mean net tp x2 / trail30",
 """SELECT CASE WHEN prior_runner2::DOUBLE/prior_known = 0 THEN '0' WHEN prior_runner2::DOUBLE/prior_known < 0.2 THEN '<20 %' WHEN prior_runner2::DOUBLE/prior_known < 0.5 THEN '20-50 %' ELSE '>=50 %' END, out_block, count(*), round(avg((mult_6h>=2)::INT),3), round(avg((x_tp2-1)*cost_sol),4), round(avg((x_tr30-1)*cost_sol),4)
    FROM x WHERE moment='c1' AND prior_known >= 3 GROUP BY 1,2 ORDER BY 1,2""")
show("same for f7 (reversed pools: creator = coin_creator of the mint? only if the mint is a pump mint): n with history", "SELECT count(*), sum((prior_mints IS NOT NULL)::INT) FROM x WHERE moment='f7'")
con.execute(f"COPY x TO '{W}/creator_entries.parquet' (FORMAT PARQUET)")
log("done")
