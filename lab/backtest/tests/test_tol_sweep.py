"""The entry tolerance sweep (backtest.tol_sweep, E70): the runs' grid check (T2), the rows per tol, E63's own cells at
tol 0.05 (T1), the drain and exit-retry shares and the paired difference against tol 0.05."""

import json
import os

import duckdb
import pytest

from backtest import capital_ceiling as cc
from backtest import tol_sweep as ts

LO = cc.DEV[0]
COLS = ("family", "segment", "variant", "q", "overlay", "tau", "size_sol", "d", "t", "day", "pool", "farm", "skipped",
        "entry_failed", "cost", "proceeds", "exit_slot", "exit_decision_slot", "exit_attempts", "d_ms", "depth")
TYPES = ("VARCHAR", "VARCHAR", "VARCHAR", "DOUBLE", "VARCHAR", "VARCHAR", "DOUBLE", "INTEGER", "UBIGINT", "VARCHAR", "VARCHAR",
         "VARCHAR", "BOOLEAN", "BOOLEAN", "BIGINT", "BIGINT", "UBIGINT", "UBIGINT", "INTEGER", "DOUBLE", "DOUBLE")
DAYS = ("2026-09-12", "2026-09-12", "2026-09-13", "2026-09-14")
POOLS = ("A", "B", "C", "D")


def write_run(root, name, tol, rows, **cfg):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "parts"))
    base = {"engine_commit": "abc", "store": {"slot_start": cc.DEV[0], "slot_end_exclusive": cc.DEV[1], "git_commit": "s"},
            "families": ["F7"], "segments": ["pool_reversed"], "f7_grid": "v1,exits,big", "tau": ["empirical"], "q": [0.5],
            "variants": ["tp4_T600", "hold300", "flow300_tp100_H6"], "sizes_sol": [0.5, 1.0, 1.25], "delays": [2, 3], "slippage_tol": tol}
    base.update(cfg)
    with open(os.path.join(d, "config.json"), "w") as f:
        json.dump(base, f)
    con = duckdb.connect()
    con.execute(f"CREATE TABLE x ({', '.join(f'{c} {t}' for c, t in zip(COLS, TYPES))})")
    con.executemany(f"INSERT INTO x VALUES ({', '.join('?' * len(COLS))})", rows)
    con.execute(f"COPY x TO '{d}/parts/part-000000.parquet' (FORMAT parquet)")
    return d


def rows_for(tol, drain_pool="D"):
    out = []
    for v in ("flow300_tp100_H6", "hold300", "tp4_T600"):
        for size in (0.5, 1.25):
            for d in (2, 3):
                for i, pool in enumerate(POOLS):
                    t = LO + 10_000 + i * 1000
                    cost = int(size * 1e9)
                    failed = pool == "B" and tol < 0.05  # the tight tolerance refuses B's entry
                    ret = -0.95 if pool == drain_pool else 0.2 - tol
                    proceeds = 0 if failed else int(cost * (1 + ret))
                    out.append(("F7", "pool_reversed", v, 0.5, "none", "empirical", size, d, t, DAYS[i], pool,
                                "flagged" if pool == "C" else "organic", False, failed, 0 if failed else cost, proceeds,
                                None if failed else t + 100, t + 90, 2 if pool == "A" else 1, 270.0 * d, 200e9))
    return out


@pytest.fixture
def lab(tmp_path):
    root = str(tmp_path)
    runs = [write_run(root, f"t{int(t * 100):03d}", t, rows_for(t)) for t in (0.02, 0.10, 0.15)]
    e63 = write_run(root, "e63", 0.05, rows_for(0.05))
    keys = os.path.join(root, "keys.parquet")
    duckdb.connect().execute(f"""COPY (SELECT * FROM (VALUES ('A', {LO + 10_000}::UBIGINT, 2, '5m', 3), ('D', {LO + 13_000}::UBIGINT, 2, '5m', 2))
                    t(pool, t, d, h, paid_buyers_60s)) TO '{keys}' (FORMAT parquet)""")
    con = duckdb.connect()
    cc.load(con, [e63], keys)
    days, cells = cc.compute(con, False)
    js = os.path.join(root, "ceiling.json")
    json.dump({"cells": cells, "days": days}, open(js, "w"), default=str)
    return {"root": root, "parts": runs + [e63], "keys": keys, "e63_json": js}


def test_the_sweep_and_its_gates(lab):
    out = os.path.join(lab["root"], "out")
    assert ts.main(["--parts", *lab["parts"], "--e63-json", lab["e63_json"], "--c3-keys", lab["keys"], "--out", out, "--memory", "1GB"]) == 0
    res = json.load(open(os.path.join(out, "tol.json")))
    assert {k: v["status"] for k, v in res["gates"].items()} == {"T0": "GREEN", "T1": "GREEN", "T2": "GREEN", "T3": "GREEN"}
    assert res["gates"]["T1"]["cells"] == 2 * 2 * 2  # 2 leads x 2 sizes x 2 scenarios
    h5 = {(c["tol"], c["size"], c["scenario"]): c for c in res["leads"]["H5"]["cells"]}
    # H5 is organic: A, B, D (C is flagged); D drains (proceeds 5 % of cost); A's exit was retried.
    c = h5[(0.05, 0.5, "base")]
    assert c["unlimited"]["rows"] == 3 and c["drain_pct"] == round(100 / 3, 2) and c["exit_retry_pct"] == round(100 / 3, 2)
    tight = h5[(0.02, 0.5, "base")]
    assert tight["unlimited"]["entry_failed_pct"] == round(100 / 3, 2) and tight["drain_pct"] == 50.0  # B refused: A and D filled
    assert "vs_005" in tight and "vs_005" not in c
    c3 = {(c["tol"], c["size"], c["scenario"]): c for c in res["leads"]["C3/hold300"]["cells"]}
    assert c3[(0.05, 0.5, "base")]["unlimited"]["rows"] == 2  # the arm keys A and D
    md = open(os.path.join(out, "tol.md")).read()
    assert "one parameter for the entry" in md and "DRY RUN" not in md


def test_the_runs_must_differ_only_in_their_tolerance(lab, tmp_path):
    other = write_run(str(tmp_path), "grid", 0.10, rows_for(0.10), f7_grid="v1")
    with pytest.raises(cc.Refused, match="f7_grid"):
        ts.grid([lab["parts"][0], other, lab["parts"][2], lab["parts"][3]])
    twice = write_run(str(tmp_path), "twice", 0.02, rows_for(0.02))
    with pytest.raises(cc.Refused, match="slippage_tol"):
        ts.grid([lab["parts"][0], twice, lab["parts"][2], lab["parts"][3]])
    with pytest.raises(cc.Refused, match="slippage_tol"):  # E63's run must come last
        ts.grid([lab["parts"][3], lab["parts"][0], lab["parts"][1], lab["parts"][2]])


def test_a_differing_e63_cell_is_red(lab):
    e63 = json.load(open(lab["e63_json"]))
    next(c for c in e63["cells"] if c["candidate"] == "H5" and c["d"] == 3)["unlimited"]["sol_day"] += 1e-9
    json.dump(e63, open(lab["e63_json"], "w"))
    out = os.path.join(lab["root"], "out2")
    assert ts.main(["--parts", *lab["parts"], "--e63-json", lab["e63_json"], "--c3-keys", lab["keys"], "--out", out, "--memory", "1GB"]) == 1


def test_t0_pins_the_engine_and_the_cost_defaults(tmp_path):
    assert ts.engine_check()["status"] == "GREEN"  # this checkout's engine is E63's (d15ac45)
    here = os.path.dirname(ts.__file__)
    copy = tmp_path / "backtest"
    copy.mkdir()
    for n in ts.ENGINE_SHA256:
        (copy / n).write_bytes(open(os.path.join(here, n), "rb").read())
    (copy / "venues.py").write_bytes((copy / "venues.py").read_bytes() + b"\n# changed\n")
    res = ts.engine_check(here=str(copy))
    assert res["status"] == "RED" and list(res["differ"]) == ["venues.py"]
    other = tmp_path / "defaults.yaml"
    other.write_text("costs: {}\n")
    assert "week2-defaults.yaml" in ts.engine_check(defaults=str(other))["differ"]


def test_the_split_by_leg_adds_up():
    def row(pool, t, pnl, proceeds, failed=False, cost=100):
        return (t, t + 5, t + 4, 3, cost, pnl, failed, "2026-09-12", pool, proceeds, 270.0)
    ref = [row("A", 1, 10, 110), row("B", 2, -5, 95), row("C", 3, 0, 0, failed=True), row("D", 4, -95, 5)]
    tol = [row("A", 1, 12, 112), row("B", 2, 0, 0, failed=True), row("C", 3, 20, 120), row("E", 9, -99, 1)]
    x = ts.split(tol, ref, 1)
    assert (x["new"]["n"], x["lost"]["n"], x["common"]["n"]) == (2, 2, 1)  # new C, E; lost B, D; common A
    assert x["new"]["drain_pct"] == 50.0 and x["lost"]["drain_pct"] == 50.0
    assert x["common"]["d_sol_day"] == 2 / 1e9 and x["common"]["entry_cost_differs"] == 0
    assert abs(x["new"]["sol_day"] - x["lost"]["sol_day"] + x["common"]["d_sol_day"] + x["failed_fees_d_sol_day"] - x["total_d_sol_day"]) < 1e-12
