"""Revival (spec research/spec-revival-2026-10-19.md v0.1a, roadmap #167): old PumpSwap pools with a sudden inflow.
Exploration on dev only (no hold-out, no clean set); SOL-quoted pools only (pools.quote_class in sol / reversed).

Step 1, `aggregate` (NAS: one scan of the pool legs): per (pool, UTC hour) the trades, token buys / sells, SOL volume
(sol_amount, the SOL side of every leg), unique traders and buyers, net SOL inflow (token buys - token sells, in SOL),
farmer trades and SOL, the first and last leg, the end-of-hour state (sol_depth, b, e, vq, bps) and, per threshold D
in THRESHOLDS (SOL), the first leg at which the hour's cumulative SOL volume reaches D: its slot and tx_index, the
unique traders up to and including that leg, and the SOL depth after it. Every 'state at a leg' (end state, threshold
leg) keys on the full block order (slot, tx_index, outer_ix, inner_ix): a round-trip transaction has two trade legs with
the same (slot, tx_index), and a key on those two alone would pick one of them arbitrarily (per thread count or bucket). Only hours with >= 1 trade. The hour is the
UTC hour of the leg's block time (blocks.block_time), so an hour row can span two UTC days at an epoch edge only through
the slot bounds of a smoke (`--from-slot` / `--to-slot` cut hours, flagged in the run json).

  LAB_STORE=<dev2> python -m wallets.revival aggregate [--out DIR] [--from-slot A --to-slot B] [--buckets N]

Writes <out>/pool_hour.parquet (ordered by pool, hour), <out>/pools.parquet (the SOL pools' metadata: orientation,
created_in_period, create_slot, mayhem, coin_creator, b0, q0) and <out>/run-aggregate.json. The legs are scanned per
key-hash bucket of the pool (LAB_MARKOUT_BUCKETS-style, `--buckets`), the union over the buckets is the unbucketed
result; one bucket on the VPS smoke, 8-16 on the NAS. Slots stay before the hold-out (common.guard).

The NAS script skips the aggregate when pool_hour.parquet exists (resumable), so a run on a new commit needs a fresh
REVIVAL_DIR. Step 2, `detect` (VPS, on the aggregate): the sleeping pools and the revival signals of the 18 cells, as a parts-like
signals.parquet for `wallets.markout` (step 3, a NAS job when the signals are many).

  python -m wallets.revival detect --agg <aggregate dir> --out <dir> [--first-day 2026-09-21] [--last-day D]

Step 3, `report` (VPS): frequency per cell and day, liquidity at t (depth, own impact at 0.5 / 2 / 5 SOL, the size at 1 %
impact) from the signals, and, with --markout, the markout tables per cell (markout_report's tables on the REV rows).

  python -m wallets.revival report --out <dir> [--signals P] [--markout DIR] [--boot N] [--report-out revival.md]
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
      SELECT pool, slot, tx_index, outer_ix, inner_ix, trader, token_buy, sol_amount, sol_depth, b, e, vq, bps, farmer, hour,
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
                f"arg_min(tx_index, (slot, tx_index, outer_ix, inner_ix)) FILTER (WHERE cum >= {lam}) AS t{d}_tx",
                f"count(DISTINCT trader) FILTER (WHERE cum_before < {lam}) AS t{d}_traders",
                f"arg_min(sol_depth, (slot, tx_index, outer_ix, inner_ix)) FILTER (WHERE cum >= {lam}) AS t{d}_depth"]
    return f"""
      SELECT pool, hour, count(*) AS trades, count(*) FILTER (WHERE token_buy) AS buys, count(*) FILTER (WHERE NOT token_buy) AS sells,
             sum(sol_amount) AS sol_volume, COALESCE(sum(sol_amount) FILTER (WHERE token_buy), 0) AS sol_buy,
             COALESCE(sum(sol_amount) FILTER (WHERE NOT token_buy), 0) AS sol_sell,
             COALESCE(sum(sol_amount) FILTER (WHERE token_buy), 0) - COALESCE(sum(sol_amount) FILTER (WHERE NOT token_buy), 0) AS net_inflow,
             count(DISTINCT trader) AS traders, count(DISTINCT trader) FILTER (WHERE token_buy) AS buyers,
             count(*) FILTER (WHERE farmer) AS farmer_trades, COALESCE(sum(sol_amount) FILTER (WHERE farmer), 0) AS farmer_sol,
             min(slot) AS first_slot, max(slot) AS last_slot,
             arg_max(sol_depth, (slot, tx_index, outer_ix, inner_ix)) AS end_depth, arg_max(b, (slot, tx_index, outer_ix, inner_ix)) AS end_b,
             arg_max(e, (slot, tx_index, outer_ix, inner_ix)) AS end_e, arg_max(vq, (slot, tx_index, outer_ix, inner_ix)) AS end_vq, arg_max(bps, (slot, tx_index, outer_ix, inner_ix)) AS end_bps,
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


# --- step 2: sleeping pools and revivals on the aggregate (VPS) -------------------------------------------------

SLEEP_S = (1, 5)         # SOL per traded hour: the 7-day median at or below S (spec §2)
SLEEP_QUIET = 0.8        # share of the 168 hours without a trade, at least
REVIVE_T = (5, 10, 20)   # unique traders in the revival hour, at least
REVIVE_P90_X = 10        # hour volume at least 10 x the 7-day p90 (and at least D)
WEEK_S = 7 * 86400
AGE_DAYS = 10
FIRST_DAY = "2026-09-21"  # pools from before the window are >= 10 days old by construction from day 10 (spec: age note)
MS_PER_SLOT = 267.3       # REF_MS: the 10-day age of an in-window pool in slots (approximation; the store's regimes are 266-317 ms)


def detect(agg_dir, out_dir, first_day=FIRST_DAY, last_day=None, store=None, require_mint=False):
    """Signals of the 18 cells (S x D x T) from pool_hour.parquet: per pool and hour the trailing 7-day stats over the hours
    before it (median and p90 of the SOL volume over the traded hours, the number of traded hours); sleeping = median <= S
    (no traded hour counts as sleeping too) and >= 80 % of the 168 hours without a trade; a revival hour = sleeping before it,
    volume >= max(D, 10 x p90), >= T unique traders, and the D threshold reached in the hour; signal slot t = the first slot at
    which the hour's cumulative volume reached D (point in time; traders up to that leg are reported as traders_at_t: the spec's
    shift of t to the first slot with >= T traders needs the legs and is left to the markout step's caller). Age: a pool from
    before the window from FIRST_DAY on, an in-window pool from create_slot + 10 days; no mayhem pools. One signal per pool,
    day and cell (the earliest hour). Mayhem pools are excluded (not in the spec; the store's mayhem flag, as every other
    exploration). Writes signals.parquet in the parts' column layout (family REV, variant = the cell, venue pool, segment by
    orientation, farm organic / flagged by the hour's farmer share >= 0.5, mint from the store's pools table: NULL for most
    pools from before the window, which have no creation event in the store; `mint_known` says so, run-detect.json counts them,
    and require_mint drops them; markout keys pools on `pool`, so they stay in by default) plus run-detect.json."""
    con = connect(store or STORE, memory=os.environ.get("LAB_WALLETS_MEMORY", "1GB"), threads=int(os.environ.get("LAB_WALLETS_THREADS", "2")))
    os.makedirs(out_dir, exist_ok=True)
    ph, po = os.path.join(agg_dir, "pool_hour.parquet"), os.path.join(agg_dir, "pools.parquet")
    # the aggregate's pools.parquet has no mint; the signals carry it for markout (the store's pools table, read-only)
    con.execute("CREATE OR REPLACE TEMP TABLE pm AS SELECT pool, mint FROM pools")
    hour_min = con.execute(f"SELECT min(hour) FROM read_parquet('{ph}')").fetchone()[0]
    age_slots = int(round(AGE_DAYS * 86400 * 1000 / MS_PER_SLOT))
    progress("start", note=f"aggregate {agg_dir}, first day {first_day}, history from {hour_min}")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE roll AS
      SELECT h.*, p.orientation, pm.mint, p.created_in_period, p.create_slot, p.mayhem,
             CAST(DATE '1970-01-01' + CAST(h.hour // 86400 AS INTEGER) AS VARCHAR) AS day,
             count(*) OVER w AS hours_traded_7d, quantile_cont(h.sol_volume, 0.5) OVER w AS med_7d, quantile_cont(h.sol_volume, 0.9) OVER w AS p90_7d
      FROM read_parquet('{ph}') h JOIN read_parquet('{po}') p USING (pool) LEFT JOIN pm USING (pool)
      WHERE NOT p.mayhem
      WINDOW w AS (PARTITION BY h.pool ORDER BY h.hour RANGE BETWEEN {WEEK_S} PRECEDING AND 1 PRECEDING)""")
    progress("rolling 7-day stats", _count(con, "roll"))
    cells = [(S, D, T) for S in SLEEP_S for D in THRESHOLDS for T in REVIVE_T]
    sel = []
    for S, D, T in cells:
        sel.append(f"""
          SELECT 'S{S}_D{D}_T{T}' AS variant, {S} AS s_sol, {D} AS d_sol, {T} AS t_min, pool, mint, orientation, day, hour, t{D}_slot AS t, t{D}_tx AS t_tx,
                 t{D}_traders AS traders_at_t, t{D}_depth AS depth_at_t, sol_volume, traders, buyers, net_inflow, trades,
                 farmer_trades::DOUBLE / trades AS farmer_share, med_7d, p90_7d, hours_traded_7d, end_depth
          FROM roll
          WHERE hour >= {hour_min} + {WEEK_S}
            AND (hours_traded_7d = 0 OR med_7d <= {S * SOL})
            AND (168 - hours_traded_7d) >= {SLEEP_QUIET} * 168
            AND sol_volume >= GREATEST({D * SOL}, {REVIVE_P90_X} * COALESCE(p90_7d, 0))
            AND traders >= {T} AND t{D}_slot IS NOT NULL
            AND ((NOT created_in_period AND day >= '{first_day}') OR (create_slot IS NOT NULL AND t{D}_slot >= create_slot + {age_slots}))
            {f"AND day <= '{last_day}'" if last_day else ""}""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE sig AS
      SELECT * EXCLUDE (rn) FROM (SELECT *, row_number() OVER (PARTITION BY variant, pool, day ORDER BY hour) AS rn FROM ({' UNION ALL '.join(sel)})) WHERE rn = 1""")
    n = _count(con, "sig")
    progress("signals", n, f"{len(cells)} cells")
    out = os.path.join(out_dir, "signals.parquet")
    con.execute(f"""
      COPY (SELECT 'REV' AS family, variant, mint, t, 'pool' AS venue, pool, orientation,
                   CASE WHEN orientation = 'reversed' THEN 'pool_reversed' ELSE 'pool_normal' END AS segment,
                   CASE WHEN farmer_share >= 0.5 THEN 'flagged' ELSE 'organic' END AS farm, day, 0.5 AS size_sol,
                   NULL::VARCHAR AS ref_mint, NULL::BIGINT AS ref_t, NULL::VARCHAR AS trigger_trader, mint IS NOT NULL AS mint_known,
                   s_sol, d_sol, t_min, hour, t_tx, traders_at_t, depth_at_t, sol_volume, traders, buyers, net_inflow, trades, farmer_share,
                   med_7d, p90_7d, hours_traded_7d, end_depth
            FROM sig {"WHERE mint IS NOT NULL" if require_mint else ""} ORDER BY variant, pool, t) TO '{out}' (FORMAT parquet, COMPRESSION zstd)""")
    null_mint, null_mint_pools = con.execute("SELECT sum((mint IS NULL)::INT), count(DISTINCT pool) FILTER (WHERE mint IS NULL) FROM sig").fetchone()
    per_cell = {r[0]: {"signals": r[1], "pools": r[2], "days": r[3], "reversed": r[4], "traders_at_t_ge_T": r[5]}
                for r in con.execute("SELECT variant, count(*), count(DISTINCT pool), count(DISTINCT day), sum((orientation = 'reversed')::INT), "
                                     "sum((traders_at_t >= t_min)::INT) FROM sig GROUP BY 1 ORDER BY 1").fetchall()}
    sleeping = {str(S): dict(con.execute(f"""SELECT day, count(DISTINCT pool) FROM roll WHERE hour >= {hour_min} + {WEEK_S} AND (hours_traded_7d = 0 OR med_7d <= {S * SOL})
                                            AND (168 - hours_traded_7d) >= {SLEEP_QUIET} * 168 AND day >= '{first_day}' GROUP BY 1 ORDER BY 1""").fetchall()) for S in SLEEP_S}
    info = {"step": "detect", "aggregate": agg_dir, "store": store or STORE, "first_day": first_day, "last_day": last_day, "history_from_hour": hour_min,
            "cells": len(cells), "sleep_s_sol": list(SLEEP_S), "thresholds_sol": list(THRESHOLDS), "revive_t": list(REVIVE_T),
            "revive_p90_x": REVIVE_P90_X, "sleep_quiet_share": SLEEP_QUIET, "age_days": AGE_DAYS, "age_slots_in_window": age_slots,
            "signals": n, "signals_without_mint": int(null_mint or 0), "pools_without_mint": int(null_mint_pools or 0),
            "require_mint": require_mint, "mayhem_excluded": True, "per_cell": per_cell, "sleeping_pools_per_day_with_trades": sleeping,
            "commit": git_commit(), "seconds": round(sum(PHASES.values()), 1), "phases_seconds": PHASES}
    with open(os.path.join(out_dir, "run-detect.json"), "w") as f:
        json.dump(info, f, indent=1)
    progress("written", n, out)
    con.close()
    return info


# --- step 3: the report (VPS): per cell the markout tables, liquidity and frequency ------------------------------

H_SUMMARY = ("1m", "5m", "15m", "1h", "6h")
IMPACT_SIZES = (0.5, 2.0, 5.0)  # SOL
F7_DEPTH_SOL = 150


def liquidity(con, signals_path):
    """Per cell: the SOL depth x at t (p10 / p50 / p90), the share >= 150 SOL (F7's floor), and for a buy of 0.5 / 2 / 5 SOL
    on a constant-product pool with that depth the pool share s / (x + s) and the average fill s / x above the mid (model
    assumptions that ignore the fee and the token side), plus the size at which the pool share reaches 1 % (x / 99)."""
    rows = con.execute(f"""
      SELECT variant, count(*), quantile_cont(depth_at_t, 0.1) / 1e9, quantile_cont(depth_at_t, 0.5) / 1e9, quantile_cont(depth_at_t, 0.9) / 1e9,
             avg((depth_at_t >= {F7_DEPTH_SOL * SOL})::INT),
             {', '.join(f"quantile_cont({sz} / (depth_at_t / 1e9 + {sz}), 0.5)" for sz in IMPACT_SIZES)},
             {', '.join(f"quantile_cont({sz} / (depth_at_t / 1e9), 0.5)" for sz in IMPACT_SIZES)},
             quantile_cont(depth_at_t / 1e9 / 99, 0.5), avg((traders_at_t >= t_min)::INT), avg((orientation = 'reversed')::INT),
             avg((farm = 'flagged')::INT)
      FROM read_parquet('{signals_path}') WHERE depth_at_t IS NOT NULL GROUP BY 1 ORDER BY 1""").fetchall()
    keys = ["n", "depth_p10", "depth_p50", "depth_p90", "share_ge_150", *[f"impact_{sz}" for sz in IMPACT_SIZES], *[f"fill_{sz}" for sz in IMPACT_SIZES], "size_1pct_p50",
            "traders_at_t_ge_T", "reversed_share", "flagged_share"]
    return {r[0]: dict(zip(keys, r[1:])) for r in rows}


def frequency(con, signals_path):
    rows = con.execute(f"SELECT variant, day, count(*) FROM read_parquet('{signals_path}') GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
    out = {}
    for v, day, n in rows:
        out.setdefault(v, {})[day] = n
    return out


def report(out_path, signals_path, markout_dir=None, detect_json=None, boot=10_000):
    from . import markout_report as mr
    import duckdb
    con = duckdb.connect()
    con.execute(f"SET threads TO {int(os.environ.get('LAB_WALLETS_THREADS', '2'))}; SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1GB')}'")
    liq, freq = liquidity(con, signals_path), frequency(con, signals_path)
    cells = sorted(liq)
    det = json.load(open(detect_json)) if detect_json else {}
    L = ["## Revival (spec research/spec-revival-2026-10-19.md v0.1a): sleeping PumpSwap pools with a sudden inflow, dev 1033-1046", "",
         f"Signals {signals_path}; cells S (7-day median SOL per traded hour <= S, >= 80 % of the hours quiet) x D (the hour's cumulative volume reaches D SOL; "
         f"t = that slot) x T (unique traders in the hour); one signal per pool, day and cell; pools from before the window from {det.get('first_day', FIRST_DAY)}, "
         f"in-window pools from create_slot + {AGE_DAYS} days; mayhem pools excluded (not in the spec; the store's flag). "
         f"**Point in time:** only the D crossing is known at t; `traders >= T` and `volume >= 10 x p90` are facts of the end of the hour. The main markout "
         f"table per cell is therefore the subset with **traders at the D leg >= T** (`traders_at_t >= T`: known at t), with the dropped share beside it; "
         f"the end-of-hour rows are shown as a second line and are a selection on the outcome (E39/E40 lesson), not a signal. "
         f"Signals without a mint: {det.get('signals_without_mint', '?')} of {det.get('signals', '?')} (pre-window pools without a creation event; markout keys pools on pool). "
         f"Exploration on used dev data: a positive cell is a candidate trial, not a finding.", ""]
    L += ["### Frequency: signals per day per cell", "", "| cell | " + " | ".join(sorted({d for v in freq.values() for d in v})) + " | total |",
          "|---|" + "---|" * (len({d for v in freq.values() for d in v}) + 1)]
    days = sorted({d for v in freq.values() for d in v})
    for v in cells:
        L.append(f"| {v} | " + " | ".join(str(freq.get(v, {}).get(d, 0)) for d in days) + f" | {sum(freq.get(v, {}).values())} |")
    if det.get("sleeping_pools_per_day_with_trades"):
        for S, per in det["sleeping_pools_per_day_with_trades"].items():
            L.append(f"| sleeping pools (S{S}, traded that day) | " + " | ".join(str(per.get(d, "")) for d in days) + " | |")
    L += ["", "### Liquidity at t (SOL depth x of the pool after the D leg; constant-product model assumption: pool share = s / (x + s), average fill = s / x above mid)", "",
          "| cell | n | depth p10 / p50 / p90 (SOL) | >= 150 SOL | pool share 0.5 / 2 / 5 SOL (median) | average fill 0.5 / 2 / 5 SOL (median) | size at 1 % pool share (median) | traders at t >= T | reversed | flagged |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for v in cells:
        q = liq[v]
        L.append(f"| {v} | {q['n']} | {q['depth_p10']:.1f} / {q['depth_p50']:.1f} / {q['depth_p90']:.1f} | {q['share_ge_150'] * 100:.0f}% | "
                 + " / ".join(f"{q[f'impact_{sz}'] * 100:.1f}%" for sz in IMPACT_SIZES) + " | "
                 + " / ".join(f"{q[f'fill_{sz}'] * 100:.1f}%" for sz in IMPACT_SIZES)
                 + f" | {q['size_1pct_p50']:.2f} SOL | {q['traders_at_t_ge_T'] * 100:.0f}% | {q['reversed_share'] * 100:.0f}% | {q['flagged_share'] * 100:.0f}% |")
    summary = {}
    if markout_dir:
        if os.environ.get("LAB_REPORT_WORKERS", "1") != "1":
            mr._pool(mr.workers())
        try:
            mr.load(con, markout_dir)
            con.execute(f"CREATE OR REPLACE TEMP TABLE pit AS SELECT variant, pool, t, traders_at_t >= t_min AS pit FROM read_parquet('{signals_path}')")
            ds = [r[0] for r in con.execute("SELECT DISTINCT d FROM m WHERE family = 'REV' ORDER BY 1").fetchall()]
            L += ["", "### Markouts per cell (net after costs, 0.5 SOL round trip, base; trimmed mean with day CI; dead = the pool could not pay)", "",
                  "Main line = **point in time** (traders at the D leg >= T; `dropped` = the share of the cell's signals left out); second line = "
                  "end of hour (all signals of the cell: a selection on the hour's outcome, not a signal).", "",
                  "| cell | d | rows | dropped | " + " | ".join(f"n / net trim / CI / dead @ {h}" for h in H_SUMMARY) + " |", "|---|---|---|---|" + "---|" * len(H_SUMMARY)]
            for v in cells:
                n_all, n_pit = con.execute("SELECT count(*), sum(pit::INT) FROM pit WHERE variant = ?", [v]).fetchone()
                dropped = 1 - (n_pit or 0) / n_all if n_all else None
                for d in ds:
                    for label, extra in (("point in time", f" AND (pool, t) IN (SELECT pool, t FROM pit WHERE variant = '{v}' AND pit)"), ("end of hour", "")):
                        cols, key = [], f"{d}|{label}"
                        summary.setdefault(v, {})[key] = {}
                        for h in H_SUMMARY:
                            st = mr.stats(con, f"family = 'REV' AND list_contains(variants, '{v}') AND d = {d} AND h = '{h}'{extra}", "net", boot)
                            if st:
                                summary[v][key][h] = {k: st[k] for k in ("n", "trim", "lo", "hi", "dead", "pos")}
                                cols.append(f"{st['n']} / {mr.fmt(st['trim'], 1, True, True)} / {mr.fmt(st['lo'], 1, True, True)} … {mr.fmt(st['hi'], 1, True, True)} / {mr.fmt(st['dead'], 0, True)}")
                            else:
                                cols.append("–")
                        L.append(f"| {v} | {d} | {label} | {'' if label == 'end of hour' or dropped is None else f'{dropped * 100:.0f}%'} | " + " | ".join(cols) + " |")
            L.append("")
            L.append("Cells with n < 30 at a horizon are counted, not read. The full per-cell tables (blocks and regimes) follow for the point-in-time subset only.")
            L.append("")
            for v in cells:
                for d in ds:
                    L.append(mr.table(con, f"REV · {v} · d = {d} · point in time (traders at the D leg >= T)",
                                      f"family = 'REV' AND list_contains(variants, '{v}') AND d = {d} AND (pool, t) IN (SELECT pool, t FROM pit WHERE variant = '{v}' AND pit)", boot))
        finally:
            mr.close_pool()
    md = "\n".join(L) + "\n"
    with open(out_path, "w") as f:
        f.write(md)
    with open(os.path.splitext(out_path)[0] + ".json", "w") as f:
        json.dump({"signals": signals_path, "markout": markout_dir, "boot": boot, "liquidity": liq, "frequency": freq, "markout_summary": summary,
                   "commit": git_commit()}, f, indent=1, default=float)
    return md


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["aggregate", "detect", "report"])
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "revival-v1"))
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--buckets", type=int, default=int(os.environ.get("LAB_MARKOUT_BUCKETS", "1")))
    ap.add_argument("--store")
    ap.add_argument("--agg", help="detect: the aggregate directory (default --out)")
    ap.add_argument("--first-day", default=FIRST_DAY)
    ap.add_argument("--require-mint", action="store_true", help="detect: drop signals whose pool has no mint in the store (most pre-window pools)")
    ap.add_argument("--last-day")
    ap.add_argument("--signals", help="report: detect's signals.parquet (default <out>/detect/signals.parquet)")
    ap.add_argument("--markout", help="report: the markout directory of the signals (markout-revival.parquet); without it: frequency and liquidity only")
    ap.add_argument("--boot", type=int, default=10_000)
    ap.add_argument("--report-out", help="report: markdown path (default <out>/revival.md)")
    a = ap.parse_args()
    if a.buckets < 1:
        raise SystemExit("--buckets must be >= 1")
    if a.step == "report":
        sig = a.signals or os.path.join(a.out, "detect", "signals.parquet")
        dj = os.path.join(os.path.dirname(sig), "run-detect.json")
        out = a.report_out or os.path.join(a.out, "revival.md")
        report(out, sig, a.markout, dj if os.path.exists(dj) else None, a.boot)
        print(json.dumps({"out": out}))
        return
    if a.step == "detect":
        info = detect(a.agg or a.out, a.out, a.first_day, a.last_day, a.store, a.require_mint)
        print(json.dumps({k: info[k] for k in ("signals", "cells", "seconds")}))
        return
    info = aggregate(a.out, a.from_slot, a.to_slot, a.buckets, a.store)
    print(json.dumps({k: info[k] for k in ("rows", "pools_with_trades", "trades", "sol_volume", "hours_reaching", "seconds")}))


if __name__ == "__main__":
    main()
