"""The snapshot export (paper/snapshot_export.py) holds what LiveFeed loads from the same store: the same tokens,
pools and pool classes (`_load_snapshot`), and per pool the same farmer-seed trades (`_seed_pool`'s query) over
the exported window. The Rust live route reads these files instead of the store.

Since snapshot_version 2 (M1 C1a-2) it also holds the store's meta the replay checks (meta_bounds, ms_per_slot,
chunk_list, the farmer rule, each chunk's h24 as the store computed it) and, for a replay of [from, to), the farmer
history over [H0, from) of the keys that trade in it.

M1 C2-5: with a history range it also holds curve_hold.parquet, the net curve tokens per (mint, trader) before `from`
of the mints that trade on the curve in [from, to) (research/m1-c2-positionsim-spec-2026-10-19.md §4.1): recounted here
on dev2 near the store's start (few earlier rows) and on a tiny store built here (a leg without is_buy, a NULL trader, a
zero net, a leg at from - 1, values beyond 2^53, the BIGINT edges, and the refusals). paper/tests/curve_hold_proof.py
proves it equal to the engine's own seed."""

import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest import streams
from paper import snapshot_export
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


# ----------------------------------------------------------------------------- curve_hold.parquet (M1 C2-5)

CURVE_SCHEMA = pa.schema([("mint", pa.string()), ("trader", pa.string()), ("net_t", pa.int64())])


def _hold_key(row):
    """The export's order: (mint, trader), the NULL trader last (ORDER BY mint, trader NULLS LAST)."""
    return row[0], row[1] is None, row[1] or ""


def _recount(rows):
    """Holdings.seed's sums written out again (replay.py:225-230), in exact Python ints: per (mint, trader) the tokens
    bought minus sold over the legs with an is_buy; then only the non-zero ones, in the export's order."""
    net = {}
    for mint, trader, is_buy, t in rows:
        if is_buy is not None:
            net[(mint, trader)] = net.get((mint, trader), 0) + (t if is_buy else -t)
    return sorted(((m, w, n) for (m, w), n in net.items() if n != 0), key=_hold_key)


def _hold_rows(path):
    t = pq.read_table(path)
    assert t.schema.equals(CURVE_SCHEMA), t.schema  # net_t BIGINT: a HUGEINT without the CAST would be a DOUBLE
    return [(r["mint"], r["trader"], r["net_t"]) for r in t.to_pylist()]


@pytest.fixture(scope="module")
def hold(tmp_path_factory):
    """dev2 near its start, [446258000, 446260000): mints with few earlier curve rows (364 mints in the range, 9,431
    earlier rows of 101 of them), small enough for an exact recount from the raw rows here."""
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    con = streams.open_store(STORE, threads=1, memory="512MB")
    try:
        store_meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    finally:
        con.close()
    a, b = store_meta["slot_start"] + 2_000, store_meta["slot_start"] + 4_000
    out = tmp_path_factory.mktemp("hold")
    return out, export(STORE, str(out), history=(a, b), seed=False), store_meta, (a, b)


def test_curve_hold_is_the_recounted_net_per_mint_and_trader_before_from(hold):
    out, meta, store_meta, (a, b) = hold
    con = streams.open_store(STORE, threads=2, memory="1GB")
    try:
        # every curve row before `from` of the mints that trade on the curve in [from, to), as stored (t a HUGEINT: an int)
        mints = {r[0] for r in con.execute(f"SELECT mint FROM curve WHERE slot >= {a} AND slot < {b}").fetchall()}
        con.execute("CREATE TEMP TABLE m (mint VARCHAR)")
        con.executemany("INSERT INTO m VALUES (?)", [(m,) for m in sorted(mints)])
        raw = con.execute(f"SELECT c.mint, c.trader, c.is_buy, c.t FROM curve c JOIN m USING (mint) WHERE c.slot < {a}").fetchall()
    finally:
        con.close()
    want = _recount(raw)
    got = _hold_rows(out / "curve_hold.parquet")
    assert got == want and len(got) > 0  # the same rows, in the same order
    assert all(n != 0 for _, _, n in got) and {m for m, _, _ in got} <= mints
    skipped = sum(1 for r in raw if r[2] is None)
    assert meta["curve_hold"] == {"lo": store_meta["slot_start"], "hi": a, "to": b, "rows": len(got), "skipped_null_is_buy": skipped}
    assert meta["counts"]["curve_hold"] == len(got)
    if store_meta["slot_start"] == 446256000:  # dev2 (measured 2026-10-19)
        assert (len(mints), len(raw), len({r[0] for r in raw}), skipped) == (364, 9431, 101, 0)
    assert json.load(open(out / "meta.json")) == meta


def tiny_store(path, curve, slots=(1000, 3000)):
    """A store with only what the export and ReplayFeed read: meta, blocks, two pool trades, mints, one pool, and the
    curve rows (mint, slot, trader, is_buy, t) with the store's types (t HUGEINT); the other curve columns are constant.
    Two chunks, [lo, lo + 1000) at 400 ms a slot (h24 216,000) and [lo + 1000, hi) at 100 s a slot (h24 864)."""
    lo, hi = slots
    con = duckdb.connect(str(path))
    try:
        meta = {"slot_start": lo, "slot_end_exclusive": hi, "ms_per_slot": {}, "git_commit": "tiny",
                "farmer": {"ratio": 0.05, "min_trades": 2, "window_ms": 86_400_000},
                "chunk_list": [{"slots": [lo, lo + 1000], "extractor_sha256": "S"}, {"slots": [lo + 1000, hi], "extractor_sha256": "S"}]}
        con.execute("CREATE TABLE meta AS SELECT ? AS json", [json.dumps(meta)])
        con.execute("CREATE TABLE blocks (slot UBIGINT, block_time BIGINT)")
        con.executemany("INSERT INTO blocks VALUES (?, ?)", [(lo, 1_000_000), (lo + 100, 1_000_040), (lo + 900, 1_000_360),
                                                            (lo + 1000, 2_000_000), (lo + 1400, 2_040_000)])
        con.execute("""CREATE TABLE pool (pool VARCHAR, trader VARCHAR, kind VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix BIGINT,
                       inner_ix BIGINT, token_buy BOOLEAN, token_amount BIGINT)""")
        con.executemany("INSERT INTO pool VALUES ('P1', 'W', 'buy', ?, 0, 0, 2, true, 5)", [(lo + 500,), (lo + 1100,)])
        con.execute("""CREATE TABLE mints (mint VARCHAR, create_slot UBIGINT, creator VARCHAR, mayhem BOOLEAN, quote_mint VARCHAR,
                       complete_slot UBIGINT, pool VARCHAR)""")
        con.executemany("INSERT INTO mints VALUES (?, ?, 'C', false, 'So11111111111111111111111111111111111111112', NULL, NULL)",
                        sorted({(r[0], lo) for r in curve}))
        con.execute("""CREATE TABLE pools (pool VARCHAR, mint VARCHAR, orientation VARCHAR, quote_class VARCHAR, mayhem BOOLEAN,
                       coin_creator VARCHAR, create_slot UBIGINT)""")
        con.execute(f"INSERT INTO pools VALUES ('P1', 'MP', 'normal', 'sol', false, NULL, {lo})")
        con.execute("""CREATE TABLE curve (mint VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, variant VARCHAR,
                       is_buy BOOLEAN, t HUGEINT, q HUGEINT, fee HUGEINT, creator_fee HUGEINT, fee_bps INTEGER, creator_bps INTEGER,
                       vt HUGEINT, vq HUGEINT, rt HUGEINT, rq HUGEINT, trader VARCHAR, arg_budget HUGEINT, arg_max_cost HUGEINT,
                       arg_min_tokens HUGEINT, arg_min_quote_out HUGEINT, outer_program VARCHAR)""")
        con.executemany("""INSERT INTO curve VALUES (?, ?, ?, 0, 1, 'buy', ?, ?::HUGEINT, 1000, 10, 3, 95, 30, 1073000000000000,
                           30000000000, 793100000000000, 1000000000, ?, NULL, NULL, NULL, NULL, 'P')""",
                        [(m, s, i, buy, None if t is None else str(t), w) for i, (m, s, w, buy, t) in enumerate(curve)])
    finally:
        con.close()
    return str(path)


# (mint, slot, trader, is_buy, t) of a store over [1000, 3000); the replay [2000, 2500)
TINY = [
    ("A", 1100, "W1", True, 10), ("A", 1200, "W1", False, 4),  # W1: 6 here
    ("A", 1150, "W2", True, 5), ("A", 1250, "W2", False, 5),  # W2: 0, absent
    ("A", 1300, None, True, 7),  # the NULL trader: a key of its own
    ("A", 1350, "W3", None, 100),  # no is_buy: skipped and counted (a plain CASE would book -100)
    ("A", 1400, "W4", True, 2**53 + 1),  # a DOUBLE would round it to 2^53
    ("A", 1999, "W1", True, 4),  # at from - 1, the seed's last slot (curve_history(mint, from - 1)): W1 6 + 4 = 10
    ("A", 2100, "W1", True, 1000),  # in the replay: not before from
    ("B", 2200, "W1", True, 50),  # trades in the replay, nothing before it
    ("C", 1100, "W1", True, 9), ("C", 1200, "W5", None, 3),  # nothing in the replay: not exported, its NULL is_buy not counted
    ("D", 1500, "W1", True, 11), ("D", 2500, "W1", True, 1),  # its later row is at `to`, outside [from, to)
    ("E", 1600, "W1", False, 3), ("E", 2000, "W2", True, 1),  # a negative net; a row at `from` is in the replay
    ("G", 1100, "W1", False, 2**62), ("G", 1200, "W1", False, 2**62), ("G", 2300, "W1", True, 1),  # -2^63, BIGINT's lowest
]
TINY_HOLD = [("A", "W1", 10), ("A", "W4", 2**53 + 1), ("A", None, 7), ("E", "W1", -3), ("G", "W1", -(2**63))]


def test_curve_hold_on_a_tiny_store_skips_a_leg_without_is_buy_and_keeps_non_zero_nets_exactly(tmp_path):
    store = tiny_store(tmp_path / "tiny.duckdb", TINY)
    out = tmp_path / "snap"
    meta = export(store, str(out), history=(2000, 2500), seed=False)
    traded = {m for m, s, *_ in TINY if 2000 <= s < 2500}
    assert traded == {"A", "B", "E", "G"}
    assert _hold_rows(out / "curve_hold.parquet") == TINY_HOLD == _recount([(m, w, buy, t) for m, s, w, buy, t in TINY
                                                                            if s < 2000 and m in traded])
    assert meta["curve_hold"] == {"lo": 1000, "hi": 2000, "to": 2500, "rows": 5, "skipped_null_is_buy": 1}
    assert meta["farm_hist"]["hi"] == 2000 and meta["counts"]["curve_hold"] == 5
    assert json.load(open(out / "meta.json")) == meta
    # the replay from the store's start has nothing before it: an empty file (R2's case)
    meta = export(store, str(out), history=(1000, 2500), seed=False)
    assert _hold_rows(out / "curve_hold.parquet") == [] and meta["curve_hold"]["rows"] == 0
    # an export without a history range leaves no curve_hold.parquet of an earlier one behind
    meta = export(store, str(out))
    assert "curve_hold" not in meta and not (out / "curve_hold.parquet").exists()


def test_curve_hold_refusals_leave_the_export_as_it_was_and_an_overflow_leaves_no_meta(tmp_path, monkeypatch):
    out = tmp_path / "snap"
    good = tiny_store(tmp_path / "good.duckdb", TINY)
    meta = export(good, str(out), history=(2000, 2500), seed=False)

    def unchanged():
        return {f.name: f.stat().st_mtime_ns for f in out.iterdir()}

    before = unchanged()
    # a curve row without t before from: the engine cannot read the mint's history (streams._curve_legs), refused first
    no_t = tiny_store(tmp_path / "no_t.duckdb", TINY + [("A", 1700, "W6", True, None)])
    with pytest.raises(ValueError, match="have no t"):
        export(no_t, str(out), history=(2000, 2500), seed=False)
    assert unchanged() == before and json.load(open(out / "meta.json")) == meta
    # without t in a mint that does not trade in the replay ([2000, 2100): E only): not summed, no refusal
    assert export(no_t, str(tmp_path / "other"), history=(2000, 2100), seed=False)["curve_hold"]["rows"] == 1
    # the curve history reads from the store's start: [1000, 2500) touches a period that H0 = 1136 does not
    for name, period in (("HOLDOUT", (1050, 1100)), ("CLEAN", (1000, 1001))):
        monkeypatch.setattr(snapshot_export, name, period)
        with pytest.raises(ValueError, match=r"curve history \[1000, 2500\) touches"):
            export(good, str(out), history=(2000, 2500), seed=False)
        monkeypatch.undo()
        assert unchanged() == before and json.load(open(out / "meta.json")) == meta
    # a net beyond BIGINT (2^63): the CAST fails instead of rounding; the export is broken and leaves no meta.json
    over = tiny_store(tmp_path / "over.duckdb", [("F", 1100, "W1", True, 2**62), ("F", 1200, "W1", True, 2**62), ("F", 2100, "W1", True, 1)])
    with pytest.raises(duckdb.Error, match="(?i)out of range|overflow"):
        export(over, str(out), history=(2000, 2500), seed=False)
    assert not (out / "meta.json").exists()
