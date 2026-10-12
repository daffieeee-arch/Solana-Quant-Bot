"""Shared read-only data access for the lab notebooks (not a notebook itself).

Every notebook that queries data goes through connect(): DuckDB in memory with memory_limit 1GB and
2 threads, and lab/analytics' analytics.duckdb attached READ_ONLY. Raw Parquet can be read with
read_parquet() on the same connection. Nothing here writes.
"""
import os

import duckdb

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
ANALYTICS = os.path.join(DATA, "analytics", "analytics.duckdb")
MEMORY_LIMIT = "1GB"
THREADS = 2


def connect(attach_analytics=True):
    """An in-memory DuckDB connection within the VPS limits; analytics.duckdb attached read-only as `a`."""
    con = duckdb.connect(":memory:", config={"memory_limit": MEMORY_LIMIT, "threads": THREADS})
    if attach_analytics and os.path.isfile(ANALYTICS):
        con.execute(f"ATTACH '{ANALYTICS}' AS a (READ_ONLY)")
    return con
