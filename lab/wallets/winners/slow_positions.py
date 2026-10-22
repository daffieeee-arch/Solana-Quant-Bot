"""Positions per wallet x mint of group C from c_legs.parquet (light: sums + entry/exit rows via row_number), prices from
c_prices.parquet, F7 trigger overlap. Output c_pos.parquet."""
import duckdb, time
import os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb")
W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
X=f"{DATA}/backtests/exits-dev-1033-1046-befacff/parts/*.parquet"
t0=time.time(); log=lambda *a: print(round(time.time()-t0,1), *a, flush=True)
con=duckdb.connect(); con.execute(f"SET memory_limit='1400MB'; SET threads TO 2; SET preserve_insertion_order=false; SET TimeZone='UTC'; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='20GB'")
con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
con.execute("CREATE TABLE days AS SELECT strftime(to_timestamp(block_time), '%Y-%m-%d') AS day, min(slot)::BIGINT AS slot0 FROM s.blocks GROUP BY 1 ORDER BY 2")
con.execute(f"""CREATE TABLE legs AS SELECT wallet, mint, venue, pool, slot, tx_index, is_buy, sol, tok, priority_fee, jito_tip, tx_fee, create_slot, pool_create, farmer, depth, bps, mayhem, other_payer, outer_program FROM '{W}/c_legs.parquet'""")
con.execute("CREATE TABLE txf AS SELECT wallet, min(mint) AS mint, any_value(tx_fee + priority_fee + jito_tip) AS fee FROM legs GROUP BY wallet, slot, tx_index")
con.execute("CREATE TABLE fees AS SELECT wallet, mint, sum(fee) AS fees FROM txf GROUP BY 1,2")
log("fees", con.execute("SELECT count(*) FROM fees").fetchone())
con.execute("""CREATE TABLE agg0 AS SELECT wallet, mint,
  min(CASE WHEN is_buy THEN slot END) AS fb, max(CASE WHEN is_buy THEN slot END) AS lb, min(CASE WHEN NOT is_buy THEN slot END) AS fs, max(CASE WHEN NOT is_buy THEN slot END) AS ls,
  count(*) FILTER (WHERE is_buy) AS n_buys, count(*) FILTER (WHERE NOT is_buy) AS n_sells,
  sum(CASE WHEN is_buy THEN sol END) AS buy_sol, sum(CASE WHEN NOT is_buy THEN sol END) AS sell_sol, sum(CASE WHEN is_buy THEN tok END) AS tok_in, sum(CASE WHEN NOT is_buy THEN tok END) AS tok_out,
  any_value(create_slot) AS create_slot, avg(farmer::INT) AS farmer_share, avg(priority_fee) AS prio, max(jito_tip) > 0 AS tipped, any_value(outer_program) AS outer_program,
  bool_or(mayhem) AS mayhem, bool_or(other_payer) AS other_payer, count(DISTINCT venue) AS n_venues
  FROM legs GROUP BY 1,2""")
con.execute("CREATE TABLE agg AS SELECT a.*, COALESCE(f.fees, 0) AS fees FROM agg0 a LEFT JOIN fees f USING (wallet, mint)")
log("agg", con.execute("SELECT count(*) FROM agg").fetchone())
con.execute("""CREATE TABLE e1 AS SELECT l.wallet, l.mint, min(l.tx_index) AS tx FROM legs l JOIN agg a ON a.wallet = l.wallet AND a.mint = l.mint AND l.slot = a.fb WHERE l.is_buy GROUP BY 1,2""")
con.execute("""CREATE TABLE ent AS SELECT l.wallet, l.mint, l.venue AS entry_venue, l.pool AS entry_pool, l.sol/l.tok AS entry_price, l.depth AS entry_depth, l.sol AS first_buy_sol, l.bps AS entry_bps, l.pool_create, CASE WHEN l.venue = 'curve' THEN l.mint END AS ckey, CASE WHEN l.venue <> 'curve' THEN l.pool END AS pkey
  FROM legs l JOIN e1 ON e1.wallet = l.wallet AND e1.mint = l.mint AND e1.tx = l.tx_index JOIN agg a ON a.wallet = l.wallet AND a.mint = l.mint AND l.slot = a.fb WHERE l.is_buy AND l.tok > 0""")
con.execute("""CREATE TABLE x1 AS SELECT l.wallet, l.mint, max(l.tx_index) AS tx FROM legs l JOIN agg a ON a.wallet = l.wallet AND a.mint = l.mint AND l.slot = a.ls WHERE NOT l.is_buy GROUP BY 1,2""")
con.execute("""CREATE TABLE ext AS SELECT l.wallet, l.mint, any_value(l.venue) AS exit_venue, any_value(l.pool) AS exit_pool FROM legs l JOIN x1 ON x1.wallet = l.wallet AND x1.mint = l.mint AND x1.tx = l.tx_index JOIN agg a ON a.wallet = l.wallet AND a.mint = l.mint AND l.slot = a.ls WHERE NOT l.is_buy GROUP BY 1,2""")
log("ent/ext", con.execute("SELECT (SELECT count(*) FROM ent), (SELECT count(*) FROM ext)").fetchone())
log("before pos2")
con.execute(f"CREATE TABLE pxp AS SELECT * FROM '{W}/c_prices.parquet' WHERE venue = 'pool'")
con.execute(f"CREATE TABLE pxc AS SELECT * FROM '{W}/c_prices.parquet' WHERE venue = 'curve'")
con.execute("""CREATE TABLE pos2 AS
SELECT a.*, e.* EXCLUDE (wallet, mint), x.* EXCLUDE (wallet, mint), d.day AS entry_day,
  CASE WHEN a.fb IS NULL THEN 'tokens_in' WHEN tok_in > 0 AND abs(tok_out - tok_in) <= 0.05 * tok_in THEN 'closed' WHEN tok_out > 1.05 * tok_in THEN 'tokens_in' ELSE 'open' END AS kind,
  sell_sol - buy_sol - fees AS net_cash, CASE WHEN tok_in > tok_out THEN tok_in - tok_out ELSE 0 END AS inv,
  COALESCE(pc.first_price, pm.first_price) AS first_price, COALESCE(pc.last_price, pm.last_price) AS last_price, COALESCE(pc.last_depth, pm.last_depth) AS last_depth,
  COALESCE(pc.n_traders_epoch, pm.n_traders_epoch) AS n_traders_epoch,
  CASE WHEN e.entry_venue = 'curve' THEN a.fb - a.create_slot ELSE a.fb - e.pool_create END AS entry_delay,
  a.ls - a.fb AS hold, a.fs - a.fb AS hold_first, CASE WHEN a.fb < 446256000 + 432000*4 THEN 316.4 ELSE 267.1 END AS ms
FROM agg a LEFT JOIN ent e USING (wallet, mint) LEFT JOIN ext x USING (wallet, mint) ASOF LEFT JOIN days d ON d.slot0 <= a.fb
LEFT JOIN pxp pm ON pm.key = e.pkey LEFT JOIN pxc pc ON pc.key = e.ckey""")
log("pos2 done")
con.execute("""CREATE TABLE pos3 AS SELECT *, CASE WHEN kind = 'open' AND inv > 0 THEN least(inv * COALESCE(last_price, 0), COALESCE(last_depth, 0)) ELSE 0 END AS open_value,
  CASE WHEN hold IS NULL THEN 'h open/no sell' WHEN hold <= 3 THEN 'a <=3 slots' WHEN hold * ms <= 60e3 THEN 'b <=1 min' WHEN hold * ms <= 300e3 THEN 'c 1-5 min' WHEN hold * ms <= 900e3 THEN 'd 5-15 min'
       WHEN hold * ms <= 3600e3 THEN 'e 15-60 min' WHEN hold * ms <= 6*3600e3 THEN 'f 1-6 h' ELSE 'g >6 h' END AS hold_bucket FROM pos2""")
con.execute(f"COPY pos3 TO '{W}/c_pos3.parquet' (FORMAT PARQUET)")
log("pos3 saved")
con.execute(f"CREATE TABLE trig AS SELECT DISTINCT pool, t, farm FROM read_parquet('{X}') WHERE variant = 'tp4_T600' AND d = 1 AND family = 'F7'")
log("trig", con.execute("SELECT count(*) FROM trig").fetchone())
con.execute("""CREATE TABLE tb AS SELECT p.wallet, p.mint, max(t.t) AS trig_before FROM pos3 p JOIN trig t ON t.pool = p.entry_pool AND t.t <= p.fb GROUP BY 1,2""")
con.execute("""CREATE TABLE td AS SELECT p.wallet, p.mint, min(t.t) AS trig_during FROM pos3 p JOIN trig t ON t.pool = p.entry_pool AND t.t > p.fb AND t.t <= COALESCE(p.ls, p.fb) GROUP BY 1,2""")
con.execute("CREATE TABLE tn AS SELECT pool, count(*) AS n_trig_pool FROM trig GROUP BY 1")
con.execute("CREATE TABLE pos4 AS SELECT p.*, tb.trig_before, td.trig_during, COALESCE(tn.n_trig_pool, 0) AS n_trig_pool FROM pos3 p LEFT JOIN tb USING (wallet, mint) LEFT JOIN td USING (wallet, mint) LEFT JOIN tn ON tn.pool = p.entry_pool")
con.execute(f"COPY pos4 TO '{W}/c_pos.parquet' (FORMAT PARQUET)")
log("positions", con.execute("SELECT count(*), count(DISTINCT wallet) FROM pos4").fetchone())
print(con.execute("SELECT kind, count(*), round(sum(net_cash)/1e9), round(sum(open_value)/1e9) FROM pos4 GROUP BY 1").fetchall())
