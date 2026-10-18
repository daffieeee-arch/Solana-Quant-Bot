"""Agenda idea 2, first step (lab/reports/2026-10-17-verkenning-drains-whale.md, E35), one dev epoch: every curve buy >= X SOL on a curve <= 5 min old (not mayhem), the price multiple at that
moment, the buyer's farmer/known-wallet status, and the marks 1 / 5 / 15 / 60 min later for a 0.5 SOL follower entering
3 slots after the buy (engine curve math on the logged post-state; base costs; the curve's completion/migration = exit at the
last curve state). Output whale-<epoch>.parquet + tables."""
# no-quote-filter: consumed data, conclusions audited 2026-10-18 (lab/reports/2026-10-18-audit-niet-sol-quote.md)
import duckdb, time, sys, os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab"); S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb"); W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
E=int(sys.argv[1]) if len(sys.argv) > 1 else 1040; LO=E*432000; HI=LO+432000; MS=316.4 if E <= 1036 else 267.1
XMIN=float(sys.argv[2]) if len(sys.argv) > 2 else 10.0  # SOL
t0=time.time(); log=lambda *a: print(round(time.time()-t0,1), *a, flush=True)
con=duckdb.connect(); con.execute(f"SET memory_limit='1400MB'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='20GB'")
con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
sl = lambda sec: round(sec*1000/MS)
# big buys on young curves (mint created in the window, not mayhem)
con.execute(f"""CREATE TABLE big AS
  SELECT c.mint, c.slot::BIGINT AS slot, c.tx_index::BIGINT AS tx_index, c.q::DOUBLE AS sol, c.t::DOUBLE AS tok, c.trader, c.vq::DOUBLE AS vq_post, c.vt::DOUBLE AS vt_post, c.rq::DOUBLE AS rq_post,
         (c.fee_bps + c.creator_bps) AS bps, c.jito_tip > 0 AS tipped, m.create_slot::BIGINT AS create_slot, m.creator, c.trader = m.creator AS by_creator
  FROM s.curve c JOIN s.mints m USING (mint)
  WHERE c.is_buy AND c.q >= {XMIN}e9 AND c.slot >= {LO} AND c.slot < {HI} AND NOT COALESCE(m.mayhem, false) AND NOT c.mayhem_trade
    AND m.create_slot >= {LO} AND c.slot <= m.create_slot + {sl(300)}""")
log("big buys", con.execute("SELECT count(*), count(DISTINCT mint), count(DISTINCT trader) FROM big").fetchone())
# one event per mint: the first big buy (the follower reacts once)
con.execute("CREATE TABLE ev AS SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY mint ORDER BY slot, tx_index) rn FROM big) WHERE rn = 1")
# price before the buy, first price, and all later curve states of the mint within 60 min (+ 3 slots)
con.execute(f"""CREATE TABLE rows AS SELECT c.mint, c.slot::BIGINT AS slot, c.tx_index::BIGINT AS tx_index, c.vq::DOUBLE AS vq, c.vt::DOUBLE AS vt, c.q::DOUBLE AS q, c.t::DOUBLE AS t, c.is_buy, c.trader, c.variant
  FROM s.curve c JOIN ev ON ev.mint = c.mint WHERE c.slot >= ev.create_slot AND c.slot <= ev.slot + {sl(3600)} + 3""")
log("rows", con.execute("SELECT count(*) FROM rows").fetchone())
con.execute("""CREATE TABLE pre AS SELECT ev.mint,
  arg_min(r.q/r.t, (r.slot, r.tx_index)) FILTER (WHERE r.t > 0) AS first_price,
  arg_max(struct_pack(q := r.vq, t := r.vt), (r.slot, r.tx_index)) FILTER (WHERE (r.slot, r.tx_index) < (ev.slot, ev.tx_index)) AS st_before,
  count(*) FILTER (WHERE (r.slot, r.tx_index) < (ev.slot, ev.tx_index)) AS n_before, count(DISTINCT r.trader) FILTER (WHERE (r.slot, r.tx_index) < (ev.slot, ev.tx_index) AND r.is_buy) AS buyers_before,
  arg_max(struct_pack(q := r.vq, t := r.vt), (r.slot, r.tx_index)) FILTER (WHERE r.slot < ev.slot + 3) AS st_entry,
  bool_or(r.variant ILIKE '%complete%' AND r.slot < ev.slot + 3) AS complete_before_entry
  FROM ev JOIN rows r USING (mint) GROUP BY 1""")
cols = []
for h in (60, 300, 900, 3600):
    cols.append(f"arg_max(struct_pack(q := r.vq, t := r.vt), (r.slot, r.tx_index)) FILTER (WHERE r.slot < ev.slot + 3 + {sl(h)}) AS st_{h}")
    cols.append(f"bool_or(r.variant ILIKE '%complete%' AND r.slot < ev.slot + 3 + {sl(h)}) AS done_{h}")
    cols.append(f"max(r.vq) FILTER (WHERE r.slot >= ev.slot + 3 AND r.slot < ev.slot + 3 + {sl(h)}) AS maxvq_{h}")
con.execute(f"CREATE TABLE post AS SELECT ev.mint, {', '.join(cols)}, max(r.slot) AS last_slot FROM ev JOIN rows r USING (mint) GROUP BY 1")
con.execute("CREATE MACRO buy_tok(st, sol, f) AS CASE WHEN st IS NULL OR st.q <= 0 OR st.t <= 0 THEN NULL ELSE st.t - st.t * st.q / (st.q + sol * (1 - f/10000.0)) END")
con.execute("CREATE MACRO sell_sol(st, tok, f) AS CASE WHEN st IS NULL OR tok IS NULL OR st.q <= 0 OR st.t <= 0 THEN NULL ELSE (st.q - st.q * st.t / (st.t + tok)) * (1 - f/10000.0) END")
COST=2*705000
nets = ", ".join(f"CASE WHEN done_{h} THEN NULL ELSE sell_sol(st_{h}, buy_tok(st_entry, 5e8, bps), bps) - 5e8 - {COST} END AS net_{h}" for h in (60,300,900,3600))
con.execute(f"""CREATE TABLE out AS SELECT ev.*, pre.* EXCLUDE (mint), post.* EXCLUDE (mint), {MS} AS ms,
   CASE WHEN st_before IS NULL THEN NULL ELSE (st_before.q / st_before.t) / first_price END AS mult_before,
   (vq_post / vt_post) / first_price AS mult_after, (ev.slot - ev.create_slot) * {MS} / 1000 AS age_s, {nets}
   FROM ev JOIN pre USING (mint) JOIN post USING (mint)""")
con.execute(f"COPY out TO '{W}/whale-{E}.parquet' (FORMAT PARQUET)")
def show(t, q):
    print("\n== "+t)
    for r in con.execute(q).fetchall(): print(r)
show("events: n | mints | by creator share | tipped share | complete before entry | median age s | median mult before/after | median buyers before",
 "SELECT count(*), count(DISTINCT mint), round(avg(by_creator::INT),3), round(avg(tipped::INT),3), round(avg(complete_before_entry::INT),3), round(median(age_s)), round(median(mult_before),2), round(median(mult_after),2), median(buyers_before) FROM out")
show("0.5 SOL follower +3 slots: hold | n (not completed) | mean net | median | share>0 | p5 | p95 | completed share (NULL = migrated before the hold ends)",
 """SELECT h, count(n), round(avg(n)/1e9,4), round(median(n)/1e9,4), round(avg((n>0)::INT),3), round(quantile_cont(n,0.05)/1e9,3), round(quantile_cont(n,0.95)/1e9,3), round(1 - count(n)::DOUBLE/count(*),3)
    FROM (SELECT 60 h, net_60 n FROM out UNION ALL SELECT 300, net_300 FROM out UNION ALL SELECT 900, net_900 FROM out UNION ALL SELECT 3600, net_3600 FROM out) GROUP BY 1 ORDER BY 1""")
show("by buy size: size | n | mean net 60/300/900 s | share>0 at 300",
 "SELECT CASE WHEN sol < 20e9 THEN '10-20' WHEN sol < 40e9 THEN '20-40' ELSE '>=40' END, count(*), round(avg(net_60)/1e9,4), round(avg(net_300)/1e9,4), round(avg(net_900)/1e9,4), round(avg((net_300>0)::INT),3) FROM out GROUP BY 1 ORDER BY 1")
show("by mult before the buy: mult | n | mean net 60/300/900 s | share>0 at 300",
 "SELECT CASE WHEN mult_before IS NULL THEN 'first trade' WHEN mult_before < 1.5 THEN '<1.5x' WHEN mult_before < 3 THEN '1.5-3x' WHEN mult_before < 10 THEN '3-10x' ELSE '>=10x' END, count(*), round(avg(net_60)/1e9,4), round(avg(net_300)/1e9,4), round(avg(net_900)/1e9,4), round(avg((net_300>0)::INT),3) FROM out GROUP BY 1 ORDER BY 1")
show("by age at the buy: age | n | mean net 60/300/900 s | share>0 at 300",
 "SELECT CASE WHEN age_s < 10 THEN '<10 s' WHEN age_s < 60 THEN '10-60 s' WHEN age_s < 180 THEN '1-3 min' ELSE '3-5 min' END, count(*), round(avg(net_60)/1e9,4), round(avg(net_300)/1e9,4), round(avg(net_900)/1e9,4), round(avg((net_300>0)::INT),3) FROM out GROUP BY 1 ORDER BY 1")
show("by creator / tipped: by_creator | tipped | n | mean net 300 | share>0",
 "SELECT by_creator, tipped, count(*), round(avg(net_300)/1e9,4), round(avg((net_300>0)::INT),3) FROM out GROUP BY 1,2 ORDER BY 1,2")
show("by buyers before: n buyers | n | mean net 300/900 | share>0 at 300",
 "SELECT CASE WHEN buyers_before < 5 THEN '<5' WHEN buyers_before < 20 THEN '5-19' WHEN buyers_before < 50 THEN '20-49' ELSE '>=50' END, count(*), round(avg(net_300)/1e9,4), round(avg(net_900)/1e9,4), round(avg((net_300>0)::INT),3) FROM out GROUP BY 1 ORDER BY 1")
show("peak vq within 5/15 min after entry vs entry vq: share with peak >= 1.5x / 2x entry (what a trailing exit could catch)",
 "SELECT round(avg((maxvq_300 >= 1.5*st_entry.q)::INT),3), round(avg((maxvq_300 >= 2*st_entry.q)::INT),3), round(avg((maxvq_900 >= 1.5*st_entry.q)::INT),3), round(avg((maxvq_900 >= 2*st_entry.q)::INT),3) FROM out")
log("done")
