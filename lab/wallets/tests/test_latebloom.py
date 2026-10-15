"""Late bloomers vs drains on a mini store: point-in-time features (pre-trigger hour, LP deposits, post-entry windows),
the outcome classes from the markout and survival inputs, and the weighted AUC."""

import json
import os

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from wallets import latebloom

SLOT0 = 446_256_000
T = SLOT0 + 20_000           # the trigger slot; 400 ms per slot in the mini store → 30 s = 75 slots, 60 s = 150 slots
SOL = 10**9
MS = 400.0


def _store(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": SLOT0, "slot_end_exclusive": SLOT0 + 432_000, "ms_per_slot": {str(SLOT0 // 432_000): MS}})])
    con.execute("CREATE TABLE pools (pool VARCHAR, create_slot BIGINT, coin_creator VARCHAR, b0 BIGINT)")
    con.execute("INSERT INTO pools VALUES ('P1', ?, 'CREATOR', ?), ('P2', ?, 'CREATOR', ?)", [T - 1500, 80 * SOL, T - 150, 50 * SOL])  # 10 min and 1 min old
    con.execute("""CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, trader VARCHAR, token_buy BOOLEAN,
                   sol_amount BIGINT, b BIGINT, e BIGINT, farmer BOOLEAN)""")
    rows = []
    # P1: the creator deposits at create; two buyers (A, B) before t; the trigger wallet W sells at t in a round-trip tx
    rows += [("deposit", "P1", T - 1500, 1, "CREATOR", None, 0, 100 * SOL, 10**12, False)]
    rows += [("sell", "P1", T - 1000, 1, "A", True, 2 * SOL, 102 * SOL, 10**12, False), ("sell", "P1", T - 900, 1, "B", True, 1 * SOL, 103 * SOL, 10**12, True)]
    rows += [("buy", "P1", T, 7, "W", False, 1 * SOL, 102 * SOL, 10**12, False), ("sell", "P1", T, 7, "X", True, 1 * SOL, 103 * SOL, 10**12, False)]
    # after the entry (t + 1): a new buyer C at +10 slots (mid up 10 %), old buyer A at +50, a sell at +100 (mid down), nothing else
    rows += [("sell", "P1", T + 11, 1, "C", True, 3 * SOL, 110 * SOL, 10**12, False), ("sell", "P1", T + 51, 1, "A", True, 1 * SOL, 111 * SOL, 10**12, False),
             ("buy", "P1", T + 101, 1, "C", False, 2 * SOL, 95 * SOL, 10**12, False)]
    # P2: no deposit seen, trigger wallet W sells at t alone (no round trip), then the LP drains at +40 slots and a dust trade follows
    rows += [("sell", "P2", T - 100, 1, "D", True, 1 * SOL, 50 * SOL, 10**12, False), ("buy", "P2", T, 3, "W", False, 1 * SOL, 49 * SOL, 10**12, False)]
    rows += [("withdraw", "P2", T + 41, 1, "LP", None, 0, 1, 10**8, False), ("sell", "P2", T + 60, 1, "E", True, 0, 2, 10**8, False)]
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.close()


def _inputs(tmp_path):
    parts = tmp_path / "parts"
    os.makedirs(parts)
    rows = [{"family": "F7", "variant": v, "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": f"M{p}", "pool": p,
             "t": T, "day": "2026-09-22", "farm": "organic" if p == "P1" else "flagged", "regime": "320ms", "trigger_trader": "W",
             "trigger_farmer": False, "depth": 100 * SOL if p == "P1" else 50 * SOL, "d": 1}
            for p in ("P1", "P2") for v in ("tp4_T600", "tp6_T600")]
    pq.write_table(pa.Table.from_pylist(rows), parts / "part-0.parquet")
    mk = []
    for p, nets in (("P1", {"1m": 0.05, "5m": 0.08, "15m": -0.02}), ("P2", {"1m": -1.0, "5m": -1.0, "15m": -1.0})):
        base = {"family": "F7", "segment": "pool_reversed", "pool": p, "t": T, "d": 1, "ms_per_slot": MS, "cut": False, "impact": 0.006}
        mk.append({**base, "h": "", "target": T + 1, "mid": 0.1 if p == "P1" else 0.049, "net": None})
        for h, n in nets.items():
            slots = int(np.ceil({"1m": 60_000, "5m": 300_000, "15m": 900_000}[h] / MS))
            mk.append({**base, "h": h, "target": T + 1 + slots, "mid": 1.0, "net": n})
    pq.write_table(pa.Table.from_pylist(mk), tmp_path / "markout.parquet")
    sv = [{"pool": "P1", "t": T, "drain_slot": T + 3000, "event": True, "first_withdraw_slot": T + 3000, "last_trade_slot": T + 2500},
          {"pool": "P2", "t": T, "drain_slot": T + 41, "event": True, "first_withdraw_slot": T + 41, "last_trade_slot": T + 60}]
    pq.write_table(pa.Table.from_pylist(sv), tmp_path / "survival.parquet")
    return str(parts), str(tmp_path / "markout.parquet"), str(tmp_path / "survival.parquet")


def test_features_and_outcomes(tmp_path):
    _store(tmp_path / "s.duckdb")
    parts, mk, sv = _inputs(tmp_path)
    info = latebloom.run(parts, mk, sv, str(tmp_path / "out"), store=str(tmp_path / "s.duckdb"), boot=50)
    assert info["triggers"] == 2
    con = duckdb.connect()
    r = {x["pool"]: x for x in con.execute(f"SELECT * FROM '{tmp_path / 'out' / 'latebloom.parquet'}'").arrow().read_all().to_pylist()}
    p1, p2 = r["P1"], r["P2"]
    # outcomes: P1 alive and up at 5 min, down at 15 min; P2 drained at +41 slots (inside 5 min) and dead
    assert p1["late_5"] and not p1["drained_5"] and not p1["late_15"] and not p1["drained_15"]
    assert p2["drained_5"] and not p2["late_5"] and p2["drained_15"]
    # point-in-time features at t
    assert abs(p1["age_min"] - 10.0) < 1e-9 and abs(p2["age_min"] - 1.0) < 1e-9
    assert p1["trades_1h"] == 4 and p1["buyers_1h"] == 3 and abs(p1["vol_1h_sol"] - 5.0) < 1e-9 and abs(p1["farmer_share_1h"] - 0.25) < 1e-9
    assert p1["deposits_pre"] == 1 and abs(p1["last_deposit_min"] - 10.0) < 1e-9 and p1["deposit_by_creator"] and not p1["deposit_by_trigger"]
    assert p2["deposits_pre"] == 0 and p2["last_deposit_min"] is None and not p2["deposit_by_creator"]
    assert p1["rt_tx"] is True and p2["rt_tx"] is False
    assert p1["wash_share_1h"] == 0.0 and p1["wash_1h"] is False   # volume before t, no round trip: 0, not NULL
    assert p1["init_depth_sol"] == 80.0 and abs(p1["depth_ratio"] - 1.25) < 1e-12
    assert p1["depth_sol"] == 100.0 and not p1["farm_flagged"] and p2["farm_flagged"] and p1["regime_320"]
    # post-entry windows: 30 s = 75 slots after t + 1 → legs at +11 and +51; 60 s = 150 slots → also the sell at +101
    assert p1["trades_30"] == 2 and p1["new_buyers_30"] == 1 and abs(p1["inflow_sol_30"] - 4.0) < 1e-9 and not p1["withdraw_30"]
    assert p1["trades_60"] == 3 and p1["new_buyers_60"] == 1 and abs(p1["inflow_sol_60"] - 2.0) < 1e-9
    assert abs(p1["drawup_pct_30"] - 11.0) < 1e-6 and abs(p1["drawdown_pct_30"] - 10.0) < 1e-6   # mid 110 and 111 vs entry 100
    assert abs(p1["drawdown_pct_60"] - (-5.0)) < 1e-6
    assert p2["withdraw_30"] and p2["trades_30"] == 1 and p2["new_buyers_30"] == 1   # the dust trade after the drain
    md = (tmp_path / "out" / "latebloom.md").read_text()
    assert "| days 10-18 | 2 | 50.0 % | 50.0 % | 0.0 % |" in md


def test_weighted_auc():
    x = np.array([1.0, 2.0, 3.0, 4.0, 2.0])
    y = np.array([False, False, True, True, True])
    w = np.ones(5)
    # positives 3, 4, 2 vs negatives 1, 2: pairs (3>1, 3>2, 4>1, 4>2, 2>1, 2=2 half) = 5.5 of 6
    assert abs(latebloom.auc_w(x, y, w) - 5.5 / 6) < 1e-12
    assert abs(latebloom.auc_w(x, y, np.array([2.0, 1, 1, 1, 1])) - 8.5 / 9) < 1e-12  # x = 1 counts twice: 3 + 3 + (2 + 0.5) of 3 × 3
    assert latebloom.auc_w(x, np.zeros(5, dtype=bool), w) is None
