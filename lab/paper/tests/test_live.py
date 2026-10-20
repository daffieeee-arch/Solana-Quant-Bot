"""LiveFeed over recorder segments gives exactly the replay's events.

The fixture cuts 4,000 slots of an OF1 chunk into four segments in the recorder's layout (the
recorder writes the extractor's own rows, so OF1 rows are what it would have written), with the
store's tokens and pools as the snapshot and the store's farmer history of the day before. The
events must equal ReplayFeed's over the same slots: venue, key, position and the whole leg.
"""

import glob
import json
import os
from types import SimpleNamespace

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
    # the store is known before LO only: LiveFeed refuses a store past its first live slot without snapshot_before (LAB
    # review on #478, for every feed with a store)
    feed = LiveFeed(root, store=STORE, follow=False, h24=h24, memory="500MB", **{"snapshot_before": LO, **kw})
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
    assert {k: feed.stats[k] for k in ("segments", "late_rows", "unknown_pools", "backfill_rows")} == \
        {"segments": 4, "late_rows": 0, "unknown_pools": 0, "backfill_rows": 0}
    assert sum(1 for k in live if k[0] == "pool" and k[4].farmer) > 0  # the farmer flag was exercised


def test_the_segments_dump_gives_the_stores_lines(setup):
    """paper.legs_dump --segments (M3-09a) as paper.engine --live builds the feed (the store's farmer seed, not the
    test's): the same lines as the store form over the same slots, window by window. The store reaches past LO, so the
    start takes it as known before LO (LiveFeed refuses a store past its first live slot without snapshot_before)."""
    from paper import legs_dump

    root, h24, _, replay = setup
    live = legs_dump.dump_segments(root, STORE, LO, HI, window=SEG, snapshot_before=LO, h24=h24, memory="500MB")
    store = legs_dump.dump(STORE, LO, HI, window=SEG)
    assert live["events"] == len(replay) and live["kinds"] == store["kinds"]
    assert [w[:4] for w in live["windows"]] == store["windows"] and live["sha256"] == store["sha256"]
    assert live["history"]["count"] == live["flag_fixes"]["count"] == 0
    assert live["usage"]["count"] == 4 and live["stats"]["segments"] == 4
    assert live["farm_seed"]["first_live_slot"] == LO and live["farm_seed"]["h24"] == h24


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
    """A pool created inside the segments is classified from its CreatePoolEvent as the store does: the feed knows the
    store before LO only (snapshot_before), and learns the segments' pools as the whole store has them (a second feed,
    for its lookups only: it reads no segment, so it starts nothing)."""
    root, h24, hist, _ = setup
    store = LiveFeed(root, store=STORE, follow=False, h24=h24, memory="500MB")
    feed = LiveFeed(root, store=STORE, follow=False, h24=h24, memory="500MB", snapshot_before=LO)
    created = []
    for _, seg, meta in segment_dirs(root):
        f = glob.glob(os.path.join(seg, "pump_amm", "CreatePoolEvent", "*.parquet"))
        if f:
            created += [r[0] for r in feed.con.execute(f"SELECT pool FROM read_parquet('{f[0]}')").fetchall()]
    if not created:
        pytest.skip("no pool created in the fixture slots")
    want = {p: store.pools.get(p) for p in created}
    store.close()
    assert all(feed.pools.get(p) is None for p in created)  # created at or after LO: not in the snapshot before it
    list(feed.events())
    for p in created:
        assert feed.pools.get(p) == want[p], p


def test_a_pool_only_the_account_streams_name_is_not_learned(tmp_path):
    """Creation-filters spec v0.2 M1: the recorder's pool account streams (pool_creations, pool_accounts) see every pool
    write, old pools included, so a pool they alone name is not learned; it is counted once in
    pools_seen_without_creation and the segment hands the engine a usage row with the count. A CreatePoolEvent of the
    same pool, later, learns it with its own slot."""
    import pyarrow.parquet as pq

    from paper.live import WSOL

    rows = {"slot": [101, 105], "pool": ["P1", "P1"], "base_mint": [WSOL, WSOL], "quote_mint": ["M1", "M1"],
            "coin_creator": ["C1", "C1"], "is_mayhem_mode": [False, False]}
    for seq in (0, 1):
        d = tmp_path / "segments" / f"{seq:09d}"
        (d / "pool_creations").mkdir(parents=True)
        pq.write_table(pa.table(rows), str(d / "pool_creations" / "part-00000.parquet"))
        (d / "_segment.json").write_text(json.dumps({"seq": seq, "first_slot": 100 + 100 * seq, "last_slot": 199 + 100 * seq, "complete": True}))
    d = tmp_path / "segments" / "000000001" / "pump_amm" / "CreatePoolEvent"
    d.mkdir(parents=True)
    pq.write_table(pa.table({"slot": [150], "tx_index": [0], "pool": ["P2"], "base_mint": [WSOL], "quote_mint": ["M2"],
                             "coin_creator": ["C2"], "is_mayhem_mode": [False]}), str(d / "part-00000.parquet"))
    feed = LiveFeed(str(tmp_path), follow=False, h24=1000, memory="200MB")
    assert list(feed.events()) == []
    assert "P1" not in feed.pools and "P1" not in feed.pool_class and feed.pools["P2"].create_slot == 150
    assert feed.stats["pools_seen_without_creation"] == 1  # once, though both segments name it
    usage = feed.drain_usage()
    assert [(u["stream"], u["messages"], u["seq"]) for u in usage] == [("pools_seen_without_creation", 1, 0)]
    assert [k for k, *_ in feed.drain_learned()] == ["pool"]  # P2 only
    feed.close()


def test_the_segment_notes_name_creation_detection_probes_and_late_probes(tmp_path):
    """Creation-filters spec v0.2 M4, S3: a segment whose filter has no creation detection (new_reversed false), whose
    probes a downscale dropped, or whose backfill reached past Triton's replay window hands the engine a usage row each;
    a segment from before the filter summary (no new_reversed key) none."""
    filt = {"new_reversed": False, "dropped": ["pool_accounts", "pool_creations", "new_vaults", "backfill", "tx_probes", "tx_new_reversed"]}
    metas = [{"filter": filt, "backfills": [
                 {"pool": "PB", "status": "failed", "rows": 0, "error": "code: 'Operation was attempted past the valid range', "
                                                                        "message: \"broadcast from 5 is not available, last available: 9\""},
                 {"pool": "PA", "status": "failed", "rows": 0, "error": "connect: timed out"},
                 {"pool": "PC", "status": "complete", "rows": 3}]},
             {"filter": {"tx_pools": 3, "tx_probes": 9}}]
    for seq, extra in enumerate(metas):
        d = tmp_path / "segments" / f"{seq:09d}"
        d.mkdir(parents=True)
        (d / "_segment.json").write_text(json.dumps({"seq": seq, "first_slot": 100 + 100 * seq, "last_slot": 199 + 100 * seq,
                                                     "complete": True, "usage": [{"minute": 7, "stream": "tx_probes", "bytes": 5}], **extra}))
    feed = LiveFeed(str(tmp_path), follow=False, h24=1000, memory="200MB")
    list(feed.events())
    usage = [(u["stream"], u["messages"] if "messages" in u else None, u["seq"]) for u in feed.drain_usage()]
    assert usage == [("tx_probes", None, 0), ("creation_detection_off", 1, 0), ("probes_off", 1, 0), ("late_probe", 1, 0),
                     ("tx_probes", None, 1)]
    assert {k: feed.stats[k] for k in ("creation_detection_off_segments", "probes_off_segments", "late_probes")} == \
        {"creation_detection_off_segments": 1, "probes_off_segments": 1, "late_probes": 1}
    feed.close()


def test_a_token_created_live_keeps_its_creator_and_old_ones_go(tmp_path):
    """The recorder's --pump-creates: a token created after the snapshot gets its creator from the CreateEvent,
    also when the creator moved the creator fee to a sharing config, which becomes the pool's coin creator
    (#233, job 84). Tokens that never complete are dropped after TOKEN_KEEP_DAYS."""
    import pyarrow.parquet as pq

    from paper import live
    from paper.engine import Engine
    from paper.live import WSOL

    d = tmp_path / "segments" / "000000000"
    for t in ("pump/CreateEvent", "pump_amm/CreatePoolEvent"):
        (d / t).mkdir(parents=True)
    pq.write_table(pa.table({"slot": [101, 102], "tx_index": [0, 0], "mint": ["M1", "M2"], "user": ["C1", "C2"],
                             "is_mayhem_mode": [False, False], "quote_mint": [None, None]},
                            schema=pa.schema([("slot", pa.uint64()), ("tx_index", pa.int64()), ("mint", pa.string()),
                                              ("user", pa.string()), ("is_mayhem_mode", pa.bool_()), ("quote_mint", pa.string())])),
                   str(d / "pump" / "CreateEvent" / "part-00000.parquet"))
    pq.write_table(pa.table({"slot": [105], "tx_index": [0], "pool": ["P1"], "base_mint": ["M1"], "quote_mint": [WSOL],
                             "coin_creator": ["S1"], "is_mayhem_mode": [False]}),
                   str(d / "pump_amm" / "CreatePoolEvent" / "part-00000.parquet"))
    (d / "_segment.json").write_text(json.dumps({"seq": 0, "first_slot": 100, "last_slot": 110, "complete": True}))
    feed = LiveFeed(str(tmp_path), follow=False, h24=1000, memory="200MB")
    assert list(feed.events()) == []
    assert (feed.token("M1").creator, feed.pools["P1"].coin_creator) == ("C1", "S1")
    eng = SimpleNamespace(feed=feed, burn_slot=0, dev=(0, 10**9))
    assert Engine._universe(eng, "pool", "P1") == (True, "C1", "S1", "normal")  # F7's creator rule sees C1 and S1
    feed.tokens["M1"] = live.TokenInfo("M1", 101, "C1", False, None, None, "P1")  # graduated: kept
    feed._prune(102 + live.TOKEN_KEEP_DAYS * 1000 + 1, 1000)  # M2 (slot 102) is older than the keep window
    assert set(feed.tokens) == {"M1"} and feed.stats["tokens_dropped"] == 1
    assert feed.con.execute("SELECT list(mint ORDER BY mint) FROM live_mints").fetchone()[0] == ["M1"]
    feed.close()


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
    feed = LiveFeed(root, store=STORE, follow=False, h24=h24, memory="500MB", snapshot_before=LO)
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
            if "pool" not in pq.read_schema(f).names:
                continue
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
    hist = feed.drain_history()  # the backfill's trades for the engine's farming window, never events
    assert hist and {p for p, _ in hist} == {pool} and feed.drain_history() == []
    assert all(l.kind in ("buy", "sell") and l.slot < start + 1000 for _, legs in hist for l in legs)
    # P's farmer history reaches back before segment 2 (from the backfill).
    assert any(s < start for (p, _), dq in feed.farmers.hist.items() if p == pool for s, *_ in dq)


def test_a_live_restart_after_the_keeper_ends_with_the_uninterrupted_journal(setup, tmp_path):
    """The high-water-mark restart (paper/restart.py) on the recorder's segments: a run dies in segment 3, the keeper
    removes segment 0, and the restart (LiveFeed with the journal's learned pools, tokens and epoch span; the farmer
    history as the run had it at segment 1) ends with the uninterrupted run's journal. Variant tp4_T120: no position
    opened in segment 0 is still open at the HWM (that one could not be carried)."""
    import shutil

    from paper import restart as RS
    from paper.engine import Engine
    from paper.journal import Journal
    from paper.journal_diff import compare

    _, h24, hist, _ = setup
    chunk = [c for c in data.complete_chunks() if c[0] <= LO and HI <= c[1]][0]
    full = str(tmp_path / "full")
    make_segments(chunk, full, usage=False)

    class Crash(LiveFeed):
        """Both runs know the whole store, as before the store bound (LAB review on #478 runs it for every store feed): this
        test is about the high-water-mark restart. A restart from segment 1 knows a token created in segment 0 only when
        it graduated (live.py learns tokens at their completion or migration), so with the store before LO only it drops
        that token's later curve rows and a curve position on it; that restart gap is reported to LAB on its own.
        Production cannot start this way: the bound refuses a store past the first live slot."""
        crash_slot = None

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.bound_checked = True  # test only: the whole store (see the docstring)

        def events(self):
            for e in super().events():
                if self.crash_slot is not None and e.slot >= self.crash_slot:
                    raise RuntimeError("simulated crash")
                yield e

    def run(root, name, crash_slot=None, farmers=None):
        j = Journal(str(tmp_path / name))
        feed = Crash(root, store=STORE, follow=False, h24=h24, memory="500MB", learned=j.rows("learned"))
        feed.crash_slot = crash_slot
        if farmers is None:
            feed.farmers.seed(hist)
        else:
            feed.farmers = farmers
        eng = Engine(feed, j, None, ignore_risk=True, variants=["0.04:120"], n2_k=0, clock=feed.clock, backup=False,
                     restart=True)
        try:
            eng.run()
        except RuntimeError:
            j.db.rollback()  # the process died: what it wrote after its last commit is gone
            j.close()
            raise
        finally:
            feed.close()
        return eng

    u = run(full, "u")
    with pytest.raises(RuntimeError, match="simulated crash"):
        run(full, "r", crash_slot=LO + 1_600)
    # The keeper removes segment 0; the farmer history of its trades is the run's (the restart's own seed would lack it).
    later, first = str(tmp_path / "later"), str(tmp_path / "first")
    shutil.copytree(full, later)
    shutil.rmtree(os.path.join(later, "segments", f"{0:09d}"))
    os.makedirs(os.path.join(first, "segments"))
    shutil.copytree(os.path.join(full, "segments", f"{0:09d}"), os.path.join(first, "segments", f"{0:09d}"))
    f0, _ = live_events(first, h24, hist)  # segment 0's farmer history (pool trades: the same with the store before LO)
    r = run(later, "r", farmers=f0.farmers)
    f0.close()
    # What segment 0 taught (pools created there) is known to the restart, from the journal.
    seg0 = [x["key"] for x in u.journal.rows("learned") if x["kind"] == "pool" and x["seq"] == 0]
    assert all(x in r.feed.pool_class for x in seg0)
    print(f"learned in segment 0: {len(seg0)} pools; loaded {r.feed.stats.get('learned_loaded')}; "
          f"signals {len(u.journal.rows('signals'))}, exits {len(u.journal.rows('exits'))}")

    def seen_before_the_restart(table, key, a, b):
        if table == "signals" and a and b and {c for c in a if a[c] != b[c]} == {"pool_seen_slot"}:
            return "pool first seen before the restart's start"
        return None

    res = compare(u.journal.dir, r.journal.dir, causes=[seen_before_the_restart])
    assert res["green"], res["tables"]
    assert r.journal.rows("heartbeat") == u.journal.rows("heartbeat")
    assert r.journal.state(RS.HWM_STATE_KEY) == u.journal.state(RS.HWM_STATE_KEY)
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))
    assert len(rec) == 1 and rec[0]["first_slot"] >= LO + SEG
    assert {k: v for k, v in rec[0]["counts"].items() if k not in RS.BEFORE} == {}, rec
    assert r.feed.clock.per_epoch == u.feed.clock.per_epoch  # the epoch's span starts at segment 0's first block


LATE_POOL = "4w2DBFyJPHZo2hdkJUg7ejLxCJz3dXfCaMdS7zJGc6RZ"


def late_backfill_root(root, tmp_path, pool=LATE_POOL):
    """The fixture's recording with `pool` taken from segment 1 on: its segment-0 rows move into a backfill that comes
    with segment 2 (a probe followed mid-life; 102 of the pool's 275 farmer flags differ in arrival order)."""
    import shutil

    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    late = str(tmp_path / "late")
    shutil.copytree(root, late)
    seg0, seg2 = (os.path.join(late, "segments", f"{i:09d}") for i in (0, 2))
    moved = 0
    for tdir in glob.glob(os.path.join(seg0, "pump_amm", "*")):
        f = os.path.join(tdir, "part-0.parquet")
        t = pq.read_table(f)
        if "pool" not in t.column_names:
            continue
        mask = pc.fill_null(pc.equal(t["pool"], pool), False)
        mine = t.filter(mask)
        if mine.num_rows:
            d = os.path.join(seg2, "backfill", "pump_amm", os.path.basename(tdir))
            os.makedirs(d, exist_ok=True)
            pq.write_table(mine, os.path.join(d, "part-0.parquet"))
            pq.write_table(t.filter(pc.invert(mask)), f)
            moved += mine.num_rows
    assert moved > 0
    with open(os.path.join(seg2, "_segment.json")) as fh:
        meta = json.load(fh)
    meta["backfills"] = [{"pool": pool, "from_slot": LO, "to_slot": LO + SEG - 1, "status": "complete", "rows": moved}]
    with open(os.path.join(seg2, "_segment.json"), "w") as fh:
        json.dump(meta, fh)
    return late


def test_a_late_backfill_corrects_the_recorded_trades_farmer_flags(setup, tmp_path, monkeypatch):
    """A probe followed mid-life (shadow job 91, pool 4reQ...): the recorder takes pool P from segment 1 on, and P's
    earlier trades (segment 0) come as a backfill with segment 2. LiveFeed flagged P's trades of segment 1 without that
    history (here 102 of 275 flags differ); when the backfill arrives it computes them again (_reflag) and the
    engine's farming window gets them (_farm_fix): the window, and so every farm label after it, equals the one of the
    recording that had P from the start. Without the correction it does not."""
    from paper.engine import Engine
    from paper.journal import Journal

    root, h24, hist, _ = setup
    P = LATE_POOL
    late = late_backfill_root(root, tmp_path)

    def farm_window(r, name):
        feed = LiveFeed(r, store=STORE, follow=False, h24=h24, memory="500MB", snapshot_before=LO)
        feed.farmers.seed(hist)
        eng = Engine(feed, Journal(str(tmp_path / name)), None, ignore_risk=True, n2_k=0, clock=feed.clock, backup=False)
        eng.run()
        feed.close()
        return eng.farm_win.get(P, []), feed.stats["farmer_flags_fixed"], eng

    full, none, _ = farm_window(root, "full")
    fixed, n, e_fixed = farm_window(late, "late")
    assert none == 0 and n > 0 and len(full) > 100
    assert fixed == full
    # The corrections are counted per day for the daily report (journal state farm_flag_fixes, report.farm_lines).
    days = json.loads(e_fixed.j.state("farm_flag_fixes"))  # {day: [fixed, of them in pools with an open position]}
    assert sum(v[0] for v in days.values()) == n and all(0 <= v[1] <= v[0] for v in days.values())
    from paper.report import farm_lines

    day = next(iter(days))
    assert any(f"{days[day][0]} recorded trades' farmer flags corrected" in x and f"{days[day][1]} of them in pools with an "
               "open position" in x for x in farm_lines(e_fixed.j, day))
    monkeypatch.setattr(LiveFeed, "_reflag", lambda self, pools, h24: None)
    unfixed, _, e_unfixed = farm_window(late, "unfixed")
    assert unfixed != full and sum(1 for a, b in zip(unfixed, full) if a != b) > 0
    # LAB review: the correction changes the farm labels only, never a position. With farm 'all' (no decision on the
    # label) orders, fills, exits and positions are the same with and without it; only signals' farm column may differ.
    for t in ("orders", "fills", "exits", "positions"):
        assert e_fixed.j.rows(t) == e_unfixed.j.rows(t), t
    strip = lambda rows: [{k: v for k, v in r.items() if k != "farm"} for r in rows]  # noqa: E731
    assert strip(e_fixed.j.rows("signals")) == strip(e_unfixed.j.rows("signals"))


def test_a_late_backfill_just_after_a_restart_leaves_the_rows_up_to_the_hwm(setup, tmp_path):
    """LAB review on (b) with #331: the run dies in segment 1, before pool P's late backfill (segment 2). The restart
    replays up to the HWM with the journal's decisions, then the backfill arrives and P's flags are corrected
    (_reflag, _farm_fix). The rows up to the HWM stay the first run's, and the final journal equals the run without
    the restart."""
    from paper import restart as RS
    from paper.engine import Engine
    from paper.journal import Journal
    from paper.journal_diff import compare

    root, h24, hist, _ = setup
    late = late_backfill_root(root, tmp_path)

    class Crash(LiveFeed):
        crash_slot = None

        def events(self):
            for e in super().events():
                if self.crash_slot is not None and e.slot >= self.crash_slot:
                    raise RuntimeError("simulated crash")
                yield e

    def run(name, crash_slot=None):
        j = Journal(str(tmp_path / name))
        feed = Crash(late, store=STORE, follow=False, h24=h24, memory="500MB", learned=j.rows("learned"), snapshot_before=LO)
        feed.crash_slot = crash_slot
        feed.farmers.seed(hist)
        eng = Engine(feed, j, None, ignore_risk=True, variants=["0.04:120"], n2_k=0, clock=feed.clock, backup=False,
                     restart=True)
        try:
            eng.run()
        except RuntimeError:
            j.db.rollback()
            j.close()
            raise
        finally:
            feed.close()
        return eng

    u = run("u")
    with pytest.raises(RuntimeError, match="simulated crash"):
        run("r", crash_slot=LO + SEG + 400)  # in segment 1: P's backfill (segment 2) has not arrived
    j1 = Journal(str(tmp_path / "r"))
    hwm = json.loads(j1.state(RS.HWM_KEY))["slot"]
    before = {t: j1.rows(t) for t in ("signals", "orders", "fills", "exits")}
    j1.close()
    assert hwm < LO + 2 * SEG  # before the backfill's segment
    r = run("r")
    assert r.feed.stats["farmer_flags_fixed"] > 0  # the correction ran after the restart's HWM
    res = compare(u.journal.dir, r.journal.dir)
    assert res["green"], res["tables"]
    after = {t: r.journal.rows(t) for t in before}
    assert all(row in after[t] for t in before for row in before[t])  # the rows up to the HWM are unchanged
    rec = json.loads(r.journal.state(RS.RESTARTS_KEY))
    assert len(rec) == 1 and {k: v for k, v in rec[0]["counts"].items() if k not in RS.BEFORE} == {}, rec
    assert r.farm_win.get(LATE_POOL) == u.farm_win.get(LATE_POOL)
