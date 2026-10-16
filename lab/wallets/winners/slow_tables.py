import duckdb
import os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
con=duckdb.connect(); con.execute(f"SET memory_limit='1200MB'; SET threads TO 2; SET temp_directory='{DATA}/tmp/winners-v1'")
con.execute(f"""CREATE TABLE p AS SELECT *, entry_day >= '2026-09-20' AS out_block, net_cash + open_value AS net_val,
  CASE WHEN buy_sol < 1e9 THEN 's <1' WHEN buy_sol < 5e9 THEN 't 1-5' WHEN buy_sol < 20e9 THEN 'u 5-20' ELSE 'v >20' END AS size_b,
  CASE WHEN entry_delay IS NULL THEN 'onbekend' WHEN entry_delay * ms <= 60e3 THEN 'A <=1 min' WHEN entry_delay * ms <= 300e3 THEN 'B 1-5 min' WHEN entry_delay * ms <= 1800e3 THEN 'C 5-30 min' WHEN entry_delay * ms <= 6*3600e3 THEN 'D 30 min-6 h' WHEN entry_delay * ms <= 24*3600e3 THEN 'E 6-24 h' ELSE 'F >24 h' END AS age_b,
  CASE WHEN trig_before IS NULL THEN 'none' WHEN (fb - trig_before) * ms <= 300e3 THEN 'trig <=5 min before' WHEN (fb - trig_before) * ms <= 3600e3 THEN 'trig 5-60 min before' ELSE 'trig >1 h before' END AS trig_b
  FROM '{W}/c_pos.parquet' WHERE kind <> 'tokens_in'""")
def show(title, sql):
    print("\n== " + title); 
    for r in con.execute(sql).fetchall(): print(r)
show("1 kind x hold bucket: n | wallets | Σ net cash | Σ open value | win share | median SOL | mean SOL | top-1% share of positive",
 """SELECT kind, hold_bucket, count(*), count(DISTINCT wallet), round(sum(net_cash)/1e9), round(sum(open_value)/1e9), round(avg((net_val>0)::INT),2), round(median(net_val)/1e9,3), round(avg(net_val)/1e9,3),
    round(sum(CASE WHEN rk <= cnt*0.01 THEN net_val END)/sum(CASE WHEN net_val>0 THEN net_val END),2)
    FROM (SELECT *, row_number() OVER (PARTITION BY kind, hold_bucket ORDER BY net_val DESC) rk, count(*) OVER (PARTITION BY kind, hold_bucket) cnt FROM p) GROUP BY 1,2 ORDER BY 1,2""")
show("1b same, days 1-9 vs 10-18 (out_block): kind | bucket | out | n | Σ net_val | win | median",
 """SELECT kind, hold_bucket, out_block, count(*), round(sum(net_val)/1e9), round(avg((net_val>0)::INT),2), round(median(net_val)/1e9,3) FROM p GROUP BY 1,2,3 ORDER BY 1,2,3""")
show("2 slow (>=1 min or open) by venue x hold x size: n | wallets | Σ net_val | win | median | mean | Σ out-block",
 """SELECT entry_venue, hold_bucket, size_b, count(*), count(DISTINCT wallet), round(sum(net_val)/1e9), round(avg((net_val>0)::INT),2), round(median(net_val)/1e9,3), round(avg(net_val)/1e9,3), round(sum(CASE WHEN out_block THEN net_val END)/1e9)
    FROM p WHERE hold_bucket >= 'c' GROUP BY 1,2,3 ORDER BY 1,2,3""")
show("3 slow pool positions by pool age at entry: venue | age | n | Σ net_val | win | median | mean | mean size",
 """SELECT entry_venue, age_b, count(*), round(sum(net_val)/1e9), round(avg((net_val>0)::INT),2), round(median(net_val)/1e9,3), round(avg(net_val)/1e9,3), round(avg(buy_sol)/1e9,2) FROM p WHERE hold_bucket >= 'c' GROUP BY 1,2 ORDER BY 1,2""")
show("3b slow curve positions by price multiple at entry vs first price: n | Σ | win | median | mean",
 """SELECT CASE WHEN entry_price/first_price < 1.5 THEN '<1.5x' WHEN entry_price/first_price < 3 THEN '1.5-3x' WHEN entry_price/first_price < 10 THEN '3-10x' ELSE '>=10x' END, count(*), round(sum(net_val)/1e9), round(avg((net_val>0)::INT),2), round(median(net_val)/1e9,3), round(avg(net_val)/1e9,3)
    FROM p WHERE hold_bucket >= 'c' AND entry_venue = 'curve' AND first_price > 0 GROUP BY 1 ORDER BY 1""")
show("4 slow pool positions vs our F7 tp4 triggers (entry pool): venue | trig | n | Σ net_val | win | median | mean | share with trigger during hold",
 """SELECT entry_venue, trig_b, count(*), round(sum(net_val)/1e9), round(avg((net_val>0)::INT),2), round(median(net_val)/1e9,3), round(avg(net_val)/1e9,3), round(avg((trig_during IS NOT NULL)::INT),2) FROM p WHERE hold_bucket >= 'c' AND entry_venue <> 'curve' GROUP BY 1,2 ORDER BY 1,2""")
show("5 exits of slow closed positions: venue | hold | n | median sell/buy | share sold above last price (left before the end) | median last/entry price | share pool dead at end (last_depth < 1 SOL)",
 """SELECT entry_venue, hold_bucket, count(*), round(median(sell_sol/buy_sol),2), round(avg((sell_sol/buy_sol > COALESCE(last_price,0)/entry_price)::INT),2), round(median(COALESCE(last_price,0)/entry_price),3), round(avg((COALESCE(last_depth,0) < 1e9)::INT),2)
    FROM p WHERE hold_bucket >= 'c' AND kind = 'closed' AND entry_price > 0 GROUP BY 1,2 ORDER BY 1,2""")
# wallet archetypes: dominant hold bucket by position count among all positions, dominant venue
con.execute("""CREATE TABLE wa AS SELECT wallet, arg_max(hb, n) AS hold_arch, arg_max(ev, n2) AS venue_arch FROM
  (SELECT wallet, hold_bucket AS hb, count(*) n, NULL::VARCHAR ev, NULL::BIGINT n2 FROM p GROUP BY 1,2 UNION ALL SELECT wallet, NULL, NULL, entry_venue, count(*) FROM p GROUP BY 1,4) GROUP BY 1""")
show("6 wallet archetypes (dominant hold bucket x venue): n wallets | Σ net_val days 1-9 | Σ 10-18 | share wallets > 0 on 10-18 | median per wallet 10-18 | median positions/wallet | median buy SOL",
 """SELECT w.hold_arch, w.venue_arch, count(DISTINCT w.wallet), round(sum(CASE WHEN NOT out_block THEN net_val END)/1e9), round(sum(CASE WHEN out_block THEN net_val END)/1e9),
    round(avg(x.pos)::DOUBLE,2), round(median(x.o)/1e9,1), median(x.n), round(median(x.b)/1e9,2)
    FROM wa w JOIN p USING (wallet) JOIN (SELECT wallet, (sum(CASE WHEN out_block THEN net_val END) > 0)::INT AS pos, sum(CASE WHEN out_block THEN net_val END) AS o, count(*) AS n, median(buy_sol) AS b FROM p GROUP BY 1) x USING (wallet)
    GROUP BY 1,2 ORDER BY 1,2""")
show("6b slow wallets (dominant hold >= c): their slow positions only, 10-18: wallet | hold | venue | n | Σ | win | median",
 """SELECT left(w.wallet,8), w.hold_arch, w.venue_arch, count(*), round(sum(net_val)/1e9,1), round(avg((net_val>0)::INT),2), round(median(net_val)/1e9,3) FROM wa w JOIN p USING (wallet) WHERE w.hold_arch >= 'c' AND hold_bucket >= 'c' AND out_block GROUP BY 1,2,3 ORDER BY sum(net_val) DESC LIMIT 25""")
show("7 farmer share and other-payer among slow pool positions: venue | hold | n | mean farmer share | share other payer | share tipped | mean prio (lamports)",
 """SELECT entry_venue, hold_bucket, count(*), round(avg(farmer_share),2), round(avg(other_payer::INT),2), round(avg(tipped::INT),2), round(avg(prio)) FROM p WHERE hold_bucket >= 'c' GROUP BY 1,2 ORDER BY 1,2""")
show("8 open positions: venue | n | Σ cash (negative = still invested) | Σ open value | share open value > cost | median last/entry price",
 """SELECT entry_venue, count(*), round(sum(net_cash)/1e9), round(sum(open_value)/1e9), round(avg((open_value > buy_sol)::INT),2), round(median(COALESCE(last_price,0)/entry_price),3) FROM p WHERE kind = 'open' AND entry_price > 0 GROUP BY 1""")
