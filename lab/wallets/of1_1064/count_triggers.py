"""Step 2 of the trigger-supply question (coordinator 2026-10-23): on a forward store built from OF1 epoch 1064 (one UTC day),
count the dev tournament's F7 triggers (backtest.strategies.f7_signals_sql, unchanged) per hour and compare them, pool by pool,
with the live recorder's triggers and probe pools of the same hours (small inputs synced from the VPS).

COUNTS ONLY. Epoch 1064 is the future forward window for H5/C3: this script reads the store's `pool`, `pools`, `mints`, `blocks`
and `meta` tables and the trigger function; it never computes a fill, an exit, a return or any P&L, and it must not be extended
to do so without a pre-registration.

  python -m wallets.of1_1064.count_triggers --store <forward store> --from-slot A --to-slot B --inputs <dir> --out <dir>
"""
import argparse
import json
import os
import time

import duckdb

from backtest.strategies import Clock, f7_signals_sql
from backtest.store import load_defaults

INPUT_FILES = ("live-triggers-explore.parquet", "probe-pools-explore.parquet", "check-chunk-recorder.json", "SHA256SUMS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--from-slot", type=int, required=True)
    ap.add_argument("--to-slot", type=int, required=True)
    ap.add_argument("--inputs", required=True, help="dir with the recorder's live-triggers / probe-pools parquet and their SHA256SUMS")
    ap.add_argument("--out", required=True)
    ap.add_argument("--memory", default=os.environ.get("LAB_DUCKDB_MEM", "4GB"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    lo, hi = a.from_slot, a.to_slot
    assert lo // 432_000 == 1064 and (hi - 1) // 432_000 == 1064, "epoch 1064 only"
    for f in INPUT_FILES:
        assert os.path.exists(os.path.join(a.inputs, f)), f"missing input {f}"
    con = duckdb.connect(a.store, read_only=True)
    con.execute(f"SET memory_limit='{a.memory}'; SET threads=2; SET TimeZone='UTC'")
    if os.environ.get("LAB_SPILL_DIR"):
        con.execute(f"SET temp_directory='{os.environ['LAB_SPILL_DIR']}'")
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    assert meta.get("period") in ("forward", "paper", "dev"), meta.get("period")
    print("store", a.store, "period", meta.get("period"), "slots", meta.get("slot_start"), meta.get("slot_end_exclusive"), "ms_per_slot", meta.get("ms_per_slot"), flush=True)
    clock = Clock.from_store(con)
    cfg = load_defaults()
    t0 = time.time()
    trig = f7_signals_sql(con, cfg, lo - 600_000, hi + 200_000, clock=clock, trig_range=(lo, hi - 1))
    print(f"f7_signals_sql: {len(trig)} triggers in [{lo}, {hi}) ({time.time() - t0:.0f}s)", flush=True)
    con.execute("CREATE TEMP TABLE trig (venue VARCHAR, mint VARCHAR, t BIGINT, depth BIGINT, pool VARCHAR, orientation VARCHAR, trader VARCHAR, trader_farmer BOOLEAN, flagged BOOLEAN)")
    con.executemany("INSERT INTO trig VALUES (?,?,?,?,?,?,?,?,?)", trig)
    # the inputs cover the whole explore window (into epoch 1065): window them to [lo, hi) (LAB review on #537)
    live_all = con.execute(f"SELECT count(*) FROM read_parquet('{os.path.join(a.inputs, 'live-triggers-explore.parquet')}') WHERE orientation = 'reversed'").fetchone()[0]
    con.execute(f"CREATE TEMP TABLE live AS SELECT pool, t FROM read_parquet('{os.path.join(a.inputs, 'live-triggers-explore.parquet')}') WHERE orientation = 'reversed' AND t >= {lo} AND t < {hi}")
    probe_all = con.execute(f"SELECT count(*) FROM read_parquet('{os.path.join(a.inputs, 'probe-pools-explore.parquet')}')").fetchone()[0]
    con.execute(f"CREATE TEMP TABLE probe AS SELECT pool, t0 FROM read_parquet('{os.path.join(a.inputs, 'probe-pools-explore.parquet')}') WHERE t0 < {hi}")
    R_inputs = {"live_triggers_in_window": con.execute("SELECT count(*) FROM live").fetchone()[0], "live_triggers_total": live_all,
                "probe_pools_t0_before_hi": con.execute("SELECT count(*) FROM probe").fetchone()[0], "probe_pools_total": probe_all}
    print("inputs windowed to [lo, hi):", R_inputs, flush=True)
    con.execute(f"CREATE TEMP TABLE hr AS SELECT slot, date_trunc('hour', to_timestamp(block_time)) AS h FROM blocks WHERE slot >= {lo} AND slot < {hi}")
    R = {"store": a.store, "window": [lo, hi], "ms_per_slot": meta.get("ms_per_slot"), "counts_only": True, "inputs": R_inputs,
         "rule": "epoch 1064 is the future forward window for H5/C3: trigger counts only, never P&L"}
    R["of1_triggers"] = con.execute("SELECT orientation, count(*), count(DISTINCT pool) FROM trig GROUP BY 1 ORDER BY 1").fetchall()
    print("OF1 triggers by orientation (n, pools):", R["of1_triggers"], flush=True)
    R["per_hour"] = con.execute("""
        WITH o AS (SELECT h, count(*) AS of1_rev, count(DISTINCT pool) AS of1_pools FROM trig JOIN hr ON hr.slot = trig.t WHERE orientation = 'reversed' GROUP BY 1),
             l AS (SELECT hr.h, count(*) AS live, count(DISTINCT pool) AS live_pools FROM live JOIN hr ON hr.slot = live.t GROUP BY 1),
             b AS (SELECT h, count(*) AS n_both FROM trig JOIN live USING (pool, t) JOIN hr ON hr.slot = trig.t WHERE trig.orientation = 'reversed' GROUP BY 1),
             d AS (SELECT hr.h, count(DISTINCT e.pool) FILTER (WHERE e.sol_depth >= 120e9) AS pools_ge120, count(DISTINCT e.pool) FILTER (WHERE e.sol_depth >= 150e9) AS pools_ge150
                   FROM pool e JOIN pools p USING (pool) JOIN hr ON hr.slot = e.slot WHERE p.orientation = 'reversed' AND e.kind IN ('buy', 'sell') GROUP BY 1)
        SELECT h, COALESCE(of1_rev, 0), COALESCE(of1_pools, 0), COALESCE(live, 0), COALESCE(live_pools, 0), COALESCE(n_both, 0), COALESCE(pools_ge120, 0), COALESCE(pools_ge150, 0)
        FROM hr_hours LEFT JOIN o USING (h) LEFT JOIN l USING (h) LEFT JOIN b USING (h) LEFT JOIN d USING (h) ORDER BY h""".replace("hr_hours", "(SELECT DISTINCT h FROM hr)")).fetchall()
    print("per hour (h, OF1 reversed triggers, OF1 trigger pools, live triggers, live pools, both, reversed pools >= 120 SOL, >= 150 SOL):")
    for r in R["per_hour"]:
        print("  ", r)
    R["sets"] = con.execute("""SELECT sum((l.pool IS NULL)::INT) AS of1_only, sum((l.pool IS NOT NULL)::INT) AS both,
                               (SELECT count(*) FROM live lv WHERE NOT EXISTS (SELECT 1 FROM trig x WHERE x.pool = lv.pool AND x.t = lv.t AND x.orientation = 'reversed')) AS live_only
                               FROM trig tr LEFT JOIN live l USING (pool, t) WHERE tr.orientation = 'reversed'""").fetchone()
    print("reversed (pool, t): OF1-only / both / live-only:", R["sets"], flush=True)
    R["of1_only_by_pool_class"] = con.execute("""
        SELECT CASE WHEN pr.pool IS NOT NULL THEN 'followed (probe pool on the VPS, probed at or before t)' ELSE 'not followed by the recorder at t' END AS cls,
               count(*) AS triggers, count(DISTINCT tr.pool) AS pools
        FROM trig tr LEFT JOIN live l USING (pool, t) LEFT JOIN probe pr ON pr.pool = tr.pool AND pr.t0 <= tr.t
        WHERE tr.orientation = 'reversed' AND l.pool IS NULL GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    print("OF1-only triggers by whether the recorder followed the pool:", R["of1_only_by_pool_class"], flush=True)
    R["of1_only_pool_depth"] = con.execute("""
        WITH mx AS (SELECT pool, max(sol_depth) AS maxd, min(slot) AS first_slot FROM pool WHERE kind IN ('buy', 'sell') GROUP BY 1)
        SELECT round(quantile_cont(maxd / 1e9, 0.5)), round(100.0 * avg((maxd >= 120e9)::INT), 1), count(DISTINCT tr.pool)
        FROM trig tr LEFT JOIN live l USING (pool, t) JOIN mx ON mx.pool = tr.pool WHERE tr.orientation = 'reversed' AND l.pool IS NULL""").fetchone()
    print("OF1-only trigger pools: median max SOL depth in the day, share ever >= 120 SOL, pools:", R["of1_only_pool_depth"], flush=True)
    R["seconds"] = round(time.time() - t0)
    with open(os.path.join(a.out, "count_triggers.json"), "w") as f:
        json.dump(R, f, indent=1, default=str)
    print("written", os.path.join(a.out, "count_triggers.json"))


if __name__ == "__main__":
    main()
