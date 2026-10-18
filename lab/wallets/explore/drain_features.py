"""Agenda idea 1, step 2 (lab/reports/2026-10-17-drain-timing.md, E37): point-in-time features at the trigger slot t for every H4/H5 dev position (organic, d 1/2) and the drain
outcome after t (first withdraw - t). Features use only rows with slot < t. Output d2_trigger_features.parquet."""
import duckdb, time, os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab"); S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb"); W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
t0=time.time(); log=lambda *a: print(round(time.time()-t0,1), *a, flush=True)
con=duckdb.connect(); con.execute(f"SET memory_limit='1400MB'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='20GB'")
con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
con.execute(f"""CREATE TABLE tr AS SELECT DISTINCT pool, t FROM '{W}/d1_h45_pools.parquet'""")   # one row per (pool, t): the trigger
# creator_never_traded is point-in-time since Codex finding 7 (2026-10-18): no trade of the creator before the pool's create_slot
con.execute(f"""CREATE TABLE k AS SELECT pool, creator, create_slot, b0, creator_prior_pools, NOT COALESCE(creator_traded_before, false) AS creator_never_traded, ft, ms, day, factory FROM '{W}/k2_pools.parquet'""")
con.execute("CREATE TABLE trk AS SELECT tr.*, k.* EXCLUDE (pool) FROM tr JOIN k USING (pool)")
log("triggers", con.execute("SELECT count(*), count(DISTINCT pool) FROM trk").fetchone())
# all rows of those pools from the create to t + 6 h (bounded): features before t, outcome after t
EP=[(446256000+432000*i, 446256000+432000*(i+1)) for i in range(14)]
first=True
for lo,hi in EP:
    con.execute(f"CREATE OR REPLACE TABLE te AS SELECT * FROM trk WHERE t >= {lo} AND t < {hi}")
    con.execute(f"""CREATE OR REPLACE TABLE r AS SELECT te.pool, te.t, p.slot::BIGINT AS slot, p.tx_index, p.kind, p.trader, p.token_buy, p.sol_amount::DOUBLE AS sol, p.sol_depth::DOUBLE AS depth,
        p.jito_tip > 0 AS tipped, p.farmer, p.base::DOUBLE / nullif(p.b, 0) AS frac
      FROM s.pool p JOIN te ON te.pool = p.pool WHERE p.slot >= te.create_slot AND p.slot < te.t + round(6*3600*1000/te.ms) AND p.kind IN ('buy','sell','withdraw','deposit')""")
    con.execute("""CREATE OR REPLACE TABLE f AS SELECT te.pool, te.t,
        count(*) FILTER (WHERE slot < te.t AND kind IN ('buy','sell')) AS n_trades_pre, count(DISTINCT trader) FILTER (WHERE slot < te.t AND kind IN ('buy','sell')) AS n_traders_pre,
        count(DISTINCT trader) FILTER (WHERE slot < te.t AND kind IN ('buy','sell') AND token_buy) AS n_buyers_pre,
        sum(CASE WHEN token_buy THEN sol ELSE -sol END) FILTER (WHERE slot < te.t AND kind IN ('buy','sell')) AS inflow_pre,
        sum(sol) FILTER (WHERE slot < te.t AND kind IN ('buy','sell')) AS vol_pre,
        sum(CASE WHEN token_buy THEN sol ELSE -sol END) FILTER (WHERE slot < te.t AND slot >= te.t - round(60000/te.ms) AND kind IN ('buy','sell')) AS inflow_1m,
        sum(CASE WHEN token_buy THEN sol ELSE -sol END) FILTER (WHERE slot < te.t AND slot >= te.t - round(300000/te.ms) AND kind IN ('buy','sell')) AS inflow_5m,
        count(*) FILTER (WHERE slot < te.t AND slot >= te.t - round(60000/te.ms) AND kind IN ('buy','sell')) AS n_trades_1m,
        avg(tipped::INT) FILTER (WHERE slot < te.t AND kind IN ('buy','sell')) AS tip_share_pre, avg(farmer::INT) FILTER (WHERE slot < te.t AND kind IN ('buy','sell')) AS farmer_share_pre,
        count(*) FILTER (WHERE slot < te.t AND trader = te.creator AND kind IN ('buy','sell')) AS creator_trades_pre,
        sum(CASE WHEN token_buy THEN sol ELSE -sol END) FILTER (WHERE slot < te.t AND trader = te.creator AND kind IN ('buy','sell')) AS creator_net_pre,
        count(*) FILTER (WHERE slot < te.t AND kind = 'withdraw') AS wd_pre, max(frac) FILTER (WHERE slot < te.t AND kind = 'withdraw') AS wd_frac_pre,
        count(*) FILTER (WHERE slot < te.t AND kind = 'deposit') AS dep_pre,
        arg_max(depth, (slot, tx_index)) FILTER (WHERE slot < te.t AND kind IN ('buy','sell')) AS depth_t,
        max(depth) FILTER (WHERE slot < te.t AND kind IN ('buy','sell')) AS depth_max_pre,
        max(sol) FILTER (WHERE slot < te.t AND kind IN ('buy','sell') AND token_buy) AS max_buy_pre,
        max(sol) FILTER (WHERE slot < te.t AND kind IN ('buy','sell') AND NOT token_buy) AS max_sell_pre,
        min(slot) FILTER (WHERE slot >= te.t AND kind = 'withdraw') AS first_wd_after, arg_min(frac, slot) FILTER (WHERE slot >= te.t AND kind = 'withdraw') AS first_wd_frac_after,
        min(slot) FILTER (WHERE slot >= te.t AND kind = 'withdraw' AND frac >= 0.5) AS first_drain_after
      FROM r JOIN te USING (pool, t) GROUP BY 1,2""")
    con.execute("CREATE OR REPLACE TABLE e AS SELECT te.*, f.* EXCLUDE (pool, t) FROM te LEFT JOIN f USING (pool, t)")
    if first: con.execute("CREATE TABLE allf AS SELECT * FROM e"); first=False
    else: con.execute("INSERT INTO allf SELECT * FROM e")
    log("epoch", lo, con.execute("SELECT count(*) FROM e").fetchone()[0])
con.execute(f"COPY allf TO '{W}/d2_trigger_features.parquet' (FORMAT PARQUET)")
log("done", con.execute("SELECT count(*), count(first_wd_after), round(median((first_wd_after - t) * ms / 60000),1) FROM allf").fetchone())
