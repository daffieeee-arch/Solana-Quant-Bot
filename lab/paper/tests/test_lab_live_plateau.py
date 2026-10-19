"""lab/live/plateau.py, C4's plateau gate (M1 C4-3; spec research/m1-c4-plateau-spec-2026-10-19.md v0.4 §2), on
synthetic --mem-log files in memlog.rs's form."""

import importlib.util
import json
import math
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("plateau", os.path.join(HERE, "..", "..", "live", "plateau.py"))
plateau = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plateau)

MB, GB = 10**6, 10**9
M0 = 29_300_000  # a unix minute (2025)
RUN = {"p1_journal_sha": "a" * 64, "p2_journal_sha": "a" * 64, "wallets_equal": True, "exit_codes": {"p1": 0, "p2": 0, "p3": 0},
       "segments_read": 3145, "segments_expected": 3145, "incomplete_skipped": [], "p1_secs": 600.0}


def log(path, hours=52, rss_day=0.0, heap_day=0.0, keys_day=0.0, diurnal=0.3, jitter=5 * MB, busy_day=0.0):
    """A --mem-log of `hours` of chain time: RSS 3 GB, heap 2.5 GB and interned_keys 5 M (64 B each), each growing by its
    per-day amount; recent_keys (windowed) swinging +-diurnal over the day; busy, a small insert-only map (40 B an entry),
    growing by busy_day entries a day; wallet_txs filling up to its cap of 100; every minute 100 events of 1-3 us."""
    with open(path, "w") as f:
        events = 0
        for i in range(hours * 60):
            day = i / 1440
            wave = 1 + diurnal * math.sin(2 * math.pi * day)
            noise = jitter * math.sin(i * 7.3)
            keys = int(5_000_000 + keys_day * day)
            busy = int(200 + busy_day * day)
            structs = {
                "recent_keys": {"entries": int(4000 * wave), "bytes_est": 100_000, "group": "state"},
                "interned_keys": {"entries": keys, "bytes_est": 64 * keys, "group": "snapshot"},
                "run_curve_sums": {"entries": 1_000_000, "bytes_est": 50 * MB, "group": "state"},
                "busy": {"entries": busy, "bytes_est": 40 * busy, "group": "state"},
                "wallet_txs": {"entries": min(100, i // 20), "bytes_est": 200 * min(100, i // 20), "group": "state"},
                "ops": {"entries": 0, "bytes_est": 0, "group": "state"},
            }
            events += 100
            f.write(json.dumps({"minute": M0 + i, "slot": 454_896_000 + i * 275, "events": events, "segments": i // 1,
                                "rss_bytes": int(3 * GB + rss_day * day + noise), "heap_bytes": int(2.5 * GB + heap_day * day + noise),
                                "heap_allocs": 1000, "structures": structs,
                                "time_ns": {"n": 100, "p50": 1500, "p90": 2500, "p99": 2900, "p999": 2900, "max": 3000,
                                            "buckets": [[1472, 50], [2496, 49], [2944, 1]]}}) + "\n")
        f.write(json.dumps({"minute": M0 + hours * 60 - 1, "end": True, "slot": 0, "events": events, "segments": None,
                            "rss_bytes": 3 * GB, "heap_bytes": int(2.5 * GB), "heap_allocs": 1000, "structures": structs,
                            "time_ns": {"n": events, "p50": 1500, "p90": 2500, "p99": 2900, "p999": 2900, "max": 3000, "buckets": []}}) + "\n")
    return path


def failing(res):
    return [c["check"] for c in res["checks"] if not c["ok"]]


def test_a_flat_run_is_green_and_the_windowed_swing_is_reported_only(tmp_path):
    res = plateau.evaluate(plateau.load(log(tmp_path / "p1.jsonl", diurnal=-0.3)), RUN)
    assert res["ok"], failing(res)
    assert res["last"] - res["start"] + 1 == 1440 and (res["start"] - res["first"]) / 60 == 28.0
    rk = res["structures"]["recent_keys"]
    # a 30 % daily swing that rises over the window is above the 10 % entries test (slope and last 2 h); v0.4 reports it
    # without gating it, with its max ratio (the window's max over the 24 h before it: the same swing, so 1)
    assert rk["windowed"] and not rk["gated"] and rk["slope_day"] > 0.1 * rk["mean"] and rk["last2"] > 1.1 * rk["first2"]
    assert rk["max_ratio"] == 1.0
    assert res["structures"]["interned_keys"]["gated"] and res["structures"]["interned_keys"]["ok"]


def test_rss_and_heap_growth_fail_their_gates(tmp_path):
    res = plateau.evaluate(plateau.load(log(tmp_path / "p1.jsonl", rss_day=200 * MB, heap_day=50 * MB)), RUN)
    assert failing(res) == ["RSS slope (MB per 24 h)", "RSS last 2 h - first 2 h (MB)"]
    res = plateau.evaluate(plateau.load(log(tmp_path / "p1b.jsonl", heap_day=120 * MB)), RUN)
    assert failing(res) == ["heap slope (MB per 24 h)"]  # 22 h apart: +110 MB, under 150
    res = plateau.evaluate(plateau.load(log(tmp_path / "p1c.jsonl", rss_day=4 * GB)), RUN)
    assert "RSS max of the run (GB)" in failing(res)  # 3 GB + 52 h of 4 GB a day passes 6 GB


def test_a_growing_cumulative_structure_fails(tmp_path):
    # interned_keys + 600 k a day: 12 % of its ~5.9 M mean per 24 h, and 38 MB a day
    res = plateau.evaluate(plateau.load(log(tmp_path / "p1.jsonl", keys_day=600_000)), RUN)
    assert failing(res) == ["structures gated: failing"]
    assert next(c for c in res["checks"] if c["check"] == "structures gated: failing")["value"] == ["interned_keys"]
    assert res["structures"]["interned_keys"]["bytes_growth"] > 30 * MB
    res = plateau.evaluate(plateau.load(log(tmp_path / "p1b.jsonl", keys_day=400_000)), RUN)  # ~7 %: passes
    assert res["ok"], failing(res)


def test_the_1_mb_floor_and_the_bounded_wallets(tmp_path):
    # spec v0.5 (E7): busy doubles in a day (200 -> 400 entries, +100 % of its mean) but grows by only 8 KB: it passes
    # the floor; wallet_txs fills up to its cap in the window and is reported, not gated
    res = plateau.evaluate(plateau.load(log(tmp_path / "p1.jsonl", busy_day=200)), RUN)
    assert res["ok"], failing(res)
    busy, wallets = res["structures"]["busy"], res["structures"]["wallet_txs"]
    assert busy["gated"] and busy["ok"] and busy["slope_day"] > 0.1 * busy["mean"] and busy["bytes_growth"] < plateau.STRUCT_FLOOR
    assert wallets["windowed"] and not wallets["gated"]
    # the same growth at 1 MB or more fails: 30,000 entries of 40 B a day is 1.2 MB
    res = plateau.evaluate(plateau.load(log(tmp_path / "p1b.jsonl", busy_day=30_000)), RUN)
    assert next(c for c in res["checks"] if c["check"] == "structures gated: failing")["value"] == ["busy"]


def test_short_input_missing_facts_and_nondeterminism_are_red(tmp_path):
    res = plateau.evaluate(plateau.load(log(tmp_path / "p1.jsonl", hours=40)), RUN)
    assert failing(res) == ["warm-up before the window (h)"]
    p1 = plateau.load(log(tmp_path / "p1b.jsonl"))
    assert failing(plateau.evaluate(p1, None)) == ["run facts (run.json)"]
    assert failing(plateau.evaluate(p1, {**RUN, "p2_journal_sha": "b" * 64})) == ["determinism: P1 and P2 journal sha256"]
    assert failing(plateau.evaluate(p1, {**RUN, "wallets_equal": False})) == ["determinism: wallet files"]
    assert failing(plateau.evaluate(p1, {**RUN, "exit_codes": {"p1": 0, "p2": 101}})) == ["the runs' exit codes"]
    assert failing(plateau.evaluate(p1, {**RUN, "segments_read": 3140})) == ["segments read"]
    assert plateau.evaluate(p1, {**RUN, "segments_read": 3140, "incomplete_skipped": ["1", "2", "3", "4", "5"]})["ok"]


def test_the_quantiles_follow_memlog_buckets():
    # memlog.rs: one bucket per ns below 64, then 32 per power of two; a quantile is its bucket's top, at most the max
    assert [plateau.bucket_top(x) for x in (5, 63, 64, 90, 126, 1472, 2944)] == [5, 63, 65, 91, 127, 1503, 3007]
    q = plateau.quantiles({x: 1 for x in range(1, 64)} | {64: 2, 90: 2, 98: 2}, 100)
    assert (q["n"], q["p50"], q["p90"], q["p99"], q["max"]) == (69, 35, 63, 99, 100)
    assert plateau.quantiles({}, 0)["p99"] == 0
    assert plateau.slope([0, 1, 2], [1, 3, 5]) == 2 and plateau.slope([1], [1]) == 0


def test_the_cli_writes_the_report_and_one_line(tmp_path, capsys):
    p1 = log(tmp_path / "p1.jsonl")
    p3 = log(tmp_path / "p3.jsonl", hours=52, rss_day=0)
    (tmp_path / "run.json").write_text(json.dumps(RUN))
    out = tmp_path / "plateau.md"
    assert plateau.main(["--p1", str(p1), "--p2", str(p1), "--p3", str(p3), "--run", str(tmp_path / "run.json"),
                         "--spec-sha", "f" * 64, "--out", str(out)]) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("lab-live plateau minutes [") and line.endswith("GREEN") and "replay, not live latency" in line
    md = out.read_text()
    for part in ("# lab-live plateau (M1 C4-3): GREEN", "f" * 64, "| recent_keys | state | yes |  |", "max / warm-up max",
                 "not live latency", "## P3 (the chunks, the upper bound) (reported)", "Events per second"):
        assert part in md, part
    assert plateau.main(["--p1", str(tmp_path / "none.jsonl"), "--spec-sha", "x", "--out", str(out)]) == 2


@pytest.mark.parametrize("name", sorted(plateau.WINDOWED))
def test_the_windowed_names_are_memlog_structures(name):
    # every name the gate treats as windowed is one lab-core or the replay reports (memlog.rs `group` puts them in state
    # or legs_buffer); a typo would gate a windowed structure silently
    core = open(os.path.join(HERE, "..", "..", "core", "src", "signals.rs")).read() + open(
        os.path.join(HERE, "..", "..", "core", "src", "paper.rs")).read() + open(
        os.path.join(HERE, "..", "..", "core", "src", "f7.rs")).read() + open(
        os.path.join(HERE, "..", "..", "core", "src", "farm.rs")).read() + open(
        os.path.join(HERE, "..", "..", "core", "src", "n2.rs")).read()
    live = os.path.join(HERE, "..", "..", "live", "src", "legs_read.rs")
    assert f'"{name}"' in core + open(live).read(), name


def test_the_journal_sha_ignores_the_runs_summary_and_row_order(tmp_path, capsys):
    import sqlite3

    def journal(d, rows, summary):
        d.mkdir()
        db = sqlite3.connect(d / "journal.sqlite")
        db.execute("CREATE TABLE signals (key TEXT, t INTEGER)")
        db.execute("CREATE TABLE state (k TEXT PRIMARY KEY, v TEXT)")
        db.executemany("INSERT INTO signals VALUES (?, ?)", rows)
        db.executemany("INSERT INTO state VALUES (?, ?)", [("groups", "g"), ("lab_live_paper", summary)])
        db.commit()
        db.close()
        return d

    a = journal(tmp_path / "a", [("k1", 1), ("k2", 2)], '{"secs": 1.0}')
    b = journal(tmp_path / "b", [("k2", 2), ("k1", 1)], '{"secs": 2.5}')  # another order, another summary
    c = journal(tmp_path / "c", [("k1", 1), ("k2", 3)], '{"secs": 1.0}')  # one row differs
    assert plateau.journal_sha(a) == plateau.journal_sha(b) != plateau.journal_sha(c)
    assert plateau.main(["--journal-sha", str(a)]) == 0 and capsys.readouterr().out.strip() == plateau.journal_sha(a)
    assert plateau.main(["--journal-sha", str(tmp_path / "none")]) == 2
