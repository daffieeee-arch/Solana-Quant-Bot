"""Checks on a money-v1 run before the money report is interpreted (coordinator, 2026-10-14): is the P&L per group
realised (cash minus network fees) or marked (the change in the marked inventory value); the per-day totals over all
wallets; the concentration of the loss over wallets per group; and the stratum coverage of the retail placebo (retail
is the bulk of all wallets, so its placebo draws from a tiny complement). Writes a Markdown file. Heavy (it rebuilds
the labels over all wallet-days): NAS.

  python -m wallets.money_checks --money DIR [--clusters DIR] [--views] [--out FILE]
"""

import argparse
import os
import sys

import duckdb

from . import money_report as R


def table(con, sql):
    rows = con.execute(sql).fetchall()
    cols = [d[0] for d in con.description]
    L = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        L.append("| " + " | ".join(f"{v:,.1f}" if isinstance(v, float) else ("–" if v is None else str(v)) for v in r) + " |")
    return "\n".join(L)


def render(con):
    L = ["## Money checks\n", "### Realised vs marked per group, binding days (SOL)\n",
         table(con, """SELECT label, count(DISTINCT wallet) AS wallets, sum(cash) / 1e9 AS cash, sum(fees) / 1e9 AS fees, sum(cash - fees) / 1e9 AS realised,
                       sum(dvalue) / 1e9 AS marked_change, sum(pnl) / 1e9 AS pnl, sum(buy_sol) / 1e9 AS bought, sum(sell_sol) / 1e9 AS sold,
                       sum(CASE WHEN day = (SELECT max(day) FROM meas) THEN value_end ELSE 0 END) / 1e9 AS value_end_last
                FROM meas GROUP BY label ORDER BY pnl"""),
         "\n### All wallets per day: cash, network fees, change in marked value, marked value at the day's end, active wallets\n",
         table(con, """SELECT day, sum(cash) / 1e9 AS cash, sum(fees) / 1e9 AS fees, sum(dvalue) / 1e9 AS dvalue, sum(value_end) / 1e9 AS value_end,
                       count(*) FILTER (WHERE n_legs > 0) AS active FROM wd GROUP BY day ORDER BY day"""),
         "\n### Farmers per binding day: realised vs marked\n",
         table(con, """SELECT day, count(*) AS n, sum(cash - fees) / 1e9 AS realised, sum(dvalue) / 1e9 AS marked, sum(pnl) / 1e9 AS pnl, sum(buy_sol) / 1e9 AS bought
                FROM meas WHERE label = 'farmer' GROUP BY day ORDER BY day"""),
         "\n### Concentration of the loss over wallets, per group (binding days)\n",
         table(con, """SELECT label, count(*) AS wallets, sum(p) / 1e9 AS total, sum(p) FILTER (WHERE rk <= 10) / 1e9 AS worst10, sum(p) FILTER (WHERE rk <= 100) / 1e9 AS worst100,
                       sum(p) FILTER (WHERE p > 0) / 1e9 AS winners_total, median(p) / 1e9 AS median_wallet
                FROM (SELECT label, wallet, sum(pnl) AS p, row_number() OVER (PARTITION BY label ORDER BY sum(pnl)) AS rk FROM meas GROUP BY label, wallet)
                GROUP BY label ORDER BY total"""),
         "\n### Retail placebo: label mix and stratum coverage\n",
         table(con, "SELECT label, count(*) AS wallets FROM strata GROUP BY label ORDER BY wallets DESC"),
         "",
         table(con, """SELECT count(*) AS retail_strata, count(*) FILTER (WHERE n_other > 0) AS with_candidates,
                       sum(n_retail) AS retail_members, sum(n_retail) FILTER (WHERE n_other > 0) AS retail_members_matched, sum(n_other) AS non_retail_wallets
                FROM (SELECT pos_dec, act, buy_dec, sum((label = 'retail')::INT) AS n_retail, sum((label <> 'retail')::INT) AS n_other FROM strata GROUP BY ALL)
                WHERE n_retail > 0""")]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--money", required=True)
    ap.add_argument("--clusters")
    ap.add_argument("--views", action="store_true", help="wallet-day rows as views on the parquet (fits a few GB; much slower)")
    ap.add_argument("--out")
    a = ap.parse_args()
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{os.environ.get('LAB_WALLETS_MEMORY', '1500MB')}'; SET threads TO 2; SET preserve_insertion_order = false")
    spill = os.environ.get("LAB_SPILL_DIR")
    if spill:
        os.makedirs(spill, exist_ok=True)
        con.execute(f"SET temp_directory = '{spill}'; SET max_temp_directory_size = '{os.environ.get('LAB_SPILL_MAX', '1GB')}'")
    R.load(con, a.money, a.clusters, materialize=not a.views)
    con.execute("""
      CREATE OR REPLACE TABLE strata AS
      SELECT wallet, label, ntile(10) OVER (ORDER BY COALESCE(n_positions, 0)) AS pos_dec, least(COALESCE(active_days, 0), 9) AS act,
             ntile(10) OVER (ORDER BY COALESCE(median_buy_sol, 0)) AS buy_dec
      FROM labels WHERE wallet IN (SELECT DISTINCT wallet FROM meas)""")
    md = render(con)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md + "\n")
        print(a.out, file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
