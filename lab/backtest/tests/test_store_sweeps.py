"""store.build on a forward-protocol table the dev data does not have: PumpSwap fee sweeps (from
slot 452670480 on). A 2,000-slot dev window copied as its own chunk, plus one synthetic
SweepPoolFeeEvent for a pool created inside the window: the build runs and keeps the sweep."""

import glob
import os
import shutil

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest import data, store

LO, HI = 450700000, 450702000  # epoch 1043: 10 graduations, so pools created (and classified) inside
WSOL = "So11111111111111111111111111111111111111112"


def mini_chunk(src, out):
    con = data.connect(threads=1, memory="700MB")
    tables = sorted({os.path.relpath(os.path.dirname(f), src[2])
                     for f in glob.glob(os.path.join(src[2], "**", "*.parquet"), recursive=True)})
    for t in tables:
        s = data.parquet(t, [src])
        if con.execute(f"SELECT count(*) FROM {s} WHERE slot >= {LO} AND slot < {HI}").fetchone()[0]:
            os.makedirs(os.path.join(out, t), exist_ok=True)
            con.execute(f"COPY (SELECT * FROM {s} WHERE slot >= {LO} AND slot < {HI}) "
                        f"TO '{os.path.join(out, t, 'part-0.parquet')}' (FORMAT parquet)")
    pool = con.execute(f"""SELECT pool FROM read_parquet('{os.path.join(out, "pump_amm/CreatePoolEvent/part-0.parquet")}')
                           WHERE quote_mint = '{WSOL}' ORDER BY slot LIMIT 1""").fetchone()[0]
    con.close()
    shutil.copy(os.path.join(src[2], "_manifest.json"), out)  # the build reads the chunk's manifest
    return (LO, HI, out), pool


def test_a_build_with_fee_sweeps(tmp_path, monkeypatch):
    src = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]]
    if not src:
        pytest.skip("epoch-1043 chunk not available")
    chunk, pool = mini_chunk(src[0], str(tmp_path / "chunk"))
    os.makedirs(tmp_path / "chunk" / "pump_amm" / "SweepPoolFeeEvent")
    sweeps = pa.table({"slot": pa.array([HI - 10, HI - 5], pa.uint64()), "tx_index": pa.array([1, 2], pa.uint64()),
                       "outer_ix": pa.array([0, 0], pa.uint64()), "inner_ix": pa.array([1, 1], pa.uint64()),
                       "pool": [pool, "11111111111111111111111111111111"],  # the second pool is not a SOL market
                       "amount": pa.array([123_456, 7], pa.uint64()), "bucket": pa.array([0, 1], pa.uint64())})
    pq.write_table(sweeps, str(tmp_path / "chunk" / "pump_amm" / "SweepPoolFeeEvent" / "part-0.parquet"))
    monkeypatch.setattr(store.health, "wait_ok", lambda *a, **k: 0)
    out = str(tmp_path / "mini.duckdb")
    store.build("dev", out, store.load_defaults(), [chunk], log=lambda m: None)
    con = duckdb.connect(out, read_only=True)
    assert con.execute("SELECT pool, amount, bucket FROM pool_sweeps").fetchall() == [(pool, 123_456, 0)]
    assert con.execute("SELECT quote_class FROM pools WHERE pool = ?", [pool]).fetchone()[0] in ("sol", "reversed")
