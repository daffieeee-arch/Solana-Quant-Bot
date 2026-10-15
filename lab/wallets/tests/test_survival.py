"""Survival (D) on a mini store: the drain as the event, censoring, Kaplan-Meier at the grid, the K3 window and
the inactivity exit before / after the drain."""

import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from wallets import survival

SLOT0 = 446_256_000
S = SLOT0 + 1000
SOL = 10**9


def _store(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": SLOT0, "slot_end_exclusive": SLOT0 + 432_000, "ms_per_slot": {str(SLOT0 // 432_000): 400.0}})])
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, b BIGINT, base BIGINT, sol_amount BIGINT)")
    rows = []
    # P1: two trades, then the LP removes everything at S + 300 (2 min at 400 ms)
    rows += [("sell", "P1", S + 10, 1, 100 * SOL, 1 * SOL, 1 * SOL), ("buy", "P1", S + 20, 1, 101 * SOL, 1 * SOL, 1 * SOL), ("withdraw", "P1", S + 300, 1, 100 * SOL, 100 * SOL, 0)]
    # P2: a trade every 100 slots until S + 1000, a half withdraw at S + 500, the drain at S + 1200 (8 min)
    rows += [("sell", "P2", S + 100 * i, 1, 100 * SOL, 1 * SOL, 1 * SOL) for i in range(1, 11)]
    rows += [("withdraw", "P2", S + 500, 2, 100 * SOL, 50 * SOL, 0), ("withdraw", "P2", S + 1200, 1, 50 * SOL, 50 * SOL, 0)]
    # P3: one trade, never drained (censored at t + 3000 slots = 20 min)
    rows += [("sell", "P3", S + 5, 1, 100 * SOL, 1 * SOL, 1 * SOL)]
    # P4: one trade, drained at S + 75 (0.5 min): inside the K3 hold of 600 slots
    rows += [("sell", "P4", S + 5, 1, 100 * SOL, 1 * SOL, 1 * SOL), ("withdraw", "P4", S + 75, 1, 100 * SOL, 100 * SOL, 0)]
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.close()


def _parts(path):
    rows = [{"family": "F7", "variant": "tp4_T600", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": f"M{i}", "pool": f"P{i}",
             "t": S, "day": "2026-09-11", "farm": "organic" if i < 3 else "flagged"} for i in range(1, 5)]
    rows.append({**rows[0], "variant": "tp6_T600"})  # a second exit variant of the same trigger: one row
    os.makedirs(path)
    pq.write_table(pa.Table.from_pylist(rows), os.path.join(path, "part-0.parquet"))


def test_drain_survival_and_inactivity_exit(tmp_path):
    _store(tmp_path / "s.duckdb")
    _parts(str(tmp_path / "parts"))
    info = survival.run(str(tmp_path / "parts"), str(tmp_path / "out"), store=str(tmp_path / "s.duckdb"), observe_slots=3000)
    assert info["triggers"] == 4 and info["events"] == 3
    con = duckdb.connect()
    r = {x[0]: x for x in con.execute(f"SELECT pool, event, time_min, drain_in_k3_hold, first_withdraw_frac, exit_1m, drain_slot FROM '{tmp_path / 'out' / 'survival.parquet'}'").fetchall()}
    assert r["P1"][1] and abs(r["P1"][2] - 2.0) < 1e-9 and r["P1"][3] and r["P1"][4] == 1.0
    assert r["P2"][1] and abs(r["P2"][2] - 8.0) < 1e-9 and not r["P2"][3] and abs(r["P2"][4] - 0.5) < 1e-12  # the half withdraw is not the drain
    assert not r["P3"][1] and abs(r["P3"][2] - 20.0) < 1e-9 and r["P3"][6] is None             # censored at t + 3000 slots
    assert r["P4"][1] and abs(r["P4"][2] - 0.5) < 1e-9 and r["P4"][3]
    # inactivity exit of 1 min = 150 slots: P1 fires at S + 20 + 150 (before the drain at S + 300), P4 at S + 5 + 150 (after its drain at S + 75)
    assert r["P1"][5] == S + 170 and r["P4"][5] == S + 155 and r["P2"][5] == S + 1150
    md = (tmp_path / "out" / "survival.md").read_text()
    all_row = next(ln for ln in md.splitlines() if ln.startswith("| all |"))
    cells = [c.strip() for c in all_row.split("|")[1:-1]]
    # S(1 min) = 3/4, S(3 min) = 2/4, S(10 min) = 1/4, median 2.0 min, 2 of 4 drained within 600 slots
    assert cells[1] == "4" and cells[2] == "3" and cells[3].startswith("75.0 %") and cells[5].startswith("50.0 %") and cells[7].startswith("25.0 %")
    assert cells[11] == "2.0" and cells[12].startswith("50.0 %")
    assert "| 1 min | 4 (100.0 %) | 2 (50.0 %) |" in md  # P1 and P2 fire before their drain; P4 after; P3 fires with no drain
    assert "the first withdraw is already the drain (≥ 90%) in 2 (66.7 %)" in md
