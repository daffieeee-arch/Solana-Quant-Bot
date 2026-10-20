"""LiveFeed's long-run state and the measured latency, without recorder event data: the bounded
prune (farmer window), and a synthetic segment (arrivals plus _segment.json) through LiveFeed,
the engine's signal fields, the journal and the daily report's latency lines."""

import json
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest.store import Chunk
from backtest.strategies import Clock
from paper import live
from paper.engine import Engine
from paper.journal import Journal
from paper.live import LiveFeed, segment_dirs
from paper.report import daily_report

BT = 1_700_000_000  # block time of slot 1000 (s)
NS = 1_000_000_000


def test_prune_keeps_only_the_farmer_window_of_backfill_keys(tmp_path):
    f = LiveFeed(str(tmp_path), store=None, follow=False)
    f.farm_keys["P"] |= {(100, 0, 0, 0), (5_000, 1, 0, 0)}
    f.farm_keys["Q"].add((200, 0, 0, 0))
    f.curve_hist["M"].append(SimpleNamespace(slot=150))
    f.curve_hist["N"].append(SimpleNamespace(slot=4_500))
    f._prune(5_100, 1_000)
    assert dict(f.farm_keys) == {"P": {(5_000, 1, 0, 0)}}  # Q: no key left, as before its first backfill
    assert set(f.curve_hist) == {"N"}


def segment(root, seq, first, last, written, arrivals):
    d = root / "segments" / f"{seq:09d}"
    (d / "arrivals").mkdir(parents=True)
    cols = ("slot", "tx_index", "signature", "received_unix_ns", "created_unix_ns", "stream")
    pq.write_table(pa.table({c: [r[i] for r in arrivals] for i, c in enumerate(cols)}), str(d / "arrivals" / "part-0.parquet"))
    (d / "_segment.json").write_text(json.dumps({"seq": seq, "first_slot": first, "last_slot": last, "complete": True,
                                                  "written_unix": written}))


def test_latency_from_a_synthetic_segment_to_the_daily_report(tmp_path):
    # Two triggers in one segment closed at BT + 2 s: tx 5 arrived twice (the earliest counts), tx 7 once.
    segment(tmp_path, 1, 1_000, 1_001, BT + 2, [
        (1_000, 5, "a", BT * NS + 900_000_000, BT * NS + 850_000_000, "tx_probes"),
        (1_000, 5, "a", BT * NS + 620_000_000, BT * NS + 600_000_000, "tx_watchlist"),
        (1_001, 7, "b", BT * NS + 1_400_000_000, None, "tx_watchlist")])
    f = LiveFeed(str(tmp_path), store=None, follow=False)
    [(seq, seg, meta)] = segment_dirs(str(tmp_path))
    arrival = f._arrival_of(Chunk((meta["first_slot"], meta["last_slot"] + 1, seg)), meta)
    assert arrival(1_000, 5) == (BT * NS + 620_000_000, BT * NS + 600_000_000, BT + 2)
    assert arrival(1_001, 7) == (BT * NS + 1_400_000_000, None, BT + 2)
    assert arrival(1_001, 8) == (None, None, BT + 2)  # no arrival row: only the segment's close

    clock = Clock()

    def eng_ns(**kw):
        # K3's delay (d_ms = 600, owner decision 2026-10-14) through the engine's own _d_at.
        ns = SimpleNamespace(d=2, d_ms=600, clock=clock, **kw)
        ns._d_at = lambda t: Engine._d_at(ns, t)
        return ns

    # The journaled d_ms is the simulated delay: 600 ms in whole slots (2 at 267.3 ms), back in ms.
    d_ms = round(clock.slots(600, 1_000) * clock.ms_per_slot(1_000))
    assert d_ms == 535
    eng = lambda slot, tx: eng_ns(slot_arrivals={(slot, tx): arrival(slot, tx)}, slot_block_time=BT)  # noqa: E731
    lat5 = Engine._latency(eng(1_000, 5), SimpleNamespace(slot=1_000, tx=5))
    assert lat5 == {"block_time": BT, "received_unix_ns": BT * NS + 620_000_000, "created_unix_ns": BT * NS + 600_000_000,
                    "latency_ms": 620, "segment_written_unix": BT + 2, "segment_lag_ms": 2_000, "d_ms": d_ms}
    lat7 = Engine._latency(eng(1_001, 7), SimpleNamespace(slot=1_001, tx=7))
    assert (lat7["latency_ms"], lat7["segment_lag_ms"]) == (1_400, 2_000)
    replay = eng_ns(slot_arrivals={}, slot_block_time=BT)
    assert Engine._latency(replay, SimpleNamespace(slot=1_000, tx=5)) is None  # replay: the fields stay NULL

    j = Journal(str(tmp_path / "journal"))
    day = "2023-11-14"
    for var in ("tp4_T600", "tp6_T600"):  # two variants and an N2 signal share a trigger: counted once
        j.put("signals", family="F7", variant=var, key="P", t=1_000, day=day, status="entered", **lat5)
    j.put("signals", family="N2_F7", variant="tp4_T600", key="Q", t=1_000, day=day, status="entered", **lat5)
    j.put("signals", family="F7", variant="tp4_T600", key="R", t=1_001, day=day, status="entered", **lat7)
    j.put("signals", family="F7", variant="tp4_T600", key="S", t=990, day=day, status="entered")  # replay row: ignored
    j.commit()
    daily_report(j, day)
    report = (tmp_path / "journal" / day / "report.md").read_text()
    assert f"- triggers 2; simulated d {d_ms} ms (median)" in report
    assert f"- receive - block time: median 1010 ms, p90 1322 ms; later than d: {(620 > d_ms) + (1_400 > d_ms)} of 2" in report
    assert "- segment close - block time: median 2000 ms, p90 2000 ms; later than d: 2 of 2" in report


def test_backfill_overlap_with_earlier_recorded_rows_counts_once(tmp_path):
    """A probe's recorded rows come in segment N, its backfill (overlapping them) in N + 1: the backfill
    row is history already seen and is dropped; a backfill row first, then the recorded one: the event
    stays, the farmer history does not count it again."""
    f = LiveFeed(str(tmp_path), store=None, follow=False)
    rec = ("p", "POOL", 1_000, 7, 2, 0)
    assert f._seen(rec, 1_000, "rec") is None  # segment N: recorded
    f._history("POOL", True, 5, ["buy", 1_000, 7, 2, 0])  # segment N + 1: the same trade from the backfill
    assert f.stats["backfill_duplicates"] == 1 and not f.farmers.hist
    hist = ("p", "POOL", 1_001, 3, 1, 0)
    assert f._seen(hist, 1_001, "hist") is None  # a backfill row first
    assert f._seen(hist, 1_001, "rec") == "hist"  # its recorded twin: an event, but no second farmer count
    assert f._seen(rec, 1_000, "rec") == "rec"  # recorded twice (a restart): dropped
    # Keys older than SEEN_KEEP_SLOTS behind the newest are forgotten.
    f._seen(("p", "POOL", 1_000 + live.SEEN_KEEP_SLOTS + 10, 0, 0, 0), 1_000 + live.SEEN_KEEP_SLOTS + 10, "rec")
    assert rec not in f.seen_keys and hist not in f.seen_keys


def test_heap_keeps_one_of_equal_events_without_comparing_them(tmp_path):
    from paper.feed import Event

    class Twice(LiveFeed):
        def _segments(self):
            return [(0, "a", {}), (1, "b", {})] if self.next_seq == 0 else []

        def segment_events(self, seq, seg, meta):
            leg = SimpleNamespace(slot=100)  # not orderable: equal tuples must never reach it
            return [Event(100, 1, 0, 0, None, "pool", "P", leg), Event(101, 0, 0, 0, None, "pool", "P", leg)]

    f = Twice(str(tmp_path), store=None, follow=False)
    out = [(e.slot, e.tx_index) for e in f.events()]
    # Segment b repeats both: 100 (already released, at the last released slot) and 101 (still queued).
    assert out == [(100, 1), (101, 0)] and f.stats["duplicate_rows"] == 2


def test_unknown_pools_are_counted_and_logged(caplog):
    import os

    fix = os.path.join(os.path.dirname(live.__file__), "..", "backtest", "tests", "fixtures", "smoke1054_multihop")
    c = Chunk((455_328_000, 455_329_000, fix))
    f = LiveFeed("/nonexistent-recorder-root", store=None, follow=False)
    with caplog.at_level("WARNING", logger="paper.live"):
        f._unknown_pools(c, 7)
        f._unknown_pools(c, 8)
    assert f.stats["unknown_pool_rows"] == 2 * 37  # 20 buys and 17 sells of multi-hop hops, none known
    assert f.stats["unknown_pools"] == len(f.unknown_seen) > 0
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 1 and msgs[0].startswith("segment 7:")  # the second segment brought no new pool


def test_unknown_mints_are_counted_and_logged(caplog):
    # M3-09a's note v0.3: LiveFeed dropped the curve rows of mints it does not know without a count (11,606 of job 91's
    # 11,717). Now every such row is counted, and each new mint logged once.
    import os

    from backtest.store import _curve_trades

    fix = os.path.join(os.path.dirname(live.__file__), "..", "backtest", "tests", "fixtures", "smoke1054_multihop")
    c = Chunk((455_328_000, 455_329_000, fix))
    f = LiveFeed("/nonexistent-recorder-root", store=None, follow=False)
    rows = f.con.execute(f"SELECT mint, count(*) FROM ({_curve_trades(c, mints=None)}) x GROUP BY mint ORDER BY 2 DESC, 1").fetchall()
    assert rows and sum(n for _, n in rows) > rows[0][1]  # more than one mint
    with caplog.at_level("WARNING", logger="paper.live"):
        f._unknown_mints(c, 7)
        f._unknown_mints(c, 8)
    total = sum(n for _, n in rows)
    assert (f.stats["unknown_mint_rows"], f.stats["unknown_mints"], len(f.unknown_mints_seen)) == (2 * total, len(rows), len(rows))
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 1 and msgs[0].startswith("segment 7:")
    # a mint the feed knows (live_mints) is admitted, so its rows are not counted
    g = LiveFeed("/nonexistent-recorder-root", store=None, follow=False)
    g.con.execute("INSERT INTO live_mints VALUES (?)", [rows[0][0]])
    g._unknown_mints(c, 7)
    assert (g.stats["unknown_mint_rows"], g.stats["unknown_mints"]) == (total - rows[0][1], len(rows) - 1)


def test_unknown_pools_count_their_withdraw_deposit_and_boost_rows():
    # M3-09a's note v0.3: the last 2 of job 91's 11,717 were withdraws of the 2 unknown pools, which unknown_pool_rows
    # (buys and sells only) did not count. Two withdraws, a deposit and a boost of dev2's epoch 1043, no pool known.
    import os

    fix = os.path.join(os.path.dirname(live.__file__), "..", "backtest", "tests", "fixtures", "pool_other_1043")
    c = Chunk((450_576_000, 450_578_000, fix))
    f = LiveFeed("/nonexistent-recorder-root", store=None, follow=False)
    pools = f.con.execute(f"SELECT count(DISTINCT pool) FROM read_parquet('{fix}/pump_amm/*/*.parquet', union_by_name = true)").fetchone()[0]
    f._unknown_pools(c, 3)
    assert (f.stats["unknown_pool_rows"], f.stats["unknown_pool_other_rows"], f.stats["unknown_pools"]) == (0, 4, pools)


def fake_store(path, slots):
    import duckdb

    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE pool (pool VARCHAR, kind VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT,
                   trader VARCHAR, token_buy BOOLEAN, token_amount BIGINT)""")
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, 0, 0, 0, ?, ?, 10)",
                    [("P", "buy" if i % 2 else "sell", s, f"W{i % 3}", bool(i % 2)) for i, s in enumerate(slots)])
    for t in ("mints (mint VARCHAR, create_slot UBIGINT, creator VARCHAR, mayhem BOOLEAN, quote_mint VARCHAR, "
              "complete_slot UBIGINT, pool VARCHAR)",
              "pools (pool VARCHAR, mint VARCHAR, orientation VARCHAR, quote_class VARCHAR, mayhem BOOLEAN, "
              "coin_creator VARCHAR, create_slot UBIGINT)"):
        con.execute(f"CREATE TABLE {t}")
    con.close()
    return str(path)


def test_seed_window_and_when_the_farm_labels_are_complete(tmp_path):
    h24 = 1_000
    cases = (  # (store slots, first live slot) -> (lo, hi, complete_from)
        (range(0, 5_000, 10), 4_000, (3_000, 4_000, 4_000)),  # the store reaches the first live slot
        (range(3_500, 5_000, 10), 4_000, (3_500, 4_000, 4_500)),  # it starts late: complete one day after its start
        (range(0, 2_000, 10), 4_000, (3_000, 3_000, 5_000)),  # it ends before: a gap, complete a day after the start
    )
    for i, (slots, first, want) in enumerate(cases):
        # a store that reaches the first live slot is taken as known before it (LiveFeed refuses it whole: below)
        before = first if max(slots) >= first else None
        f = LiveFeed(str(tmp_path), store=fake_store(tmp_path / f"s{i}.duckdb", slots), follow=False, seed_farmers=True,
                     snapshot_before=before)
        f._init_seed(first, h24)
        assert (f.farm_seed["lo"], f.farm_seed["hi"], f.farm_seed["complete_from"]) == want
        f._seed_pool("P")
        n = sum(len(dq) for dq in f.farmers.hist.values())
        assert n == sum(1 for s in slots if want[0] <= s < want[1])  # the pool's trades of the window, once
        f._seed_pool("P")
        # Once per pool; a seed window left empty by a gap queries nothing.
        assert sum(len(dq) for dq in f.farmers.hist.values()) == n and f.stats["seeded_pools"] == (want[1] > want[0])
        f.close()


def test_a_start_never_knows_the_store_past_its_first_live_slot(tmp_path):
    """LAB review on Codex finding 3 (383a24d): a store whose pool rows reach the first live slot needs snapshot_before (at
    or before that slot: paper.shadow's lo may lie just before it); one that ends before the start is taken whole."""
    store = fake_store(tmp_path / "s.duckdb", range(0, 5_000, 10))
    for before, first, ok in ((None, 4_000, False), (4_001, 4_000, False), (4_000, 4_000, True), (3_990, 4_000, True), (None, 5_000, True)):
        f = LiveFeed(str(tmp_path), store=store, follow=False, seed_farmers=True, snapshot_before=before)
        try:
            if ok:
                f._init_seed(first, 1_000)
                assert f.farm_seed["first_live_slot"] == first
            else:
                with pytest.raises(SystemExit, match="snapshot_before"):
                    f._init_seed(first, 1_000)
        finally:
            f.close()
