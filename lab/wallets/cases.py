"""Onderzoek 1b, casusselectie: a "trending" list from our own data (owner request, 2026-10-11).

Only development slots, pump tokens with a SOL quote (curve and canonical migration pool; reversed
and other pools are farming venues and are left out). Prices are reserve prices (curve: virtual quote / virtual token; pool: e / b, or
b / e in a reversed pool), so dust trades cannot distort them; the market cap is price x total
supply, in SOL. Lists require >= MIN_TRADERS traders and >= MIN_VOLUME_SOL volume.

Per UTC day (python -m wallets.cases --day D):
  1. trending 24 h: top-5 by (24 h return x SOL volume), volume >= MIN_VOLUME_SOL;
  2. pumpers/dumpers: top-5 by SOL volume among tokens that ended the day <= 10% of their peak.
Over a range (--seven --from-slot A --to-slot B; graduations with 7 days of data after them):
  3. trending 7 days: top-10 market cap 7 days after graduation (stayers), and top-10 peak
     market cap within 7 days that lost >= 95% by day 7 (collapsed).

Each case gets a timeline (create, graduation, peak, end), the runner features at graduation
+ 5 / + 15 min (runners1b.py), the insider share and creator sells, and the five biggest sells
in the first 24 h with their timing. cases_report.py renders Markdown with a DexScreener
link per mint for the owner; no other data source is used.

The per-token aggregates of every day (daily-agg-<day>.parquet) and of the 7-day window
(weekly-agg-<from>.parquet) are saved, so --reselect rebuilds the lists with a changed rule
without touching the store; --reselect --timelines adds the timelines of the new cases.
"""

import argparse
import datetime as dt
import glob
import json
import os
import sys
import time

import duckdb
import pyarrow.parquet as pq

from . import runners1b as runners
from .common import OUT_ROOT, SOL, Clock, connect, git_commit, guard
from .episodes import POOL_KEY, SOL_QUOTES

MIN_VOLUME_SOL = 300
MIN_TRADERS = 100
DAY_MS = 86_400_000
TOP_DAY, TOP_WEEK, TOP_SELLERS = 5, 10, 5

# Self-funded structure / dev bundle (coordinator, 2026-10-11; sharpened twice after the full runs): the
# token graduated in its create slot, or at most MAX_BUNDLE_CURVE_BUYERS wallets bought the whole curve.
# "> 50% of the pool buys by one party" caught 46 of 100 trending tokens, "create slot and <= 2 curve
# buyers" 85, but the same operators also use 3-5 curve wallets (all 10 "organic" 7-day collapses of the
# second run had exactly 4, and 35 of the 100 "organic" risers graduated in their create slot).
# curve_buyers is NULL for a token whose curve lies before the store; such a token is not flagged.
MAX_BUNDLE_CURVE_BUYERS = 5
SELF_FUNDED_SQL = f"COALESCE(grad_in_create OR curve_buyers <= {MAX_BUNDLE_CURVE_BUYERS}, false)"


def day_slots(con, day):
    d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    t0, t1 = int(d0.timestamp()), int((d0 + dt.timedelta(days=1)).timestamp())
    lo, hi = con.execute("SELECT min(slot) FILTER (WHERE block_time >= ?), min(slot) FILTER (WHERE block_time >= ?) "
                         "FROM blocks", [t0, t1]).fetchone()
    if lo is None or hi is None:
        raise SystemExit(f"day {day} is not fully in the store")
    return int(lo), int(hi)


def trades_sql(lo, hi):
    """pump tokens only: the curve and the canonical normal migration pool (reserve price e / b;
    `vault` is the real SOL in the pool, or the curve's real SOL reserves)."""
    quotes = ",".join(f"'{q}'" for q in SOL_QUOTES)
    return f"""
      SELECT c.mint AS key, c.mint, c.slot::BIGINT AS slot, c.tx_index, c.trader, c.is_buy AS token_buy,
             c.q::DOUBLE AS sol, c.vq::DOUBLE / c.vt AS px, c.rq::DOUBLE AS vault
      FROM curve c JOIN mints m USING (mint)
      WHERE c.slot >= {lo} AND c.slot < {hi} AND c.vt > 0 AND NOT m.mayhem AND m.quote_mint IN ({quotes})
      UNION ALL
      SELECT m.mint, m.mint, e.slot::BIGINT, e.tx_index, e.trader, e.token_buy, e.sol_amount::DOUBLE,
             e.e::DOUBLE / e.b, e.vault::DOUBLE
      FROM pool e JOIN pools p USING (pool) JOIN mints m ON m.mint = p.mint AND m.pool = p.pool
      WHERE e.slot >= {lo} AND e.slot < {hi} AND e.kind IN ('buy', 'sell') AND e.b > 0 AND e.e > 0
        AND p.quote_class = 'sol' AND p.orientation = 'normal' AND NOT COALESCE(p.mayhem, false) AND NOT m.mayhem"""


MIN_VAULT_DAY_SOL, MIN_VAULT_WEEK_SOL = 5, 10  # real SOL in the pool at the valuation moment

BUCKETS = 8  # per-key aggregation in hash buckets of the key: bounded working set


def daily_cases(con, day, lo, hi):
    con.execute("""CREATE OR REPLACE TEMP TABLE d (key VARCHAR, mint VARCHAR, trades BIGINT, traders BIGINT, volume_sol DOUBLE,
                   p_first DOUBLE, p_last DOUBLE, p_max DOUBLE, peak_slot BIGINT, first_slot BIGINT, last_slot BIGINT,
                   vault_end DOUBLE, buy_sol DOUBLE, top_buyer_share DOUBLE)""")
    con.execute("CREATE OR REPLACE TEMP TABLE tb (key VARCHAR, top_buyer_share DOUBLE)")
    for b in range(BUCKETS):
        con.execute(f"""
          INSERT INTO d
          SELECT key, any_value(mint), count(*), count(DISTINCT trader), sum(sol) / {SOL},
                 arg_min(px, slot * 1048576 + tx_index), arg_max(px, slot * 1048576 + tx_index),
                 max(px), arg_max(slot, px), min(slot), max(slot), arg_max(vault, slot * 1048576 + tx_index) / {SOL},
                 sum(sol) FILTER (WHERE token_buy) / {SOL}, NULL
          FROM ({trades_sql(lo, hi)}) WHERE hash(key) % {BUCKETS} = {b} GROUP BY key""")
        con.execute(f"""
          INSERT INTO tb
          SELECT key, max(bs) / sum(bs) FROM (SELECT key, trader, sum(sol) AS bs FROM ({trades_sql(lo, hi)})
                                             WHERE token_buy AND hash(key) % {BUCKETS} = {b} GROUP BY key, trader) GROUP BY key""")
    # top_buyer_share stays as information (the pool buys of a structure are spread over several wallets).
    con.execute("""
      CREATE OR REPLACE TEMP TABLE d2 AS
      SELECT d.* EXCLUDE (top_buyer_share), tb.top_buyer_share, COALESCE(m.complete_slot = m.create_slot, false) AS grad_in_create,
             cb.curve_buyers, false AS self_funded
      FROM d LEFT JOIN tb USING (key) LEFT JOIN mints m ON m.mint = d.mint
      LEFT JOIN (SELECT c.mint, count(DISTINCT c.trader) AS curve_buyers FROM curve c SEMI JOIN d ON d.mint = c.mint
                 WHERE c.is_buy GROUP BY c.mint) cb ON cb.mint = d.mint""")
    con.execute("CREATE OR REPLACE TEMP TABLE d AS SELECT * FROM d2")
    sel = daily_select(con, day)
    # All tokens of the day with their aggregates: re-selection with other rules needs no store scan.
    daily_agg = con.execute(f"SELECT '{day}' AS day, * FROM d ORDER BY volume_sol DESC").to_arrow_table()
    return sel, daily_agg


def daily_select(con, day):
    """The three daily lists from temp table d (the day's per-token aggregates; `self_funded` is recomputed
    from grad_in_create and curve_buyers, so a saved daily-agg file can be re-selected with a new rule)."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE d AS SELECT * REPLACE ({SELF_FUNDED_SQL} AS self_funded) FROM d")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE sel AS
      SELECT * FROM (
        SELECT 'trending_24h' AS list, row_number() OVER (ORDER BY (p_last / p_first - 1) * volume_sol DESC) AS rank, d.*,
               p_last / p_first - 1 AS ret_24h, p_max / p_first AS peak_mult, p_last / p_max AS end_of_peak
        FROM d WHERE volume_sol >= {MIN_VOLUME_SOL} AND traders >= {MIN_TRADERS} AND p_last > p_first
          AND vault_end >= {MIN_VAULT_DAY_SOL}) WHERE rank <= {TOP_DAY}
      UNION ALL
      SELECT * FROM (
        SELECT 'trending_24h_organic' AS list, row_number() OVER (ORDER BY (p_last / p_first - 1) * volume_sol DESC) AS rank, d.*,
               p_last / p_first - 1 AS ret_24h, p_max / p_first AS peak_mult, p_last / p_max AS end_of_peak
        FROM d WHERE volume_sol >= {MIN_VOLUME_SOL} AND traders >= {MIN_TRADERS} AND p_last > p_first
          AND vault_end >= {MIN_VAULT_DAY_SOL} AND NOT self_funded) WHERE rank <= {TOP_DAY}
      UNION ALL
      SELECT * FROM (
        SELECT 'dumpers_24h' AS list, row_number() OVER (ORDER BY volume_sol DESC) AS rank, d.*,
               p_last / p_first - 1 AS ret_24h, p_max / p_first AS peak_mult, p_last / p_max AS end_of_peak
        FROM d WHERE p_last <= 0.1 * p_max AND volume_sol >= {MIN_VOLUME_SOL} AND traders >= {MIN_TRADERS}) WHERE rank <= {TOP_DAY}""")
    return con.execute(f"SELECT '{day}' AS day, * FROM sel ORDER BY list, rank").to_arrow_table()


def weekly_cases(con, lo, hi, clock):
    """Graduations in [lo, hi - 7 d): market cap at + 7 d and the peak inside those 7 days."""
    n7 = clock.slots(7 * DAY_MS, lo)
    con.execute(f"CREATE OR REPLACE TEMP TABLE g AS {runners.grads_sql(lo, hi - n7)}")
    con.execute("""CREATE OR REPLACE TEMP TABLE w (mint VARCHAR, pool VARCHAR, open_slot BIGINT, create_slot BIGINT, grad_in_create BOOLEAN,
                   p_open DOUBLE, supply BIGINT, symbol VARCHAR, name VARCHAR, p_peak DOUBLE, peak_slot BIGINT, p_7d DOUBLE,
                   volume_7d DOUBLE, traders_7d BIGINT, vault_7d DOUBLE)""")
    con.execute(f"CREATE OR REPLACE TEMP TABLE gcb AS SELECT c.mint, count(DISTINCT c.trader) AS curve_buyers FROM curve c "
                f"SEMI JOIN g ON g.mint = c.mint WHERE c.is_buy GROUP BY c.mint")
    for b in range(BUCKETS):
        con.execute(f"""
          INSERT INTO w
          SELECT g.mint, g.pool, g.open_slot, g.create_slot, g.grad_in_create, g.p_open, m.supply, m.symbol, m.name,
                 max(e.e::DOUBLE / e.b), arg_max(e.slot, e.e::DOUBLE / e.b),
                 arg_max(e.e::DOUBLE / e.b, e.slot::BIGINT * 1048576 + e.tx_index),
                 sum(e.sol_amount) / {SOL}, count(DISTINCT e.trader), arg_max(e.vault, e.slot::BIGINT * 1048576 + e.tx_index) / {SOL}
          FROM g JOIN mints m USING (mint) JOIN pool e ON e.pool = g.pool AND e.kind IN ('buy', 'sell') AND e.b > 0
            AND e.slot >= g.open_slot AND e.slot < g.open_slot + {n7}
          WHERE hash(g.pool) % {BUCKETS} = {b}
          GROUP BY ALL""")
    con.execute("CREATE OR REPLACE TEMP TABLE w2 AS SELECT w.*, gcb.curve_buyers, false AS self_funded FROM w LEFT JOIN gcb USING (mint)")
    sel = weekly_select(con)
    return sel, con.execute("SELECT * FROM w2 ORDER BY p_peak * supply DESC").to_arrow_table()


def weekly_select(con):
    """The 7-day lists from temp table w2 (graduations with their 7-day aggregates; `self_funded` recomputed)."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE w2 AS SELECT * REPLACE ({SELF_FUNDED_SQL} AS self_funded) FROM w2")
    lists = []
    for suffix, extra in (("", ""), ("_organic", " AND NOT self_funded")):
        lists.append(f"""
          SELECT * FROM (
            SELECT 'stayers_7d{suffix}' AS list, row_number() OVER (ORDER BY p_7d * supply DESC) AS rank, w2.*,
                   p_7d * supply / {SOL} AS mcap_7d_sol, p_peak * supply / {SOL} AS mcap_peak_sol, p_7d / p_peak AS end_of_peak
            FROM w2 WHERE volume_7d >= {MIN_VOLUME_SOL} AND traders_7d >= {MIN_TRADERS} AND vault_7d >= {MIN_VAULT_WEEK_SOL}{extra}) WHERE rank <= {TOP_WEEK}
          UNION ALL
          SELECT * FROM (
            SELECT 'collapsed_7d{suffix}' AS list, row_number() OVER (ORDER BY p_peak * supply DESC) AS rank, w2.*,
                   p_7d * supply / {SOL} AS mcap_7d_sol, p_peak * supply / {SOL} AS mcap_peak_sol, p_7d / p_peak AS end_of_peak
            FROM w2 WHERE p_7d <= 0.05 * p_peak AND volume_7d >= {MIN_VOLUME_SOL} AND traders_7d >= {MIN_TRADERS}{extra}) WHERE rank <= {TOP_WEEK}""")
    return con.execute(" UNION ALL ".join(lists) + " ORDER BY list, rank").to_arrow_table()


def timelines(con, mints, clock):
    """Per mint: create, graduation, peak and end of the first 24 h, runner features, big sellers."""
    if not mints:  # a re-selection with nothing new: executemany rejects an empty parameter list
        return {}
    con.execute("CREATE OR REPLACE TEMP TABLE want_m (mint VARCHAR)")
    con.executemany("INSERT INTO want_m VALUES (?)", [(m,) for m in mints])
    rows = con.execute("""SELECT m.mint, m.create_slot, m.complete_slot, m.migrate_slot, m.pool, m.creator, m.symbol, m.name, m.supply
                          FROM mints m SEMI JOIN want_m USING (mint)""").fetchall()
    out = {}
    n_day = clock.slots(DAY_MS, rows[0][1] if rows else 0)
    n_ins = clock.slots(runners.INSIDER_MS, rows[0][1] if rows else 0)
    for mint, c, done, open_slot, pool, creator, symbol, name, supply in rows:
        tl = {"mint": mint, "symbol": symbol, "name": name, "create_slot": c, "complete_slot": done, "open_slot": open_slot,
              "creator": creator, "supply": supply, "dexscreener": f"https://dexscreener.com/solana/{mint}"}
        if open_slot is not None and pool:
            feat, _ = runners.build(con, int(open_slot), int(open_slot) + 1, clock)
            if feat.num_rows:
                tl.update({k: v for k, v in feat.to_pylist()[0].items()
                           if k.startswith(("t5_", "t15_")) or k in ("max_mult", "end_mult", "runner3", "runner10", "grad_in_create")})
            sellers = con.execute(f"""
              WITH ins AS (SELECT DISTINCT trader FROM curve WHERE mint = ? AND is_buy AND slot <= ? + {n_ins})
              SELECT e.trader, e.sol_amount / {SOL} AS sol, (e.slot - ?) * {clock.ms_per_slot(int(open_slot))} / 60000 AS minutes_after_open,
                     e.trader = ? AS is_creator, e.trader IN (SELECT trader FROM ins) AS is_insider
              FROM pool e WHERE e.pool = ? AND e.kind IN ('buy', 'sell') AND NOT e.token_buy
                AND e.slot >= ? AND e.slot < ? + {n_day}
              ORDER BY e.sol_amount DESC LIMIT {TOP_SELLERS}""",
                                  [mint, c, open_slot, creator, pool, open_slot, open_slot]).fetchall()
            tl["big_sellers"] = [{"wallet": w, "sol": s, "minutes_after_open": mm, "creator": ic, "insider": ii}
                                 for w, s, mm, ic, ii in sellers]
            peak = con.execute(f"""SELECT arg_max(slot, e::DOUBLE / b), max(e::DOUBLE / b) FROM pool
                                   WHERE pool = ? AND kind IN ('buy', 'sell') AND b > 0 AND slot >= ? AND slot < ? + {n_day}""",
                               [pool, open_slot, open_slot]).fetchone()
            tl["peak_slot_24h"] = peak[0]
        out[mint] = tl
    return out


def write_timelines(path, meta, new):
    """Merge new timelines into an existing timelines file (re-selection keeps the ones already computed)."""
    old = json.load(open(path))["timelines"] if os.path.exists(path) else {}
    old.update(new)
    with open(path, "w") as f:
        json.dump({**meta, "timelines": old}, f, indent=1, default=str)
    return old


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="UTC day for the daily lists")
    ap.add_argument("--seven", action="store_true", help="the 7-day lists over --from-slot/--to-slot")
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--symbols", help="comma-separated symbols: timelines of these tokens (the most-traded mint per symbol)")
    ap.add_argument("--reselect", action="store_true",
                    help="re-select the lists from the saved daily-agg / weekly-agg files in --out (no store scan); "
                         "timelines only with --timelines, and only for mints that have none yet")
    ap.add_argument("--timelines", action="store_true", help="with --reselect: compute the missing timelines from the store")
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "cases-v1"))
    a = ap.parse_args()
    need_store = not a.reselect or a.timelines or a.symbols
    con = connect(memory=os.environ.get("LAB_CASES_MEMORY", os.environ.get("LAB_WALLETS_MEMORY", "1000MB"))) if need_store \
        else duckdb.connect()
    clock = Clock.from_store(con) if need_store else None
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    if a.day:
        agg_path, tl_path = os.path.join(a.out, f"daily-agg-{a.day}.parquet"), os.path.join(a.out, f"timelines-{a.day}.json")
        if a.reselect:
            con.execute("CREATE OR REPLACE TEMP TABLE d AS SELECT * EXCLUDE (day) FROM read_parquet(?)", [agg_path])
            sel = daily_select(con, a.day)
        else:
            lo, hi = day_slots(con, a.day)
            guard(hi)
            sel, agg = daily_cases(con, a.day, lo, hi)
            pq.write_table(agg, agg_path)
        mints = [m for m in sel.column("mint").to_pylist() if m]
        if a.reselect and not a.timelines:
            tl = {}
        else:
            have = json.load(open(tl_path))["timelines"] if a.reselect and os.path.exists(tl_path) else {}
            tl = timelines(con, [m for m in mints if m not in have], clock)
        pq.write_table(sel, os.path.join(a.out, f"cases-{a.day}.parquet"))
        if tl or not a.reselect:
            write_timelines(tl_path, {"day": a.day, "commit": git_commit()}, tl)
        print(f"{a.day}: {sel.num_rows} cases, {len(tl)} new timelines, {time.time() - t0:.0f}s"
              + (" (re-selected from daily-agg)" if a.reselect else ""), file=sys.stderr)
    if a.symbols:
        syms = [x.strip().lower() for x in a.symbols.split(",") if x.strip()]
        rows = con.execute("""
          SELECT lower(m.symbol) AS s, m.mint, COALESCE(ps.n_buy + ps.n_sell, 0) AS n
          FROM mints m LEFT JOIN pool_stats ps ON ps.pool = m.pool WHERE lower(m.symbol) IN (SELECT unnest(?))
          QUALIFY row_number() OVER (PARTITION BY lower(m.symbol) ORDER BY n DESC, m.create_slot) = 1""", [syms]).fetchall()
        found = {r[0]: r[1] for r in rows}
        tl = timelines(con, list(found.values()), clock) if found else {}
        with open(os.path.join(a.out, "timelines-symbols.json"), "w") as f:
            json.dump({"symbols": syms, "found": found, "missing": [x for x in syms if x not in found], "commit": git_commit(),
                       "timelines": tl}, f, indent=1, default=str)
        print(f"symbols: {len(found)} found, missing {[x for x in syms if x not in found]}, {time.time() - t0:.0f}s", file=sys.stderr)
    if a.seven:
        guard(a.to_slot)
        agg_path, tl_path = os.path.join(a.out, f"weekly-agg-{a.from_slot}.parquet"), os.path.join(a.out, f"timelines-7d-{a.from_slot}.json")
        if a.reselect:
            con.execute("CREATE OR REPLACE TEMP TABLE w2 AS SELECT * FROM read_parquet(?)", [agg_path])
            sel = weekly_select(con)
        else:
            sel, agg = weekly_cases(con, a.from_slot, a.to_slot, clock)
            pq.write_table(agg, agg_path)
        mints = sel.column("mint").to_pylist()
        if a.reselect and not a.timelines:
            tl = {}
        else:
            have = json.load(open(tl_path))["timelines"] if a.reselect and os.path.exists(tl_path) else {}
            tl = timelines(con, [m for m in mints if m not in have], clock)
        pq.write_table(sel, os.path.join(a.out, f"cases-7d-{a.from_slot}.parquet"))
        if tl or not a.reselect:
            write_timelines(tl_path, {"from_slot": a.from_slot, "to_slot": a.to_slot, "commit": git_commit()}, tl)
        print(f"7d {a.from_slot}-{a.to_slot}: {sel.num_rows} cases, {len(tl)} new timelines, {time.time() - t0:.0f}s"
              + (" (re-selected from weekly-agg)" if a.reselect else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
