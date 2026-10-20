"""paper.arrivals_fixture: M3-11's arrivals fixture root (spec v0.3 §5.4), on hand-made segments and a hand-made journal."""

import json
import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from paper import arrivals_fixture as A
from paper.journal import Journal


def _put(d, table, cols):
    os.makedirs(os.path.join(d, table), exist_ok=True)
    pq.write_table(pa.table(cols), os.path.join(d, table, "part-00000.parquet"))


def _rec(tmp_path, n=6, per=100):
    """Segments 0..n-1 of `per` slots from 1000: a block every 10 slots (block time slot // 10), a buy every 10 slots in
    transactions 0 and 3."""
    root = tmp_path / "rec"
    for seq in range(n):
        lo = 1000 + per * seq
        d = root / "segments" / f"{seq:09d}"
        slots = list(range(lo, lo + per, 10))
        _put(str(d), "blocks", {"slot": pa.array(slots, pa.uint64()), "block_time": pa.array([s // 10 for s in slots], pa.int64()),
                                "skipped": pa.array([False] * len(slots))})
        _put(str(d), "pump_amm/BuyEvent", {"slot": pa.array(slots * 2, pa.uint64()), "tx_index": pa.array([0] * len(slots) + [3] * len(slots), pa.uint64()),
                                           "pool": pa.array(["P"] * 2 * len(slots))})
        (d / "watchlist.json").write_text("{}")
        (d / "_segment.json").write_text(json.dumps({"seq": seq, "first_slot": lo, "last_slot": lo + per - 1, "complete": True,
                                                     "usage": []}))
    return str(root)


def _journal(tmp_path, slots):
    j = Journal(str(tmp_path / "py"))
    for i, t in enumerate(slots):
        j.put("signals", family="F7", variant="tp4_T600", key=f"P{i}", t=t, status="entered")
    j.put("signals", family="N2_F7", variant="tp4_T600", key="Q", t=1210, status="entered")  # not an F7 row
    j.commit()
    return str(tmp_path / "py")


def test_the_window_holds_three_signal_slots_after_its_history_segments(tmp_path):
    rec = _rec(tmp_path)
    # signals at 1010 (in the first segment: no history before it), 1220, 1230 and 1350: the first window with two
    # history segments is [0, 3] (segments 2 and 3 hold 1220, 1230 and 1350)
    info = A.make(rec, _journal(tmp_path, [1010, 1220, 1220, 1230, 1350]), str(tmp_path / "out"), before=2)
    assert (info["seqs"], info["segments"], info["first_slot"]) == ([0, 3], 4, 1000)
    assert (info["full"], info["no_created"], info["no_block_time"], info["two_streams"]) == (1220, 1230, 1350, 1220)
    seg = tmp_path / "out" / "segments"
    assert sorted(os.listdir(seg)) == [f"{s:09d}" for s in range(4)]
    s2 = seg / "000000002"
    assert os.path.islink(s2 / "pump_amm") and os.path.islink(s2 / "watchlist.json") and not os.path.islink(s2 / "blocks")
    meta = json.loads((s2 / "_segment.json").read_text())
    assert (meta["seq"], meta["written_unix"]) == (2, 1290 // 10 + 2)
    arr = pq.read_table(str(s2 / "arrivals" / "part-00000.parquet")).to_pylist()
    by = {}
    for r in arr:
        by.setdefault((r["slot"], r["tx_index"]), []).append(r)
    assert len(by) == 20  # every transaction of the segment
    # slot 1220 (A): two streams, tx_probes 50 ms earlier; slot 1230 (B): no created; others one row with created
    a = sorted(by[(1220, 3)], key=lambda r: r["stream"])
    assert [r["stream"] for r in a] == ["tx_probes", "tx_watchlist"]
    assert a[1]["received_unix_ns"] == 122 * 10**9 + 350_000_000 + 3_000_000 and a[0]["received_unix_ns"] == a[1]["received_unix_ns"] - 50_000_000
    assert a[1]["created_unix_ns"] == a[1]["received_unix_ns"] - 200_000_000
    assert [r["created_unix_ns"] for r in by[(1230, 0)]] == [None]
    assert len(by[(1240, 0)]) == 1 and by[(1240, 0)][0]["created_unix_ns"] is not None
    # slot 1350 (C): its block row dropped in segment 3, its transactions still arrive (measured from the original block)
    blocks = pq.read_table(str(seg / "000000003" / "blocks" / "part-00000.parquet")).column("slot").to_pylist()
    assert 1350 not in blocks and 1340 in blocks and len(blocks) == 9
    arr3 = pq.read_table(str(seg / "000000003" / "arrivals" / "part-00000.parquet")).to_pylist()
    assert any(r["slot"] == 1350 and r["received_unix_ns"] == 135 * 10**9 + 350_000_000 for r in arr3)
    # nothing under the recorder changed
    assert not os.path.exists(os.path.join(rec, "segments", "000000002", "arrivals"))
    assert "written_unix" not in json.loads(open(os.path.join(rec, "segments", "000000002", "_segment.json")).read())
    assert json.loads((tmp_path / "out" / "fixture.json").read_text()) == info


def test_too_few_signals_or_a_bad_out_is_refused(tmp_path):
    rec = _rec(tmp_path, n=4)
    with pytest.raises(SystemExit, match="three F7 signal slots"):
        A.make(rec, _journal(tmp_path, [1210, 1220]), str(tmp_path / "a"), before=2)
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("x")
    with pytest.raises(SystemExit, match="not empty"):
        A.make(rec, str(tmp_path / "py"), str(tmp_path / "full"))
    with pytest.raises(SystemExit, match="inside"):
        A.make(rec, str(tmp_path / "py"), os.path.join(rec, "fixture"))
