"""buyback on a mini store: the classes (self, linked through the fee payer, other), the rt_tx buy in the trigger transaction,
the windows through the Clock, the self share and flag, the markout join and the persistence block."""
import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from wallets import buyback as bb

SLOT0 = 446_256_000
SOL = 10**9
W1 = 150  # 60 s at 400 ms


def _store(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": SLOT0, "slot_end_exclusive": SLOT0 + 432_000, "ms_per_slot": {str(SLOT0 // 432_000): 400.0}})])
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, trader VARCHAR, fee_payer VARCHAR, "
                "token_buy BOOLEAN, sol_amount BIGINT, orientation VARCHAR)")
    rows = []

    def leg(kind, pool, slot, tx, trader, payer, sol, buy=None):
        # P3 is a reversed pool; token_buy NULL on a leg means: the side is read from kind by orientation (TOKEN_BUY)
        rows.append((kind, pool, slot, tx, 0, len(rows), trader, payer, kind == "buy" if buy is None else buy, sol, "reversed" if pool == "P3" else "normal"))

    # trigger A: pool P1 at T, seller S (payer PAY): same-tx buy 1 SOL (rt), self buys 3 SOL in 1 m, a linked buy by wallet L paid by PAY
    # 2 SOL in 1 m, other buys 1 SOL in 1 m and 10 SOL between 1 m and 5 m; one self buy at 6 m (in 15 m only)
    T = SLOT0 + 1000
    leg("sell", "P1", T, 7, "S", "PAY", 20 * SOL)
    leg("buy", "P1", T, 7, "S", "PAY", 1 * SOL)            # rt_tx
    leg("buy", "P1", T, 8, "X", "X", 5 * SOL)              # same slot, another tx: counted (slot = t only through rt) -> not counted
    leg("buy", "P1", T + 10, 1, "S", "S", 3 * SOL)         # self
    leg("buy", "P1", T + 20, 1, "L", "PAY", 2 * SOL)       # linked (paid by the trigger payer)
    leg("buy", "P1", T + 30, 1, "O", "O", 1 * SOL)         # other
    leg("sell", "P1", T + 40, 1, "O", "O", 1 * SOL)        # a sell: not a buy
    leg("buy", "P1", T + W1 + 5, 1, "O2", "O2", 10 * SOL)  # other, 5 m window
    leg("buy", "P1", T + 900, 1, "S", "S", 4 * SOL)        # self, 15 m window only (900 slots = 6 m)
    # trigger B: pool P2, seller S2, no buys at all
    leg("sell", "P2", T + 5000, 1, "S2", "S2", 10 * SOL)
    # trigger C: pool P3 in the store's REVERSED spelling (a token sell is a pool 'buy' of WSOL, a token buy a pool 'sell'),
    # seller S again on a later day, only other buys
    T3 = SLOT0 + 300_000
    leg("buy", "P3", T3, 1, "S", "S", 10 * SOL, buy=False)
    leg("sell", "P3", T3 + 1, 1, "Z", "Z", 2 * SOL, buy=True)
    leg("sell", "P3", T3 + 2, 1, "Z", "Z", 1 * SOL, buy=False)  # a token sell in reversed spelling: not a buy
    leg("sell", "P3", T3 + 3, 1, "Z2", "Z2", 3 * SOL, buy=None)  # token_buy NULL: a pool 'sell' in a reversed pool = a token buy
    rows[-1] = rows[-1][:8] + (None,) + rows[-1][9:]
    leg("buy", "P3", T3 + 4, 1, "Z3", "Z3", 7 * SOL, buy=None)   # token_buy NULL: a pool 'buy' in a reversed pool = a token sell
    rows[-1] = rows[-1][:8] + (None,) + rows[-1][9:]
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.close()
    return T, T3


def _parts(path, T, T3):
    base = {"family": "F7", "segment": "pool_reversed", "variant": "tp4_T600", "size_sol": 0.5, "d": 2, "q": 0.5, "overlay": None, "farm": "flagged",
            "trigger_farmer": False}
    rows = [base | {"pool": "P1", "t": T, "trigger_trader": "S", "day": "2026-09-12"},
            base | {"pool": "P2", "t": T + 5000, "trigger_trader": "S2", "day": "2026-09-12"},
            base | {"pool": "P3", "t": T3, "trigger_trader": "S", "day": "2026-09-22"},
            base | {"pool": "P1", "t": T, "trigger_trader": "S", "day": "2026-09-12", "d": 1},  # another d: not a K3 row
            base | {"pool": "P1", "t": T, "trigger_trader": "S", "day": "2026-09-12", "variant": "tp6_T600"}]
    os.makedirs(path)
    pq.write_table(pa.Table.from_pylist(rows), os.path.join(path, "part-0.parquet"))


def _markout(path, T, T3):
    rows = []
    for pool, t, g in (("P1", T, 0.10), ("P2", T + 5000, -0.05), ("P3", T3, 0.02)):
        for h in ("1m", "5m", "15m"):
            rows.append({"family": "F7", "segment": "pool_reversed", "pool": pool, "mint": "M" + pool, "t": t, "d": 2, "h": h, "gross": g, "net": g - 0.05})
            rows.append({"family": "F7", "segment": "pool_reversed", "pool": pool, "mint": "M" + pool, "t": t, "d": 1, "h": h, "gross": 9.0, "net": 9.0})
    os.makedirs(path)
    pq.write_table(pa.Table.from_pylist(rows), os.path.join(path, "markout-f7rev.parquet"))


def test_buyback_classes_windows_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    T, T3 = _store(tmp_path / "s.duckdb")
    _parts(tmp_path / "parts", T, T3)
    _markout(tmp_path / "mk", T, T3)
    res = bb.run(str(tmp_path / "parts"), str(tmp_path / "out"), str(tmp_path / "mk"), "f7rev", store=str(tmp_path / "s.duckdb"), boot=50)
    assert {k: res["triggers"][k] for k in ("n", "trigger_leg_found", "days", "sellers", "payer_known")} == {"n": 3, "trigger_leg_found": 3, "days": 2, "sellers": 2, "payer_known": 3}
    assert con_count(tmp_path, "pool = 'P3'", "all_1m") == 5 * SOL  # 2 (token_buy true) + 3 (NULL, pool 'sell' in a reversed pool); the 7 is a token sell
    assert con_count(tmp_path, "pool = 'P3'", "net_1m_flow") == (5 - 1 - 7) * SOL
    con = duckdb.connect()
    r = con.execute(f"SELECT rt_tx, rt_sol, self_1m, linked_1m, all_1m, self_5m, all_5m, self_15m, all_15m, self_share_1m, own_share_1m, self_buyback, lg_1m, block "
                    f"FROM read_parquet('{tmp_path}/out/buyback.parquet') WHERE pool = 'P1'").fetchone()
    assert r[0] is True and r[1] == 1 * SOL
    assert (r[2], r[3], r[4]) == (3 * SOL, 2 * SOL, 6 * SOL)      # the same-slot other-tx buy is not in the window (slot = t, not rt)
    assert (r[5], r[6]) == (3 * SOL, 16 * SOL) and (r[7], r[8]) == (7 * SOL, 20 * SOL)
    assert abs(r[9] - 0.5) < 1e-12 and abs(r[10] - 5 / 6) < 1e-12 and r[11] == "yes" and r[13] == "days 1-9"
    assert abs(r[12] - __import__("math").log(1.10)) < 1e-9
    flags = dict(con.execute(f"SELECT self_buyback, count(*) FROM read_parquet('{tmp_path}/out/buyback.parquet') GROUP BY 1").fetchall())
    assert flags == {"yes": 1, "no buy": 1, "no": 1}
    assert abs(res["inflow"]["1m"]["self_share_of_sol"] - 3 / 11) < 1e-12 and res["inflow"]["15m"]["self_sol"] == 7.0  # 6 (P1) + 5 (P3) SOL bought in 1 m
    assert res["rt_tx"] == {"triggers": 1, "sol": 1.0}
    # net flow of P1 at 1 m: buys 6 SOL − the other wallet's 1 SOL sell = 5; the seller's own net 3
    assert con_count(tmp_path, "pool = 'P1'", "net_1m_flow") == 5 * SOL and con_count(tmp_path, "pool = 'P1'", "self_net_1m") == 3 * SOL
    assert res["net_flow"]["1m"]["triggers_net_positive"] == 1 and abs(res["net_flow"]["1m"]["self_share_of_net"] - 3 / (5 - 3)) < 1e-12
    # persistence: S has 2 block-1 triggers (< MIN_TRIGGERS 3) -> no seller qualifies
    assert res["persistence"]["sellers_block1"] == 0
    md = (tmp_path / "out" / "buyback.md").read_text()
    assert "| 1m | 8 | 3 | 37.50% |" in md and "self_buyback at 1 m: yes 1, no 1, no buy 1" in md


def con_count(tmp_path, where, col):
    return duckdb.connect().execute(f"SELECT {col} FROM read_parquet('{tmp_path}/out/buyback.parquet') WHERE {where}").fetchone()[0]


def test_min_triggers_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    monkeypatch.setattr(bb, "MIN_TRIGGERS", 1)
    T, T3 = _store(tmp_path / "s.duckdb")
    _parts(tmp_path / "parts", T, T3)
    res = bb.run(str(tmp_path / "parts"), str(tmp_path / "out"), store=str(tmp_path / "s.duckdb"), boot=50)
    p = res["persistence"]
    # S: block 1 rate 0.5 (P1 yes, P2 is S2's) -> S has one block-1 trigger with yes -> buyback seller; S2 one with 'no buy' -> rate 0
    assert p["sellers_block1"] == 2 and p["buyback_sellers_1"] == 1 and p["sellers_in_both"] == 1
    assert p["rate_block2_of_buyback_sellers_1"] == 0.0 and p["block2_triggers"] == {"n": 1, "by_block1_sellers": 1, "by_block1_buyback_sellers": 1}
    assert res["markouts"]["yes"]["lg_1m"] is None  # no markouts joined
