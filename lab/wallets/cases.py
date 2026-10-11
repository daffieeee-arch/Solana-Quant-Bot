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
+ 5 / + 15 min (runners.py), the insider share and creator sells, and the five biggest sells
in the first 24 h with their timing. The report (--report) renders Markdown with a DexScreener
link per mint for the owner; no other data source is used.
"""

import argparse
import datetime as dt
import glob
import json
import os
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq

from . import runners
from .common import OUT_ROOT, SOL, Clock, connect, git_commit, guard
from .episodes import POOL_KEY, SOL_QUOTES

MIN_VOLUME_SOL = 300
MIN_TRADERS = 100
DAY_MS = 86_400_000
TOP_DAY, TOP_WEEK, TOP_SELLERS = 5, 10, 5


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
    # Self-funded structure: one buyer graduated the token in its create slot and one party made more
    # than half of the day's pool buys (coordinator, 2026-10-11). Shown as a flag; lists with and without.
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE d2 AS
      SELECT d.* EXCLUDE (top_buyer_share), tb.top_buyer_share, COALESCE(m.complete_slot = m.create_slot, false) AS grad_in_create,
             cb.curve_buyers,
             COALESCE(m.complete_slot = m.create_slot AND cb.curve_buyers <= 2 AND tb.top_buyer_share > 0.5, false) AS self_funded
      FROM d LEFT JOIN tb USING (key) LEFT JOIN mints m ON m.mint = d.mint
      LEFT JOIN (SELECT c.mint, count(DISTINCT c.trader) AS curve_buyers FROM curve c SEMI JOIN d ON d.mint = c.mint
                 WHERE c.is_buy GROUP BY c.mint) cb ON cb.mint = d.mint""")
    con.execute("CREATE OR REPLACE TEMP TABLE d AS SELECT * FROM d2")
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
    return con.execute(f"""
      SELECT * FROM (
        SELECT 'stayers_7d' AS list, row_number() OVER (ORDER BY p_7d * supply DESC) AS rank, w.*,
               p_7d * supply / {SOL} AS mcap_7d_sol, p_peak * supply / {SOL} AS mcap_peak_sol, p_7d / p_peak AS end_of_peak
        FROM w WHERE volume_7d >= {MIN_VOLUME_SOL} AND traders_7d >= {MIN_TRADERS} AND vault_7d >= {MIN_VAULT_WEEK_SOL}) WHERE rank <= {TOP_WEEK}
      UNION ALL
      SELECT * FROM (
        SELECT 'collapsed_7d' AS list, row_number() OVER (ORDER BY p_peak * supply DESC) AS rank, w.*,
               p_7d * supply / {SOL} AS mcap_7d_sol, p_peak * supply / {SOL} AS mcap_peak_sol, p_7d / p_peak AS end_of_peak
        FROM w WHERE p_7d <= 0.05 * p_peak AND volume_7d >= {MIN_VOLUME_SOL} AND traders_7d >= {MIN_TRADERS}) WHERE rank <= {TOP_WEEK}
      ORDER BY list, rank""").to_arrow_table()


def timelines(con, mints, clock):
    """Per mint: create, graduation, peak and end of the first 24 h, runner features, big sellers."""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="UTC day for the daily lists")
    ap.add_argument("--seven", action="store_true", help="the 7-day lists over --from-slot/--to-slot")
    ap.add_argument("--from-slot", type=int)
    ap.add_argument("--to-slot", type=int)
    ap.add_argument("--symbols", help="comma-separated symbols: timelines of these tokens (the most-traded mint per symbol)")
    ap.add_argument("--out", default=os.path.join(OUT_ROOT, "cases-v1"))
    a = ap.parse_args()
    con = connect(memory="1000MB")
    clock = Clock.from_store(con)
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    if a.day:
        lo, hi = day_slots(con, a.day)
        guard(hi)
        sel = daily_cases(con, a.day, lo, hi)
        tl = timelines(con, [m for m in sel.column("mint").to_pylist() if m], clock)
        pq.write_table(sel, os.path.join(a.out, f"cases-{a.day}.parquet"))
        with open(os.path.join(a.out, f"timelines-{a.day}.json"), "w") as f:
            json.dump({"day": a.day, "commit": git_commit(), "timelines": tl}, f, indent=1, default=str)
        print(f"{a.day}: {sel.num_rows} cases, {len(tl)} timelines, {time.time() - t0:.0f}s", file=sys.stderr)
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
        sel = weekly_cases(con, a.from_slot, a.to_slot, clock)
        tl = timelines(con, sel.column("mint").to_pylist(), clock)
        pq.write_table(sel, os.path.join(a.out, f"cases-7d-{a.from_slot}.parquet"))
        with open(os.path.join(a.out, f"timelines-7d-{a.from_slot}.json"), "w") as f:
            json.dump({"from_slot": a.from_slot, "to_slot": a.to_slot, "commit": git_commit(), "timelines": tl}, f, indent=1,
                      default=str)
        print(f"7d {a.from_slot}-{a.to_slot}: {sel.num_rows} cases, {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
