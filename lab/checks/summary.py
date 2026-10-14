"""Descriptive summary of the lab event dataset (input for the weekly checkpoint report).

Usage: python summary.py <chunks_dir> [--json out.json] [--bounded]
Only chunks whose `_manifest.json` has status "complete" are used.

--bounded (large datasets, bounded memory and spill): rows are not de-duplicated (continuity.py
counts duplicate events per chunk) and quantiles are t-digest estimates (approx_quantile). Counts,
distinct counts and sums stay exact.
"""

import argparse
import glob
import json
import os
import sys

import duckdb

SOL_QUOTES = ("11111111111111111111111111111111", "So11111111111111111111111111111111111111112")
LAMPORTS = 1e9


def chunk_dirs(root):
    out = []
    for m in sorted(glob.glob(os.path.join(root, "*", "_manifest.json"))):
        with open(m) as f:
            man = json.load(f)
        if man["status"] == "complete":
            out.append((man, os.path.dirname(m)))
    return out


def files(dirs, table):
    return sorted(f for d in dirs for f in glob.glob(os.path.join(d, table, "*.parquet")))


BOUNDED = False


def view(con, name, paths):
    if not paths:
        return False
    lit = "[" + ",".join("'" + p.replace("'", "''") + "'" for p in paths) + "]"
    dedup = "" if BOUNDED else "DISTINCT ON (signature, outer_ix, inner_ix) "
    con.execute(f"""CREATE OR REPLACE TEMP VIEW {name} AS
      SELECT {dedup}* FROM read_parquet({lit}, union_by_name = true)""")
    return True


def med(x, q=0.5):
    return f"approx_quantile({x}, {q})" if BOUNDED else (f"median({x})" if q == 0.5 else f"quantile_cont({x}, {q})")


def one(con, sql):
    row = con.execute(sql).fetchone()
    cols = [d[0] for d in con.description]
    return dict(zip(cols, row))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--json")
    ap.add_argument("--bounded", action="store_true", help="no de-duplication, approximate quantiles (large datasets)")
    a = ap.parse_args(argv)
    global BOUNDED
    BOUNDED = a.bounded

    chunks = chunk_dirs(a.root)
    dirs = [d for _, d in chunks]
    con = duckdb.connect()
    # Times are printed as UTC: never through the host's time zone (Europe/Amsterdam on the VPS).
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"SET threads TO {int(os.environ.get('LAB_DUCKDB_THREADS', '4'))}")
    con.execute(f"SET memory_limit = '{os.environ.get('LAB_DUCKDB_MEM', '6GB')}'")
    tmp = os.environ.get("LAB_DUCKDB_TMP")
    if tmp:
        con.execute(f"SET temp_directory = '{tmp}'")
        con.execute(f"SET max_temp_directory_size = '{os.environ.get('LAB_DUCKDB_TMP_MAX', '2GB')}'")
    con.execute("SET preserve_insertion_order = false")
    sol = ", ".join(f"'{q}'" for q in SOL_QUOTES)
    out = {
        "mode": "bounded (no de-duplication; approximate quantiles)" if BOUNDED else "exact",
        "chunks": len(chunks),
        "slots": sum(m["slot_end_exclusive"] - m["slot_start"] for m, _ in chunks),
        "extract_seconds": round(sum(m["elapsed_seconds"] for m, _ in chunks)),
        "transactions_scanned": sum(m["counters"]["txs_seen"] for m, _ in chunks),
        "parquet_bytes": sum(os.path.getsize(f) for d in dirs for f in glob.glob(os.path.join(d, "*", "*", "*.parquet"))
                             + glob.glob(os.path.join(d, "*", "*.parquet"))),
    }
    blocks = files(dirs, "blocks")
    if blocks:
        lit = "[" + ",".join(f"'{p}'" for p in blocks) + "]"
        out["time"] = one(con, f"""SELECT strftime(to_timestamp(min(block_time)), '%Y-%m-%d %H:%M:%S UTC') AS first_block, strftime(to_timestamp(max(block_time)), '%Y-%m-%d %H:%M:%S UTC') AS last_block,
            count(*) FILTER (WHERE NOT skipped) AS blocks FROM read_parquet({lit})""")

    if view(con, "trades", files(dirs, "pump/TradeEvent")):
        out["pump_trades"] = one(con, f"""SELECT count(*) AS trades, count(*) FILTER (WHERE is_buy) AS buys,
            count(DISTINCT mint) AS mints_traded, count(DISTINCT "user") AS traders,
            round(sum(COALESCE(quote_amount, sol_amount)) FILTER (WHERE COALESCE(quote_mint, '{SOL_QUOTES[0]}') IN ({sol})) / {LAMPORTS}, 1) AS sol_volume,
            count(*) FILTER (WHERE COALESCE(quote_mint, '{SOL_QUOTES[0]}') NOT IN ({sol})) AS non_sol_quote_trades,
            {med('fee_basis_points')} AS fee_bps_median, {med('creator_fee_basis_points')} AS creator_fee_bps_median,
            count(*) FILTER (WHERE mayhem_mode) AS mayhem_trades
            FROM trades""")
        out["pump_trades_per_mint"] = one(con, """SELECT quantile_disc(n, 0.5) AS p50, quantile_disc(n, 0.9) AS p90,
            quantile_disc(n, 0.99) AS p99, max(n) AS max FROM (SELECT mint, count(*) AS n FROM trades GROUP BY 1)""")
        out["pump_costs"] = one(con, f"""SELECT {med('priority_fee')} / {LAMPORTS} AS priority_fee_sol_p50,
            {med('priority_fee', 0.9)} / {LAMPORTS} AS priority_fee_sol_p90,
            avg((jito_tip > 0)::INT) AS share_with_jito_tip, {med('jito_tip')} FILTER (WHERE jito_tip > 0) / {LAMPORTS} AS jito_tip_sol_p50,
            avg((tx_version = 'v1')::INT) AS share_v1_tx
            FROM trades""")
    if view(con, "creates", files(dirs, "pump/CreateEvent")):
        out["pump_creates"] = one(con, "SELECT count(*) AS creates, count(DISTINCT creator) AS creators FROM creates")
    if view(con, "completes", files(dirs, "pump/CompleteEvent")):
        out["pump_completes"] = one(con, "SELECT count(*) AS graduations FROM completes")
    if view(con, "amm_buys", files(dirs, "pump_amm/BuyEvent")) and view(con, "amm_sells", files(dirs, "pump_amm/SellEvent")):
        out["pumpswap"] = one(con, """SELECT (SELECT count(*) FROM amm_buys) AS buys, (SELECT count(*) FROM amm_sells) AS sells,
            (SELECT count(*) FROM (SELECT DISTINCT pool FROM amm_buys UNION SELECT DISTINCT pool FROM amm_sells)) AS pools""")
    failed = files(dirs, "failed_txs")
    if failed:
        lit = "[" + ",".join(f"'{p}'" for p in failed) + "]"
        out["failed_txs"] = one(con, f"""SELECT count(*) AS failed, sum(tx_fee) / {LAMPORTS} AS fees_paid_sol
            FROM read_parquet({lit})""")
        out["failed_top_errors"] = [{"error": e[:120], "count": c} for e, c in con.execute(f"""
            SELECT regexp_replace(error, '[0-9]+', 'N', 'g') AS e, count(*) FROM read_parquet({lit})
            GROUP BY 1 ORDER BY 2 DESC LIMIT 5""").fetchall()]

    text = json.dumps(out, indent=2, default=str)
    if a.json:
        with open(a.json, "w") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    sys.exit(main())
