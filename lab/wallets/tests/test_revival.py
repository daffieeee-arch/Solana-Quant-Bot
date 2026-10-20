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
        # P, hour A + 1: one sell, then a round-trip transaction (buy 3 SOL, sell 3 SOL in one tx: two legs with the same
        # (slot, tx_index), ordered by outer_ix / inner_ix): the hour ends at the sell leg (depth 48), the 10 SOL threshold
        # is never reached, and the end state must not depend on which of the two legs a (slot, tx_index) key would pick
        ("sell", "P", SLOT0 + 21, 1, 0, 0, "d", False, 1 * SOL, 49 * SOL, 1, 2, 3, 20, 5, 0, False),
        ("buy", "P", SLOT0 + 25, 4, 2, 0, "rt", True, 3 * SOL, 52 * SOL, 5, 5, 5, 20, 5, 0, False),
        ("sell", "P", SLOT0 + 25, 4, 2, 1, "rt", False, 3 * SOL, 48 * SOL, 6, 6, 6, 20, 5, 0, False),
        # R, hour A + 1: a round trip of 6 + 6 SOL crosses 10 SOL on its second leg: t10 keys on that leg (depth 118),
        # traders up to it = 1
        ("sell", "R", SLOT0 + 26, 7, 1, 0, "g", True, 6 * SOL, 124 * SOL, 1, 2, 3, 25, 5, 0, False),
        ("buy", "R", SLOT0 + 26, 7, 1, 1, "g", False, 6 * SOL, 118 * SOL, 1, 2, 3, 25, 5, 0, False),
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
    assert set(rows) == {("P", hour_a), ("P", hour_a + 3600), ("R", hour_a), ("R", hour_a + 3600)}
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
    assert (p2[c["trades"]], p2[c["net_inflow"]], p2[c["sol_volume"]], p2[c["traders"]]) == (3, -1 * SOL, 7 * SOL, 2)
    assert (p2[c["end_depth"]], p2[c["end_b"]], p2[c["end_e"]], p2[c["end_vq"]]) == (48 * SOL, 6, 6, 6) and p2[c["t10_slot"]] is None
    r2 = rows[("R", hour_a + 3600)]
    assert (r2[c["trades"]], r2[c["net_inflow"]], r2[c["t10_slot"]], r2[c["t10_tx"]], r2[c["t10_traders"]], r2[c["t10_depth"]], r2[c["end_depth"]]) == \
        (2, 0, SLOT0 + 26, 7, 1, 118 * SOL, 118 * SOL)
    assert info["rows"] == 4 and info["sol_pools"] == 2 and info["hours_reaching"] == {"10": 3, "20": 1, "50": 0}
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
    assert a == b and one["rows"] == four["rows"] == 4 and four["buckets"] == 4
    # a smoke on slots [10, 20): hour A only, flagged as cut
    sm = revival.aggregate(str(tmp_path / "smoke"), lo=SLOT0 + 10, hi=SLOT0 + 20, store=str(tmp_path / "mini.duckdb"))
    s, _ = _rows(tmp_path / "smoke")
    assert set(s) == {("P", hour_a), ("R", hour_a)} and sm["partial_edge_hours"] is True
    with pytest.raises(ValueError):
        revival.aggregate(str(tmp_path / "bad"), hi=revival.HOLDOUT_START + 1, store=str(tmp_path / "mini.duckdb"))


def test_detect(tmp_path, monkeypatch):
    """A sleeping pre-window pool with one burst hour on day 10, an always-active pool, a young in-window pool and a mayhem pool."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path))
    agg = tmp_path / "agg"
    agg.mkdir()
    day0 = 1_789_000_000 // 86400 * 86400  # some UTC midnight; FIRST_DAY is passed explicitly below
    H = 3600
    rows = []

    def hour(pool, h, vol_sol, traders, farmer_trades=0, t10=None, t20=None, t50=None, tr=None, depth=100 * SOL):
        rows.append({"pool": pool, "hour": h, "trades": 10, "buys": 6, "sells": 4, "sol_volume": int(vol_sol * SOL), "sol_buy": int(vol_sol * SOL),
                     "sol_sell": 0, "net_inflow": int(vol_sol * SOL), "traders": traders, "buyers": traders, "farmer_trades": farmer_trades,
                     "farmer_sol": 0, "first_slot": SLOT0 + (h - day0) // 267 * 1000, "last_slot": SLOT0 + (h - day0) // 267 * 1000 + 5,
                     "end_depth": depth, "end_b": 1, "end_e": 2, "end_vq": 3, "end_bps": 25,
                     "t10_slot": t10, "t10_tx": 1 if t10 else None, "t10_traders": tr, "t10_depth": depth if t10 else None,
                     "t20_slot": t20, "t20_tx": 1 if t20 else None, "t20_traders": tr, "t20_depth": depth if t20 else None,
                     "t50_slot": t50, "t50_tx": 1 if t50 else None, "t50_traders": tr, "t50_depth": depth if t50 else None})
    # A: pre-window, one 0.5 SOL hour per day for 9 days (sleeping), then on day 9 (= 2026-09-21 by the first_day below) a
    # 25 SOL burst hour with 12 traders, a second burst the same day (deduplicated), and on day 10 a 4 SOL hour (below D)
    for dday in range(9):
        hour("A", day0 + dday * 86400 + 3 * H, 0.5, 1)
    burst = day0 + 9 * 86400 + 5 * H
    hour("A", burst, 25, 12, t10=SLOT0 + 900_000, t20=SLOT0 + 900_010, tr=7)
    hour("A", burst + 2 * H, 30, 15, t10=SLOT0 + 901_000, t20=SLOT0 + 901_010, tr=9)
    hour("A", day0 + 10 * 86400 + 5 * H, 4, 6)
    # B: active every hour (50 SOL): never sleeping
    for h in range(0, 11 * 24):
        hour("B", day0 + h * H, 50, 20, t10=SLOT0 + 1000 + h, t20=SLOT0 + 1000 + h, t50=SLOT0 + 1000 + h, tr=20)
    # C: in-window pool created 2 days before its burst: too young
    hour("C", burst, 25, 12, t10=SLOT0 + 950_000, tr=12)
    # M: mayhem pool, sleeping then bursting: excluded
    for dday in range(9):
        hour("M", day0 + dday * 86400 + 3 * H, 0.5, 1)
    hour("M", burst, 25, 12, t10=SLOT0 + 960_000, tr=12)
    pq.write_table(pa.table({k: [r[k] for r in rows] for k in rows[0]}), agg / "pool_hour.parquet")
    pools = {"pool": ["A", "B", "C", "M"], "orientation": ["reversed", "normal", "reversed", "reversed"], "quote_class": ["reversed", "sol", "reversed", "reversed"],
             "created_in_period": [False, False, True, False], "create_slot": [None, None, SLOT0 + 900_000 - 2 * 323_000, None],
             "mayhem": [False, False, False, True], "coin_creator": ["c"] * 4, "b0": [1] * 4, "q0": [2] * 4}
    pq.write_table(pa.table(pools), agg / "pools.parquet")
    first_day = str(__import__("datetime").date(1970, 1, 1) + __import__("datetime").timedelta(days=(day0 + 9 * 86400) // 86400))
    st = duckdb.connect(str(tmp_path / "mini.duckdb"))
    st.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR)")
    st.execute("INSERT INTO pools VALUES ('A', 'MA'), ('B', 'MB'), ('C', 'MC'), ('M', 'MM')")
    st.close()
    out = tmp_path / "det"
    info = revival.detect(str(agg), str(out), first_day=first_day, store=str(tmp_path / "mini.duckdb"))
    con = duckdb.connect()
    sig = con.execute(f"SELECT variant, pool, t, traders_at_t, farm, segment, day FROM read_parquet('{out}/signals.parquet') ORDER BY 1, 2").fetchall()
    # A fires for D10 and D20 (25 SOL >= max(D, 10 x p90 = 5)), for T5 and T10 (12 traders), under S1 and S5; not D50, not T20
    variants = sorted({v for v, *_ in sig})
    assert variants == sorted(f"S{S}_D{D}_T{T}" for S in (1, 5) for D in (10, 20) for T in (5, 10))
    assert {p for _, p, *_ in sig} == {"A"}
    assert con.execute(f"SELECT DISTINCT mint FROM read_parquet('{out}/signals.parquet')").fetchall() == [("MA",)]
    assert all(t == SLOT0 + 900_000 or t == SLOT0 + 900_010 for _, _, t, *_ in sig)  # the first burst of the day only
    assert all(tr == 7 and farm == "organic" and seg == "pool_reversed" and day == first_day for _, _, _, tr, farm, seg, day in sig)
    assert info["signals"] == 8 and info["cells"] == 18 and info["per_cell"]["S1_D10_T5"]["traders_at_t_ge_T"] == 1
    assert info["per_cell"]["S1_D10_T10"]["traders_at_t_ge_T"] == 0  # 7 traders up to the D leg, 12 in the hour
    assert info["signals_without_mint"] == 0 and info["mayhem_excluded"] is True
    # the report without a markout: frequency, sleeping pools, liquidity (depth 100 SOL: pool share 0.5 % at 0.5 SOL, average
    # fill 0.5 %, 1 % at 1.01 SOL), the point-in-time share, and the legend's selection note
    md = revival.report(str(out / "revival.md"), str(out / "signals.parquet"), None, str(out / "run-detect.json"))
    assert f"| S1_D10_T5 | 1 |" in md.replace("| S1_D10_T5 | 1 | 1 |", "| S1_D10_T5 | 1 |") and "sleeping pools (S1" in md
    liq = [l for l in md.splitlines() if l.startswith("| S1_D10_T5 | 1 | 100.0")]
    assert len(liq) == 1 and "| 0% | 0.5% / 2.0% / 4.8% | 0.5% / 2.0% / 5.0% | 1.01 SOL | 100% | 100% | 0% |" in liq[0]
    assert "traders at the D leg >= T" in md and "mayhem pools excluded" in md and "dev 1033-1046" in md
    assert (out / "revival.json").exists()
    # require_mint drops a pool without a mint in the store
    st = duckdb.connect(str(tmp_path / "mini.duckdb"))
    st.execute("UPDATE pools SET mint = NULL WHERE pool = 'A'")
    st.close()
    info2 = revival.detect(str(agg), str(tmp_path / "det2"), first_day=first_day, store=str(tmp_path / "mini.duckdb"), require_mint=True)
    assert info2["signals_without_mint"] == 8 and info2["pools_without_mint"] == 1
    assert con.execute(f"SELECT count(*) FROM read_parquet('{tmp_path}/det2/signals.parquet')").fetchone()[0] == 0
    info3 = revival.detect(str(agg), str(tmp_path / "det3"), first_day=first_day, store=str(tmp_path / "mini.duckdb"))
    assert con.execute(f"SELECT count(*), sum(mint_known::INT) FROM read_parquet('{tmp_path}/det3/signals.parquet')").fetchone() == (8, 0)
