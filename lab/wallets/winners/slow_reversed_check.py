"""The 25 reversed-pool wallets of group C: are they the pools' LP (deposit/withdraw rows), coin creator, or third parties?"""
import duckdb, time
import os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb")
W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
t0=time.time(); log=lambda *a: print(round(time.time()-t0,1), *a, flush=True)
con=duckdb.connect(); con.execute(f"SET memory_limit='1200MB'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='20GB'")
con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
con.execute(f"CREATE TABLE rw AS SELECT wallet, count(*) AS n_pos, count(*) FILTER (WHERE kind = 'closed') AS n_closed, round(sum(net_cash + open_value)/1e9,1) AS net FROM '{W}/c_pos.parquet' WHERE entry_venue = 'pool_reversed' GROUP BY 1 HAVING count(*) >= 100")
log("wallets", con.execute("SELECT count(*), sum(n_pos), sum(net) FROM rw").fetchone())
con.execute(f"CREATE TABLE rp AS SELECT DISTINCT p.wallet, p.entry_pool AS pool, p.fb, p.kind FROM '{W}/c_pos.parquet' p JOIN rw USING (wallet) WHERE entry_venue = 'pool_reversed'")
con.execute("CREATE TABLE pl AS SELECT DISTINCT pool FROM rp")
log("pools", con.execute("SELECT count(*) FROM pl").fetchone())
con.execute("""CREATE TABLE lp AS SELECT p.pool, p.kind, p.trader, min(p.slot) AS first_slot, count(*) AS n FROM s.pool p JOIN pl USING (pool) WHERE p.kind IN ('deposit','withdraw') GROUP BY 1,2,3""")
log("lp rows", con.execute("SELECT count(*) FROM lp").fetchone())
print("== per wallet: pools traded | pools where the wallet itself deposits/withdraws | pools where it is the coin_creator | pools with a withdraw by anyone | pools created in the period | median pool age at entry (slots)")
for r in con.execute("""SELECT left(rp.wallet, 10), rw.n_pos, rw.net, count(DISTINCT rp.pool), count(DISTINCT CASE WHEN lp.trader = rp.wallet THEN rp.pool END),
      count(DISTINCT CASE WHEN po.coin_creator = rp.wallet THEN rp.pool END), count(DISTINCT CASE WHEN lp.kind = 'withdraw' THEN rp.pool END), count(DISTINCT CASE WHEN po.created_in_period THEN rp.pool END),
      median(rp.fb - po.create_slot)
    FROM rp JOIN rw USING (wallet) LEFT JOIN lp ON lp.pool = rp.pool LEFT JOIN s.pools po ON po.pool = rp.pool GROUP BY 1,2,3 ORDER BY rw.net DESC""").fetchall(): print(r)
print("== who withdraws from these pools: distinct withdrawers, share of pools whose withdrawer is one of the 25, share where the withdrawer is the coin_creator")
print(con.execute("""SELECT count(DISTINCT lp.trader), round(avg((lp.trader IN (SELECT wallet FROM rw))::INT),3), round(avg((lp.trader = po.coin_creator)::INT),3), count(DISTINCT lp.pool)
    FROM lp JOIN s.pools po USING (pool) WHERE lp.kind = 'withdraw'""").fetchone())
print("== the 25 wallets' first-buy timing vs the pool's first trade of anyone: share where the wallet's buy is the pool's first trade (needs pool rows) -> via store first trade slot")
con.execute("""CREATE TABLE ft AS SELECT p.pool, min(p.slot) AS first_trade, arg_min(p.trader, (p.slot, p.tx_index)) AS first_trader FROM s.pool p JOIN pl USING (pool) WHERE p.kind IN ('buy','sell') GROUP BY 1""")
print(con.execute("""SELECT round(avg((ft.first_trader = rp.wallet)::INT),3), round(avg((rp.fb = ft.first_trade)::INT),3), round(median(rp.fb - ft.first_trade)) FROM rp JOIN ft USING (pool)""").fetchone())
log("done")
