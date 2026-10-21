"""E58 runner census, step 2 (research/spec-runner-census-2026-10-21.md §3-§4 with the v0.2 declarations; thresholds fixed in
advance): from the pool-hour series of runner_hours (step 1, NAS) on the VPS, which multi-day risers our universe (SOL-quoted
PumpSwap pools) would have seen at all, and a first descriptive markout of one pre-declared momentum entry on the runners
inside and outside it. VPS-light: run in a scope (MemoryMax 3G, DuckDB <= 2 GB, spill under $LAB/tmp):

  python -m wallets.explore.runner_census HOURS_DIR OUT_DIR [--parts F7_PARTS_DIR] [--boot 10000]

Definitions (the spec's, as code constants):
- dense hourly series per pool from its first hour to its last leg + TAIL_HOURS (capped at the store's last hour); hours
  without a leg carry the previous close, depth and fee tier (`carried`); only pools whose depth ever reaches the secondary
  floor enter the window computation (the others can never be a runner);
- units: sol and reversed pools in SOL; 'other' pools in SOL through the quote mint's own SOL pool when one exists (the sol pool
  whose base mint is the quote mint, the one with the most pool-hours; its carried hourly close = SOL per quote token), else
  USDC in USDC, else 'quote' units without a floor (counted); on store v2 the 'other' class has no legs: counted from pools only;
- runner: close >= RISE x the minimum close over the EXISTING rows of [u - WIN_HI, u - WIN_LO] (the pool at least WIN_LO
  hours old; a partial window is allowed and its share reported), with the depth at the hour of that minimum >= the floor
  (SOL: LIQ_SOL primary, LIQ_SOL_2 counts only; USDC: LIQ_USDC / LIQ_USDC_2); one runner per pool per movement (a new
  movement only after the close falls below RESET x that minimum);
- universe: quote_class in ('sol', 'reversed') ("seen"); with --parts, the pools that carry an F7 pool_reversed trigger;
- momentum entry: the end of the first hour with close >= ENTRY_RISE x the close ENTRY_LAG hours earlier and depth >= the
  primary floor; one entry per pool per movement (reset below RESET x that reference close); markouts at HORIZONS hours:
  net = close_h / close_u x (1 - f) (1 - s / D_h) / ((1 + f) (1 + s / D_u)) - 1 - network (the sell impact at the depth at h,
  the buy impact at the entry depth), f the pool's fee tier, s = SIZE_SOL (or SIZE_USDC), network = 2 x BASE_LAMPORTS in
  points of the SOL stake (the USDC stake takes the same points: 100 USDC ~ 0.5 SOL at the spec's $200 / SOL); wiped (-100 %)
  when D_h < max(WIPED_ABS, WIPED_REL x D_u); an entry without the horizon (store end) is counted, not scored; unit = pool
  (mean over its scored entries), TRIM-trimmed mean, day bootstrap (BOOT, seed 7) over the UTC day of the pool's first entry;
  inside vs outside the universe, SOL and USDC units separately.
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
TAIL_HOURS = 96
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SEED = 7
RULE = {"rise": RISE, "window_hours": [WIN_LO, WIN_HI], "partial_window": "allowed (pool >= 24 h old), share reported", "reset": RESET, "liq_sol": [LIQ_SOL, LIQ_SOL_2],
        "liq_usdc": [LIQ_USDC, LIQ_USDC_2], "entry_rise": ENTRY_RISE, "entry_lag_hours": ENTRY_LAG, "entry_reset": RESET, "horizons": HORIZONS, "size": {"sol": SIZE_SOL, "usdc": SIZE_USDC},
        "base_lamports": BASE_LAMPORTS, "network_points": 2 * BASE_LAMPORTS / 1e9 / SIZE_SOL, "sell_impact": "at the depth at h", "wiped": {"abs_sol": WIPED_ABS_SOL, "abs_usdc": WIPED_ABS_USDC, "rel": WIPED_REL},
        "tail_hours": TAIL_HOURS, "trim": TRIM, "seed": SEED, "unit": "pool", "day_label": "first entry day"}


def floors(unit, secondary=False):
    if unit == "SOL":
        return LIQ_SOL_2 if secondary else LIQ_SOL
    if unit == "USDC":
        return LIQ_USDC_2 if secondary else LIQ_USDC
    return None


def load(con, hours_dir):
    """Dense carried hourly series in the pool's unit (pools with hours; tail TAIL_HOURS after the last leg), the SOL conversion of
    'other' pools, the windows for the pools whose depth ever reaches the secondary floor. Returns the grid sizes."""
    con.execute(f"CREATE OR REPLACE TABLE pm AS SELECT * FROM '{hours_dir}/pools.parquet'")
    con.execute(f"CREATE OR REPLACE TABLE hb AS SELECT * FROM '{hours_dir}/hour_bounds.parquet'")
    con.execute(f"CREATE OR REPLACE TABLE h0 AS SELECT * FROM '{hours_dir}/hours.parquet'")
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
    store_last = con.execute("SELECT max(hour) FROM hb").fetchone()[0]
    con.execute(f"""
      CREATE OR REPLACE TABLE g AS
      SELECT pool, hour, close, depth, fee_bps, trades, close IS NULL AS carried FROM (
        SELECT d.pool, d.hour,
               last_value(h.close IGNORE NULLS) OVER w AS close, last_value(h.depth IGNORE NULLS) OVER w AS depth,
               last_value(h.fee_bps IGNORE NULLS) OVER w AS fee_bps, COALESCE(h.trades, 0) AS trades
        FROM (SELECT p.pool, hb.hour FROM (SELECT pool, min(hour) AS h_first, least(max(hour) + {TAIL_HOURS}, {store_last}) AS h_last FROM h0 GROUP BY 1) p
              JOIN hb ON hb.hour BETWEEN p.h_first AND p.h_last) d
        LEFT JOIN h0 h ON h.pool = d.pool AND h.hour = d.hour
        WINDOW w AS (PARTITION BY d.pool ORDER BY d.hour ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
      WHERE close IS NOT NULL AND depth IS NOT NULL""")
    con.execute("""
      CREATE OR REPLACE TABLE gu AS
      SELECT g.pool, g.hour, g.trades, g.carried, g.fee_bps, u.unit, u.quote_class, u.orientation, u.seen,
             CASE WHEN u.quote_class = 'other' AND u.qsol_pool IS NOT NULL THEN g.close * q.close ELSE g.close END AS price,
             CASE WHEN u.quote_class = 'other' AND u.qsol_pool IS NOT NULL THEN g.depth * q.close ELSE g.depth END AS depth
      FROM g JOIN pu u USING (pool) LEFT JOIN g q ON q.pool = u.qsol_pool AND q.hour = g.hour
      WHERE u.quote_class <> 'other' OR u.qsol_pool IS NULL OR q.close IS NOT NULL""")
    con.execute(f"""
      CREATE OR REPLACE TABLE liq AS SELECT pool FROM gu GROUP BY pool, unit
      HAVING max(depth) >= CASE unit WHEN 'SOL' THEN {LIQ_SOL_2} WHEN 'USDC' THEN {LIQ_USDC_2} ELSE 1e300 END""")
    con.execute(f"""
      CREATE OR REPLACE TABLE gw AS
      SELECT *, min(price) OVER w AS min_win, arg_min(depth, price) OVER w AS depth_at_min, count(*) OVER w AS n_win,
             lag(price, {ENTRY_LAG}) OVER (PARTITION BY pool ORDER BY hour) AS price_lag,
             hour - min(hour) OVER (PARTITION BY pool) >= {WIN_LO} AS old_enough
      FROM gu SEMI JOIN liq USING (pool) WINDOW w AS (PARTITION BY pool ORDER BY hour ROWS BETWEEN {WIN_HI} PRECEDING AND {WIN_LO} PRECEDING)""")
    n_g, n_gp = con.execute("SELECT count(*), count(DISTINCT pool) FROM gu").fetchone()
    n_w, n_wp = con.execute("SELECT count(*), count(DISTINCT pool) FROM gw").fetchone()
    return {"dense_pool_hours": n_g, "pools_with_hours": n_gp, "window_pool_hours": n_w, "pools_at_secondary_floor": n_wp, "store_last_hour": store_last}


def _events(price, cand, ref_vals):
    """Indices of one event per movement: the first candidate, then the next only after the price has fallen below RESET x the
    reference of the last event (vectorised per pool: candidates are few, the reset check is a slice minimum)."""
    out, prev, ref = [], None, None
    for i in np.flatnonzero(cand):
        if prev is not None and price[prev + 1:i + 1].min() >= RESET * ref:
            continue
        out.append(int(i))
        prev, ref = i, ref_vals[i]
    return out


COLS = ("pool", "hour", "price", "depth", "fee_bps", "trades", "carried", "unit", "quote_class", "orientation", "seen", "min_win", "depth_at_min", "n_win", "price_lag", "old_enough")


def detect(con, parts_dir=None, batch=200_000):
    """Runners at both floors and the momentum entries at the primary floor, streamed per pool."""
    f7 = set()
    if parts_dir:
        files = sorted(glob.glob(os.path.join(parts_dir, "*.parquet")))
        if files:
            lit = "[" + ",".join(f"'{f}'" for f in files) + "]"
            f7 = {r[0] for r in con.execute(f"SELECT DISTINCT pool FROM read_parquet({lit}, union_by_name = true) WHERE family = 'F7' AND segment = 'pool_reversed' AND pool IS NOT NULL").fetchall()}
    runners, entries = [], []

    def flush(rows):
        if not rows:
            return
        pool, unit, seen = rows[0][0], rows[0][7], rows[0][10]
        price = np.array([r[2] for r in rows], dtype=float)
        depth = np.array([r[3] for r in rows], dtype=float)
        min_win = np.array([np.nan if r[11] is None else r[11] for r in rows], dtype=float)
        depth_at_min = np.array([np.nan if r[12] is None else r[12] for r in rows], dtype=float)
        n_win = np.array([r[13] or 0 for r in rows], dtype=int)
        price_lag = np.array([np.nan if r[14] is None else r[14] for r in rows], dtype=float)
        old = np.array([bool(r[15]) for r in rows])
        for secondary in (False, True):
            fl = floors(unit, secondary)
            if fl is None:
                continue
            cand = old & (n_win > 0) & np.isfinite(min_win) & (price >= RISE * min_win) & (depth_at_min >= fl)
            for i in _events(price, cand, min_win):
                r = rows[i]
                runners.append({"pool": pool, "hour": r[1], "price": r[2], "depth": r[3], "unit": unit, "quote_class": r[8], "orientation": r[9], "seen": seen,
                                "min_win": r[11], "depth_at_min": r[12], "n_win": int(r[13]), "partial_window": int(r[13]) < WIN_HI - WIN_LO + 1,
                                "floor": "secondary" if secondary else "primary", "floor_value": fl, "f7_trigger": pool in f7})
        fl = floors(unit)
        if fl is not None:
            cand = np.isfinite(price_lag) & (price >= ENTRY_RISE * price_lag) & (depth >= fl)
            for i in _events(price, cand, price_lag):
                r = rows[i]
                entries.append({"pool": pool, "hour": r[1], "price": r[2], "depth": r[3], "fee_bps": r[4], "unit": unit, "quote_class": r[8], "orientation": r[9], "seen": seen,
                                "price_lag": r[14], "f7_trigger": pool in f7})

    cur = con.execute(f"SELECT {', '.join(COLS)} FROM gw ORDER BY pool, hour")
    rows, pool = [], None
    while True:
        chunk = cur.fetchmany(batch)
        if not chunk:
            break
        for rec in chunk:
            if rec[0] != pool:
                flush(rows)
                rows, pool = [], rec[0]
            rows.append(rec)
    flush(rows)
    return runners, entries


def markouts(con, entries):
    """Net at the horizons for every entry in one join on the dense series; an entry without the horizon is counted, not scored."""
    if not entries:
        return []
    con.execute("CREATE OR REPLACE TABLE ent (pool VARCHAR, hour BIGINT)")
    con.executemany("INSERT INTO ent VALUES (?, ?)", [(e["pool"], e["hour"]) for e in entries])
    sel = ", ".join(f"g{h}.price AS p{h}, g{h}.depth AS d{h}" for h in HORIZONS)
    joins = " ".join(f"LEFT JOIN gu g{h} ON g{h}.pool = e.pool AND g{h}.hour = e.hour + {h}" for h in HORIZONS)
    later = {(r[0], r[1]): r[2:] for r in con.execute(f"SELECT e.pool, e.hour, {sel} FROM ent e {joins}").fetchall()}
    network = 2 * BASE_LAMPORTS / 1e9 / SIZE_SOL
    out = []
    for e in entries:
        s = SIZE_SOL if e["unit"] == "SOL" else SIZE_USDC
        wiped_abs = WIPED_ABS_SOL if e["unit"] == "SOL" else WIPED_ABS_USDC
        f = (e["fee_bps"] or 0) / 1e4
        row = dict(e)
        vals = later[(e["pool"], e["hour"])]
        for j, h in enumerate(HORIZONS):
            p_h, d_h = vals[2 * j], vals[2 * j + 1]
            if p_h is None or d_h is None:
                row[f"net_{h}h"] = None
            elif d_h < max(wiped_abs, WIPED_REL * e["depth"]):
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


def analyse(con, runners, entries, mk, boot, hour_of_day):
    res = {"rule": RULE, "runners": {}, "markouts": {}, "counts": {}, "pools": {}}
    res["pools"]["by_class"] = {f"{r[0]}/{r[1]}/{r[2]}": {"pools": r[3], "with_hours": r[4], "seen": bool(r[5])} for r in con.execute(
        "SELECT u.quote_class, u.orientation, u.unit, count(*), count(h.pool), u.seen FROM pu u LEFT JOIN (SELECT DISTINCT pool FROM h0) h USING (pool) GROUP BY 1, 2, 3, 6 ORDER BY 4 DESC").fetchall()}
    res["pools"]["other_without_hours"] = con.execute("SELECT count(*) FROM pu u LEFT JOIN (SELECT DISTINCT pool FROM h0) h USING (pool) WHERE u.quote_class = 'other' AND h.pool IS NULL").fetchone()[0]
    for floor in ("primary", "secondary"):
        tab = {}
        for r in runners:
            if r["floor"] != floor:
                continue
            key = f"{r['quote_class']}/{r['orientation']}/{r['unit']}"
            t = tab.setdefault(key, {"runners": 0, "pools": set(), "seen": r["seen"], "f7_pools": set(), "partial": 0})
            t["runners"] += 1
            t["pools"].add(r["pool"])
            t["partial"] += int(r["partial_window"])
            if r["f7_trigger"]:
                t["f7_pools"].add(r["pool"])
        res["runners"][floor] = {k: {"runners": v["runners"], "pools": len(v["pools"]), "seen": v["seen"], "pools_with_f7_trigger": len(v["f7_pools"]), "partial_window": v["partial"]} for k, v in tab.items()}
        tot = sum(v["runners"] for v in tab.values())
        seen = sum(v["runners"] for v in tab.values() if v["seen"])
        res["counts"][floor] = {"runners": tot, "seen": seen, "share_seen": seen / tot if tot else None, "pools": len({p for v in tab.values() for p in v["pools"]}),
                                "partial_window": sum(v["partial"] for v in tab.values())}
    for unit in ("SOL", "USDC"):
        for side, flag in (("inside", True), ("outside", False)):
            rows = [m for m in mk if m["unit"] == unit and m["seen"] == flag]
            per_pool = {}
            for m in rows:
                per_pool.setdefault(m["pool"], []).append(m)
            cell = {"entries": len(rows), "pools": len(per_pool)}
            for h in HORIZONS:
                vals, days = [], []
                for p, ms in per_pool.items():
                    v = [m[f"net_{h}h"] for m in ms if m[f"net_{h}h"] is not None]
                    if v:
                        vals.append(float(np.mean(v)))
                        days.append(hour_of_day(min(m["hour"] for m in ms)))
                cell[f"net_{h}h"] = _stat(vals, days, boot)
                cell[f"entries_without_{h}h"] = sum(1 for m in rows if m[f"net_{h}h"] is None)
            res["markouts"][f"{unit}_{side}"] = cell
    return res


def _c(x):
    return "–" if x is None else f"{x * 100:+.1f}"


def markdown(res, hours_dir):
    L = [f"## E58 runner census ({os.path.basename(hours_dir.rstrip('/'))})", "",
         f"Runner = close >= {RISE:.0f}x the minimum close over the existing rows of [u − {WIN_HI} h, u − {WIN_LO} h] (pool >= {WIN_LO} h old; partial windows allowed and counted) with the depth at that minimum >= the floor "
         f"(SOL {LIQ_SOL:.0f} primary / {LIQ_SOL_2:.0f} secondary; USDC {LIQ_USDC:,.0f} / {LIQ_USDC_2:,.0f}); one per pool per movement (reset below {RESET}x the minimum). "
         f"Seen = SOL-quoted PumpSwap pool (sol or reversed); 'other' pools in SOL through the quote mint's SOL pool when it exists, USDC pools in USDC, the rest without a floor; "
         f"on store v2 the 'other' class has no legs: {res['pools']['other_without_hours']:,} 'other' pools are counted without hours.", "",
         "| quote / orientation / unit | pools | with hours | seen |", "|---|---|---|---|"]
    for k, v in res["pools"]["by_class"].items():
        L.append(f"| {k} | {v['pools']:,} | {v['with_hours']:,} | {v['seen']} |")
    L.append("")
    for floor in ("primary", "secondary"):
        c = res["counts"][floor]
        L += [f"**{floor} floor:** {c['runners']:,} runners in {c['pools']:,} pools; seen by the universe {c['seen']:,} ({(c['share_seen'] or 0) * 100:.0f} %); partial window {c['partial_window']:,}.", "",
              "| quote / orientation / unit | runners | pools | seen | pools with an F7 trigger | partial window |", "|---|---|---|---|---|---|"]
        for k, v in sorted(res["runners"][floor].items(), key=lambda kv: -kv[1]["runners"]):
            L.append(f"| {k} | {v['runners']:,} | {v['pools']:,} | {v['seen']} | {v['pools_with_f7_trigger']:,} | {v['partial_window']:,} |")
        L.append("")
    L += [f"Momentum entry (close >= {ENTRY_RISE:.0f}x the close {ENTRY_LAG} h earlier, depth >= the primary floor, one per movement with the {RESET}x reset; {SIZE_SOL} SOL / {SIZE_USDC:.0f} USDC round trip with the pool's fee tier, "
          f"the buy impact at the entry depth, the sell impact at the depth at h, the base network cost {RULE['network_points'] * 100:.2f} pt; wiped = −100 % when the depth at h < max(1 SOL, 1 % of the entry depth)); "
          f"unit = pool, {TRIM * 100:.1f} % trimmed, day bootstrap; entries without the horizon (store end) counted, not scored:", "",
          "| unit / side | entries | pools | net 1 h | 6 h | 24 h | wiped 24 h | without 24 h |", "|---|---|---|---|---|---|---|---|"]
    for k, v in res["markouts"].items():
        cells = []
        for h in HORIZONS:
            s = v.get(f"net_{h}h")
            cells.append(f"{_c(s['mean'])} [{_c(s['lo'])}; {_c(s['hi'])}]" if s else "–")
        w = v.get("net_24h")
        L.append(f"| {k} | {v['entries']:,} | {v['pools']:,} | " + " | ".join(cells) + f" | {(w['wiped'] * 100):.0f} % | {v['entries_without_24h']:,} |" if w
                 else f"| {k} | {v['entries']:,} | {v['pools']:,} | – | – | – | – | {v['entries_without_24h']:,} |")
    L += ["", "Limits: hourly bars miss intra-hour runs; dev lies before the synthetic migration of 7 Oct and before TTWO; a 3x in a quote token's units can be the quote's own move (hence the SOL conversion); "
          "the 'other' class has no legs in store v2 (counted, not detected); Meteora / Raydium are unknown (no decoders in our data).", ""]
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
    lab = os.environ.get("LAB_DATA", "/home/chupa/Solana-project/data-old-faithful-one/lab")
    spill = os.environ.get("LAB_SPILL_DIR", os.path.join(lab, "tmp", f"runner-census-{os.getpid()}"))
    os.makedirs(spill, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET threads TO {int(os.environ.get('LAB_WALLETS_THREADS', '2'))}; SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1500MB')}'; "
                f"SET temp_directory = '{spill}'; SET max_temp_directory_size = '{os.environ.get('LAB_SPILL_MAX', '30GB')}'; SET preserve_insertion_order = false")
    sizes = load(con, a.hours_dir)
    print(f"grid: {sizes}", file=sys.stderr, flush=True)
    runners, entries = detect(con, a.parts)
    print(f"{len(runners):,} runner rows, {len(entries):,} entries ({time.time() - t0:.0f} s)", file=sys.stderr, flush=True)
    mk = markouts(con, entries)
    hb = dict(con.execute("SELECT hour, strftime(to_timestamp(first_block_time), '%Y-%m-%d') FROM hb").fetchall())
    res = analyse(con, runners, entries, mk, a.boot, lambda h: hb.get(h, str(h)))
    res["input"] = sizes | {"runner_rows": len(runners), "entries": len(entries), "parts": a.parts, "seconds": round(time.time() - t0, 1)}
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
    print(json.dumps(res["counts"] | {"grid": sizes, "seconds": res["input"]["seconds"]}))


if __name__ == "__main__":
    main()
