"""LiveFeed over recorder segments gives exactly the replay's events.

The fixture cuts 4,000 slots of an OF1 chunk into four segments in the recorder's layout (the
recorder writes the extractor's own rows, so OF1 rows are what it would have written), with the
store's tokens and pools as the snapshot and the store's farmer history of the day before. The
events must equal ReplayFeed's over the same slots: venue, key, position and the whole leg.
"""

import glob
import json
import os

import pyarrow as pa
import pytest

from backtest import data, streams
from backtest.store import DAY_MS
from paper.feed import Event, ReplayFeed
from paper.live import FarmerFlags, LiveFeed, segment_dirs

STORE = "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"
LO, HI, SEG = 450700000, 450702000, 500


def make_segments(chunk, root, lo=LO, hi=HI, seg=SEG, usage=True):
    """Copy every table of the chunk for [lo, hi) into segments of `seg` slots under root."""
    con = data.connect(threads=1, memory="700MB")
    tables = sorted({os.path.relpath(os.path.dirname(f), chunk[2]) for f in glob.glob(os.path.join(chunk[2], "**", "*.parquet"),
                                                                                       recursive=True)})
    for seq, a in enumerate(range(lo, hi, seg)):
        b = min(a + seg, hi)
        d = os.path.join(root, "segments", f"{seq:09d}")
        for t in tables:
            src = data.parquet(t, [chunk], missing_ok=True)
            n = con.execute(f"SELECT count(*) FROM {src} WHERE slot >= {a} AND slot < {b}").fetchone()[0]
            if n:
                os.makedirs(os.path.join(d, t), exist_ok=True)
                con.execute(f"COPY (SELECT * FROM {src} WHERE slot >= {a} AND slot < {b}) "
                            f"TO '{os.path.join(d, t, 'part-0.parquet')}' (FORMAT parquet)")
        meta = {"seq": seq, "first_slot": a, "last_slot": b - 1, "complete": True,
                "usage": [{"minute": 29_000_000 + seq, "stream": "watchlist_tx", "bytes": 1000 * (seq + 1), "messages": 10}]
                if usage else []}
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "_segment.json"), "w") as f:
            json.dump(meta, f)
    con.close()


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    chunk = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]]
    if not chunk or not os.path.exists(STORE):
        pytest.skip("dev store or its chunk not available")
    root = str(tmp_path_factory.mktemp("rec"))
    make_segments(chunk[0], root)
    con = streams.open_store(STORE, threads=2, memory="1GB")
    a, b = chunk[0][0], chunk[0][1]
    # The store's farmer window of this chunk (store.build: from the chunk's block times).
    ms = con.execute(f"""SELECT (max(block_time) - min(block_time)) * 1000.0 / greatest(max(slot) - min(slot), 1)
                         FROM blocks WHERE slot >= {a} AND slot < {b}""").fetchone()[0]
    h24 = round(DAY_MS / ms)
    # The farmer flag of a trade needs only its own wallet's history in its pool: seed those pairs.
    con.execute(f"""CREATE TEMP TABLE want_pairs AS SELECT DISTINCT pool, trader FROM pool
                    WHERE slot >= {LO} AND slot < {HI} AND kind IN ('buy', 'sell')""")
    hist = con.execute(f"""SELECT pool, trader, slot, token_buy, token_amount FROM pool SEMI JOIN want_pairs USING (pool, trader)
                           WHERE kind IN ('buy', 'sell') AND slot >= {LO - h24} AND slot < {LO}
                           ORDER BY slot, tx_index, outer_ix, inner_ix""").fetchall()
    replay = [key(e) for e in ReplayFeed(STORE, LO, HI, con=con).events()]
    con.close()
    return root, h24, hist, replay


def live_events(root, h24, hist, **kw):
    feed = LiveFeed(root, store=STORE, follow=False, h24=h24, memory="500MB", **kw)
    feed.farmers.seed(hist)
    return feed, [key(e) for e in feed.events()]


def key(e):
    return (e.venue, e.key, e.order, e.block_time, e.leg)


def test_live_events_equal_the_replay(setup):
    root, h24, hist, replay = setup
    feed, live = live_events(root, h24, hist)
    assert len(live) == len(replay) > 1000
    diffs = [(a, b) for a, b in zip(live, replay) if a != b]
    assert not diffs, diffs[:3]
    assert feed.stats == {"segments": 4, "late_rows": 0, "unknown_pools": 0}
    assert sum(1 for k in live if k[0] == "pool" and k[4].farmer) > 0  # the farmer flag was exercised


def test_segment_boundaries_and_order_do_not_matter(setup, tmp_path):
    """Segments of another size give the same events (a slot is never released early)."""
    root, h24, hist, replay = setup
    chunk = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]][0]
    make_segments(chunk, str(tmp_path), seg=173, usage=False)
    _, live = live_events(str(tmp_path), h24, hist)
    assert live == replay


def test_usage_rows_reach_the_engine(setup):
    root, h24, hist, _ = setup
    feed, _ = live_events(root, h24, hist)
    usage = feed.drain_usage()
    assert [u["bytes"] for u in usage] == [1000, 2000, 3000, 4000] and feed.drain_usage() == []


def test_new_pools_are_classified_like_the_store(setup):
    """A pool created inside the segments is classified from its CreatePoolEvent as the store does."""
    root, h24, hist, _ = setup
    feed = LiveFeed(root, store=STORE, follow=False, h24=h24, memory="500MB")
    created = []
    for _, seg, meta in segment_dirs(root):
        f = glob.glob(os.path.join(seg, "pump_amm", "CreatePoolEvent", "*.parquet"))
        if f:
            created += [r[0] for r in feed.con.execute(f"SELECT pool FROM read_parquet('{f[0]}')").fetchall()]
    if not created:
        pytest.skip("no pool created in the fixture slots")
    snapshot = {p: feed.pools.get(p) for p in created}
    for p in created:  # forget them, as if they were created after the snapshot
        feed.pools.pop(p, None)
        feed.pool_class.pop(p, None)
    feed.con.execute("DELETE FROM live_pools WHERE pool IN (SELECT unnest(?))", [created])
    list(feed.events())
    for p in created:
        assert feed.pools.get(p) == snapshot[p], p


def test_pools_from_the_recorders_pool_account_streams(tmp_path):
    """A pool the recorder only saw on its pool account stream (pool_creations) is known to
    LiveFeed with the CreatePoolEvent's classification: reversed when the base is WSOL."""
    import pyarrow.parquet as pq

    from paper.live import WSOL

    d = tmp_path / "segments" / "000000000"
    (d / "pool_creations").mkdir(parents=True)
    rows = {"slot": [101, 105], "pool": ["P1", "P1"], "base_mint": [WSOL, WSOL], "quote_mint": ["M1", "M1"],
            "coin_creator": ["C1", "C1"], "is_mayhem_mode": [False, False]}
    pq.write_table(pa.table(rows), str(d / "pool_creations" / "part-00000.parquet"))
    (d / "_segment.json").write_text(json.dumps({"seq": 0, "first_slot": 100, "last_slot": 110, "complete": True}))
    feed = LiveFeed(str(tmp_path), follow=False, h24=1000, memory="200MB")
    assert list(feed.events()) == []
    p = feed.pools["P1"]
    assert (p.mint, p.orientation, p.quote_class, p.create_slot) == ("M1", "reversed", "reversed", 101)


def test_farmer_flags_window():
    f = FarmerFlags()
    f.add("P", "W", 100, True, 10)
    f.add("P", "W", 101, False, 10)
    assert f.flag("P", "W", 102, 1000)  # two trades, net 0
    assert not f.flag("P", "W", 101, 1000)  # same-slot trades do not count: one earlier trade
    assert not f.flag("P", "W", 1101, 1000)  # slot 100 left the window: one trade
    assert not f.flag("P", "X", 102, 1000)


def test_live_feed_never_returns_unfinished_segments(tmp_path):
    d = tmp_path / "segments" / "000000000"
    d.mkdir(parents=True)
    (d / "_segment.json").write_text(json.dumps({"seq": 0, "first_slot": 1, "last_slot": 2, "complete": False}))
    assert segment_dirs(str(tmp_path)) == []


def test_event_type_is_shared():
    assert Event.__module__ == "paper.feed"


def test_engine_on_the_live_feed(setup, tmp_path):
    """The engine journals the recorder's usage per minute and writes the open-position pools for
    the recorder; the live clock measures the epoch's slot time from the block times."""
    from paper.engine import Engine
    from paper.journal import Journal

    root, h24, hist, _ = setup
    feed = LiveFeed(root, store=STORE, follow=False, h24=h24, memory="500MB")
    feed.farmers.seed(hist)
    j = Journal(str(tmp_path / "journal"))
    Engine(feed, j, None, ignore_risk=True, n2_k=0, clock=feed.clock, backup=False).run()
    usage = j.rows("usage")
    assert sorted(u["bytes"] for u in usage) == [1000, 2000, 3000, 4000]
    with open(os.path.join(root, "open-pools.json")) as f:
        assert isinstance(json.load(f)["pools"], list)
    assert 250 < feed.clock.per_epoch[LO // 432_000] < 300  # epoch 1043: ~268 ms per slot


def test_reconcile_finds_nothing_then_every_kind_of_difference(tmp_path):
    """Recorder segments cut from OF1 reconcile to zero; a dropped row is a gap, an extra row is
    extra, and a changed reserve is a mismatch on that column. Needs only the chunk (light)."""
    import pyarrow.parquet as pq

    from paper import reconcile

    chunk = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]]
    if not chunk:
        pytest.skip("the fixture's OF1 chunk is not available")
    chunk = chunk[0]
    root = str(tmp_path / "rec")
    make_segments(chunk, root, usage=False)
    first = os.path.join(root, "segments", "000000000")
    buys = pq.read_table(glob.glob(os.path.join(first, "pump_amm", "BuyEvent", "*.parquet"))[0])
    pools = sorted(set(buys.column("pool").to_pylist()))[:20]
    for _, seg, _ in segment_dirs(root):
        with open(os.path.join(seg, "watchlist.json"), "w") as f:
            json.dump({"pools": [{"pool": p} for p in pools], "wallets": [], "new_pools": True}, f)
    clean = reconcile.run(root, os.path.dirname(chunk[2]))
    assert clean["ok"] and clean["totals"]["segments_checked"] == 4, clean["totals"]
    assert sum(t["live"] for s in clean["segments"] for t in s["tables"].values()) > 0
    # The fixture's 10 graduations are compared too (the recorder's tx_new_pools stream).
    grads = sum(s["tables"].get("pump/CompletePumpAmmMigrationEvent", {}).get("live", 0) for s in clean["segments"])
    assert grads == 10, grads
    # Damage the first segment's buys of the watched pools: drop one, change a reserve of another,
    # and add a buy of a watched pool that OF1 does not have.
    rows = buys.to_pylist()
    idx = [i for i, r in enumerate(rows) if r["pool"] in pools]
    dropped, changed = idx[0], idx[1]
    rows[changed]["pool_base_token_reserves"] += 1
    extra = dict(rows[changed], inner_ix=rows[changed]["inner_ix"] + 1000)
    rows = [r for i, r in enumerate(rows) if i != dropped] + [extra]
    pq.write_table(pa.Table.from_pylist(rows, schema=buys.schema), glob.glob(os.path.join(first, "pump_amm", "BuyEvent", "*.parquet"))[0])
    bad = reconcile.run(root, os.path.dirname(chunk[2]))
    assert not bad["ok"]
    t = bad["segments"][0]["tables"]["pump_amm/BuyEvent"]
    assert (t["missing"], t["extra"], t["mismatched"]) == (1, 1, 1), t
    assert t["columns"] == {"pool_base_token_reserves": 1}


def test_a_backfill_gives_the_probe_pools_history_without_events(setup, tmp_path):
    """The recorder followed pool P only from segment 2 on; P's earlier transactions came with
    segment 2 as a backfill (overlapping segment 2 itself). LiveFeed's events must be the replay's
    minus P's events before segment 2: the backfill gives farmer history and leg context, no events,
    and the overlap counts once."""
    import pyarrow.parquet as pq

    root, h24, hist, replay = setup
    chunk = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]][0]
    rec = str(tmp_path / "rec")
    make_segments(chunk, rec, usage=False)
    segs = segment_dirs(rec)
    counts = {}
    for _, seg, _ in segs[:3]:
        for f in glob.glob(os.path.join(seg, "pump_amm", "*Event", "*.parquet")):
            for p in pq.read_table(f, columns=["pool"]).column("pool").to_pylist():
                counts[p] = counts.get(p, 0) + 1
    pool = max(counts, key=counts.get)
    third = segs[2]
    moved = {}
    for i, (_, seg, _) in enumerate(segs[:3]):
        for f in glob.glob(os.path.join(seg, "pump_amm", "*Event", "*.parquet")):
            t = pq.read_table(f)
            if "pool" not in t.column_names:
                continue
            mine = [r for r in t.to_pylist() if r["pool"] == pool]
            if not mine:
                continue
            table = os.path.relpath(os.path.dirname(f), seg)
            moved.setdefault(table, (t.schema, []))[1].extend(mine)
            if i < 2:  # not recorded before segment 2: only in the backfill
                pq.write_table(pa.Table.from_pylist([r for r in t.to_pylist() if r["pool"] != pool], schema=t.schema), f)
    n = 0
    for table, (schema, rows) in moved.items():
        os.makedirs(os.path.join(third[1], "backfill", table), exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), os.path.join(third[1], "backfill", table, "part-00000.parquet"))
        n += len(rows)
    meta = dict(third[2], backfills=[{"id": f"{pool}-{LO}", "pool": pool, "from_slot": LO, "to_slot": third[2]["last_slot"],
                                      "rows": n, "status": "complete"}])
    with open(os.path.join(third[1], "_segment.json"), "w") as f:
        json.dump(meta, f)
    feed, live = live_events(rec, h24, hist)
    start = third[2]["first_slot"]
    want = [k for k in replay if not (k[0] == "pool" and k[1] == pool and k[2][0] < start)]
    assert len(want) < len(replay)
    assert live == want
    assert feed.stats["backfill_rows"] > 0 and feed.stats["backfill_duplicates"] == 0
    # P's farmer history reaches back before segment 2 (from the backfill).
    assert any(s < start for (p, _), dq in feed.farmers.hist.items() if p == pool for s, *_ in dq)
