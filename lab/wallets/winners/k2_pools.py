"""K2 point-in-time on dev (lab/reports/2026-10-17-k2-fabriekspools.md): every reversed pool created in dev (not mayhem) with what is visible in its first 2 minutes
(after its first trade), the creator's history before the pool, the states for a 0.5 SOL entry at minute 2 (+ d) and an
exit at minute 7 (+ d), and the factory label (traded by the 25-bot cluster of winners II). Output k2_pools.parquet.

Codex round 2 (2026-10-18), point 1: k2_sim's rule at the minute-1 entry used the 2-minute features (look-ahead). The
features now also exist over the first minute (`*_1m`: n_traders_1m, buy_sol_1m, sell_sol_1m, tip_share_1m,
creator_trades_1m; window [ft, ft + w2 // 2)), so the minute-1 entry has a rule on minute-1 information only. The
2-minute columns are unchanged (E34/E37 read them).

  PYTHONPATH=lab python -m wallets.winners.k2_pools [--smoke]   # --smoke: epochs 1040-1041 only

Reads c_pos.parquet (the factory label) from LAB_DATA_ROOT/wallets/winners-v1; writes under LAB_WALLETS_OUT/winners-v1
(default LAB_DATA_ROOT/wallets). DuckDB memory LAB_WALLETS_MEMORY (default 1400MB)."""
import argparse, duckdb, time, os, sys
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb"); W_IN = f"{DATA}/wallets/winners-v1"
W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
MEM = os.environ.get("LAB_WALLETS_MEMORY", "1400MB")
t0=time.time(); log=lambda *a: print(round(time.time()-t0,1), *a, flush=True)


def main(argv=None):
  ap = argparse.ArgumentParser(); ap.add_argument("--smoke", action="store_true"); a = ap.parse_args(argv)
  os.makedirs(W, exist_ok=True); os.makedirs(f"{DATA}/tmp/winners-v1", exist_ok=True)
  con=duckdb.connect(); con.execute(f"SET memory_limit='{MEM}'; SET threads TO 2; SET preserve_insertion_order=false; SET TimeZone='UTC'; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='20GB'")
  con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
  con.execute(f"CREATE TABLE fac AS SELECT DISTINCT entry_pool AS pool FROM '{W_IN}/c_pos.parquet' WHERE entry_venue = 'pool_reversed' AND wallet IN (SELECT wallet FROM '{W_IN}/c_pos.parquet' WHERE entry_venue = 'pool_reversed' GROUP BY 1 HAVING count(*) >= 100)")
  con.execute("CREATE TABLE days AS SELECT strftime(to_timestamp(block_time), '%Y-%m-%d') AS day, min(slot)::BIGINT AS slot0 FROM s.blocks GROUP BY 1 ORDER BY 2")
  con.execute("""CREATE TABLE pools AS SELECT po.pool, po.coin_creator AS creator, po.create_slot::BIGINT AS create_slot, po.b0, po.q0, po.quote_mint, po.mint, po.can_boost,
    (po.pool IN (SELECT pool FROM fac)) AS factory FROM s.pools po WHERE po.orientation = 'reversed' AND po.created_in_period AND NOT COALESCE(po.mayhem, false)""")
  log("pools", con.execute("SELECT count(*), sum(factory::INT) FROM pools").fetchone())
  # creator history from the pools table (cheap) and from all trades (one pass)
  con.execute("""CREATE TABLE ch AS SELECT p.pool, count(q.pool) AS creator_prior_pools, count(q.pool) FILTER (WHERE q.orientation = 'reversed') AS creator_prior_reversed
    FROM pools p LEFT JOIN s.pools q ON q.coin_creator = p.creator AND q.create_slot < p.create_slot GROUP BY 1""")
  con.execute("CREATE TABLE creators AS SELECT DISTINCT creator FROM pools")
  # Codex finding 7 (2026-10-18): n_trades counts the creator's trades over the WHOLE dev period (also after the pool's
  # creation): kept as creator_n_trades_period for reporting only. Point-in-time: creator_trades_before = the creator's
  # trades before the pool's create_slot (creator_traded_before = that count > 0).
  con.execute("""CREATE TABLE ctr AS SELECT p.trader AS creator, p.slot::BIGINT AS slot FROM s.pool p JOIN creators c ON c.creator = p.trader WHERE p.kind IN ('buy','sell')
    UNION ALL SELECT c2.trader, c2.slot::BIGINT FROM s.curve c2 JOIN creators c ON c.creator = c2.trader""")
  con.execute("CREATE TABLE ct AS SELECT creator, min(slot) AS first_trade_slot, count(*) AS n_trades FROM ctr GROUP BY 1")
  con.execute("CREATE TABLE ctb AS SELECT p.pool, count(ctr.slot) AS creator_trades_before FROM pools p LEFT JOIN ctr ON ctr.creator = p.creator AND ctr.slot < p.create_slot GROUP BY 1")
  log("creator history", con.execute("SELECT count(*) FROM ct").fetchone(), con.execute("SELECT sum((creator_trades_before > 0)::INT) FROM ctb").fetchone())
  EP=[(446256000+432000*i, 446256000+432000*(i+1)) for i in (range(7, 9) if a.smoke else range(14))]
  first=True
  for lo,hi in EP:
      ms = 316.4 if lo < 446256000+432000*4 else 267.1
      w2 = round(120_000/ms); w1 = w2 // 2; ent = round(120_000/ms); hold = round(300_000/ms)
      con.execute(f"""CREATE OR REPLACE TABLE pe AS SELECT * FROM pools WHERE create_slot >= {lo} AND create_slot < {hi}""")
      con.execute(f"""CREATE OR REPLACE TABLE r AS SELECT p.pool, p.slot::BIGINT AS slot, p.tx_index, p.kind, p.trader, p.token_buy, p.sol_amount::DOUBLE AS sol, p.sol_depth::DOUBLE AS depth,
          p.token_amount::DOUBLE AS tok, p.b::DOUBLE AS b, p.e::DOUBLE AS e, (p.lp_bps + p.protocol_bps + p.creator_bps) AS bps, p.jito_tip > 0 AS tipped, p.lp_supply, p.lp_amount, p.farmer
        FROM s.pool p JOIN pe ON pe.pool = p.pool WHERE p.slot >= pe.create_slot AND p.slot < pe.create_slot + {w2 + ent + hold + 60}""")
      con.execute("""CREATE OR REPLACE TABLE ft AS SELECT pool, min(slot) AS ft, arg_min(sol/tok, (slot, tx_index)) AS p0, arg_min(depth, (slot, tx_index)) AS depth0, arg_min(trader, (slot, tx_index)) AS first_trader
        FROM r WHERE kind IN ('buy','sell') AND tok > 0 GROUP BY 1""")
      con.execute(f"""CREATE OR REPLACE TABLE f2 AS SELECT r.pool,
          count(*) FILTER (WHERE kind IN ('buy','sell')) AS n_trades, count(DISTINCT trader) FILTER (WHERE kind IN ('buy','sell')) AS n_traders,
          sum(CASE WHEN token_buy THEN sol END) AS buy_sol, sum(CASE WHEN NOT token_buy THEN sol END) AS sell_sol, max(CASE WHEN token_buy THEN sol END) AS max_buy,
          arg_max(sol/tok, (slot, tx_index)) FILTER (WHERE kind IN ('buy','sell') AND tok > 0) AS p2, arg_max(depth, (slot, tx_index)) FILTER (WHERE kind IN ('buy','sell')) AS depth2,
          avg(tipped::INT) FILTER (WHERE kind IN ('buy','sell')) AS tip_share, avg(farmer::INT) FILTER (WHERE kind IN ('buy','sell')) AS farmer_share,
          count(*) FILTER (WHERE kind IN ('buy','sell') AND trader = pe.creator) AS creator_trades, count(*) FILTER (WHERE kind = 'deposit') AS deposits, count(*) FILTER (WHERE kind = 'withdraw') AS withdraws,
          count(*) FILTER (WHERE kind = 'boost') AS boosts, max(lp_supply) AS lp_supply, count(DISTINCT trader) FILTER (WHERE kind IN ('buy','sell') AND token_buy) AS n_buyers,
          sum(CASE WHEN token_buy THEN sol END) FILTER (WHERE slot < ft.ft + {w1}) AS buy_sol_1m,
          sum(CASE WHEN NOT token_buy THEN sol END) FILTER (WHERE slot < ft.ft + {w1}) AS sell_sol_1m,
          count(DISTINCT trader) FILTER (WHERE kind IN ('buy','sell') AND slot < ft.ft + {w1}) AS n_traders_1m,
          avg(tipped::INT) FILTER (WHERE kind IN ('buy','sell') AND slot < ft.ft + {w1}) AS tip_share_1m,
          count(*) FILTER (WHERE kind IN ('buy','sell') AND trader = pe.creator AND slot < ft.ft + {w1}) AS creator_trades_1m
        FROM r JOIN ft USING (pool) JOIN pe USING (pool) WHERE r.slot >= ft.ft AND r.slot < ft.ft + {w2} GROUP BY 1""")
      # states for the simulation: last row (any kind) before entry/exit slots, d = 2 and 3; drain before exit
      con.execute(f"""CREATE OR REPLACE TABLE st AS SELECT r.pool,
          arg_max(struct_pack(q := b, t := e), (slot, tx_index)) FILTER (WHERE slot < ft.ft + {ent} + 2) AS e2,
          arg_max(struct_pack(q := b, t := e), (slot, tx_index)) FILTER (WHERE slot < ft.ft + {ent} + 3) AS e3,
          arg_max(struct_pack(q := b, t := e), (slot, tx_index)) FILTER (WHERE slot < ft.ft + {ent} + {hold} + 2) AS x2,
          arg_max(struct_pack(q := b, t := e), (slot, tx_index)) FILTER (WHERE slot < ft.ft + {ent} + {hold} + 3) AS x3,
          min(slot) FILTER (WHERE kind = 'withdraw') AS first_withdraw, max(slot) FILTER (WHERE kind IN ('buy','sell')) AS last_trade_seen,
          any_value(bps) AS bps, count(*) FILTER (WHERE kind IN ('buy','sell') AND slot >= ft.ft + {ent} AND slot < ft.ft + {ent} + {hold}) AS trades_in_hold
        FROM r JOIN ft USING (pool) GROUP BY 1""")
      con.execute(f"""CREATE OR REPLACE TABLE e AS SELECT pe.*, ch.creator_prior_pools, ch.creator_prior_reversed, ct.first_trade_slot AS creator_first_trade, ct.n_trades AS creator_n_trades_period,
          ctb.creator_trades_before, (ctb.creator_trades_before > 0) AS creator_traded_before, ft.ft, ft.p0, ft.depth0, ft.first_trader = pe.creator AS creator_first_trade_in_pool, ft.ft - pe.create_slot AS create_to_first,
          f2.* EXCLUDE (pool), st.* EXCLUDE (pool), {ms} AS ms, {ent} AS ent_slots, {hold} AS hold_slots, d.day
        FROM pe LEFT JOIN ch USING (pool) LEFT JOIN ct ON ct.creator = pe.creator LEFT JOIN ctb USING (pool) LEFT JOIN ft USING (pool) LEFT JOIN f2 USING (pool) LEFT JOIN st USING (pool)
        ASOF LEFT JOIN days d ON d.slot0 <= pe.create_slot""")
      if first: con.execute("CREATE TABLE allp AS SELECT * FROM e"); first=False
      else: con.execute("INSERT INTO allp SELECT * FROM e")
      log("epoch", lo, con.execute("SELECT count(*), count(ft), sum(factory::INT) FROM e").fetchone())
  con.execute(f"COPY allp TO '{W}/k2_pools.parquet' (FORMAT PARQUET)")
  log("done", con.execute("SELECT count(*), count(ft), sum(factory::INT) FROM allp").fetchone())


if __name__ == "__main__":
  sys.exit(main())
