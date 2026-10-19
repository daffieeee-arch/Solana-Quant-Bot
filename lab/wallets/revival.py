"""Revival (spec research/spec-revival-2026-10-19.md v0.1a, roadmap #167): old PumpSwap pools with a sudden inflow.
Exploration on dev only (no hold-out, no clean set); SOL-quoted pools only (pools.quote_class in sol / reversed).

Step 1, `aggregate` (NAS: one scan of the pool legs): per (pool, UTC hour) the trades, token buys / sells, SOL volume
(sol_amount, the SOL side of every leg), unique traders and buyers, net SOL inflow (token buys - token sells, in SOL),
farmer trades and SOL, the first and last leg, the end-of-hour state (sol_depth, b, e, vq, bps) and, per threshold D
in THRESHOLDS (SOL), the first leg at which the hour's cumulative SOL volume reaches D: its slot and tx_index, the
unique traders up to and including that leg, and the SOL depth after it. Only hours with >= 1 trade. The hour is the
UTC hour of the leg's block time (blocks.block_time), so an hour row can span two UTC days at an epoch edge only through
the slot bounds of a smoke (`--from-slot` / `--to-slot` cut hours, flagged in the run json).

  LAB_STORE=<dev2> python -m wallets.revival aggregate [--out DIR] [--from-slot A --to-slot B] [--buckets N]

Writes <out>/pool_hour.parquet (ordered by pool, hour), <out>/pools.parquet (the SOL pools' metadata: orientation,
created_in_period, create_slot, mayhem, coin_creator, b0, q0) and <out>/run-aggregate.json. The legs are scanned per
key-hash bucket of the pool (LAB_MARKOUT_BUCKETS-style, `--buckets`), the union over the buckets is the unbucketed
result; one bucket on the VPS smoke, 8-16 on the NAS. Slots stay before the hold-out (common.guard).

Steps 2 and 3 (`detect`: sleeping pools and revivals on the aggregate; markout on the signals) come after this job.
"""
import argparse
import json
import os
import sys
import time

from .common import HOLDOUT_START, OUT_ROOT, SOL, STORE, connect, git_commit, guard

THRESHOLDS = (10, 20, 50)  # SOL: the hour's cumulative volume at which a revival signal fires (spec §1)
KINDS = ("buy", "sell")  # pool legs that are trades; boost / deposit / withdraw are not
_T0 = [time.time()]
PHASES = {}


def progress(phase, rows=None, note=""):
    now = time.time()
    dt = now - _T0[0]
    _T0[0] = now
    PHASES[phase] = round(PHASES.get(phase, 0) + dt, 1)
    print(f"{time.strftime('%H:%M:%S')} {phase}: {rows if rows is not None else ''} rows {dt:,.1f}s {note}".replace("  ", " "),
          file=sys.stderr, flush=True)


def _count(con, table):
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def legs_sql(lo, hi, bucket, nb):
    """The trade legs of the SOL pools in [lo, hi) of one key-hash bucket, with their UTC hour and the hour's running
    SOL volume before and after each leg (block order: slot, tx_index, outer_ix, inner_ix)."""
    where = [f"p.kind IN {KINDS}", "po.quote_class IN ('sol', 'reversed')"]
    if lo is not None:
        where.append(f"p.slot >= {lo}")
    if hi is not None:
        where.append(f"p.slot < {hi}")
    if nb > 1:
        where.append(f"hash(p.pool) % {nb} = {bucket}")
    return f"""
      SELECT pool, slot, tx_index, trader, token_buy, sol_amount, sol_depth, b, e, vq, bps, farmer, hour,
             sum(sol_amount) OVER w - sol_amount AS cum_before, sum(sol_amount) OVER w AS cum
      FROM (SELECT p.pool, p.slot::BIGINT AS slot, p.tx_index, p.outer_ix, p.inner_ix, p.trader, p.token_buy,
                   COALESCE(p.sol_amount, 0)::BIGINT AS sol_amount, p.sol_depth, p.b, p.e, p.vq,
                   COALESCE(p.lp_bps, 0) + COALESCE(p.protocol_bps, 0) + COALESCE(p.creator_bps, 0) AS bps,
                   COALESCE(p.farmer, false) AS farmer, (bl.block_time // 3600) * 3600 AS hour
            FROM pool p JOIN pools po USING (pool) JOIN blocks bl USING (slot)
            WHERE {' AND '.join(where)})
      WINDOW w AS (PARTITION BY pool, hour ORDER BY slot, tx_index, outer_ix, inner_ix ROWS UNBOUNDED PRECEDING)"""


def hour_sql(legs="legs"):
    """One row per (pool, hour) from the legs table (legs_sql columns)."""
    thr = []
    for d in THRESHOLDS:
        lam = d * SOL
        thr += [f"min(slot) FILTER (WHERE cum >= {lam}) AS t{d}_slot",
                f"arg_min(tx_index, (slot, tx_index)) FILTER (WHERE cum >= {lam}) AS t{d}_tx",
                f"count(DISTINCT trader) FILTER (WHERE cum_before < {lam}) AS t{d}_traders",
                f"arg_min(sol_depth, (slot, tx_index)) FILTER (WHERE cum >= {lam}) AS t{d}_depth"]
    return f"""
      SELECT pool, hour, count(*) AS trades, count(*) FILTER (WHERE token_buy) AS buys, count(*) FILTER (WHERE NOT token_buy) AS sells,
             sum(sol_amount) AS sol_volume, COALESCE(sum(sol_amount) FILTER (WHERE token_buy), 0) AS sol_buy,
             COALESCE(sum(sol_amount) FILTER (WHERE NOT token_buy), 0) AS sol_sell,
             COALESCE(sum(sol_amount) FILTER (WHERE token_buy), 0) - COALESCE(sum(sol_amount) FILTER (WHERE NOT token_buy), 0) AS net_inflow,
             count(DISTINCT trader) AS traders, count(DISTINCT trader) FILTER (WHERE token_buy) AS buyers,
             count(*) FILTER (WHERE farmer) AS farmer_trades, COALESCE(sum(sol_amount) FILTER (WHERE farmer), 0) AS farmer_sol,
             min(slot) AS first_slot, max(slot) AS last_slot,
             arg_max(sol_depth, (slot, tx_index)) AS end_depth, arg_max(b, (slot, tx_index)) AS end_b,
             arg_max(e, (slot, tx_index)) AS end_e, arg_max(vq, (slot, tx_index)) AS end_vq, arg_max(bps, (slot, tx_index)) AS end_bps,
             {', '.join(thr)}
      FROM {legs} GROUP BY pool, hour"""


def aggregate(out_dir, lo=None, hi=None, buckets=1, store=None):
    if hi is not None:
        guard(hi)
    mem = os.environ.get("LAB_WALLETS_MEMORY", "1GB")
    threads = int(os.environ.get("LAB_WALLETS_THREADS", "2"))
    con = connect(store or STORE, memory=mem, threads=threads)
    os.makedirs(out_dir, exist_ok=True)
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    end = meta["slot_end_exclusive"]
    if hi is None and end > HOLDOUT_START:
        raise SystemExit(f"store ends at {end}, past the hold-out start {HOLDOUT_START}: pass --to-slot")
    progress("start", note=f"store {store or STORE} slots [{lo}, {hi}] buckets {buckets} memory {mem} threads {threads}")
    con.execute("CREATE OR REPLACE TEMP TABLE sol_pools AS SELECT pool, orientation, quote_class, created_in_period, create_slot::BIGINT AS create_slot, "
                          "COALESCE(mayhem, false) AS mayhem, coin_creator, b0, q0 FROM pools WHERE quote_class IN ('sol', 'reversed')")
    progress("sol pools", _count(con, "sol_pools"))
    first = True
    for b in range(buckets):
        con.execute(f"CREATE OR REPLACE TEMP TABLE legs AS {legs_sql(lo, hi, b, buckets)}")
        n_legs = _count(con, "legs")
        verb = "CREATE OR REPLACE TEMP TABLE pool_hour AS" if first else "INSERT INTO pool_hour"
        con.execute(f"{verb} {hour_sql('legs')}")
        first = False
        progress("pool_hour", _count(con, "pool_hour"), f"bucket {b + 1}/{buckets}, {n_legs:,} legs")
        con.execute("DROP TABLE legs")
    n_rows = _count(con, "pool_hour")
    ph = os.path.join(out_dir, "pool_hour.parquet")
    con.execute(f"COPY (SELECT * FROM pool_hour ORDER BY pool, hour) TO '{ph}' (FORMAT parquet, COMPRESSION zstd)")
    con.execute(f"COPY (SELECT * FROM sol_pools ORDER BY pool) TO '{os.path.join(out_dir, 'pools.parquet')}' (FORMAT parquet, COMPRESSION zstd)")
    stats = con.execute("SELECT count(DISTINCT pool), min(hour), max(hour), sum(trades), sum(sol_volume) / 1e9, "
                        + ", ".join(f"count(t{d}_slot)" for d in THRESHOLDS) + " FROM pool_hour").fetchone()
    progress("parquet written", n_rows)
    info = {"step": "aggregate", "store": store or STORE, "slots": [lo, hi], "partial_edge_hours": lo is not None or hi is not None,
            "buckets": buckets, "memory": mem, "threads": threads, "thresholds_sol": list(THRESHOLDS),
            "sol_pools": _count(con, "sol_pools"), "rows": n_rows, "pools_with_trades": stats[0], "hour_min": stats[1], "hour_max": stats[2],
            "trades": stats[3], "sol_volume": round(stats[4], 1),
            "hours_reaching": {str(d): stats[5 + i] for i, d in enumerate(THRESHOLDS)},
            "commit": git_commit(), "seconds": round(time.time() - _T0[0] + sum(PHASES.values()), 1), "phases_seconds": PHASES}
    with open(os.path.join(out_dir, "run-aggregate.json"), "w") as f:
        json.dump(info, f, indent=1)
    con.close()
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["aggregate"])
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "revival-v1"))
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--buckets", type=int, default=int(os.environ.get("LAB_MARKOUT_BUCKETS", "1")))
    ap.add_argument("--store")
    a = ap.parse_args()
    if a.buckets < 1:
        raise SystemExit("--buckets must be >= 1")
    info = aggregate(a.out, a.from_slot, a.to_slot, a.buckets, a.store)
    print(json.dumps({k: info[k] for k in ("rows", "pools_with_trades", "trades", "sol_volume", "hours_reaching", "seconds")}))


if __name__ == "__main__":
    main()
