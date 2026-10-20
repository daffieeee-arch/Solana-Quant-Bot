"""lab/live/m3_09b_accept.py: M3-09b's acceptance on summaries (spec v0.2 §4.2 and §4.3), on hand-made summaries."""

import copy
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("m3_09b_accept", os.path.join(HERE, "..", "..", "live", "m3_09b_accept.py"))
acc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acc)

STATS = {"segments": 4, "late_rows": 0, "duplicate_rows": 0, "backfill_rows": 6, "backfill_duplicates": 1, "tokens_dropped": 0,
         "seeded_pools": 2, "farmer_flags_fixed": 1, "unknown_pools": 3, "unknown_pool_rows": 7, "unknown_pool_other_rows": 1, "unknown_mints": 2,
         "unknown_mint_rows": 5}


def summary(**kw):
    """LiveFeed's summary of a run of 4 segments and 10 events in 2 windows."""
    d = lambda n, s: {"count": n, "sha256": s}  # noqa: E731
    v = {"events": 10, "kinds": {"curve": 4, "pool/buy": 6}, "learned": d(3, "l"), "new_pools": d(2, "p"), "usage": d(8, "u"),
         "arrivals": d(10, "a"), "history": d(6, "h"), "history_no_farmer": d(6, "hn"), "flag_fixes": d(1, "f"), "sha256": "s",
         "sha256_no_farmer": "n", "clock": {"per_epoch": {"1054": 400.5}, "h24": [[1, 500]]},
         "farm_seed": {"lo": 1, "hi": 2, "h24": 500, "first_live_slot": 2, "complete_from": 2}, "snapshot_before": 1, "h24": 500,
         "windows": [[0, 100, 4, "w0", "n0"], [100, 200, 6, "w1", "n1"]], "stats": dict(STATS)}
    v.update(kw)
    return v


def lab_live(v, **extra):
    """lab-live's summary of the same run (M3-10: the farmer flags LiveFeed's), with its own measurements."""
    r = copy.deepcopy(v)
    r.update({"lab_live": {"peak_rss_bytes": 1 << 20, "mem_max": {"heap": [1, 2]}}})
    r.update(extra)
    return r


def test_the_strict_tier_is_green_on_equal_summaries_and_names_each_difference():
    py = summary()
    rs = lab_live(py)
    red, info = acc.strict(py, rs, lab_live(py, lab_live={"peak_rss_bytes": 7}), 4, 10)
    assert red == [] and any("flag_fixes 1, farmer_flags_fixed 1" in i for i in info)
    # M3-10: the farmer bit is gated: another flag in a window, the history with it, a fix
    red, _ = acc.strict(py, lab_live(py, windows=[[0, 100, 4, "x", "n0"], [100, 200, 6, "w1", "n1"]], sha256="x"),
                        lab_live(py, windows=[[0, 100, 4, "x", "n0"], [100, 200, 6, "w1", "n1"]], sha256="x"), 4, 10)
    assert any(r.startswith("sha256:") for r in red) and any("1 windows differ" in r and "[0]" in r for r in red)
    fixes = lab_live(py, flag_fixes={"count": 0, "sha256": "e"})
    fixes["stats"] = dict(STATS, farmer_flags_fixed=0)
    red, _ = acc.strict(py, fixes, fixes, 4, 10)
    assert any(r.startswith("flag_fixes:") for r in red) and any("farmer_flags_fixed" in r for r in red)
    bad = lab_live(py, windows=[[0, 100, 4, "w0", "n0"], [100, 200, 6, "w1", "x"]], learned={"count": 3, "sha256": "x"})
    bad["stats"] = dict(STATS, backfill_duplicates=2)
    red, _ = acc.strict(py, bad, bad, 4, 10)
    assert any(r.startswith("learned:") for r in red) and any("stats backfill_duplicates" in r for r in red)
    assert any("1 windows differ" in r and "[100]" in r for r in red)
    # two lab-live runs that differ outside their own measurements; the input counts are hard
    red, _ = acc.strict(py, rs, lab_live(py, events=11), 4, 10)
    assert red == ["the two lab-live runs differ in events"]
    red, _ = acc.strict(py, rs, rs, 3145, 16403207)
    assert len(red) == 4 and all("not 3145" in r or "not 16,403,207" in r for r in red)


def test_a_livefeed_from_before_430_is_compared_with_99zzts_counters():
    old = {k: v for k, v in STATS.items() if k not in ("unknown_pool_other_rows", "unknown_mints", "unknown_mint_rows")}
    py = summary(stats=dict(old, unknown_pools=2))
    rs = lab_live(py)
    rs["stats"] = dict(old, unknown_pools=3, unknown_pool_other_rows=2, unknown_mint_rows=12127, unknown_mints=9)
    assert acc.strict(py, rs, rs, 4, 10)[0] == []
    rs["stats"]["unknown_pools"] = 2
    assert acc.strict(py, rs, rs, 4, 10)[0] == ["stats unknown_pools: lab-live 2, 99zzt (#430) 3"]


def restarted(v, n=1, dups=4, usage=3):
    """A summary of the same run on the injected restart: N segments more, their rows duplicates or counted again, the
    learned rows after them with another seq, their usage rows more."""
    r = copy.deepcopy(v)
    r["learned"] = {"count": v["learned"]["count"], "sha256": "l-seq"}
    r["usage"] = {"count": v["usage"]["count"] + usage, "sha256": "u2"}
    r["stats"].update(segments=v["stats"]["segments"] + n, duplicate_rows=v["stats"]["duplicate_rows"] + dups,
                      backfill_duplicates=v["stats"]["backfill_duplicates"] + 2, unknown_pool_rows=v["stats"]["unknown_pool_rows"] + 5)
    return r


def test_the_restart_equals_the_plain_run_but_for_the_copies():
    plain = summary()
    py, rs = restarted(plain), lab_live(restarted(plain))
    red, info = acc.restart(plain, py, rs, 1, 3)
    assert red == [] and "differs (seqs after the copies)" in info[-1]
    # no duplicate counted, another window, a usage count that is not the copies', a history row more
    for change, why in ((lambda v: v["stats"].update(duplicate_rows=0), "duplicate_rows 0, not above"),
                        (lambda v: v["windows"][0].__setitem__(4, "x"), "windows differ"),
                        (lambda v: v["usage"].update(count=99), "usage rows"),
                        (lambda v: v["stats"].update(backfill_rows=7), "backfill_rows 7"),
                        # spec v0.4: the key counts and the seeded pools equal, not at least as many
                        (lambda v: v["stats"].update(unknown_pools=4), "unknown_pools 4, the plain run 3"),
                        (lambda v: v["stats"].update(unknown_mints=3), "unknown_mints 3, the plain run 2"),
                        (lambda v: v["stats"].update(seeded_pools=3), "seeded_pools 3, the plain run 2")):
        bad = lab_live(restarted(plain))
        change(bad)
        red, _ = acc.restart(plain, py, bad, 1, 3)
        assert len(red) == 1 and why in red[0] and red[0].startswith("lab-live on the restart"), (why, red)
    bad = restarted(plain)
    bad["sha256"] = "other"
    red = acc.restart(plain, bad, rs, 1, 3)[0]
    assert len(red) == 1 and red[0].startswith("LiveFeed on the restart: sha256")


def test_main_reads_the_runs_and_the_copies_usage(tmp_path, capsys):
    out, root = tmp_path / "out", tmp_path / "root"
    plain = summary()
    for name, v in (("py", plain), ("rs1", lab_live(plain)), ("rs2", lab_live(plain)), ("restart-py", restarted(plain)),
                    ("restart-rs1", lab_live(restarted(plain))), ("restart-rs2", lab_live(restarted(plain)))):
        (out / name).mkdir(parents=True)
        (out / name / "summary.json").write_text(json.dumps(v))
    for seq, rows in ((7, 1), (8, 3)):  # AT 7, N 1: the copy is seq 8
        d = root / "segments" / f"{seq:09d}"
        d.mkdir(parents=True)
        (d / "_segment.json").write_text(json.dumps({"seq": seq, "usage": [{"minute": i} for i in range(rows)]}))
    assert acc.main(["compare", str(out), "4", "10"]) == 0
    assert "M3-09b: GREEN" in capsys.readouterr().out
    assert acc.main(["restart", str(out), "5", "10", str(root), "7", "1"]) == 0
    assert "M3-09b restart: GREEN" in capsys.readouterr().out
    assert acc.main(["restart", str(out), "5", "10", str(root), "6", "1"]) == 1  # the copy would be seq 7: 1 usage row, not 3
    assert "usage rows" in capsys.readouterr().out


def test_the_seed_step_needs_a_window_with_slots_and_seeded_pools(tmp_path, capsys):
    # M3-10 spec v0.2 §6.2: compare's rules on seed-*, EVENTS `-` is LiveFeed's count; an empty window or no pool seeded
    # means the seed was not exercised: RED
    out = tmp_path / "out"
    plain = summary()
    for name, v in (("seed-py", plain), ("seed-rs1", lab_live(plain)), ("seed-rs2", lab_live(plain))):
        (out / name).mkdir(parents=True)
        (out / name / "summary.json").write_text(json.dumps(v))
    assert acc.main(["seed", str(out), "4", "-"]) == 0
    assert "M3-10 seed: GREEN" in capsys.readouterr().out
    empty = summary(farm_seed={"lo": 2, "hi": 2, "h24": 500, "first_live_slot": 2, "complete_from": 502})
    for name, v in (("seed-py", empty), ("seed-rs1", lab_live(empty)), ("seed-rs2", lab_live(empty))):
        (out / name / "summary.json").write_text(json.dumps(v))
    assert acc.main(["seed", str(out), "4", "-"]) == 1
    assert "the seed was not exercised" in capsys.readouterr().out
