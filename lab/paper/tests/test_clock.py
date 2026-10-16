"""K3's stop-rule clock: the start is fixed in the journal state with a reason, before it counts anything, and the
daily report's stop-rule counter and capital simulation count the trades entered from that day (coordinator,
2026-10-16; AGENTS.md stop rule)."""

import json
import os
from datetime import datetime, timezone

import pytest

from paper import clock
from paper.journal import Journal
from paper.report import CLOCK_LATEST, daily_report

SOL = 1_000_000_000
NOW = datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc)
GROUPS = {"K3": {"primary": True, "variants": ["tp4_T600"]}, "H": {"primary": False, "variants": ["flow300_H6"]}}


def _journal(tmp_path):
    j = Journal(str(tmp_path / "j"))
    j.state("groups", json.dumps(GROUPS))
    j.commit()
    return j


def _trade(j, variant, t, entry_day, exit_day, pnl, tokens=5):
    j.put("orders", family="F7", variant=variant, key=f"P{t}", t=t, day=entry_day, entry_slot=t + 3, size=SOL // 2,
          status="filled" if tokens else "failed")
    j.put("exits", family="F7", variant=variant, key=f"P{t}", t=t, day=exit_day, entry_slot=t + 3, exit_decision_slot=t + 50,
          exit_slot=t + 52 if tokens else None, exit_reason="time" if tokens else "entry_failed", tokens=tokens,
          cost=SOL // 2, proceeds=SOL // 2 + pnl if tokens else 0, pnl=pnl, ret=pnl / (SOL // 2), exit_attempts=1)


def test_the_start_is_fixed_once_with_a_reason(tmp_path):
    j = _journal(tmp_path)
    for day, reason, msg in (("26-10-2026", "x", "YYYY-MM-DD"), ("2026-10-26", " ", "reason"),
                             ("2026-11-03", "x", CLOCK_LATEST), ("2026-10-21", "x", "in the past")):
        with pytest.raises(ValueError, match=msg):
            clock.set_clock(j, day, reason, now=NOW)
    assert j.state("clock_start") is None
    rec = clock.set_clock(j, "2026-10-26", "  the owner's target:\n Monday ", now=NOW)
    assert rec == {"date": "2026-10-26", "reason": "the owner's target: Monday", "set_at": "2026-10-22T09:30:00+00:00"}
    assert json.loads(j.state("clock_start")) == rec
    with pytest.raises(ValueError, match="set already"):
        clock.set_clock(j, "2026-10-27", "y", now=NOW)


def test_a_start_moves_only_before_its_day_and_keeps_the_earlier_one(tmp_path):
    j = _journal(tmp_path)
    first = clock.set_clock(j, "2026-10-26", "target", now=NOW)
    moved = clock.set_clock(j, "2026-10-28", "recorder fix first", now=NOW, replace=True)
    assert moved["date"] == "2026-10-28" and moved["previous"] == [first]
    on_the_day = datetime(2026, 10, 28, 0, 5, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="started on 2026-10-28: it stays"):
        clock.set_clock(j, "2026-10-30", "later", now=on_the_day, replace=True)


def test_no_start_on_a_day_with_a_primary_entry_already(tmp_path):
    j = _journal(tmp_path)
    _trade(j, "tp4_T600", 100, "2026-10-22", "2026-10-22", SOL // 10)
    _trade(j, "flow300_H6", 200, "2026-10-23", "2026-10-23", SOL // 10)  # the arms do not count
    with pytest.raises(ValueError, match="1 primary entries on or after 2026-10-22"):
        clock.set_clock(j, "2026-10-22", "today", now=NOW)
    assert clock.set_clock(j, "2026-10-23", "tomorrow", now=NOW)["date"] == "2026-10-23"


def test_the_report_counts_the_trades_entered_from_the_clock_start(tmp_path):
    j = _journal(tmp_path)
    _trade(j, "tp4_T600", 100, "2026-10-25", "2026-10-25", -SOL // 10)  # before the clock
    _trade(j, "tp4_T600", 110, "2026-10-25", "2026-10-26", -SOL // 10)  # entered before, sold after: not counted
    _trade(j, "tp4_T600", 120, "2026-10-26", "2026-10-26", SOL // 5)
    _trade(j, "tp4_T600", 130, "2026-10-26", "2026-10-26", -505_000, tokens=0)  # a failed entry: its cost, no trade
    _trade(j, "flow300_H6", 140, "2026-10-26", "2026-10-26", SOL)  # group H: no part of the stop rule
    j.put("heartbeat", minute=1, day="2026-10-26", last_slot=200, events=3, open_positions=0, gap_slots=0, note="")

    daily_report(j, "2026-10-26")
    md = open(os.path.join(j.dir, "2026-10-26", "report.md")).read()
    assert "## Stop-rule counter (all paper days so far; tp4_T600 only)" in md
    assert "- no clock start in the journal yet (python -m paper.clock): a count of every paper day, no decision" in md
    assert "- trades 3 (failed entries 1, their cost included), days 2," in md

    j.state("clock_start", json.dumps({"date": "2026-10-26", "reason": "the owner's target", "set_at": "2026-10-22T09:30:00+00:00"}))
    for day, now in (("2026-10-25", "not started, nothing counted yet"), ("2026-10-26", "- clock day 1, 1 trades: no verdict yet (the verdict from 200 filled trades and 5 trading days on")):
        daily_report(j, day)
        md = open(os.path.join(j.dir, day, "report.md")).read()
        assert "## Stop-rule counter (from the clock start 2026-10-26; tp4_T600 only)" in md
        assert "- clock start 2026-10-26 (set 2026-10-22T09:30:00+00:00; reason: the owner's target)" in md and now in md
        assert "## Capital simulation (10 SOL, at most 5 positions; from the clock start 2026-10-26)" in md
    assert "- trades 1 (failed entries 1, their cost included), days 1, net +0.1995 SOL/trade" in md
    assert "- final 10.199 SOL" in md  # 10 + 0.2 - 0.000505: the capital simulation from the clock start too


def test_the_verdict_shows_once_it_is_there_and_the_clock_ends_at_six_weeks(tmp_path):
    """Owner, 2026-10-16: the four weeks no longer hold a verdict back; it shows from 200 filled trades and 5 trading
    days on (an early GO is no error); the clock ends at day 42."""
    from paper.report import clock_lines

    c = {"date": "2026-10-26", "reason": "r", "set_at": "s"}
    assert "clock day 10, 150 trades: no verdict yet" in clock_lines(c, "2026-11-04", 150, "too few trades")[1]
    assert "clock day 10, 210 trades: the verdict is there: **GO**" in clock_lines(c, "2026-11-04", 210, "GO")[1]
    assert "clock day 30, 210 trades: the verdict is there: **ADJUST**" in clock_lines(c, "2026-11-24", 210, "ADJUST")[1]
    assert "clock day 30, 150 trades: no verdict yet" in clock_lines(c, "2026-11-24", 150, "too few trades")[1]
    assert "clock day 42: the 42-day limit, the decision is due: **too few trades**" in clock_lines(
        c, "2026-12-06", 150, "too few trades")[1]


def test_the_command_sets_and_shows_the_clock(tmp_path, capsys):
    with pytest.raises(SystemExit, match="no journal.sqlite"):
        clock.main(["--journal", str(tmp_path / "none")])
    j = _journal(tmp_path)
    j.close()
    clock.main(["--journal", j.dir])
    assert "no clock start set" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="refused: start 2026-01-05 is in the past"):
        clock.main(["--journal", j.dir, "--start", "2026-01-05", "--reason", "x"])
    clock.main(["--journal", j.dir, "--start", CLOCK_LATEST, "--reason", "the latest day"], now=NOW)
    assert json.loads(capsys.readouterr().out)["date"] == CLOCK_LATEST
    clock.main(["--journal", j.dir])
    assert json.loads(capsys.readouterr().out)["reason"] == "the latest day"


def test_the_command_waits_for_a_running_engines_commit(tmp_path, capsys):
    import sqlite3
    import threading
    import time

    j = _journal(tmp_path)
    j.close()
    locked = threading.Event()

    def engine():  # the engine holds the write lock from its first write until its minute's commit
        con = sqlite3.connect(os.path.join(j.dir, "journal.sqlite"))
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO heartbeat (minute, day, note) VALUES (1, '2026-10-22', '')")
        locked.set()
        time.sleep(6)  # longer than sqlite's default 5 s timeout
        con.commit()
        con.close()

    t = threading.Thread(target=engine)
    t.start()
    locked.wait()
    clock.main(["--journal", j.dir, "--start", "2026-10-26", "--reason", "target"], now=NOW)
    t.join()
    assert json.loads(capsys.readouterr().out)["date"] == "2026-10-26"
    j = Journal(j.dir)
    assert len(j.rows("heartbeat")) == 1 and clock.clock_of(j)["reason"] == "target"
