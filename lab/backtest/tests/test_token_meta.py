"""token_meta from one dev chunk: one row per mint, names as the store's (pump CreateEvent)."""

import os

import duckdb
import pytest

from backtest import data, token_meta

STORE = "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"


def test_one_row_per_mint_with_the_create_events_name(tmp_path):
    chunk = [c for c in data.complete_chunks() if c[0] <= 450_700_000 < c[1]]
    if not chunk or not os.path.exists(STORE):
        pytest.skip("dev chunk or store not available")
    out = str(tmp_path / "token_meta.parquet")
    counts = token_meta.build(chunk, out=out, memory="500MB")
    assert counts.get("pump_create", 0) > 1000
    con = duckdb.connect()
    n, mints = con.execute(f"SELECT count(*), count(DISTINCT mint) FROM '{out}'").fetchone()
    assert n == mints == sum(counts.values())
    con.execute(f"ATTACH '{STORE}' AS s (READ_ONLY)")
    same, total = con.execute(f"""SELECT count(*) FILTER (WHERE t.name = m.name AND t.symbol = m.symbol), count(*)
                                  FROM '{out}' t JOIN s.mints m USING (mint)""").fetchone()
    assert total > 1000 and same == total
