"""runner_hours on a mini store: the hour of a leg from blocks, the close as the price after the hour's last trade per
orientation, the depth after the hour's last leg of any kind (withdraw included), counts and volume, the pools table."""
import json

import duckdb
import pyarrow.parquet as pq
import pytest

from wallets.explore import runner_hours as rh

SOL = 10**9
S0 = 1033 * 432_000
BT0 = 1789084800  # 2026-09-11 00:00 UTC


def _store(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": S0, "slot_end_exclusive": S0 + 432_000, "ms_per_slot": {str(S0 // 432_000): 400.0}})])
    con.execute(f"CREATE TABLE blocks AS SELECT {S0} + i AS slot, ({BT0} + i * 0.4)::BIGINT AS block_time FROM range(0, 30000) t(i)")  # 9000 slots per hour at 400 ms
    con.execute("CREATE TABLE pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR, quote_mint VARCHAR, mint VARCHAR, create_slot BIGINT)")
    con.execute("INSERT INTO pools VALUES ('N', 'sol', 'normal', 'So1', 'MN', ?), ('R', 'reversed', 'reversed', NULL, 'MR', ?), ('O', 'other', 'normal', 'USDC', 'MO', ?)", [S0, S0, S0])
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, token_buy BOOLEAN, sol_amount BIGINT, farmer BOOLEAN, "
                "b BIGINT, e BIGINT, vault BIGINT, vq BIGINT, base BIGINT, e_delta BIGINT, quote_user BIGINT, lp_bps INTEGER, protocol_bps INTEGER, creator_bps INTEGER, cashback_bps INTEGER)")
    H = 9000
    rows = [
        # N, hour 0: a buy (post e 101, b 0.99e12, vq 1 SOL -> depth 100 SOL real, price 101e9/0.99e12), then a sell later in the hour (post e 100, b 1e12)
        ("buy", "N", S0 + 10, 1, 0, 0, True, 1 * SOL, False, 10**12, 100 * SOL, 99 * SOL, 1 * SOL, 10**10, 1 * SOL, 1 * SOL),
        ("sell", "N", S0 + 20, 1, 0, 0, False, 1 * SOL, True, 99 * 10**10, 101 * SOL, 100 * SOL, 1 * SOL, 10**10, -1 * SOL, 1 * SOL),
        # N, hour 1: one trade then an LP withdraw of 50 SOL: close from the trade, depth from the withdraw (vault 101 - 50 = 51)
        ("buy", "N", S0 + H + 5, 1, 0, 0, True, 2 * SOL, False, 10**12, 100 * SOL, 99 * SOL, 1 * SOL, 2 * 10**10, 2 * SOL, 2 * SOL),
        ("withdraw", "N", S0 + H + 9, 1, 0, 0, None, 0, None, 98 * 10**10, 102 * SOL, 101 * SOL, None, 10**11, None, 50 * SOL),
        # R, hour 2: a token buy = pool sell of WSOL (post b 110, e 0.9e15 -> price 110e9/0.9e15, depth 110 SOL)
        ("sell", "R", S0 + 2 * H + 1, 1, 0, 0, True, 10 * SOL, False, 100 * SOL, 10**15, 10**15, 0, 10 * SOL, -10**14, 10 * SOL),
        # O (USDC quote), hour 0: a buy with the quote in USDC units (6 decimals): post e 1,010 USDC, b 0.99e12
        ("buy", "O", S0 + 30, 1, 0, 0, True, 10 * 10**6, False, 10**12, 1000 * 10**6, 1000 * 10**6, 0, 10**10, 10 * 10**6, 10 * 10**6),
    ]
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 20, 5, 0, NULL)", [r for r in rows if r[0] != "withdraw"])
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)", [r for r in rows if r[0] == "withdraw"])
    con.close()


def test_hours_on_the_mini_store(tmp_path, monkeypatch, capfd):
    _store(tmp_path / "s.duckdb")
    monkeypatch.setattr(rh, "STORE", str(tmp_path / "s.duckdb"))
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    monkeypatch.setenv("LAB_MARKOUT_BUCKETS", "2")
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["runner_hours", str(out)])
    rh.main()
    h = {(r["pool"], r["hour"] - BT0 // 3600): r for r in pq.read_table(str(out / "hours.parquet")).to_pylist()}
    assert set(h) == {("N", 0), ("N", 1), ("R", 2), ("O", 0)}
    n0 = h[("N", 0)]
    assert n0["close"] == pytest.approx(100 * SOL / 1e12) and n0["depth"] == pytest.approx(100.0 - 1.0)  # after the sell: e 100 incl. 1 SOL virtual -> 99 real
    assert n0["trades"] == 2 and n0["buys"] == 1 and n0["farmer_trades"] == 1 and n0["volume"] == pytest.approx(2.0) and n0["fee_bps"] == 25
    n1 = h[("N", 1)]
    assert n1["close"] == pytest.approx(102 * SOL / 0.98e12) and n1["depth"] == pytest.approx(51.0) and n1["trades"] == 1 and n1["fee_bps"] == 25  # the withdraw sets the depth, not the close or the fee tier
    r2 = h[("R", 2)]
    assert r2["close"] == pytest.approx(110 * SOL / 0.9e15) and r2["depth"] == pytest.approx(110.0) and r2["buys"] == 1
    o0 = h[("O", 0)]
    assert o0["close"] == pytest.approx(1010 * 10**6 / 0.99e12) and o0["depth"] == pytest.approx(1010 * 10**6 / 1e9) and o0["volume"] == pytest.approx(0.01)  # quote units / 1e9
    pools = {r["pool"]: r for r in pq.read_table(str(out / "pools.parquet")).to_pylist()}
    assert pools["O"]["quote_class"] == "other" and pools["O"]["quote_mint"] == "USDC" and len(pools) == 3
    info = json.load(open(out / "run-runner-hours.json"))
    assert info["rows"] == 4 and info["pools"] == 3 and info["hours"] == 4 and "hours in blocks" in capfd.readouterr().err
