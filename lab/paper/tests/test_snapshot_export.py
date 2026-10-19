"""The snapshot export (paper/snapshot_export.py) holds what LiveFeed loads from the same store: the same tokens,
pools and pool classes (`_load_snapshot`), and per pool the same farmer-seed trades (`_seed_pool`'s query) over
the exported window. The Rust live route reads these files instead of the store.

Since snapshot_version 2 (M1 C1a-2) it also holds the store's meta the replay checks (meta_bounds, ms_per_slot,
chunk_list, the farmer rule, each chunk's h24 as the store computed it) and, for a replay of [from, to), the farmer
history over [H0, from) of the keys that trade in it."""

import json
import os

import duckdb
import pyarrow.parquet as pq
import pytest

from backtest import streams
from paper.feed import PoolInfo, TokenInfo
from paper.live import SOL_MARKETS, LiveFeed
from paper.snapshot_export import CLEAN, HOLDOUT, MIN_H24, SEED, _overlaps, export, history_lo

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


def test_h0_is_the_earliest_farmer_window_start_of_the_replay():
    # [lo, hi, h24] per chunk; a trade at s >= from in chunk C has the window [s - h24(C), s - 1]
    chunks = [[1000, 2000, 300], [2000, 3000, 1500], [3000, 4000, 200]]
    assert history_lo(0, chunks, 1500, 1800) == 1200  # from - h24 of its chunk, not the chunk's lo - h24
    assert history_lo(0, chunks, 1500, 2500) == 500  # the next chunk's longer day reaches further back
    assert history_lo(0, chunks, 2500, 3500) == 1000
    assert history_lo(1100, chunks, 1500, 2500) == 1100  # never before the store's start
    with pytest.raises(ValueError):
        history_lo(0, chunks, 4000, 4100)
    assert _overlaps(HOLDOUT[0] - 1, HOLDOUT[0] + 1, HOLDOUT) and not _overlaps(HOLDOUT[1], HOLDOUT[1] + 9, HOLDOUT)
    assert CLEAN == (1055 * 432_000, 1064 * 432_000)


def _store_h24(con, lo, hi):
    # store.py:486-488, written out again here (the export has its own copy): a DOUBLE ms, `or 267.3`, round half to even
    ms = con.execute(f"""SELECT (max(block_time) - min(block_time)) * 1000.0 / greatest(max(slot) - min(slot), 1)
                         FROM blocks WHERE slot >= {lo} AND slot < {hi}""").fetchone()[0] or 267.3
    return round(86_400_000 / ms)


@pytest.fixture(scope="module")
def hist(tmp_path_factory):
    """A replay of 2,000 slots from inside the store's middle chunk (a start inside a chunk), without the seed day."""
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    con = streams.open_store(STORE, threads=1, memory="512MB")
    try:
        store_meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    finally:
        con.close()
    lo, _ = store_meta["chunk_list"][len(store_meta["chunk_list"]) // 2]["slots"]
    a, b = lo + 1_000, lo + 3_000
    out = tmp_path_factory.mktemp("hist")
    return out, export(STORE, str(out), history=(a, b), seed=False), store_meta, (a, b)


def test_the_meta_is_the_stores_and_each_chunks_h24_its_own(hist):
    out, meta, store_meta, _ = hist
    assert meta["snapshot_version"] == 2 and "farm_seed" not in meta and not (out / "farm_seed.parquet").exists()
    for k in ("chunk_list", "ms_per_slot", "farmer"):
        assert meta[k] == store_meta[k], k
    con = streams.open_store(STORE, threads=1, memory="512MB")
    try:
        assert meta["meta_bounds"] == list(con.execute("SELECT min(slot), max(slot) + 1 FROM blocks").fetchone())  # feed.py:100
        assert meta["chunk_h24"] == [[c["slots"][0], c["slots"][1], _store_h24(con, *c["slots"])] for c in store_meta["chunk_list"]]
    finally:
        con.close()
    if store_meta["slot_start"] == 446256000:  # dev2: the dev chunk's h24 (spec §3.4)
        assert [450144000, 450360000, 323774] in meta["chunk_h24"]
    assert json.load(open(out / "meta.json")) == meta


def test_the_farmer_history_is_the_stores_over_h0_to_from_for_the_keys_that_trade(hist):
    out, meta, store_meta, (a, b) = hist
    h = meta["farm_hist"]
    over = [(lo, h24) for lo, hi, h24 in meta["chunk_h24"] if lo < b and hi > a]
    assert h == {"lo": max(store_meta["slot_start"], min(max(a, lo) - h24 for lo, h24 in over)), "hi": a, "to": b, "rows": h["rows"]}
    assert h["rows"] == meta["counts"]["farm_hist"] > 0
    t = pq.read_table(out / "farm_hist.parquet", columns=["slot", "tx_index", "outer_ix", "inner_ix"]).to_pydict()
    keys = list(zip(t["slot"], t["tx_index"], t["outer_ix"], t["inner_ix"]))
    assert len(keys) == h["rows"] and all(x < y for x, y in zip(keys, keys[1:]))  # strict chain order
    assert h["lo"] <= keys[0][0] and keys[-1][0] < a
    con = streams.open_store(STORE, threads=2, memory="1GB")
    try:
        # the same rows by another query: every buy and sell of [H0, from) whose (pool, trader) trades in [from, to),
        # compared by count and a sum of row hashes (the store's values, as stored)
        cols = "pool, trader, slot, tx_index, outer_ix, inner_ix, token_buy, token_amount"
        want = con.execute(f"""SELECT count(*), sum(hash({cols})::HUGEINT) FROM pool h
                               WHERE kind IN ('buy', 'sell') AND slot >= {h['lo']} AND slot < {a}
                               AND EXISTS (SELECT 1 FROM pool k WHERE k.kind IN ('buy', 'sell') AND k.slot >= {a} AND k.slot < {b}
                                           AND k.pool = h.pool AND k.trader IS NOT DISTINCT FROM h.trader)""").fetchone()
        got = con.execute(f"SELECT count(*), sum(hash({cols})::HUGEINT) FROM read_parquet('{out / 'farm_hist.parquet'}')").fetchone()
        assert got == want and want[0] == h["rows"]
    finally:
        con.close()


def test_a_history_range_outside_the_store_is_refused_and_leaves_an_export_as_it_was(hist, tmp_path):
    out, meta, store_meta, _ = hist
    before = {f.name: f.stat().st_mtime_ns for f in out.iterdir()}
    with pytest.raises(ValueError, match="store period"):
        export(STORE, str(out), history=(store_meta["slot_end_exclusive"] - 10, store_meta["slot_end_exclusive"] + 10), seed=False)
    assert {f.name: f.stat().st_mtime_ns for f in out.iterdir()} == before and json.load(open(out / "meta.json")) == meta
