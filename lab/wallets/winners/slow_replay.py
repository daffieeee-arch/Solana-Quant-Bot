"""Delayed replay of group C's slow positions (hold >= 1 min, closed, single venue key): entry at their first buy + d, exit at
their last sell + d (d = 0, 2, 3 slots, both sides), constant product on the venue's post-trade state before that slot,
fee bps from their own entry row, our base costs (2 x 705,000 lamports). Their size and 0.5 SOL. Output c_replay.parquet."""
# no-quote-filter: consumed data, conclusions audited 2026-10-18 (lab/reports/2026-10-18-audit-niet-sol-quote.md)
import duckdb, time, sys
import os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
S = os.environ.get("LAB_STORE", f"{DATA}/store/dev2.duckdb")
W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
PER=int(sys.argv[1]) if len(sys.argv) > 1 else 400   # positions per (hold bucket x venue)
BLOCK=sys.argv[2] if len(sys.argv) > 2 else "all"   # all | out (days 10-18 only, entry_day >= 2026-09-20) | lab (days 1-9)
VENUE=sys.argv[3] if len(sys.argv) > 3 else "all"  # all | curve | pool | pool_reversed
WIN=2000  # slots looked back for the last state before the entry / exit slot
t0=time.time(); log=lambda *a: print(round(time.time()-t0,1), *a, flush=True)
con=duckdb.connect(); con.execute(f"SET memory_limit='1400MB'; SET threads TO 2; SET preserve_insertion_order=false; SET temp_directory='{DATA}/tmp/winners-v1'; SET max_temp_directory_size='6GB'")
con.execute(f"ATTACH '{S}' AS s (READ_ONLY)")
con.execute(f"""CREATE TABLE smp AS SELECT * FROM (SELECT p.*, l.exit_pool, row_number() OVER (PARTITION BY hold_bucket, entry_venue ORDER BY hash(wallet || mint)) rn
  FROM '{W}/c_pos.parquet' p JOIN (SELECT wallet, mint, arg_max(pool, slot) AS exit_pool FROM '{W}/c_legs.parquet' WHERE NOT is_buy GROUP BY 1,2) l USING (wallet, mint)
  WHERE kind = 'closed' AND hold_bucket >= 'c' AND n_venues = 1 AND NOT COALESCE(mayhem, false) AND (ls - 446256000) // 432000 = (fb - 446256000) // 432000
  AND ({'true' if BLOCK == 'all' else "entry_day >= '2026-09-20'" if BLOCK == 'out' else "entry_day < '2026-09-20'"}) AND ({'true' if VENUE == 'all' else f"entry_venue = '{VENUE}'"})) WHERE rn <= {PER}""")
log("sample", con.execute("SELECT entry_venue, hold_bucket, count(*) FROM smp GROUP BY 1,2 ORDER BY 1,2").fetchall())
con.execute("CREATE TABLE sm AS SELECT wallet, mint, entry_venue AS venue, CASE WHEN entry_venue = 'curve' THEN mint ELSE entry_pool END AS key, fb, ls, first_buy_sol, buy_sol, sell_sol, net_cash, entry_bps, hold_bucket, entry_day, hold, ms FROM smp")
EP=[(446256000+432000*i, 446256000+432000*(i+1)) for i in range(14)]
def states(tag, col):
    first=True
    for lo,hi in EP:
        con.execute(f"""CREATE OR REPLACE TABLE e AS
        WITH r AS (
          SELECT sm.wallet, sm.mint, c.slot::BIGINT AS slot, c.tx_index::BIGINT AS tx_index, c.vq::DOUBLE AS q, c.vt::DOUBLE AS t
          FROM sm JOIN s.curve c ON sm.venue = 'curve' AND c.mint = sm.key AND c.slot >= sm.{col} - {WIN} AND c.slot < sm.{col} + 3
          WHERE sm.{col} >= {lo} AND sm.{col} < {hi}
          UNION ALL
          SELECT sm.wallet, sm.mint, p.slot::BIGINT, p.tx_index, CASE WHEN p.orientation = 'reversed' THEN p.b ELSE p.e END::DOUBLE, CASE WHEN p.orientation = 'reversed' THEN p.e ELSE p.b END::DOUBLE
          FROM sm JOIN s.pool p ON sm.venue <> 'curve' AND p.pool = sm.key AND p.slot >= sm.{col} - {WIN} AND p.slot < sm.{col} + 3
          WHERE p.kind IN ('buy','sell') AND sm.{col} >= {lo} AND sm.{col} < {hi})
        SELECT r.wallet, r.mint,
          arg_max(struct_pack(q := q, t := t), (slot, tx_index)) FILTER (WHERE slot < sm.{col}) AS s0,
          arg_max(struct_pack(q := q, t := t), (slot, tx_index)) FILTER (WHERE slot < sm.{col} + 2) AS s2,
          arg_max(struct_pack(q := q, t := t), (slot, tx_index)) FILTER (WHERE slot < sm.{col} + 3) AS s3,
          count(*) FILTER (WHERE slot >= sm.{col} AND slot < sm.{col} + 3) AS n_rows_3
        FROM r JOIN sm USING (wallet, mint) GROUP BY 1,2""")
        if first: con.execute(f"CREATE TABLE {tag} AS SELECT * FROM e"); first=False
        else: con.execute(f"INSERT INTO {tag} SELECT * FROM e")
        log(tag, lo, con.execute("SELECT count(*) FROM e").fetchone()[0])
states("ent", "fb"); states("ext", "ls")
con.execute("""CREATE TABLE rp AS SELECT sm.*, COALESCE(entry_bps, 125) AS fbps, ent.s0 AS e0, ent.s2 AS e2, ent.s3 AS e3, ext.s0 AS x0, ext.s2 AS x2, ext.s3 AS x3, ent.n_rows_3 AS ent_rows_3, ext.n_rows_3 AS ext_rows_3
  FROM sm LEFT JOIN ent USING (wallet, mint) LEFT JOIN ext USING (wallet, mint)""")
con.execute("CREATE MACRO buy_tok(st, sol, f) AS CASE WHEN st IS NULL OR st.q <= 0 OR st.t <= 0 THEN NULL ELSE st.t - st.t * st.q / (st.q + sol * (1 - f/10000.0)) END")
con.execute("CREATE MACRO sell_sol(st, tok, f) AS CASE WHEN st IS NULL OR tok IS NULL OR st.q <= 0 OR st.t <= 0 THEN NULL ELSE (st.q - st.q * st.t / (st.t + tok)) * (1 - f/10000.0) END")
COST=2*705000
con.execute(f"""CREATE TABLE out AS SELECT *,
  sell_sol(x0, buy_tok(e0, buy_sol, fbps), fbps) - buy_sol - {COST} AS net0, sell_sol(x2, buy_tok(e2, buy_sol, fbps), fbps) - buy_sol - {COST} AS net2, sell_sol(x3, buy_tok(e3, buy_sol, fbps), fbps) - buy_sol - {COST} AS net3,
  sell_sol(x0, buy_tok(e0, 5e8, fbps), fbps) - 5e8 - {COST} AS h0, sell_sol(x2, buy_tok(e2, 5e8, fbps), fbps) - 5e8 - {COST} AS h2, sell_sol(x3, buy_tok(e3, 5e8, fbps), fbps) - 5e8 - {COST} AS h3
  FROM rp""")
con.execute(f"COPY out TO '{W}/c_replay{'' if BLOCK == 'all' and VENUE == 'all' else '-' + BLOCK + '-' + VENUE}.parquet' (FORMAT PARQUET)")
log("done", con.execute("SELECT count(*), count(net0), count(net3) FROM out").fetchone())
