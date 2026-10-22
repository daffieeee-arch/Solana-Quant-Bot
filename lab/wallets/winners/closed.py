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
con=duckdb.connect(); con.execute(f"SET memory_limit='1500MB'; SET threads TO 2; SET preserve_insertion_order=false; SET TimeZone='UTC'; SET temp_directory='{SPILL}'; SET max_temp_directory_size='6GB'")
con.execute(f"ATTACH '{STORE}' AS s (READ_ONLY)")
con.execute(f"CREATE TABLE g AS SELECT wallet, grp, lab_winner, d_lab, d_out, net_lab, net_out FROM '{OUT}/groups.parquet' WHERE grp = 'trader' AND d_lab >= 5 AND d_out >= 3")
con.execute("""CREATE TABLE samp AS
  SELECT wallet, lab_winner FROM g WHERE lab_winner
  UNION ALL
  SELECT wallet, lab_winner FROM (SELECT wallet, lab_winner FROM g WHERE NOT lab_winner ORDER BY hash(wallet) LIMIT 11195)""")
log("sample", con.execute("SELECT lab_winner, count(*) FROM samp GROUP BY 1").fetchall())
# UTC day boundaries from the blocks: first slot of each day
con.execute("""CREATE TABLE days AS SELECT strftime(to_timestamp(block_time), '%Y-%m-%d') AS day, min(slot) AS slot0 FROM s.blocks GROUP BY 1 ORDER BY 2""")
log("days", con.execute("SELECT count(*), min(day), max(day) FROM days").fetchone())
EPOCHS=[(446256000+432000*i, 446256000+432000*(i+1)) for i in range(14)]
first=True
for lo,hi in EPOCHS:
    con.execute(f"""CREATE OR REPLACE TABLE e AS
    WITH l AS (
      SELECT c.trader AS wallet, c.mint, c.slot, c.tx_index, c.is_buy, c.q AS sol, c.t AS tok, c.tx_fee + c.priority_fee + c.jito_tip AS fee
      FROM s.curve c JOIN samp ON samp.wallet = c.trader WHERE c.slot >= {lo} AND c.slot < {hi}
      UNION ALL
      SELECT p.trader, po.mint, p.slot, p.tx_index, p.token_buy, p.sol_amount, p.token_amount, p.tx_fee + p.priority_fee + p.jito_tip
      FROM s.pool p JOIN samp ON samp.wallet = p.trader JOIN s.pools po USING (pool) WHERE p.kind IN ('buy','sell') AND p.slot >= {lo} AND p.slot < {hi}),
    f AS (SELECT wallet, mint, slot, tx_index, is_buy, sol, tok, CASE WHEN row_number() OVER (PARTITION BY wallet, slot, tx_index ORDER BY mint) = 1 THEN fee ELSE 0 END AS fee FROM l)
    SELECT wallet, mint, (SELECT max(day) FROM days WHERE slot0 <= f.slot) AS day,
      sum(CASE WHEN is_buy THEN -sol ELSE sol END) AS cash, sum(fee) AS fees, sum(CASE WHEN is_buy THEN tok ELSE 0 END) AS tok_in, sum(CASE WHEN NOT is_buy THEN tok ELSE 0 END) AS tok_out, count(*) AS n
    FROM f GROUP BY 1,2,3""")
    if first: con.execute("CREATE TABLE wmd AS SELECT * FROM e"); first=False
    else: con.execute("INSERT INTO wmd SELECT * FROM e")
    log("epoch", lo, con.execute("SELECT count(*) FROM e").fetchone()[0])
con.execute(f"COPY wmd TO '{OUT}/closed_wmd.parquet' (FORMAT PARQUET)")
# closure per wallet x mint over all of dev
con.execute("""CREATE TABLE wm AS SELECT wallet, mint, sum(tok_in) AS tin, sum(tok_out) AS tout, sum(cash) - sum(fees) AS net,
  CASE WHEN sum(tok_in) > 0 AND abs(sum(tok_out) - sum(tok_in)) <= 0.05 * sum(tok_in) THEN 'closed'
       WHEN sum(tok_out) > 1.05 * sum(tok_in) THEN 'tokens_in' WHEN sum(tok_out) < 0.95 * sum(tok_in) THEN 'tokens_out_or_open' ELSE 'other' END AS kind FROM wmd GROUP BY 1,2""")
print("\n== wallet x mint rounds by kind: n, Σ net SOL (sample of 22,390 traders)")
for r in con.execute("SELECT kind, count(*), round(sum(net)/1e9) FROM wm GROUP BY 1 ORDER BY 2 DESC").fetchall(): print(r)
# per wallet: label on days 1-9 and outcome on days 10-18, closed rounds only vs all rounds
con.execute("""CREATE TABLE pw AS
SELECT w.wallet, s.lab_winner AS lab_winner_all,
  sum(CASE WHEN m.kind = 'closed' AND w.day < '2026-09-20' THEN w.cash - w.fees END) AS net_lab_c,
  count(DISTINCT CASE WHEN m.kind = 'closed' AND w.day < '2026-09-20' THEN w.day END) AS d_lab_c,
  count(DISTINCT CASE WHEN m.kind = 'closed' AND w.day < '2026-09-20' AND w.cash - w.fees > 0 THEN w.day END) AS d_lab_c_pos_mint,
  sum(CASE WHEN m.kind = 'closed' AND w.day >= '2026-09-20' AND w.day <= '2026-09-30' THEN w.cash - w.fees END) AS net_out_c,
  count(DISTINCT CASE WHEN m.kind = 'closed' AND w.day >= '2026-09-20' AND w.day <= '2026-09-30' THEN w.day END) AS d_out_c,
  sum(CASE WHEN w.day < '2026-09-20' THEN w.cash - w.fees END) AS net_lab_all, sum(CASE WHEN w.day >= '2026-09-20' AND w.day <= '2026-09-30' THEN w.cash - w.fees END) AS net_out_all,
  sum(CASE WHEN m.kind = 'closed' THEN 1 ELSE 0 END) / count(*) AS closed_share
FROM wmd w JOIN wm m USING (wallet, mint) JOIN samp s USING (wallet) GROUP BY 1,2""")
# day-level positivity on closed rounds needs per day sums
con.execute("""CREATE TABLE pd AS SELECT w.wallet, w.day, sum(w.cash - w.fees) AS net FROM wmd w JOIN wm m USING (wallet, mint) WHERE m.kind = 'closed' GROUP BY 1,2""")
con.execute("""CREATE TABLE pw2 AS SELECT p.*, 
  (SELECT count(*) FROM pd WHERE pd.wallet = p.wallet AND pd.day < '2026-09-20' AND pd.net > 0) AS d_lab_c_pos,
  (SELECT count(*) FROM pd WHERE pd.wallet = p.wallet AND pd.day >= '2026-09-20' AND pd.day <= '2026-09-30' AND pd.net > 0) AS d_out_c_pos
  FROM pw p""")
con.execute(f"COPY pw2 TO '{OUT}/closed_wallets.parquet' (FORMAT PARQUET)")
print("\n== check A (same label as before, from the store legs, ALL rounds): P(net_out>0) winners vs losers")
print(con.execute("SELECT lab_winner_all, count(*), round(avg((net_out_all>0)::INT),3), round(median(net_out_all)/1e9,2) FROM pw2 GROUP BY 1 ORDER BY 1 DESC").fetchall())
print("== check B (label AND outcome on CLOSED rounds only): label = d_lab_c >= 5, >= 60 % of closed-round days positive, net_lab_c > 0; outcome = net_out_c > 0 with d_out_c >= 3")
print(con.execute("""SELECT (d_lab_c >= 5 AND d_lab_c_pos >= 0.6*d_lab_c AND net_lab_c > 0) AS lab_w_closed, count(*), round(avg((net_out_c>0)::INT),3), round(median(net_out_c)/1e9,2), round(avg(net_out_c)/1e9,1)
  FROM pw2 WHERE d_lab_c >= 5 AND d_out_c >= 3 GROUP BY 1 ORDER BY 1 DESC""").fetchall())
print("== check C: closed-round winners, stricter (>= 7 of 9 label days positive, net_lab_c > 10 SOL)")
print(con.execute("""SELECT count(*), round(avg((net_out_c>0)::INT),3), round(median(net_out_c)/1e9,2), round(avg(net_out_c)/1e9,1) FROM pw2 WHERE d_lab_c >= 7 AND d_lab_c_pos >= 7 AND net_lab_c > 10e9 AND d_out_c >= 3""").fetchall())
print("== closed share of rounds: lab winners (old label) vs losers")
print(con.execute("SELECT lab_winner_all, round(avg(closed_share),2), round(median(closed_share),2) FROM pw2 GROUP BY 1").fetchall())
print("== old-label winners: net_out on closed rounds vs all rounds (medians, SOL)")
print(con.execute("SELECT lab_winner_all, round(median(net_out_c)/1e9,2), round(median(net_out_all)/1e9,2), round(sum(net_out_c)/1e9), round(sum(net_out_all)/1e9) FROM pw2 GROUP BY 1").fetchall())
log("done")
