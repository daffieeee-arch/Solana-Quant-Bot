"""Access to the week-1 event dataset (Parquet chunks written by lab-extractor)."""

import glob
import json
import os

import duckdb

DEFAULT_ROOT = "/home/chupa/Solana-project/data-old-faithful-one/lab/events/v1/chunks"
SOL_QUOTES = ("11111111111111111111111111111111", "So11111111111111111111111111111111111111112")

# Instruction discriminators (first 8 bytes of instruction data) from the official IDLs.
PUMP_IX = {
    "66063d1201daebea": "buy",
    "38fc74089edfcd5f": "buy_exact_sol_in",
    "b817ee6167c5d33d": "buy_v2",
    "07051dc4f5176550": "buy_v3",
    "c2ab1c46684d5b2f": "buy_exact_quote_in_v2",
    "33e685a4017f83ad": "sell",
    "5df6823ce7e940b2": "sell_v2",
    "1c92de7726c469d5": "sell_v3",
}


def root():
    return os.environ.get("LAB_CHUNKS", DEFAULT_ROOT)


def complete_chunks(chunks_root=None):
    """(start, end, dir) of chunks whose manifest says complete, sorted by slot."""
    out = []
    for m in glob.glob(os.path.join(chunks_root or root(), "*", "_manifest.json")):
        with open(m) as f:
            man = json.load(f)
        if man["status"] == "complete":
            out.append((man["slot_start"], man["slot_end_exclusive"], os.path.dirname(m)))
    return sorted(out)


def files(table, chunks=None):
    chunks = complete_chunks() if chunks is None else chunks
    return sorted(f for _, _, d in chunks for f in glob.glob(os.path.join(d, table, "*.parquet")))


def parquet(table, chunks=None, dedup=False):
    """SQL table expression over a table of the complete chunks.

    The continuity check found no duplicate event positions in complete chunks; pass
    dedup=True to enforce it anyway (costs a hash aggregation over the table)."""
    fs = files(table, chunks)
    if not fs:
        raise FileNotFoundError(f"no parquet files for {table}")
    lit = "[" + ",".join("'" + f.replace("'", "''") + "'" for f in fs) + "]"
    src = f"read_parquet({lit}, union_by_name = true)"
    if dedup:
        return f"(SELECT * FROM {src} QUALIFY row_number() OVER (PARTITION BY slot, tx_index, outer_ix, inner_ix) = 1)"
    return src


def connect(threads=None, memory=None, temp=None):
    con = duckdb.connect()
    con.execute(f"SET threads TO {int(threads or os.environ.get('LAB_DUCKDB_THREADS', '4'))}")
    con.execute(f"SET memory_limit = '{memory or os.environ.get('LAB_DUCKDB_MEM', '6GB')}'")
    tmp = temp or os.environ.get("LAB_DUCKDB_TMP")
    if tmp:
        con.execute(f"SET temp_directory = '{tmp}'")
    con.execute("SET preserve_insertion_order = false")
    return con
