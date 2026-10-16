"""Winners II (lab/reports/2026-10-17-winnaars-traag.md, exploratory): all store legs of group C (797) on dev <= 1046 and the first/last
price per venue key. Run order: slow_legs, slow_positions, slow_replay [per-cell], slow_tables, slow_reversed_check. DuckDB <= 1.4 GB, 3G scope,
store read-only; outputs under LAB_WALLETS_OUT/winners-v1 (c_legs, c_prices)."""
import duckdb, time
import os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb")
W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
X=f"{DATA}/backtests/exits-dev-1033-1046-befacff/parts/*.parquet"
t0=time.time(); log=lambda *a: print(round(time.time()-t0,1), *a, flush=True)
con=duckdb.connect(); con.execute(f"SET memory_limit='1400MB'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='6GB'")
con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
con.execute(f"CREATE TABLE c AS SELECT wallet FROM '{W}/closed_wallets.parquet' WHERE d_lab_c >= 7 AND d_lab_c_pos >= 7 AND net_lab_c > 10e9 AND d_out_c >= 3")
con.execute("CREATE TABLE days AS SELECT strftime(to_timestamp(block_time), '%Y-%m-%d') AS day, min(slot)::BIGINT AS slot0 FROM s.blocks GROUP BY 1 ORDER BY 2")
EP=[(446256000+432000*i, 446256000+432000*(i+1)) for i in range(14)]
first=True
for lo,hi in EP:
    con.execute(f"""CREATE OR REPLACE TABLE lg AS
SELECT c2.trader AS wallet, 'curve' AS venue, c2.mint, NULL::VARCHAR AS pool, c2.slot::BIGINT AS slot, c2.tx_index::BIGINT AS tx_index, c2.is_buy, c2.q::DOUBLE AS sol, c2.t::DOUBLE AS tok,
       c2.outer_program, c2.priority_fee::BIGINT AS priority_fee, c2.jito_tip::BIGINT AS jito_tip, c2.tx_fee::BIGINT AS tx_fee, c2.fee_payer IS NOT NULL AS other_payer,
       m.create_slot::BIGINT AS create_slot, NULL::BIGINT AS pool_create, FALSE AS farmer, 'curve' AS orientation,
       c2.vq::DOUBLE AS q_res, c2.vt::DOUBLE AS t_res, c2.vq::DOUBLE AS depth, (c2.fee_bps + c2.creator_bps)::INT AS bps, c2.mayhem_trade AS mayhem
FROM s.curve c2 JOIN c ON c.wallet = c2.trader LEFT JOIN s.mints m USING (mint) WHERE c2.slot >= {lo} AND c2.slot < {hi}
UNION ALL
SELECT p.trader, CASE WHEN p.orientation = 'reversed' THEN 'pool_reversed' ELSE 'pool' END, po.mint, p.pool, p.slot::BIGINT, p.tx_index, p.token_buy, p.sol_amount::DOUBLE, p.token_amount::DOUBLE,
       p.outer_program, p.priority_fee::BIGINT, p.jito_tip::BIGINT, p.tx_fee::BIGINT, p.fee_payer IS NOT NULL, m.create_slot::BIGINT, po.create_slot::BIGINT, p.farmer, p.orientation,
       CASE WHEN p.orientation = 'reversed' THEN p.b ELSE p.e END::DOUBLE, CASE WHEN p.orientation = 'reversed' THEN p.e ELSE p.b END::DOUBLE, p.sol_depth::DOUBLE, (p.lp_bps + p.protocol_bps + p.creator_bps)::INT, po.mayhem
FROM s.pool p JOIN c ON c.wallet = p.trader JOIN s.pools po USING (pool) LEFT JOIN s.mints m ON m.mint = po.mint
WHERE p.kind IN ('buy','sell') AND p.slot >= {lo} AND p.slot < {hi}""")
    if first: con.execute("CREATE TABLE legs AS SELECT * FROM lg"); first=False
    else: con.execute("INSERT INTO legs SELECT * FROM lg")
    log("epoch", lo, con.execute("SELECT count(*) FROM lg").fetchone()[0])
con.execute(f"COPY legs TO '{W}/c_legs.parquet' (FORMAT PARQUET)")
log("legs", con.execute("SELECT count(*), count(DISTINCT wallet), count(DISTINCT mint) FROM legs").fetchone())
# first and last price per key (mint on the curve; pool on pools) from ALL traders
con.execute("CREATE TABLE km AS SELECT DISTINCT mint FROM legs WHERE venue = 'curve'")
con.execute("CREATE TABLE kp AS SELECT DISTINCT pool, orientation FROM legs WHERE venue <> 'curve'")
log("keys", con.execute("SELECT (SELECT count(*) FROM km), (SELECT count(*) FROM kp)").fetchone())
first=True
for lo,hi in EP:
    con.execute(f"""CREATE OR REPLACE TABLE fp AS
SELECT 'curve' AS venue, mint AS key, arg_min(q/t, (slot, tx_index)) AS first_price, arg_max(q/t, (slot, tx_index)) AS last_price, arg_max(vq, (slot, tx_index))::DOUBLE AS last_depth,
       max(slot)::BIGINT AS last_slot, min(slot)::BIGINT AS first_slot, count(*) AS n_rows, count(DISTINCT trader) AS n_traders
FROM s.curve JOIN km USING (mint) WHERE slot >= {lo} AND slot < {hi} AND t > 0 GROUP BY 1,2
UNION ALL
SELECT 'pool', p.pool, arg_min(p.sol_amount::DOUBLE/p.token_amount, (p.slot, p.tx_index)), arg_max(p.sol_amount::DOUBLE/p.token_amount, (p.slot, p.tx_index)), arg_max(p.sol_depth, (p.slot, p.tx_index))::DOUBLE,
       max(p.slot)::BIGINT, min(p.slot)::BIGINT, count(*), count(DISTINCT p.trader)
FROM s.pool p JOIN kp USING (pool) WHERE p.kind IN ('buy','sell') AND p.slot >= {lo} AND p.slot < {hi} AND p.token_amount > 0 GROUP BY 1,2""")
    if first: con.execute("CREATE TABLE fpe AS SELECT * FROM fp"); first=False
    else: con.execute("INSERT INTO fpe SELECT * FROM fp")
    log("prices epoch", lo)
con.execute("""CREATE TABLE px AS SELECT venue, key, arg_min(first_price, first_slot) AS first_price, arg_max(last_price, last_slot) AS last_price, arg_max(last_depth, last_slot) AS last_depth,
  min(first_slot) AS first_slot, max(last_slot) AS last_slot, sum(n_rows) AS n_rows, max(n_traders) AS n_traders_epoch FROM fpe GROUP BY 1,2""")
con.execute(f"COPY px TO '{W}/c_prices.parquet' (FORMAT PARQUET)")
# positions per wallet x mint (all venues together); fee once per tx
con.execute("""CREATE TABLE lf AS SELECT *, CASE WHEN row_number() OVER (PARTITION BY wallet, slot, tx_index ORDER BY mint) = 1 THEN tx_fee + priority_fee + jito_tip ELSE 0 END AS fee FROM legs""")
con.execute("""CREATE TABLE pos AS
SELECT wallet, mint,
  min(CASE WHEN is_buy THEN slot END) AS fb, max(CASE WHEN is_buy THEN slot END) AS lb, min(CASE WHEN NOT is_buy THEN slot END) AS fs, max(CASE WHEN NOT is_buy THEN slot END) AS ls,
  count(*) FILTER (WHERE is_buy) AS n_buys, count(*) FILTER (WHERE NOT is_buy) AS n_sells,
  sum(CASE WHEN is_buy THEN sol END) AS buy_sol, sum(CASE WHEN NOT is_buy THEN sol END) AS sell_sol, sum(CASE WHEN is_buy THEN tok END) AS tok_in, sum(CASE WHEN NOT is_buy THEN tok END) AS tok_out, sum(fee) AS fees,
  arg_min(venue, CASE WHEN is_buy THEN slot ELSE 9e18 END) AS entry_venue, arg_min(pool, CASE WHEN is_buy THEN slot ELSE 9e18 END) AS entry_pool,
  arg_min(sol/tok, CASE WHEN is_buy THEN (slot, tx_index) ELSE (9e18, 0) END) AS entry_price, arg_min(depth, CASE WHEN is_buy THEN (slot, tx_index) ELSE (9e18, 0) END) AS entry_depth,
  arg_min(sol, CASE WHEN is_buy THEN (slot, tx_index) ELSE (9e18, 0) END) AS first_buy_sol, arg_min(bps, CASE WHEN is_buy THEN (slot, tx_index) ELSE (9e18, 0) END) AS entry_bps,
  arg_max(venue, CASE WHEN NOT is_buy THEN slot ELSE -1 END) AS exit_venue,
  any_value(create_slot) AS create_slot, arg_min(pool_create, CASE WHEN is_buy THEN slot ELSE 9e18 END) AS pool_create,
  avg(farmer::INT) AS farmer_share, median(priority_fee) AS prio, max(jito_tip) > 0 AS tipped, mode(outer_program) AS outer_program, bool_or(mayhem) AS mayhem, bool_or(other_payer) AS other_payer,
  count(DISTINCT venue) AS n_venues
FROM lf GROUP BY 1,2""")
con.execute("""CREATE TABLE pos2 AS
SELECT p.*, (SELECT max(day) FROM days WHERE slot0 <= p.fb) AS entry_day,
  CASE WHEN tok_in > 0 AND abs(tok_out - tok_in) <= 0.05 * tok_in THEN 'closed' WHEN tok_out > 1.05 * tok_in THEN 'tokens_in' WHEN fb IS NULL THEN 'tokens_in' ELSE 'open' END AS kind,
  sell_sol - buy_sol - fees AS net_cash,
  CASE WHEN tok_in > tok_out THEN tok_in - tok_out ELSE 0 END AS inv,
  COALESCE(pc.first_price, pm.first_price) AS first_price, COALESCE(pc.last_price, pm.last_price) AS last_price, COALESCE(pc.last_depth, pm.last_depth) AS last_depth,
  COALESCE(pc.n_traders_epoch, pm.n_traders_epoch) AS n_traders_epoch,
  CASE WHEN entry_venue = 'curve' THEN fb - create_slot ELSE fb - pool_create END AS entry_delay,
  ls - fb AS hold, fs - fb AS hold_first,
  CASE WHEN fb < 446256000 + 432000*4 THEN 316.4 ELSE 267.1 END AS ms
FROM pos p LEFT JOIN px pm ON pm.venue = 'pool' AND pm.key = p.entry_pool LEFT JOIN px pc ON pc.venue = 'curve' AND pc.key = p.mint AND p.entry_venue = 'curve'""")
# open inventory valued at the last price of the entry venue, capped at the venue's last SOL depth (a sale cannot take more than what is there)
con.execute("""CREATE TABLE pos3 AS SELECT *, CASE WHEN kind = 'open' AND inv > 0 THEN least(inv * COALESCE(last_price, 0), COALESCE(last_depth, 0)) ELSE 0 END AS open_value,
  CASE WHEN hold IS NULL THEN 'open/no sell' WHEN hold <= 3 THEN 'a <=3 slots' WHEN hold * ms <= 60e3 THEN 'b <=1 min' WHEN hold * ms <= 300e3 THEN 'c 1-5 min' WHEN hold * ms <= 900e3 THEN 'd 5-15 min'
       WHEN hold * ms <= 3600e3 THEN 'e 15-60 min' WHEN hold * ms <= 6*3600e3 THEN 'f 1-6 h' ELSE 'g >6 h' END AS hold_bucket FROM pos2""")
# F7 triggers (tp4_T600, d=1, all farms) of the entry pool: last trigger at or before the first buy, and the first after it
con.execute(f"CREATE TABLE trig AS SELECT DISTINCT pool, t, farm FROM read_parquet('{X}') WHERE variant = 'tp4_T600' AND d = 1 AND family = 'F7'")
con.execute("""CREATE TABLE pos4 AS SELECT p.*, 
  (SELECT max(t) FROM trig WHERE trig.pool = p.entry_pool AND trig.t <= p.fb) AS trig_before,
  (SELECT min(t) FROM trig WHERE trig.pool = p.entry_pool AND trig.t > p.fb AND trig.t <= COALESCE(p.ls, p.fb)) AS trig_during,
  (SELECT count(*) FROM trig WHERE trig.pool = p.entry_pool) AS n_trig_pool
FROM pos3 p""")
con.execute(f"COPY pos4 TO '{W}/c_pos.parquet' (FORMAT PARQUET)")
log("positions", con.execute("SELECT count(*), count(DISTINCT wallet) FROM pos4").fetchone())
print(con.execute("SELECT kind, count(*), round(sum(net_cash)/1e9), round(sum(open_value)/1e9) FROM pos4 GROUP BY 1").fetchall())
