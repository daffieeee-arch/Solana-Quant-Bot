"""E58 runner census, step 1 (research/spec-runner-census-2026-10-21.md §3): the pool-hour series of EVERY PumpSwap pool in
the store, all quote classes (sol, reversed, other), for the census and the pre-declared momentum markouts on the VPS.
NAS job (run with PYTHONPATH=lab in a scope; LAB_DATA for the data root, LAB_STORE for the store):

  python -m wallets.explore.runner_hours OUT_DIR

Per (pool, UTC hour) from the store's pool legs (the hour of a leg via the blocks table: the first slot of every hour, ASOF):
  close      the price after the hour's last trade (buy / sell / boost), in the quote per token: normal and other pools
             (e + e_delta) / (b - base | + base), reversed pools (b +/- base) / (e + e_delta) = SOL per token;
  depth      the real quote side after the hour's last leg of any kind (LP legs included): normal / other pools the vault
             (e + e_delta - vq for a trade, vault -/+ quote_user for a withdraw / deposit), reversed pools b after the leg;
             in the quote's units / 1e9 (SOL for sol and reversed pools);
  trades, buys, farmer_trades, volume (sum of sol_amount of the hour's trades, quote units / 1e9).
Hours without a leg are absent (the census carries the previous close and depth forward and labels it). Buckets of sorted
pool ranges (LAB_MARKOUT_BUCKETS) bound the working set, as markout.paid_buyers. Writes hours.parquet, pools.parquet
(pool, quote_class, orientation, quote_mint, mint, create_slot), hour_bounds.parquet and run-runner-hours.json.
"""
import json
import os
import sys
import time

from wallets.common import connect, git_commit, guard

LAB = os.environ.get("LAB_DATA", "/home/chupa/Solana-project/data-old-faithful-one/lab")
STORE = os.environ.get("LAB_STORE", f"{LAB}/store/dev2.duckdb")

TRADE_PRICE = """CASE WHEN po.orientation = 'reversed'
                   THEN (CASE WHEN p.kind = 'sell' THEN p.b + p.base ELSE p.b - p.base END)::DOUBLE / (p.e + p.e_delta)
                   ELSE (p.e + p.e_delta)::DOUBLE / (CASE WHEN p.kind = 'sell' THEN p.b + p.base ELSE p.b - p.base END) END"""
DEPTH_POST = """CASE WHEN po.orientation = 'reversed'
                  THEN (CASE WHEN p.kind IN ('sell', 'deposit') THEN p.b + p.base WHEN p.kind IN ('buy', 'boost', 'withdraw') THEN p.b - p.base END)::DOUBLE
                  ELSE (CASE WHEN p.kind IN ('buy', 'sell', 'boost') THEN p.e + p.e_delta - COALESCE(p.vq, 0)
                             WHEN p.kind = 'withdraw' THEN p.vault - p.quote_user ELSE p.vault + p.quote_user END)::DOUBLE END / 1e9"""


def hour_bounds(con):
    con.execute("""CREATE OR REPLACE TEMP TABLE hour_bounds AS
                   SELECT (block_time // 3600)::BIGINT AS hour, min(slot) AS from_slot, min(block_time) AS first_block_time FROM blocks GROUP BY 1 ORDER BY 2""")
    return con.execute("SELECT count(*), min(hour), max(hour) FROM hour_bounds").fetchone()


def build(con, out, buckets):
    prefix = os.environ.get("LAB_RUNNER_POOL_PREFIX", "")  # smoke tests on the VPS: only the pools whose address starts with it
    con.execute(f"CREATE OR REPLACE TEMP TABLE pm AS SELECT pool, quote_class, orientation, quote_mint, mint, create_slot FROM pools WHERE pool LIKE '{prefix}%'")
    pools = [r[0] for r in con.execute("SELECT pool FROM pm ORDER BY pool").fetchall()]
    nb = max(1, min(buckets, len(pools)))
    bounds = [pools[i * len(pools) // nb] for i in range(nb)] + [None]
    con.execute("CREATE OR REPLACE TEMP TABLE hours (pool VARCHAR, hour BIGINT, close DOUBLE, depth DOUBLE, trades BIGINT, buys BIGINT, farmer_trades BIGINT, volume DOUBLE, last_slot BIGINT)")
    for b in range(nb):
        lo, hi = bounds[b], bounds[b + 1]
        rng = f"p.pool >= '{lo}'" + (f" AND p.pool < '{hi}'" if hi is not None else "")
        con.execute(f"""
          INSERT INTO hours
          SELECT pool, hour,
                 arg_max(price, ord) FILTER (WHERE price IS NOT NULL) AS close, arg_max(depth, ord) AS depth,
                 count(*) FILTER (WHERE is_trade) AS trades, count(*) FILTER (WHERE is_trade AND p_token_buy) AS buys,
                 count(*) FILTER (WHERE is_trade AND farmer) AS farmer_trades, sum(CASE WHEN is_trade THEN sol ELSE 0 END) / 1e9 AS volume, max(slot) AS last_slot
          FROM (
            SELECT p.pool, hb.hour, p.slot, (p.slot * 1000000000 + p.tx_index * 100000 + COALESCE(p.outer_ix, 0) * 1000 + COALESCE(p.inner_ix, 0))::BIGINT AS ord,
                   p.kind IN ('buy', 'sell', 'boost') AS is_trade, COALESCE(p.token_buy, false) AS p_token_buy, COALESCE(p.farmer, false) AS farmer, COALESCE(p.sol_amount, 0) AS sol,
                   CASE WHEN p.kind IN ('buy', 'sell', 'boost') AND p.b > 0 AND p.e > 0 THEN {TRADE_PRICE} END AS price, {DEPTH_POST} AS depth
            FROM pool p JOIN pm po USING (pool) ASOF JOIN hour_bounds hb ON p.slot >= hb.from_slot
            WHERE {rng} AND p.kind IN ('buy', 'sell', 'boost', 'withdraw', 'deposit')) x
          GROUP BY pool, hour""")
    n = con.execute("SELECT count(*), count(DISTINCT pool) FROM hours").fetchone()
    con.execute(f"COPY (SELECT * FROM hours ORDER BY pool, hour) TO '{out}/hours.parquet' (FORMAT PARQUET)")
    con.execute(f"COPY (SELECT * FROM pm ORDER BY pool) TO '{out}/pools.parquet' (FORMAT PARQUET)")
    con.execute(f"COPY (SELECT * FROM hour_bounds ORDER BY hour) TO '{out}/hour_bounds.parquet' (FORMAT PARQUET)")
    return n


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    con = connect(STORE, memory=os.environ.get("LAB_WALLETS_MEMORY", "1500MB"), threads=int(os.environ.get("LAB_WALLETS_THREADS", "2")))
    end_slot = int(json.loads(con.execute("SELECT json FROM meta").fetchone()[0])["slot_end_exclusive"])
    guard(end_slot)  # development only: the store ends before the hold-out
    nh, h0, h1 = hour_bounds(con)
    print(f"{nh} hours in blocks", file=sys.stderr, flush=True)
    n_rows, n_pools = build(con, out, int(os.environ.get("LAB_MARKOUT_BUCKETS", "4")))
    info = {"rows": n_rows, "pools": n_pools, "hours": nh, "pool_prefix": os.environ.get("LAB_RUNNER_POOL_PREFIX", ""), "hour_first": h0, "hour_last": h1, "store": STORE, "slot_end_exclusive": end_slot, "commit": git_commit(), "seconds": round(time.time() - t0, 1)}
    with open(f"{out}/run-runner-hours.json", "w") as f:
        json.dump(info, f, indent=1)
    print(json.dumps(info))


if __name__ == "__main__":
    main()
