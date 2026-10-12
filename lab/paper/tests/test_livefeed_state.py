"""LiveFeed's long-run state and the measured latency, without recorder event data: the bounded
prune (farmer window), and a synthetic segment (arrivals plus _segment.json) through LiveFeed,
the engine's signal fields, the journal and the daily report's latency lines."""

import json
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq

from backtest.store import Chunk
from backtest.strategies import Clock
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
    d_ms = round(2 * clock.ms_per_slot(1_000))
    eng = lambda slot, tx: SimpleNamespace(slot_arrivals={(slot, tx): arrival(slot, tx)}, slot_block_time=BT, d=2,  # noqa: E731
                                           clock=clock)
    lat5 = Engine._latency(eng(1_000, 5), SimpleNamespace(slot=1_000, tx=5))
    assert lat5 == {"block_time": BT, "received_unix_ns": BT * NS + 620_000_000, "created_unix_ns": BT * NS + 600_000_000,
                    "latency_ms": 620, "segment_written_unix": BT + 2, "segment_lag_ms": 2_000, "d_ms": d_ms}
    lat7 = Engine._latency(eng(1_001, 7), SimpleNamespace(slot=1_001, tx=7))
    assert (lat7["latency_ms"], lat7["segment_lag_ms"]) == (1_400, 2_000)
    replay = SimpleNamespace(slot_arrivals={}, slot_block_time=BT, d=2, clock=clock)
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
