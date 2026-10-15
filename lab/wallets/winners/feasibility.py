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
import duckdb, time
t0=time.time()
def log(*a): print(round(time.time()-t0,1), *a, flush=True)
con=duckdb.connect(); con.execute(f"SET memory_limit='1500MB'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{SPILL}'; SET max_temp_directory_size='6GB'")
con.execute(f"ATTACH '{STORE}' AS s (READ_ONLY)")
con.execute(f"CREATE TABLE cw AS SELECT * FROM '{OUT}/closed_wallets.parquet'")
con.execute(f"CREATE TABLE P AS SELECT * FROM '{OUT}/profiles.parquet'")
con.execute(f"CREATE TABLE wmd AS SELECT * FROM '{OUT}/closed_wmd.parquet'")
con.execute("""CREATE TABLE wm AS SELECT wallet, mint, CASE WHEN sum(tok_in) > 0 AND abs(sum(tok_out) - sum(tok_in)) <= 0.05 * sum(tok_in) THEN 'closed' WHEN sum(tok_out) > 1.05 * sum(tok_in) THEN 'tokens_in' ELSE 'open' END AS kind FROM wmd GROUP BY 1,2""")
con.execute("CREATE TABLE grpC AS SELECT wallet FROM cw WHERE d_lab_c >= 7 AND d_lab_c_pos >= 7 AND net_lab_c > 10e9 AND d_out_c >= 3")
# ---- (1) valuation: inventory at 09-19 and 09-30 for open rounds, marks of money-v1 (price per token unit, depth = real SOL in the venue)
con.execute(f"CREATE TABLE marks AS SELECT mint, day, price, depth FROM read_parquet('{MV}/marks.parquet') WHERE day IN ('2026-09-19','2026-09-30')")
print("marks sample:", con.execute("SELECT * FROM marks LIMIT 2").fetchall())
con.execute("""CREATE TABLE inv AS
SELECT w.wallet, w.mint, m.kind,
  sum(CASE WHEN w.day <= '2026-09-19' THEN tok_in - tok_out ELSE 0 END) AS inv19, sum(CASE WHEN w.day <= '2026-09-30' THEN tok_in - tok_out ELSE 0 END) AS inv30,
  sum(CASE WHEN w.day >= '2026-09-20' AND w.day <= '2026-09-30' THEN cash - fees ELSE 0 END) AS net_out
FROM wmd w JOIN wm m USING (wallet, mint) GROUP BY 1,2,3""")
con.execute("""CREATE TABLE val AS
SELECT i.wallet, i.kind, sum(i.net_out) AS net_out,
  sum(least(greatest(i.inv19,0) * COALESCE(m19.price,0), COALESCE(m19.depth, 0))) AS v19, sum(least(greatest(i.inv30,0) * COALESCE(m30.price,0), COALESCE(m30.depth, 0))) AS v30
FROM inv i LEFT JOIN marks m19 ON m19.mint = i.mint AND m19.day = '2026-09-19' LEFT JOIN marks m30 ON m30.mint = i.mint AND m30.day = '2026-09-30' GROUP BY 1,2""")
def val_table(name, sel):
    print(f"\n== {name}: n | closed net 10-18 (Σ, median) | (a) closed+open, open at 0 (Σ, median, share>0) | (b) closed+open, open at last mark (Σ, median, share>0) | tokens-in net (onbekend, Σ)")
    print(con.execute(f"""WITH x AS (SELECT wallet, sum(CASE WHEN kind='closed' THEN net_out END) AS c, sum(CASE WHEN kind IN ('closed','open') THEN net_out END) AS a,
        sum(CASE WHEN kind IN ('closed','open') THEN net_out END) + sum(CASE WHEN kind='open' THEN v30 - v19 ELSE 0 END) AS b, sum(CASE WHEN kind='tokens_in' THEN net_out END) AS ti
        FROM val WHERE wallet IN ({sel}) GROUP BY 1)
      SELECT count(*), round(sum(c)/1e9), round(median(c)/1e9,1), round(sum(a)/1e9), round(median(a)/1e9,1), round(avg((a>0)::INT),2), round(sum(b)/1e9), round(median(b)/1e9,1), round(avg((b>0)::INT),2), round(sum(ti)/1e9) FROM x""").fetchone())
val_table("group C (797)", "SELECT wallet FROM grpC")
val_table("top-100 (closed-round net 10-18)", "SELECT wallet FROM P")
print("\n== per archetype (top-100): n | Σ closed | Σ (a) open at 0 | Σ (b) open at mark | median (a) | share (a) > 0")
print(con.execute("""WITH x AS (SELECT wallet, sum(CASE WHEN kind='closed' THEN net_out END) AS c, sum(CASE WHEN kind IN ('closed','open') THEN net_out END) AS a,
    sum(CASE WHEN kind IN ('closed','open') THEN net_out END) + sum(CASE WHEN kind='open' THEN v30 - v19 ELSE 0 END) AS b FROM val GROUP BY 1)
  SELECT P.arch, count(*), round(sum(c)/1e9), round(sum(a)/1e9), round(sum(b)/1e9), round(median(a)/1e9,1), round(avg((a>0)::INT),2) FROM x JOIN P USING (wallet) GROUP BY 1 ORDER BY 1""").fetchall())
# ---- (2) feasibility: sample closed curve positions of the top-100, same epoch for create..first_sell
con.execute(f"CREATE TABLE legs AS SELECT * FROM '{OUT}/legs.parquet' WHERE venue = 'curve'")
con.execute("""CREATE TABLE pos AS
SELECT l.wallet, P.arch, l.mint, any_value(l.create_slot) AS create_slot, min(CASE WHEN l.is_buy THEN l.slot END) AS first_buy,
  min(CASE WHEN NOT l.is_buy THEN l.slot END) AS first_sell, arg_min(l.tx_index, CASE WHEN NOT l.is_buy THEN l.slot END) AS sell_tx,
  sum(CASE WHEN l.is_buy AND l.slot = (SELECT min(slot) FROM legs x WHERE x.wallet = l.wallet AND x.mint = l.mint AND x.is_buy) THEN l.sol END) AS buy_sol,
  sum(CASE WHEN l.is_buy THEN l.sol END) AS buy_all, sum(CASE WHEN NOT l.is_buy THEN l.sol END) AS sell_all, count(*) FILTER (WHERE l.is_buy) AS n_buys,
  median(l.priority_fee) AS prio, max(l.jito_tip) > 0 AS tipped
FROM legs l JOIN P USING (wallet) JOIN wm m USING (wallet, mint) WHERE m.kind = 'closed' GROUP BY 1,2,3 HAVING first_sell IS NOT NULL AND first_buy IS NOT NULL""")
con.execute("""CREATE TABLE smp AS SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY wallet ORDER BY hash(mint)) rn FROM pos
  WHERE (create_slot - 446256000) // 432000 = (first_sell - 446256000) // 432000 AND first_sell - create_slot < 100000 AND n_buys = 1) WHERE rn <= 150""")
log("sampled positions", con.execute("SELECT arch, count(*) FROM smp GROUP BY 1").fetchall())
EPOCHS=[(446256000+432000*i, 446256000+432000*(i+1)) for i in range(14)]
first=True
for lo,hi in EPOCHS:
    con.execute(f"""CREATE OR REPLACE TABLE e AS
    SELECT sm.wallet, sm.arch, sm.mint, sm.create_slot, sm.first_buy, sm.first_sell, sm.buy_sol, sm.sell_all, sm.prio, sm.tipped,
      max(c.slot) FILTER (WHERE c.slot < sm.first_buy) AS prev_slot,
      count(*) FILTER (WHERE c.slot = sm.first_buy AND c.trader <> sm.wallet) AS n_same_slot_others,
      any_value(c.fee_bps + c.creator_bps) FILTER (WHERE c.trader = sm.wallet AND c.slot = sm.first_buy) AS bps,
      arg_max(struct_pack(vt := c.vt, vq := c.vq), c.slot * 100000 + c.tx_index) FILTER (WHERE c.slot < sm.first_buy) AS st0,
      arg_max(struct_pack(vt := c.vt, vq := c.vq), c.slot * 100000 + c.tx_index) FILTER (WHERE c.slot < sm.first_buy + 2) AS st2,
      arg_max(struct_pack(vt := c.vt, vq := c.vq), c.slot * 100000 + c.tx_index) FILTER (WHERE c.slot < sm.first_buy + 3) AS st3,
      arg_max(struct_pack(vt := c.vt, vq := c.vq), c.slot * 100000 + c.tx_index) FILTER (WHERE c.slot * 100000 + c.tx_index < sm.first_sell * 100000 + sm.sell_tx) AS sts,
      arg_max(struct_pack(vt := c.vt, vq := c.vq), c.slot * 100000 + c.tx_index) FILTER (WHERE c.slot < sm.first_sell + 2) AS sts2,
      arg_max(struct_pack(vt := c.vt, vq := c.vq), c.slot * 100000 + c.tx_index) FILTER (WHERE c.slot < sm.first_sell + 3) AS sts3,
      bool_or(c.slot >= sm.first_buy AND c.slot < sm.first_sell AND c.variant ILIKE '%complete%') AS completed_before_sell
    FROM smp sm JOIN s.curve c ON c.mint = sm.mint AND c.slot >= sm.create_slot AND c.slot <= sm.first_sell + 3
    WHERE sm.create_slot >= {lo} AND sm.create_slot < {hi} GROUP BY ALL""")
    if first: con.execute("CREATE TABLE ctx AS SELECT * FROM e"); first=False
    else: con.execute("INSERT INTO ctx SELECT * FROM e")
    log("epoch", lo, con.execute("SELECT count(*) FROM e").fetchone()[0])
con.execute(f"COPY ctx TO '{OUT}/feas_ctx.parquet' (FORMAT PARQUET)")
# replay with the constant-product curve on virtual reserves; fee on the SOL side (bps from their own buy row; default 125)
con.execute("""CREATE TABLE rp AS
SELECT *, COALESCE(bps, 125) AS fbps,
  (first_buy - COALESCE(prev_slot, create_slot)) AS react_slots, (first_buy - create_slot) AS delay_slots,
  CASE WHEN st0 IS NULL THEN NULL ELSE st0.vt - st0.vt * st0.vq / (st0.vq + buy_sol * (1 - COALESCE(bps,125)/10000.0)) END AS tok0,
  CASE WHEN st2 IS NULL THEN NULL ELSE st2.vt - st2.vt * st2.vq / (st2.vq + buy_sol * (1 - COALESCE(bps,125)/10000.0)) END AS tok2,
  CASE WHEN st3 IS NULL THEN NULL ELSE st3.vt - st3.vt * st3.vq / (st3.vq + buy_sol * (1 - COALESCE(bps,125)/10000.0)) END AS tok3
FROM ctx""")
con.execute("""CREATE TABLE rp2 AS SELECT *,
  CASE WHEN sts IS NULL OR tok0 IS NULL THEN NULL ELSE (sts.vq - sts.vq * sts.vt / (sts.vt + tok0)) * (1 - fbps/10000.0) - buy_sol - 2 * 720619 END AS net0,
  CASE WHEN sts2 IS NULL OR tok2 IS NULL THEN NULL ELSE (sts2.vq - sts2.vq * sts2.vt / (sts2.vt + tok2)) * (1 - fbps/10000.0) - buy_sol - 2 * 720619 END AS net2,
  CASE WHEN sts3 IS NULL OR tok3 IS NULL THEN NULL ELSE (sts3.vq - sts3.vq * sts3.vt / (sts3.vt + tok3)) * (1 - fbps/10000.0) - buy_sol - 2 * 720619 END AS net3,
  sell_all - buy_sol AS net_actual FROM rp""")
con.execute(f"COPY rp2 TO '{OUT}/feas_replay.parquet' (FORMAT PARQUET)")
print("\n== feasibility per archetype (sampled closed curve positions, 1 buy): n | median delay s after create | median reaction slots to previous trade | share same slot as create | share same slot as previous trade | share with other traders in the entry slot | median prio mSOL | tipped share | completed before sell share")
for r in con.execute("""SELECT arch, count(*), round(median(delay_slots)*0.27), median(react_slots), round(avg((first_buy = create_slot)::INT),2), round(avg((first_buy = prev_slot)::INT),2), round(avg((n_same_slot_others > 0)::INT),2), round(median(prio)/1e6,3), round(avg(tipped::INT),2), round(avg(completed_before_sell::INT),2)
  FROM rp2 GROUP BY 1 ORDER BY 1""").fetchall(): print(r)
print("\n== replay per archetype: n | actual net/position (mean, SOL) | replay d=0 (mean, median, share>0) | d=2 (mean, median, share>0) | d=3 (mean, median, share>0) | corr(actual, replay0)")
for r in con.execute("""SELECT arch, count(*), round(avg(net_actual)/1e9,3), round(avg(net0)/1e9,3), round(median(net0)/1e9,3), round(avg((net0>0)::INT),2), round(avg(net2)/1e9,3), round(median(net2)/1e9,3), round(avg((net2>0)::INT),2),
  round(avg(net3)/1e9,3), round(median(net3)/1e9,3), round(avg((net3>0)::INT),2), round(corr(net_actual, net0),2) FROM rp2 WHERE net0 IS NOT NULL GROUP BY 1 ORDER BY 1""").fetchall(): print(r)
print("\n== replay per wallet (A2 only, top 15 by sampled actual Σ): wallet | n | Σ actual | Σ d0 | Σ d2 | Σ d3 | share>0 d3")
for r in con.execute("""SELECT left(wallet,10), count(*), round(sum(net_actual)/1e9,1), round(sum(net0)/1e9,1), round(sum(net2)/1e9,1), round(sum(net3)/1e9,1), round(avg((net3>0)::INT),2) FROM rp2 WHERE arch LIKE 'A2%' AND net0 IS NOT NULL GROUP BY 1 ORDER BY sum(net_actual) DESC LIMIT 15""").fetchall(): print(r)
print("\n== A2 replay d=3 by entry delay bucket (s after create) and by reaction slots")
for r in con.execute("""SELECT CASE WHEN delay_slots < 37 THEN '<10 s' WHEN delay_slots < 111 THEN '10-30 s' WHEN delay_slots < 333 THEN '30-90 s' ELSE '>90 s' END b, count(*), round(avg(net0)/1e9,3), round(avg(net3)/1e9,3), round(avg((net3>0)::INT),2) FROM rp2 WHERE arch LIKE 'A2%' AND net0 IS NOT NULL GROUP BY 1 ORDER BY 1""").fetchall(): print(r)
for r in con.execute("""SELECT CASE WHEN react_slots <= 1 THEN '<=1' WHEN react_slots <= 3 THEN '2-3' WHEN react_slots <= 10 THEN '4-10' ELSE '>10' END b, count(*), round(avg(net0)/1e9,3), round(avg(net3)/1e9,3), round(avg((net3>0)::INT),2) FROM rp2 WHERE arch LIKE 'A2%' AND net0 IS NOT NULL GROUP BY 1 ORDER BY 1""").fetchall(): print(r)
log("done")
