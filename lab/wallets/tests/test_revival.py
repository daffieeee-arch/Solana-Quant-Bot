"""revival aggregate on a mini store: hours from block times, SOL volume and inflow by direction, the threshold
crossing (slot, tx, traders up to the crossing leg, depth after it), non-SOL pools and non-trade legs left out,
buckets = unbucketed, and a smoke's slot bounds."""
import json

import duckdb
import pytest

from wallets import revival

SLOT0 = 446_256_000
SOL = 10**9
T0 = 1_789_000_000  # some UTC second; hour A = T0 // 3600 * 3600


def _store(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": SLOT0, "slot_end_exclusive": SLOT0 + 100_000})])
    con.execute("CREATE TABLE pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR, created_in_period BOOLEAN, create_slot BIGINT, "
                "mayhem BOOLEAN, coin_creator VARCHAR, b0 BIGINT, q0 BIGINT)")
    con.execute("INSERT INTO pools VALUES ('P', 'sol', 'normal', false, NULL, false, 'c', 1, 2), ('R', 'reversed', 'reversed', true, ?, false, 'c', 1, 2), "
                "('X', 'other', 'normal', false, NULL, false, 'c', 1, 2)", [SLOT0 + 5])
    hour_a = T0 // 3600 * 3600
    con.execute("CREATE TABLE blocks (slot BIGINT, block_time BIGINT)")
    # slots 10..19 in hour A, slots 20..29 in hour A + 1
    con.executemany("INSERT INTO blocks VALUES (?, ?)", [(SLOT0 + s, hour_a + (s - 10) * 60 + (3600 if s >= 20 else 0)) for s in range(10, 30)])
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, trader VARCHAR, "
                "token_buy BOOLEAN, sol_amount BIGINT, sol_depth BIGINT, b BIGINT, e BIGINT, vq BIGINT, lp_bps INTEGER, protocol_bps INTEGER, "
                "creator_bps INTEGER, farmer BOOLEAN)")
    rows = [
        # P, hour A: 4 SOL (a), 7 SOL (b, same slot later tx) -> cum 11 crosses 10 at slot 12 tx 3; then a sell 2 SOL (a, farmer)
        ("buy", "P", SLOT0 + 11, 1, 0, 0, "a", True, 4 * SOL, 104 * SOL, 1, 2, 3, 20, 5, 0, False),
        ("buy", "P", SLOT0 + 12, 3, 0, 0, "b", True, 7 * SOL, 111 * SOL, 1, 2, 3, 20, 5, 0, False),
        ("buy", "P", SLOT0 + 12, 1, 0, 0, "c", True, 0, 111 * SOL, 1, 2, 3, 20, 5, 0, False),   # earlier tx in the slot, zero SOL (order check)
        ("sell", "P", SLOT0 + 15, 1, 0, 0, "a", False, 2 * SOL, 109 * SOL, 9, 8, 7, 20, 5, 0, True),
        ("withdraw", "P", SLOT0 + 16, 1, 0, 0, "lp", None, None, 50 * SOL, 1, 2, 3, None, None, None, None),  # not a trade
        # P, hour A + 1: one sell
        ("sell", "P", SLOT0 + 21, 1, 0, 0, "d", False, 1 * SOL, 49 * SOL, 1, 2, 3, 20, 5, 0, False),
        # R (reversed; a token buy is a pool 'sell'), hour A: 30 SOL by one trader -> crosses 10 and 20 at once
        ("sell", "R", SLOT0 + 13, 2, 0, 0, "e", True, 30 * SOL, 130 * SOL, 1, 2, 3, 25, 5, 0, False),
        # X: not SOL-quoted, left out
        ("buy", "X", SLOT0 + 13, 1, 0, 0, "f", True, 99 * SOL, 1, 1, 2, 3, 20, 5, 0, False),
    ]
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.close()
    return hour_a


def _rows(path):
    con = duckdb.connect()
    return {(r[0], r[1]): r for r in con.execute(f"SELECT * FROM read_parquet('{path}/pool_hour.parquet') ORDER BY pool, hour").fetchall()}, \
        [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}/pool_hour.parquet')").fetchall()]


def test_aggregate(tmp_path, monkeypatch):
    hour_a = _store(tmp_path / "mini.duckdb")
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path))
    out = tmp_path / "out"
    info = revival.aggregate(str(out), store=str(tmp_path / "mini.duckdb"))
    rows, cols = _rows(out)
    c = {n: i for i, n in enumerate(cols)}
    assert set(rows) == {("P", hour_a), ("P", hour_a + 3600), ("R", hour_a)}
    p = rows[("P", hour_a)]
    assert (p[c["trades"]], p[c["buys"]], p[c["sells"]]) == (4, 3, 1)
    assert (p[c["sol_volume"]], p[c["sol_buy"]], p[c["sol_sell"]], p[c["net_inflow"]]) == (13 * SOL, 11 * SOL, 2 * SOL, 9 * SOL)
    assert (p[c["traders"]], p[c["buyers"]], p[c["farmer_trades"]], p[c["farmer_sol"]]) == (3, 3, 1, 2 * SOL)
    assert (p[c["first_slot"]], p[c["last_slot"]], p[c["end_depth"]], p[c["end_b"]], p[c["end_bps"]]) == (SLOT0 + 11, SLOT0 + 15, 109 * SOL, 9, 25)
    # the 10 SOL threshold: reached by the 7 SOL leg at slot 12 tx 3 (the zero-SOL tx 1 comes before it in block order);
    # traders up to and including that leg: a, c, b = 3; depth after it 111 SOL. 20 and 50 SOL: never.
    assert (p[c["t10_slot"]], p[c["t10_tx"]], p[c["t10_traders"]], p[c["t10_depth"]]) == (SLOT0 + 12, 3, 3, 111 * SOL)
    assert p[c["t20_slot"]] is None and p[c["t50_slot"]] is None
    r = rows[("R", hour_a)]
    assert (r[c["trades"]], r[c["buys"]], r[c["net_inflow"]], r[c["traders"]]) == (1, 1, 30 * SOL, 1)
    assert (r[c["t10_slot"]], r[c["t20_slot"]], r[c["t20_traders"]], r[c["t50_slot"]]) == (SLOT0 + 13, SLOT0 + 13, 1, None)
    p2 = rows[("P", hour_a + 3600)]
    assert (p2[c["trades"]], p2[c["net_inflow"]], p2[c["end_depth"]]) == (1, -1 * SOL, 49 * SOL)
    assert info["rows"] == 3 and info["sol_pools"] == 2 and info["hours_reaching"] == {"10": 2, "20": 1, "50": 0}
    assert info["partial_edge_hours"] is False
    pools = duckdb.connect().execute(f"SELECT pool, orientation, created_in_period FROM read_parquet('{out}/pools.parquet') ORDER BY 1").fetchall()
    assert pools == [("P", "normal", False), ("R", "reversed", True)]


def test_buckets_and_bounds_equal(tmp_path, monkeypatch):
    hour_a = _store(tmp_path / "mini.duckdb")
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path))
    one = revival.aggregate(str(tmp_path / "one"), store=str(tmp_path / "mini.duckdb"))
    four = revival.aggregate(str(tmp_path / "four"), buckets=4, store=str(tmp_path / "mini.duckdb"))
    a, _ = _rows(tmp_path / "one")
    b, _ = _rows(tmp_path / "four")
    assert a == b and one["rows"] == four["rows"] == 3 and four["buckets"] == 4
    # a smoke on slots [10, 20): hour A only, flagged as cut
    sm = revival.aggregate(str(tmp_path / "smoke"), lo=SLOT0 + 10, hi=SLOT0 + 20, store=str(tmp_path / "mini.duckdb"))
    s, _ = _rows(tmp_path / "smoke")
    assert set(s) == {("P", hour_a), ("R", hour_a)} and sm["partial_edge_hours"] is True
    with pytest.raises(ValueError):
        revival.aggregate(str(tmp_path / "bad"), hi=revival.HOLDOUT_START + 1, store=str(tmp_path / "mini.duckdb"))
