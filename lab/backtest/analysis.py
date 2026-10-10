"""Checks on F7 in reversed pools (coordinator review of v1.1, 2026-10-10).

  python -m backtest.analysis f7rev <run_dir> [--store dev2.duckdb]  # writes <run_dir>/f7_reversed_checks.md

  c) latency: F7 reversed by d and q (base costs, mean per trade and day-CI from summary.parquet);
  d) concentration: share of P&L from the top-10 pools and top-10 trigger wallets, and the result
     without the top-10 pools;
  e) mechanism: P&L split by whether the trigger seller bought the token back within T slots
     (T of the variant). This looks at the future of the trigger on purpose: it explains results,
     it is never a signal.
"""

import argparse
import os

import duckdb
import numpy as np

from .report import _boot
from .streams import SPILL_DIR

STORE = "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"


def _con():
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '1GB'; SET threads TO 2; SET temp_directory = '{SPILL_DIR}'")
    return con


def _table(cols, rows):
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join("" if x is None else str(x) for x in r) + " |" for r in rows]
    return out


def f7_reversed(run_dir, store=STORE, segment="pool_reversed"):
    con = _con()
    res = f"read_parquet('{run_dir}/results.parquet')"
    f7 = f"(SELECT * FROM {res} WHERE family = 'F7' AND segment = '{segment}' AND NOT skipped)"
    lines = [f"# F7 in {segment}: checks", ""]

    lines += ["## c) Latency (base costs)", ""]
    rows = con.execute(f"""
        SELECT size_sol, d, q, count(*) FILTER (WHERE ret_base IS NOT NULL) AS n, round(avg(ret_base) * 100, 2) AS mean_pct,
               round(median(ret_base) * 100, 2) AS median_pct, round(avg(pnl_base_lamports) / 1e9, 4) AS sol_per_trade
        FROM {f7} GROUP BY ALL ORDER BY ALL""").fetchall()
    lines += _table(["size SOL", "d", "q", "trades", "mean %", "median %", "SOL/trade"], rows) + [""]

    lines += ["## d) Concentration (base costs, all F7 variants)", ""]
    rows = []
    for size, d, q in con.execute(f"SELECT DISTINCT size_sol, d, q FROM {f7} ORDER BY ALL").fetchall():
        w = f"{f7} WHERE size_sol = {size} AND d = {d} AND q = {q} AND pnl_base_lamports IS NOT NULL"
        tot, n = con.execute(f"SELECT sum(pnl_base_lamports) / 1e9, count(*) FROM (SELECT * FROM {w})").fetchone()
        top_p = con.execute(f"""SELECT sum(s) FROM (SELECT sum(pnl_base_lamports) / 1e9 AS s FROM (SELECT * FROM {w})
                                GROUP BY pool ORDER BY s DESC LIMIT 10)""").fetchone()[0]
        top_w = con.execute(f"""SELECT sum(s) FROM (SELECT sum(pnl_base_lamports) / 1e9 AS s FROM (SELECT * FROM {w})
                                GROUP BY trigger_trader ORDER BY s DESC LIMIT 10)""").fetchone()[0]
        ex = con.execute(f"""WITH x AS (SELECT * FROM {w}),
                               top AS (SELECT pool FROM x GROUP BY pool ORDER BY sum(pnl_base_lamports) DESC LIMIT 10)
                             SELECT round(avg(ret_base) * 100, 2), count(*) FROM x WHERE pool NOT IN (SELECT pool FROM top)""").fetchone()
        rows.append((size, d, q, n, round(tot, 2), round(top_p, 2), round(top_w, 2), ex[0], ex[1]))
    lines += _table(["size SOL", "d", "q", "trades", "total P&L SOL", "top-10 pools", "top-10 trigger wallets",
                     "mean % without top-10 pools", "trades without"], rows) + [""]

    lines += ["## e) Mechanism: does the trigger seller buy back within T?", ""]
    con.execute(f"ATTACH '{store}' AS st (READ_ONLY)")
    con.execute(f"""CREATE TEMP TABLE trig AS
        SELECT DISTINCT pool, t, trigger_trader, TRY_CAST(split_part(variant, '_T', 2) AS BIGINT) AS T FROM {f7}""")
    con.execute("""CREATE TEMP TABLE back AS
        SELECT g.pool, g.t, g.T, bool_or(e.slot IS NOT NULL) AS bought_back
        FROM trig g LEFT JOIN st.pool e ON e.pool = g.pool AND e.trader = g.trigger_trader AND e.token_buy
             AND e.slot > g.t AND e.slot <= g.t + g.T
        GROUP BY ALL""")
    rows = con.execute(f"""
        SELECT r.size_sol, r.d, r.q, b.bought_back, count(*) AS n, round(avg(r.ret_base) * 100, 2) AS mean_pct,
               round(avg(r.pnl_base_lamports) / 1e9, 4) AS sol_per_trade,
               round(avg(r.trigger_farmer::INT) * 100, 1) AS trigger_farmer_pct
        FROM {f7} r JOIN back b ON b.pool = r.pool AND b.t = r.t AND b.T = TRY_CAST(split_part(r.variant, '_T', 2) AS BIGINT)
        WHERE r.ret_base IS NOT NULL GROUP BY r.size_sol, r.d, r.q, b.bought_back ORDER BY ALL""").fetchall()
    lines += _table(["size SOL", "d", "q", "seller bought back", "trades", "mean %", "SOL/trade", "trigger seller farmer %"],
                    rows) + [""]

    lines += ["## a) Farming split (base costs)", ""]
    rows = []
    for size, d, q, farm in con.execute(f"SELECT DISTINCT size_sol, d, q, farm FROM {f7} ORDER BY ALL").fetchall():
        days = con.execute(f"""SELECT day, sum(ret_base), count(ret_base) FROM {f7}
                               WHERE size_sol = {size} AND d = {d} AND q = {q} AND farm = '{farm}' AND ret_base IS NOT NULL
                               GROUP BY day""").fetchall()
        sums = np.array([x[1] for x in days], dtype=float)
        cnts = np.array([x[2] for x in days], dtype=float)
        lo, hi = _boot(sums, cnts)
        rows.append((size, d, q, farm, int(cnts.sum()), round(sums.sum() / cnts.sum() * 100, 2) if cnts.sum() else None,
                     None if lo is None else f"{lo * 100:+.2f} … {hi * 100:+.2f}"))
    lines += _table(["size SOL", "d", "q", "pool farming flag", "trades", "mean %", "95% CI (days)"], rows) + [""]
    path = os.path.join(run_dir, "f7_reversed_checks.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["f7rev"])
    ap.add_argument("run_dir")
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--segment", default="pool_reversed")
    a = ap.parse_args()
    print(f7_reversed(a.run_dir, a.store, a.segment))


if __name__ == "__main__":
    main()
