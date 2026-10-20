"""costgap step 1: trigger-slot legs of the F7 pool_reversed signals from the dev store, one epoch per call (timing probe)."""
import os, sys, time, duckdb
LAB = os.environ.get("LAB_DATA", "/home/chupa/Solana-project/data-old-faithful-one/lab")
ep = int(sys.argv[1]); out = sys.argv[2]
lo, hi = ep * 432000, (ep + 1) * 432000 - 1
t0 = time.time()
con = duckdb.connect(f"{LAB}/store/dev2.duckdb", read_only=True)
con.execute("SET memory_limit='1500MB'; SET threads=2; SET preserve_insertion_order=false")
con.execute(f"""CREATE TEMP TABLE k AS SELECT DISTINCT pool, t FROM '{LAB}/wallets/markout-v1/markout-f7rev.parquet'
                WHERE family='F7' AND segment='pool_reversed' AND d=2 AND h='' AND t BETWEEN {lo} AND {hi}""")
n = con.execute("SELECT count(*) FROM k").fetchone()[0]
con.execute(f"""COPY (SELECT p.kind, p.pool, p.slot, p.tx_index, p.outer_ix, p.inner_ix, p.trader, p.fee_payer, p.priority_fee, p.jito_tip, p.tx_fee,
                      p.b, p.vault, p.vq, p.e, p.lp_bps, p.protocol_bps, p.creator_bps, p.cashback_bps, p.orientation, p.token_buy,
                      p.sol_amount, p.sol_depth, p.token_amount, p.farmer
               FROM pool p JOIN k ON p.pool = k.pool AND p.slot = k.t
               WHERE p.slot BETWEEN {lo} AND {hi}) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
m = con.execute(f"SELECT count(*), count(DISTINCT pool||slot::VARCHAR) FROM '{out}'").fetchone()
print(f"epoch {ep}: keys {n}, legs {m[0]}, keys found {m[1]}, {time.time()-t0:.1f} s")
