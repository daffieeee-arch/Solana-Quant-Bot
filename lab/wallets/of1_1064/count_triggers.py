"""Step 2 of the trigger-supply question (coordinator 2026-10-23): on a forward store built from OF1 epoch 1064 (one UTC day),
count the dev tournament's F7 triggers (backtest.strategies.f7_signals_sql, unchanged) per hour and compare them, pool by pool,
with the live recorder's triggers and probe pools of the same hours (small inputs synced from the VPS).

COUNTS ONLY. Epoch 1064 is the future forward window for H5/C3: this script reads the store's `pool`, `pools`, `mints`, `blocks`
and `meta` tables and the trigger function; it never computes a fill, an exit, a return or any P&L, and it must not be extended
to do so without a pre-registration.

  python -m wallets.of1_1064.count_triggers --store <forward store> --from-slot A --to-slot B --inputs <dir> --out <dir>
      [--live-triggers live-triggers-explore.parquet] [--probe-pools probe-pools-explore.parquet]
      [--recorder-counts check-chunk-recorder.json] [--label explore]

Since 2026-10-29 (step 1 after Alpenglow, plan v0.3 section 2.5: live against OF1 for the full-coverage days 24-27 Oct): the
input file names and the window are parameters. The inputs keep the explore schema (live-triggers: venue, mint, t, depth, pool,
orientation, trader, trader_farmer, flagged; probe-pools: pool, t0, p0, first_seen); the window [lo, hi) must lie inside the
store (meta slot_start / slot_end_exclusive) and may span several epochs on a contiguous store; the label ("explore",
"fullcov") goes into count_triggers.json. The recorder-counts json is optional (absent on the full-coverage inputs).
"""
import argparse
import json
import os
import time

import duckdb

from backtest.strategies import Clock, f7_signals_sql
from backtest.store import load_defaults

INPUT_FILES = ("live-triggers-explore.parquet", "probe-pools-explore.parquet", "check-chunk-recorder.json", "SHA256SUMS")


def resolve_inputs(inputs_dir, live_triggers, probe_pools, recorder_counts=None):
    """The input paths: the two parquet files and SHA256SUMS must exist; the recorder-counts json is optional (None when absent).
    Raises FileNotFoundError naming the missing file."""
    paths = {"live": os.path.join(inputs_dir, live_triggers), "probe": os.path.join(inputs_dir, probe_pools), "sums": os.path.join(inputs_dir, "SHA256SUMS")}
    for k, f in paths.items():
        if not os.path.exists(f):
            raise FileNotFoundError(f"missing input {f}")
    rc = os.path.join(inputs_dir, recorder_counts) if recorder_counts else None
    paths["recorder_counts"] = rc if rc and os.path.exists(rc) else None
    return paths


def check_window(meta, lo, hi):
    """[lo, hi) must be a non-empty window inside the store (meta slot_start / slot_end_exclusive when present). It may span
    several epochs on a contiguous store. Returns the list of epochs it touches."""
    if not (0 < lo < hi):
        raise ValueError(f"empty or inverted window [{lo}, {hi})")
    s0, s1 = meta.get("slot_start"), meta.get("slot_end_exclusive")
    if s0 is not None and lo < int(s0):
        raise ValueError(f"window starts before the store ({lo} < {s0})")
    if s1 is not None and hi > int(s1):
        raise ValueError(f"window ends after the store ({hi} > {s1})")
    return list(range(lo // 432_000, (hi - 1) // 432_000 + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--from-slot", type=int, required=True)
    ap.add_argument("--to-slot", type=int, required=True)
    ap.add_argument("--inputs", required=True, help="dir with the recorder's live-triggers / probe-pools parquet and their SHA256SUMS")
    ap.add_argument("--out", required=True)
    ap.add_argument("--memory", default=os.environ.get("LAB_DUCKDB_MEM", "4GB"))
    ap.add_argument("--live-triggers", default=INPUT_FILES[0], help="file name of the recorder's live triggers parquet in --inputs (explore schema)")
    ap.add_argument("--probe-pools", default=INPUT_FILES[1], help="file name of the recorder's probe pools parquet in --inputs")
    ap.add_argument("--recorder-counts", default=INPUT_FILES[2], help="file name of the recorder's per-slot counts json in --inputs (optional)")
    ap.add_argument("--label", default="explore", help="input label written to count_triggers.json (explore / fullcov)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    lo, hi = a.from_slot, a.to_slot
    inputs = resolve_inputs(a.inputs, a.live_triggers, a.probe_pools, a.recorder_counts)
    con = duckdb.connect(a.store, read_only=True)
    con.execute(f"SET memory_limit='{a.memory}'; SET threads=2; SET TimeZone='UTC'")
    if os.environ.get("LAB_SPILL_DIR"):
        con.execute(f"SET temp_directory='{os.environ['LAB_SPILL_DIR']}'")
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    assert meta.get("period") in ("forward", "paper", "dev"), meta.get("period")
    epochs = check_window(meta, lo, hi)
    print("store", a.store, "period", meta.get("period"), "slots", meta.get("slot_start"), meta.get("slot_end_exclusive"), "ms_per_slot", meta.get("ms_per_slot"),
          "window epochs", epochs, "inputs", inputs, "label", a.label, flush=True)
    clock = Clock.from_store(con)
    cfg = load_defaults()
    t0 = time.time()
    trig = f7_signals_sql(con, cfg, lo - 600_000, hi + 200_000, clock=clock, trig_range=(lo, hi - 1))
    print(f"f7_signals_sql: {len(trig)} triggers in [{lo}, {hi}) ({time.time() - t0:.0f}s)", flush=True)
    con.execute("CREATE TEMP TABLE trig (venue VARCHAR, mint VARCHAR, t BIGINT, depth BIGINT, pool VARCHAR, orientation VARCHAR, trader VARCHAR, trader_farmer BOOLEAN, flagged BOOLEAN)")
    con.executemany("INSERT INTO trig VALUES (?,?,?,?,?,?,?,?,?)", trig)
    # the inputs cover the whole explore window (into epoch 1065): window them to [lo, hi) (LAB review on #537)
    live_all = con.execute(f"SELECT count(*) FROM read_parquet('{inputs['live']}') WHERE orientation = 'reversed'").fetchone()[0]
    con.execute(f"CREATE TEMP TABLE live AS SELECT pool, t FROM read_parquet('{inputs['live']}') WHERE orientation = 'reversed' AND t >= {lo} AND t < {hi}")
    probe_all = con.execute(f"SELECT count(*) FROM read_parquet('{inputs['probe']}')").fetchone()[0]
    con.execute(f"CREATE TEMP TABLE probe AS SELECT pool, t0 FROM read_parquet('{inputs['probe']}') WHERE t0 < {hi}")
    R_inputs = {"live_triggers_in_window": con.execute("SELECT count(*) FROM live").fetchone()[0], "live_triggers_total": live_all,
                "probe_pools_t0_before_hi": con.execute("SELECT count(*) FROM probe").fetchone()[0], "probe_pools_total": probe_all}
    print("inputs windowed to [lo, hi):", R_inputs, flush=True)
    con.execute(f"CREATE TEMP TABLE hr AS SELECT slot, date_trunc('hour', to_timestamp(block_time)) AS h FROM blocks WHERE slot >= {lo} AND slot < {hi}")
    R = {"store": a.store, "window": [lo, hi], "epochs": epochs, "label": a.label, "input_files": inputs, "ms_per_slot": meta.get("ms_per_slot"), "counts_only": True, "inputs": R_inputs,
         "rule": "a forward window: trigger counts only, never P&L (epoch 1064 was the forward window for H5/C3; later windows are read under the owner's release of 2026-10-24)"}
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
