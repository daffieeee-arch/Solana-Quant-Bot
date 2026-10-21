"""E58 runner census, step 2 (research/spec-runner-census-2026-10-21.md §3-§4, thresholds fixed in advance): from the pool-hour
series of runner_hours (step 1, NAS) on the VPS, which multi-day risers our universe (SOL-quoted PumpSwap pools) would have
seen at all, and a first descriptive markout of one pre-declared momentum entry on the runners inside and outside it.

  python -m wallets.explore.runner_census HOURS_DIR OUT_DIR [--parts F7_PARTS_DIR] [--boot 10000]

Definitions (the spec's, in code constants):
- dense hourly series per pool from its first hour to the last hour of the store; hours without a leg carry the previous
  close and depth (labelled `carried`);
- units: sol and reversed pools in SOL; 'other' pools converted to SOL through the quote mint's own SOL pool (the SOL pool
  whose base mint is the quote mint, the one with the most pool-hours; its carried hourly close = SOL per quote token), else
  USDC pools in USDC (the USDC mint), else 'quote' units without a liquidity floor (counted as unknown);
- runner: close >= RISE x the minimum close over [u - WIN_HI, u - WIN_LO] hours, with the depth at the hour of that minimum
  >= the floor (SOL: LIQ_SOL primary, LIQ_SOL_2 counts only; USDC: LIQ_USDC / LIQ_USDC_2); one runner per pool per
  movement (a new movement only after the close falls below RESET x that minimum);
- universe: quote_class in ('sol', 'reversed') ("seen"); with --parts, the pools that carry an F7 pool_reversed trigger;
- momentum entry: the end of the first hour with close >= ENTRY_RISE x the close ENTRY_LAG hours earlier and depth >= the
  primary floor; one entry per pool per movement (reset below RESET x that reference close); markouts at HORIZONS hours:
  net = close_h / close_u x (1 - f) (1 - s / D_h) / ((1 + f) (1 + s / D_u)) - 1 - network, with f the pool's fee tier,
  s = SIZE_SOL (or SIZE_USDC), D the depth, network = 2 x BASE_LAMPORTS / s; wiped (-100 %) when D_h < max(WIPED_ABS,
  WIPED_REL x D_u); unit = pool (mean over its entries), TRIM-trimmed mean, day bootstrap (BOOT, seed 7) over the UTC day
  of the pool's first entry; inside vs outside the universe, SOL units and USDC units separately.
Writes census.md, census.json, runners.parquet and entries.parquet in OUT_DIR.
"""
import argparse
import glob
import json
import os
import sys
import time

import duckdb
import numpy as np

from wallets.markout_report import BOOT, TRIM, day_ci, trimmed_mean

RISE, WIN_LO, WIN_HI, RESET = 3.0, 24, 72, 1.5
LIQ_SOL, LIQ_SOL_2 = 500.0, 100.0
LIQ_USDC, LIQ_USDC_2 = 100_000.0, 20_000.0
ENTRY_RISE, ENTRY_LAG = 2.0, 24
HORIZONS = (1, 6, 24)
SIZE_SOL, SIZE_USDC = 0.5, 100.0  # 0.5 SOL ≈ $100 at $200 / SOL (model assumption, as the spec's dollar floor)
BASE_LAMPORTS = 720_618
WIPED_ABS_SOL, WIPED_ABS_USDC, WIPED_REL = 1.0, 200.0, 0.01
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SEED = 7
RULE = {"rise": RISE, "window_hours": [WIN_LO, WIN_HI], "reset": RESET, "liq_sol": [LIQ_SOL, LIQ_SOL_2], "liq_usdc": [LIQ_USDC, LIQ_USDC_2], "entry_rise": ENTRY_RISE,
        "entry_lag_hours": ENTRY_LAG, "horizons": HORIZONS, "size": {"sol": SIZE_SOL, "usdc": SIZE_USDC}, "base_lamports": BASE_LAMPORTS,
        "wiped": {"abs_sol": WIPED_ABS_SOL, "abs_usdc": WIPED_ABS_USDC, "rel": WIPED_REL}, "trim": TRIM, "seed": SEED, "unit": "pool", "day_label": "first entry day"}


def load(con, hours_dir):
    """Dense carried hourly series in the pool's unit, with the SOL conversion of 'other' pools."""
    con.execute(f"CREATE OR REPLACE TABLE pm AS SELECT * FROM '{hours_dir}/pools.parquet'")
    con.execute(f"CREATE OR REPLACE TABLE hb AS SELECT * FROM '{hours_dir}/hour_bounds.parquet'")
    con.execute(f"CREATE OR REPLACE TABLE h0 AS SELECT * FROM '{hours_dir}/hours.parquet'")
    # the quote mint's SOL pool (for 'other' pools): the sol pool whose base mint is the quote mint, the one with the most pool-hours
    con.execute("""
      CREATE OR REPLACE TABLE qpool AS
      SELECT quote_mint, arg_max(pool, n) AS qsol_pool FROM (
        SELECT o.quote_mint, s.pool, count(h.hour) AS n FROM (SELECT DISTINCT quote_mint FROM pm WHERE quote_class = 'other' AND quote_mint IS NOT NULL) o
        JOIN pm s ON s.mint = o.quote_mint AND s.quote_class = 'sol' LEFT JOIN h0 h ON h.pool = s.pool GROUP BY 1, 2) GROUP BY 1""")
    con.execute(f"""
      CREATE OR REPLACE TABLE pu AS
      SELECT p.*, CASE WHEN p.quote_class IN ('sol', 'reversed') THEN 'SOL' WHEN q.qsol_pool IS NOT NULL THEN 'SOL' WHEN p.quote_mint = '{USDC}' THEN 'USDC' ELSE 'quote' END AS unit,
             q.qsol_pool, p.quote_class IN ('sol', 'reversed') AS seen
      FROM pm p LEFT JOIN qpool q USING (quote_mint)""")
    # the dense grid of every pool with at least one hour, from its first hour to the store's last hour; carried close / depth / fee tier
    con.execute("""
      CREATE OR REPLACE TABLE g AS
      SELECT pool, hour, close, depth, fee_bps, trades, close IS NULL AS carried FROM (
        SELECT d.pool, d.hour,
               last_value(h.close IGNORE NULLS) OVER w AS close, last_value(h.depth IGNORE NULLS) OVER w AS depth,
               last_value(h.fee_bps IGNORE NULLS) OVER w AS fee_bps, COALESCE(h.trades, 0) AS trades
        FROM (SELECT p.pool, hb.hour FROM (SELECT pool, min(hour) AS h_first FROM h0 GROUP BY 1) p JOIN hb ON hb.hour >= p.h_first) d
        LEFT JOIN h0 h ON h.pool = d.pool AND h.hour = d.hour
        WINDOW w AS (PARTITION BY d.pool ORDER BY d.hour ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
      WHERE close IS NOT NULL AND depth IS NOT NULL""")
    # conversion to SOL for 'other' pools with a quote-SOL pool: the carried close of that pool (SOL per quote token)
    con.execute("""
      CREATE OR REPLACE TABLE gu AS
      SELECT g.pool, g.hour, g.trades, g.carried, g.fee_bps, u.unit, u.quote_class, u.orientation, u.seen,
             CASE WHEN u.quote_class = 'other' AND u.qsol_pool IS NOT NULL THEN g.close * q.close ELSE g.close END AS price,
             CASE WHEN u.quote_class = 'other' AND u.qsol_pool IS NOT NULL THEN g.depth * q.close ELSE g.depth END AS depth
      FROM g JOIN pu u USING (pool) LEFT JOIN g q ON q.pool = u.qsol_pool AND q.hour = g.hour
      WHERE u.quote_class <> 'other' OR u.qsol_pool IS NULL OR q.close IS NOT NULL""")
    # the windows: the minimum price over [u - 72 h, u - 24 h] and the depth at that minimum; the price 24 h earlier
    con.execute(f"""
      CREATE OR REPLACE TABLE gw AS
      SELECT *, min(price) OVER w AS min_win, arg_min(depth, price) OVER w AS depth_at_min, count(*) OVER w AS n_win,
             lag(price, {ENTRY_LAG}) OVER (PARTITION BY pool ORDER BY hour) AS price_lag
      FROM gu WINDOW w AS (PARTITION BY pool ORDER BY hour ROWS BETWEEN {WIN_HI} PRECEDING AND {WIN_LO} PRECEDING)""")
    return con.execute("SELECT count(*), count(DISTINCT pool) FROM gw").fetchone()


def floors(unit, secondary=False):
    if unit == "SOL":
        return LIQ_SOL_2 if secondary else LIQ_SOL
    if unit == "USDC":
        return LIQ_USDC_2 if secondary else LIQ_USDC
    return None


def _movements(rows, ratio_col, ref_col, cond):
    """One event per movement over a pool's hourly rows (sorted): the first row where cond holds; the next only after the
    ratio falls below RESET x the reference of the event."""
    out, armed, ref = [], True, None
    for r in rows:
        if not armed:
            if r[ref_col] is not None and r["price"] < RESET * ref:
                armed = True
            continue
        if cond(r):
            out.append(r)
            armed, ref = False, r[ref_col]
    return out


def detect(con, parts_dir=None):
    """Runners at both floors and the momentum entries at the primary floor; returns (runners, entries) as lists of dicts."""
    f7 = set()
    if parts_dir:
        files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
        if files:
            lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
            f7 = {r[0] for r in con.execute(f"SELECT DISTINCT pool FROM read_parquet({lit}, union_by_name = true) WHERE family = 'F7' AND segment = 'pool_reversed' AND pool IS NOT NULL").fetchall()}
    cols = ["pool", "hour", "price", "depth", "fee_bps", "trades", "carried", "unit", "quote_class", "orientation", "seen", "min_win", "depth_at_min", "n_win", "price_lag"]
    runners, entries = [], []
    cur = con.execute(f"SELECT {', '.join(cols)} FROM gw ORDER BY pool, hour")
    pool, rows = None, []

    def flush(pool, rows):
        if not rows:
            return
        unit = rows[0]["unit"]
        for secondary in (False, True):
            fl = floors(unit, secondary)
            if fl is None:
                continue
            for r in _movements(rows, "price", "min_win", lambda r: r["min_win"] is not None and r["n_win"] >= WIN_HI - WIN_LO + 1 and r["price"] >= RISE * r["min_win"] and r["depth_at_min"] >= fl):
                runners.append(r | {"floor": "secondary" if secondary else "primary", "floor_value": fl, "f7_trigger": pool in f7})
        fl = floors(unit)
        if fl is not None:
            for r in _movements(rows, "price", "price_lag", lambda r: r["price_lag"] is not None and r["price"] >= ENTRY_RISE * r["price_lag"] and r["depth"] >= fl):
                entries.append(r | {"f7_trigger": pool in f7})

    for rec in cur.fetchall():
        d = dict(zip(cols, rec))
        if d["pool"] != pool:
            flush(pool, rows)
            pool, rows = d["pool"], []
        rows.append(d)
    flush(pool, rows)
    return runners, entries


def markouts(con, entries):
    """Net at the horizons for every entry, from the dense series; wiped = -100 %."""
    if not entries:
        return []
    con.execute("CREATE OR REPLACE TABLE ent AS SELECT * FROM (VALUES " + ",".join(f"('{e['pool']}', {e['hour']})" for e in entries) + ") t(pool, hour)")
    out = []
    for e in entries:
        s = SIZE_SOL if e["unit"] == "SOL" else SIZE_USDC
        wiped_abs = WIPED_ABS_SOL if e["unit"] == "SOL" else WIPED_ABS_USDC
        f = (e["fee_bps"] or 0) / 1e4
        network = 2 * BASE_LAMPORTS / 1e9 / SIZE_SOL  # in points of the stake (the SOL stake; the USDC stake takes the same points)
        row = {k: e[k] for k in ("pool", "hour", "unit", "quote_class", "orientation", "seen", "f7_trigger", "price", "depth", "fee_bps", "price_lag")}
        for h in HORIZONS:
            r = con.execute("SELECT price, depth FROM gu WHERE pool = ? AND hour = ?", [e["pool"], e["hour"] + h]).fetchone()
            if r is None:
                row[f"net_{h}h"] = None
                continue
            p_h, d_h = r
            if d_h < max(wiped_abs, WIPED_REL * e["depth"]):
                row[f"net_{h}h"] = -1.0
            else:
                row[f"net_{h}h"] = p_h / e["price"] * (1 - f) * (1 - s / d_h) / ((1 + f) * (1 + s / e["depth"])) - 1 - network
        out.append(row)
    return out


def _stat(values, days, boot):
    if not values:
        return None
    x = np.asarray(values, dtype=float)
    lo, hi = day_ci(x, np.asarray(days), boot, seed=SEED)
    return {"pools": len(x), "mean": float(trimmed_mean(x)), "lo": lo, "hi": hi, "median": float(np.median(x)), "wiped": float(np.mean(x <= -0.999))}


def analyse(runners, entries, mk, boot, hour_of_day):
    res = {"rule": RULE, "runners": {}, "markouts": {}, "counts": {}}
    for floor in ("primary", "secondary"):
        tab = {}
        for r in runners:
            if r["floor"] != floor:
                continue
            key = f"{r['quote_class']}/{r['orientation']}/{r['unit']}"
            t = tab.setdefault(key, {"runners": 0, "pools": set(), "seen": r["seen"], "f7_pools": set()})
            t["runners"] += 1
            t["pools"].add(r["pool"])
            if r["f7_trigger"]:
                t["f7_pools"].add(r["pool"])
        res["runners"][floor] = {k: {"runners": v["runners"], "pools": len(v["pools"]), "seen": v["seen"], "pools_with_f7_trigger": len(v["f7_pools"])} for k, v in tab.items()}
        tot = sum(v["runners"] for v in tab.values())
        seen = sum(v["runners"] for v in tab.values() if v["seen"])
        res["counts"][floor] = {"runners": tot, "seen": seen, "share_seen": seen / tot if tot else None, "pools": len({p for v in tab.values() for p in v["pools"]})}
    # markouts: unit pool, day label = the pool's first entry day
    for unit in ("SOL", "USDC"):
        for side, flag in (("inside", True), ("outside", False)):
            rows = [m for m in mk if m["unit"] == unit and m["seen"] == flag]
            per_pool = {}
            for m in rows:
                per_pool.setdefault(m["pool"], []).append(m)
            res["markouts"][f"{unit}_{side}"] = {"entries": len(rows), "pools": len(per_pool)}
            for h in HORIZONS:
                vals, days = [], []
                for p, ms in per_pool.items():
                    v = [m[f"net_{h}h"] for m in ms if m[f"net_{h}h"] is not None]
                    if v:
                        vals.append(float(np.mean(v)))
                        days.append(hour_of_day(min(m["hour"] for m in ms)))
                res["markouts"][f"{unit}_{side}"][f"net_{h}h"] = _stat(vals, days, boot)
    return res


def _c(x):
    return "–" if x is None else f"{x * 100:+.1f}"


def markdown(res, hours_dir):
    L = [f"## E58 runner census ({os.path.basename(hours_dir.rstrip('/'))})", "",
         f"Runner = close >= {RISE:.0f}x the minimum close over [u − {WIN_HI} h, u − {WIN_LO} h] with the depth at that minimum >= the floor (SOL {LIQ_SOL:.0f} primary / {LIQ_SOL_2:.0f} secondary; USDC {LIQ_USDC:,.0f} / {LIQ_USDC_2:,.0f}); "
         f"one per pool per movement (reset below {RESET}x the minimum). Seen = SOL-quoted PumpSwap pool (sol or reversed); 'other' pools in SOL through the quote mint's SOL pool when it exists, USDC pools in USDC, the rest without a floor.", ""]
    for floor in ("primary", "secondary"):
        c = res["counts"][floor]
        L += [f"**{floor} floor:** {c['runners']:,} runners in {c['pools']:,} pools; seen by the universe {c['seen']:,} ({(c['share_seen'] or 0) * 100:.0f} %).", "",
              "| quote / orientation / unit | runners | pools | seen | pools with an F7 trigger |", "|---|---|---|---|---|"]
        for k, v in sorted(res["runners"][floor].items(), key=lambda kv: -kv[1]["runners"]):
            L.append(f"| {k} | {v['runners']:,} | {v['pools']:,} | {v['seen']} | {v['pools_with_f7_trigger']:,} |")
        L.append("")
    L += [f"Momentum entry (close >= {ENTRY_RISE:.0f}x the close {ENTRY_LAG} h earlier, depth >= the primary floor; {SIZE_SOL} SOL / {SIZE_USDC:.0f} USDC round trip with the pool's fee tier, impact at the depth and the base network cost; wiped = −100 %); unit = pool, {TRIM * 100:.1f} % trimmed, day bootstrap:", "",
          "| unit / side | entries | pools | net 1 h | 6 h | 24 h | wiped 24 h |", "|---|---|---|---|---|---|---|"]
    for k, v in res["markouts"].items():
        cells = []
        for h in HORIZONS:
            s = v.get(f"net_{h}h")
            cells.append(f"{_c(s['mean'])} [{_c(s['lo'])}; {_c(s['hi'])}]" if s else "–")
        w = v.get("net_24h")
        L.append(f"| {k} | {v['entries']:,} | {v['pools']:,} | " + " | ".join(cells) + f" | {(w['wiped'] * 100):.0f} % |" if w else f"| {k} | {v['entries']:,} | {v['pools']:,} | – | – | – | – |")
    L += ["", "Limits: hourly bars miss intra-hour runs; dev lies before the synthetic migration of 7 Oct and before TTWO; a 3x in a quote token's units can be the quote's own move (hence the SOL conversion); Meteora / Raydium are unknown (no decoders in our data).", ""]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hours_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--parts", help="an F7 tournament's parts (pools with an F7 pool_reversed trigger)")
    ap.add_argument("--boot", type=int, default=BOOT)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    t0 = time.time()
    con = duckdb.connect()
    con.execute(f"SET threads TO {int(os.environ.get('LAB_WALLETS_THREADS', '2'))}; SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1500MB')}'")
    n_rows, n_pools = load(con, a.hours_dir)
    print(f"{n_rows:,} dense pool-hours of {n_pools:,} pools", file=sys.stderr, flush=True)
    runners, entries = detect(con, a.parts)
    print(f"{len(runners):,} runner rows, {len(entries):,} entries", file=sys.stderr, flush=True)
    mk = markouts(con, entries)
    hb = dict(con.execute("SELECT hour, strftime(to_timestamp(first_block_time), '%Y-%m-%d') FROM hb").fetchall())
    res = analyse(runners, entries, mk, a.boot, lambda h: hb.get(h, str(h)))
    res["input"] = {"dense_pool_hours": n_rows, "pools": n_pools, "runner_rows": len(runners), "entries": len(entries), "parts": a.parts, "seconds": round(time.time() - t0, 1)}
    if runners:
        con.execute("CREATE OR REPLACE TABLE rr AS SELECT * FROM (SELECT unnest($1, max_depth := 2))", [runners])
        con.execute(f"COPY rr TO '{a.out_dir}/runners.parquet' (FORMAT PARQUET)")
    if mk:
        con.execute("CREATE OR REPLACE TABLE ee AS SELECT * FROM (SELECT unnest($1, max_depth := 2))", [mk])
        con.execute(f"COPY ee TO '{a.out_dir}/entries.parquet' (FORMAT PARQUET)")
    with open(f"{a.out_dir}/census.md", "w") as f:
        f.write(markdown(res, a.hours_dir))
    with open(f"{a.out_dir}/census.json", "w") as f:
        json.dump(res, f, indent=1, default=float)
    print(json.dumps(res["counts"] | {"seconds": res["input"]["seconds"]}))


if __name__ == "__main__":
    main()
