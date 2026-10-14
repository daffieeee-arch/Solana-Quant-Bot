"""V_ins recompute comparison on two synthetic runs: flipped vetoes per family and period, triggers only in one run,
the verdicts and Δ rows from m1x.json, and the fallbacks counter."""

import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import vins_compare as V


def _run(path, commit, shares, verdict, fallbacks=0, reasons=False):
    os.makedirs(path / "parts")
    rows = []
    for i, (fam, day, share) in enumerate(shares):
        for overlay in ("none", "V_ins"):
            for d in (1, 2):
                rows.append({"family": fam, "variant": "k15_trail20", "mint": f"M{i}", "t": 100 + i, "day": day, "overlay": overlay, "size_sol": 0.5,
                             "d": d, "skipped": False, "ins_share": share})
                if reasons:  # a fail-closed engine (vins-definition-2026-10-15.md)
                    rows[-1]["ins_reason"] = "h_ins_gt_circ" if share is None else None
    pq.write_table(pa.Table.from_pylist(rows), path / "parts" / "part-0.parquet")
    json.dump({"engine_commit": commit, "insider_fallbacks": fallbacks}, open(path / "config.json", "w"))
    json.dump({"families": {"M1P": {"verdict": verdict, "vetoes": [
        {"period": "days 10-18", "family": "M1P", "veto": "V_ins", "size": 0.5, "d": 1, "pairs": 100, "veto_share": 0.1, "delta_sol": -0.12, "lo": -0.2, "hi": -0.03},
        {"period": "days 10-18", "family": "M1P", "veto": "V_ins", "size": 1.0, "d": 1, "pairs": 100, "veto_share": 0.1, "delta_sol": -0.13, "lo": -0.25, "hi": 0.0}]}}},
              open(path / "m1x.json", "w"))


def test_flips_and_render(tmp_path):
    old = [("M1P", "2026-09-12", 0.95), ("M1P", "2026-09-22", 0.95), ("M1P", "2026-09-23", 0.5), ("M1E", "2026-09-25", None), ("M1P", "2026-09-26", 0.3)]
    new = [("M1P", "2026-09-12", 0.95), ("M1P", "2026-09-22", 0.6), ("M1P", "2026-09-23", 0.92), ("M1E", "2026-09-25", 0.2), ("M1P", "2026-09-26", 0.3),
           ("M1P", "2026-09-27", 0.99)]
    _run(tmp_path / "old", "453d44e", old, "not useful")
    _run(tmp_path / "new", "0b9304e", new, "not useful", fallbacks=0)
    con = duckdb.connect()
    assert V.load_triggers(con, "old", str(tmp_path / "old")) == 5 and V.load_triggers(con, "new", str(tmp_path / "new")) == 6
    rows = {(r[0], r[1]): r for r in V.flips(con)}
    r = rows[("M1P", "days 10-18")]
    assert r[2] == 3 and r[3] == 3 and r[4] == 1 and r[5] == 1  # one veto dropped (0.95 -> 0.6), one added (0.5 -> 0.92)
    assert rows[("M1P", "days 1-9")][4] == 0 and rows[("M1P", "days 1-9")][5] == 0
    assert rows[("M1E", "days 10-18")][8] == 1 and rows[("M1E", "days 10-18")][9] == 0  # unknown share resolved
    assert V.only_new(con) == [("M1P", 1)]
    assert V.fallbacks(str(tmp_path / "new")) == ("config.json", 0)
    md = V.render(str(tmp_path / "old"), str(tmp_path / "new"), 5, 6, V.flips(con), V.only_new(con), V.verdicts(str(tmp_path / "old")), V.verdicts(str(tmp_path / "new")),
                  V.fallbacks(str(tmp_path / "new")))
    assert "| M1P | days 10-18 | 3 | 3 | 1 | 1 | **1** | **1** |" in md and "**0** — must be 0" in md
    assert "| old | M1P | V_ins | 1 | 100 | 10% | -0.1200 | -0.2000 … -0.0300 |" in md and "| M1P | not useful | not useful |" in md
    assert "Triggers only in the new run: M1P: 1." in md and "O7 overlay on F7 pool_reversed stays provisional" in md


def test_each_run_is_read_as_its_engine_applied_v_ins(tmp_path):
    """An old (fail-open) run let a NULL share pass; a fail-closed run (ins_reason) vetoes it. A trigger
    with a NULL share in both runs is a veto added, not hidden by reading the old run fail-closed too."""
    shares = [("M1P", "2026-09-22", None), ("M1P", "2026-09-23", 0.95)]
    _run(tmp_path / "old", "0e0cad0", shares, "not useful")
    _run(tmp_path / "new", "cf7719f", shares, "not useful", reasons=True)
    con = duckdb.connect()
    V.load_triggers(con, "old", str(tmp_path / "old"))
    V.load_triggers(con, "new", str(tmp_path / "new"))
    r = {(x[0], x[1]): x for x in V.flips(con)}[("M1P", "days 10-18")]
    assert (r[4], r[5], r[6], r[7]) == (0, 1, 1, 2)  # dropped 0, added 1 (the NULL), old vetoed 1, new vetoed 2
    assert V.SEMANTICS == {"old": "fail-open (a NULL share passed V_ins)", "new": "fail-closed (an undefined share vetoes)"}


if __name__ == "__main__":
    pytest.main([__file__])
