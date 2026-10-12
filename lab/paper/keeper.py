"""Keeps what the retention rule in AGENTS.md promises before live-retention.sh deletes a recorder segment:
the trades of tokens with a signal, the arrival times of their transactions (the trigger arrivals
among them) and 1-minute candles. The rule itself does not change (coordinator decision 2026-10-12).

  python -m paper.keeper --journal <journal dir> --out <kept dir> <segment dir> [<segment dir> ...]

Per segment, under <out>/<seq>/ (the segment directory's name):
  signal_trades/<table>.parquet   every pump/PumpSwap event row (recorded and backfill/) whose pool or
                                  mint is a key in the journal's signals (any family, any status)
  arrivals.parquet                arrivals/ rows of those rows' transactions
  candles.parquet                 every pool and curve of the segment per UTC minute: trades, base and
                                  quote volume, open/high/low/close of the trade price (quote / base,
                                  raw units: pool orientation as recorded; curve: SOL per token)
  DONE.json                       written last: counts. A segment without it is not kept yet.

The journal is read read-only (the engine keeps writing it). A missing journal means no signals yet:
the candles are kept anyway.
"""

import argparse
import glob
import json
import os
import shutil
import sqlite3
import sys

import duckdb

CURVE_PRICE = "COALESCE(quote_amount, sol_amount)::DOUBLE / NULLIF(token_amount, 0)"
# Chain order of an event as one number (inner and outer indexes stay below 1000).
ORDER = "(slot::HUGEINT * 1000000000000 + tx_index::HUGEINT * 1000000 + outer_ix * 1000 + inner_ix)"
POOL_TRADES = {  # table: (base amount, quote amount)
    "pump_amm/BuyEvent": ("base_amount_out", "quote_amount_in"),
    "pump_amm/SellEvent": ("base_amount_in", "quote_amount_out"),
}


def signal_keys(journal):
    path = os.path.join(journal, "journal.sqlite")
    if not os.path.exists(path):
        return []
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in db.execute("SELECT DISTINCT key FROM signals")]
    finally:
        db.close()


def src(d, table):
    files = sorted(glob.glob(os.path.join(d, table, "*.parquet")))
    return f"read_parquet({files!r}, union_by_name = true)" if files else None


def tables(d):
    return sorted({os.path.relpath(os.path.dirname(f), d) for f in glob.glob(os.path.join(d, "pump*", "*", "*.parquet"))})


def columns(con, sql):
    return {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {sql}").fetchall()}


def keep_segment(con, seg, out, keys):
    name = os.path.basename(seg.rstrip("/"))
    dest = os.path.join(out, name)
    tmp = dest + ".tmp"
    if os.path.exists(os.path.join(dest, "DONE.json")):
        return None
    for d in (tmp, dest):  # left-overs of an interrupted run
        if os.path.exists(d):
            shutil.rmtree(d)
    os.makedirs(os.path.join(tmp, "signal_trades"))
    counts = {"signal_trades": {}, "arrivals": 0, "candles": 0, "signal_keys": len(keys)}
    con.execute("CREATE OR REPLACE TEMP TABLE keys (k VARCHAR)")
    if keys:
        con.executemany("INSERT INTO keys VALUES (?)", [(k,) for k in keys])
    con.execute("CREATE OR REPLACE TEMP TABLE kept_tx (slot UBIGINT, tx_index UBIGINT)")
    for base in (seg, os.path.join(seg, "backfill")):
        for t in tables(base):
            s = src(base, t)
            cols = columns(con, s)
            match = [f"{c} IN (SELECT k FROM keys)" for c in ("pool", "mint") if c in cols]
            if not match:
                continue
            where = " OR ".join(match)
            n = con.execute(f"SELECT count(*) FROM {s} WHERE {where}").fetchone()[0]
            if not n:
                continue
            label = ("backfill/" if base != seg else "") + t
            path = os.path.join(tmp, "signal_trades", label.replace("/", "__") + ".parquet")
            con.execute(f"COPY (SELECT * FROM {s} WHERE {where} ORDER BY slot, tx_index) TO '{path}' (FORMAT parquet, COMPRESSION zstd)")
            con.execute(f"INSERT INTO kept_tx SELECT DISTINCT slot::UBIGINT, tx_index::UBIGINT FROM {s} WHERE {where}")
            counts["signal_trades"][label] = n
    arr = src(seg, "arrivals")
    if arr:
        sel = f"SELECT a.* FROM {arr} a SEMI JOIN (SELECT DISTINCT * FROM kept_tx) k ON a.slot = k.slot AND a.tx_index = k.tx_index"
        counts["arrivals"] = con.execute(f"SELECT count(*) FROM ({sel})").fetchone()[0]
        if counts["arrivals"]:
            con.execute(f"COPY ({sel} ORDER BY slot, tx_index) TO '{os.path.join(tmp, 'arrivals.parquet')}' (FORMAT parquet, COMPRESSION zstd)")
    parts = []
    blocks = src(seg, "blocks")
    minute = "(b.block_time // 60)" if blocks else "NULL::BIGINT"
    join = f"LEFT JOIN (SELECT slot, any_value(block_time) AS block_time FROM {blocks} GROUP BY slot) b USING (slot)" if blocks else ""
    trade = src(seg, "pump/TradeEvent")
    if trade:
        parts.append(f"""SELECT 'curve' AS venue, mint AS key, slot, tx_index, outer_ix, inner_ix, token_amount::DOUBLE AS base,
                                COALESCE(quote_amount, sol_amount)::DOUBLE AS quote, {CURVE_PRICE} AS price FROM {trade}""")
    for t, (b, q) in POOL_TRADES.items():
        s = src(seg, t)
        if s:
            parts.append(f"""SELECT 'pool' AS venue, pool AS key, slot, tx_index, outer_ix, inner_ix, {b}::DOUBLE AS base,
                                    {q}::DOUBLE AS quote, {q}::DOUBLE / NULLIF({b}, 0) AS price FROM {s}""")
    if parts:
        candles = f"""
          SELECT venue, key, {minute} AS minute, count(*) AS trades, sum(base) AS base_volume, sum(quote) AS quote_volume,
                 arg_min(price, {ORDER}) AS open, max(price) AS high, min(price) AS low, arg_max(price, {ORDER}) AS close
          FROM ({' UNION ALL '.join(parts)}) x {join}
          GROUP BY ALL ORDER BY venue, key, minute"""
        counts["candles"] = con.execute(f"SELECT count(*) FROM ({candles})").fetchone()[0]
        con.execute(f"COPY ({candles}) TO '{os.path.join(tmp, 'candles.parquet')}' (FORMAT parquet, COMPRESSION zstd)")
    with open(os.path.join(tmp, "DONE.json"), "w") as f:
        json.dump({"segment": name, **counts}, f)
    os.replace(tmp, dest)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("segments", nargs="+")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    keys = signal_keys(a.journal)
    con = duckdb.connect()
    con.execute("SET memory_limit = '1GB'; SET threads TO 2")
    failed = 0
    for seg in a.segments:
        try:
            c = keep_segment(con, seg, a.out, keys)
            if c is not None:
                print(json.dumps({"kept": seg, **c}), flush=True)
        except Exception as e:  # noqa: BLE001 - one bad segment must not stop the others; it stays undeleted
            failed += 1
            print(f"keeper: {seg} failed: {e.__class__.__name__}: {e}", file=sys.stderr, flush=True)
    con.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
