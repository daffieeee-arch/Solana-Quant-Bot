"""paper.restart_root: M3-09b's injected restart (spec v0.2 §4.3), a recorder root of symlinks with segments again."""

import json
import os

import pytest

from paper import restart_root


def _rec(tmp_path, n=5, incomplete=()):
    """A recorder root: segments 0..n-1 of 100 slots each, each with a table file and its own usage row."""
    for seq in range(n):
        d = tmp_path / "rec" / "segments" / f"{seq:09d}"
        (d / "pump" / "TradeEvent").mkdir(parents=True)
        (d / "pump" / "TradeEvent" / "part-00000.parquet").write_text(f"rows of {seq}")
        meta = {"seq": seq, "first_slot": 1000 + 100 * seq, "last_slot": 1099 + 100 * seq, "complete": seq not in incomplete,
                "usage": [{"minute": seq}]}
        (d / "_segment.json").write_text(json.dumps(meta))
    return str(tmp_path / "rec")


def _read(out):
    base = os.path.join(out, "segments")
    return {name: json.load(open(os.path.join(base, name, "_segment.json"))) for name in sorted(os.listdir(base))}


def test_the_segments_come_again_after_the_last_copied_and_the_rest_moves_up(tmp_path):
    rec = _rec(tmp_path)
    out = str(tmp_path / "restart")
    res = restart_root.make(rec, out, at=1, n=2)
    assert res == {"segments": 7, "copied": [1, 2], "copies": [3, 4], "moved_up_by": 2}
    segs = _read(out)
    # new seq -> (its slots, the original whose rows it holds)
    got = {int(name): (m["first_slot"], m["usage"][0]["minute"], m["seq"]) for name, m in segs.items()}
    assert got == {0: (1000, 0, 0), 1: (1100, 1, 1), 2: (1200, 2, 2), 3: (1100, 1, 3), 4: (1200, 2, 4), 5: (1300, 3, 5), 6: (1400, 4, 6)}
    # every entry but _segment.json is a link to the original; nothing under the recorder changed
    link = os.path.join(out, "segments", "000000003", "pump")
    assert os.path.islink(link) and os.readlink(link) == os.path.join(rec, "segments", "000000001", "pump")
    assert open(os.path.join(link, "TradeEvent", "part-00000.parquet")).read() == "rows of 1"
    assert json.load(open(os.path.join(rec, "segments", "000000003", "_segment.json")))["seq"] == 3
    assert all(m["complete"] for m in segs.values())


def test_an_incomplete_segment_keeps_its_place_and_is_never_copied(tmp_path):
    rec = _rec(tmp_path, incomplete=(4,))
    res = restart_root.make(rec, str(tmp_path / "a"), at=1, n=2)
    assert not _read(str(tmp_path / "a"))["000000006"]["complete"] and res["segments"] == 7
    with pytest.raises(SystemExit, match="segment 4 is not there once and complete"):
        restart_root.make(rec, str(tmp_path / "b"), at=3, n=2)
    with pytest.raises(SystemExit, match="segment 5 is not there"):
        restart_root.make(rec, str(tmp_path / "c"), at=5, n=1)


def test_an_out_that_is_not_empty_or_inside_the_recorder_is_refused(tmp_path):
    rec = _rec(tmp_path)
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("x")
    with pytest.raises(SystemExit, match="not empty"):
        restart_root.make(rec, str(tmp_path / "full"), at=1)
    with pytest.raises(SystemExit, match="inside"):
        restart_root.make(rec, os.path.join(rec, "restart"), at=1)
    restart_root.main(["--root", rec, "--out", str(tmp_path / "ok"), "--at", "0", "--n", "1"])
    assert len(_read(str(tmp_path / "ok"))) == 6


def test_a_later_start_links_the_segments_from_a_seq_with_their_own_seqs(tmp_path):
    # M3-10's seed step: the segments from seq 2 on, as they are; refused from a seq that is not there or not complete
    rec = _rec(tmp_path, incomplete=(1,))
    res = restart_root.link(rec, str(tmp_path / "later"), 2)
    assert res == {"segments": 3, "complete": 3, "first_slot": 1200}
    assert {int(k): m["seq"] for k, m in _read(str(tmp_path / "later")).items()} == {2: 2, 3: 3, 4: 4}
    with pytest.raises(SystemExit, match="segment 1 is not there and complete"):
        restart_root.link(rec, str(tmp_path / "x"), 1)
    with pytest.raises(SystemExit, match="segment 9 is not there"):
        restart_root.link(rec, str(tmp_path / "y"), 9)
    restart_root.main(["--root", rec, "--out", str(tmp_path / "cli"), "--from-seq", "3"])
    assert sorted(_read(str(tmp_path / "cli"))) == ["000000003", "000000004"]
    with pytest.raises(SystemExit):
        restart_root.main(["--root", rec, "--out", str(tmp_path / "z"), "--from-seq", "3", "--at", "3"])
