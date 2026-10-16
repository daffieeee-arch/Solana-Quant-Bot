"""The snapshot export (paper/snapshot_export.py) holds what LiveFeed loads from the same store: the same tokens,
pools and pool classes (`_load_snapshot`), and per pool the same farmer-seed trades (`_seed_pool`'s query) over
the exported window. The Rust live route reads these files instead of the store."""

import json
import os

import duckdb
import pytest

from backtest import streams
from paper.feed import PoolInfo, TokenInfo
from paper.live import SOL_MARKETS, LiveFeed
from paper.snapshot_export import MIN_H24, SEED, export

STORE = os.environ.get("LAB_STORE", "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb")


@pytest.fixture(scope="module")
def snap(tmp_path_factory):
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    out = tmp_path_factory.mktemp("snap")
    meta = export(STORE, str(out))
    root = tmp_path_factory.mktemp("rec")
    feed = LiveFeed(str(root), store=STORE, follow=False)
    return out, meta, feed


def _rows(path):
    con = duckdb.connect(config={"memory_limit": "1GB"})
    try:
        return con.execute(f"SELECT * FROM read_parquet('{path}')").fetchall()
    finally:
        con.close()


def test_the_tokens_and_pools_are_livefeeds(snap):
    out, meta, feed = snap
    mints = _rows(out / "mints.parquet")
    assert {r[0]: TokenInfo(*r) for r in mints} == feed.tokens and len(mints) == meta["counts"]["mints"] > 0
    pools = _rows(out / "pools.parquet")
    assert {r[0]: (r[3], r[2]) for r in pools} == feed.pool_class
    assert {r[0]: PoolInfo(*r) for r in pools if r[3] in SOL_MARKETS} == feed.pools
    assert len(pools) == meta["counts"]["pools"] > 0


def test_the_farmer_seed_is_the_stores_last_day_per_pool(snap):
    out, meta, _ = snap
    w = meta["farm_seed"]
    assert w["hi"] == meta["pool_slots"][1] and w["hi"] - w["lo"] == min(w["h24"], w["hi"] - meta["pool_slots"][0])
    assert w["h24"] >= MIN_H24
    path = out / "farm_seed.parquet"
    con = duckdb.connect(config={"memory_limit": "1GB", "threads": 2})
    n, lo, hi = con.execute(f"SELECT count(*), min(slot), max(slot) FROM read_parquet('{path}')").fetchone()
    assert n == meta["counts"]["farm_seed"] > 0 and w["lo"] <= lo and hi < w["hi"]
    pools = [r[0] for r in con.execute(f"SELECT DISTINCT pool FROM read_parquet('{path}') ORDER BY pool LIMIT 20").fetchall()]
    got = {p: con.execute(f"""SELECT trader, slot, token_buy, token_amount FROM read_parquet('{path}') WHERE pool = ?
                               ORDER BY slot, tx_index, outer_ix, inner_ix""", [p]).fetchall() for p in pools}
    con.close()
    store = streams.open_store(STORE, threads=1, memory="512MB")
    try:
        for p in pools:
            # _seed_pool's own query, over the exported window
            assert got[p] == store.execute("""SELECT trader, slot, token_buy, token_amount FROM pool WHERE pool = ?
                                              AND kind IN ('buy', 'sell') AND slot >= ? AND slot < ? ORDER BY slot, tx_index,
                                              outer_ix, inner_ix""", [p, w["lo"], w["hi"]]).fetchall()
        assert n == store.execute(f"SELECT count(*) FROM ({SEED.format(lo=w['lo'], hi=w['hi'])})").fetchone()[0]
    finally:
        store.close()
    assert json.load(open(out / "meta.json")) == meta
