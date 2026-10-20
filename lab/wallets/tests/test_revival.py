"""revival aggregate on a mini store: hours from block times, SOL volume and inflow by direction, the threshold
crossing (slot, tx, traders up to the crossing leg, depth after it), non-SOL pools and non-trade legs left out,
buckets = unbucketed, and a smoke's slot bounds; detect on an aggregate plus the candidate hours' legs in a mini store: the
point-in-time t (running volume >= max(D, 10 x p90) and running traders >= T), the shift from the D crossing, the dedup
per day, the age, mayhem and mint rules, the report, and the regression that a history cut right after the signal leg gives
the same signal."""
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


def _detect_fixture(tmp_path, truncate=False):
    """A sleeping pre-window pool A with one burst hour on day 9 (and a second burst the same day), an always-active pool B,
    a young in-window pool C and a mayhem pool M, as pool_hour rows plus the legs of A's burst hours in a mini store. A's
    7-day window before the burst (day 2 5h .. day 9 4h) holds six 0.5 SOL hours and one 2.5 SOL hour: p90 = 1.3 SOL, so
    the D10 threshold is max(10, 13) = 13 SOL, above the D crossing. The second burst of the day sees the first one in its
    window (p90 = 9.55 SOL, threshold 95.5 SOL): it feeds only the D50 cells, the D10 / D20 cells keep the earlier t. With
    truncate=True the first burst hour's history stops right after the leg that fires D10_T5 (the regression: the signal
    must not depend on what comes after it)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    agg = tmp_path / ("agg_cut" if truncate else "agg")
    agg.mkdir()
    day0 = 1_789_000_000 // 86400 * 86400  # some UTC midnight; FIRST_DAY is passed explicitly below
    H = 3600
    rows = []

    def hour(pool, h, vol_sol, traders, trades=10, farmer_trades=0, first=None, last=None, depth=100 * SOL):
        first = first if first is not None else SLOT0 + (h - day0) // 267 * 1000
        rows.append({"pool": pool, "hour": h, "trades": trades, "buys": trades, "sells": 0, "sol_volume": int(vol_sol * SOL), "sol_buy": int(vol_sol * SOL),
                     "sol_sell": 0, "net_inflow": int(vol_sol * SOL), "traders": traders, "buyers": traders, "farmer_trades": farmer_trades,
                     "farmer_sol": 0, "first_slot": first, "last_slot": last if last is not None else first + 5,
                     "end_depth": depth, "end_b": 1, "end_e": 2, "end_vq": 3, "end_bps": 25,
                     "t10_slot": None, "t10_tx": None, "t10_traders": None, "t10_depth": None, "t20_slot": None, "t20_tx": None, "t20_traders": None,
                     "t20_depth": None, "t50_slot": None, "t50_tx": None, "t50_traders": None, "t50_depth": None})
    for dday in range(9):
        hour("A", day0 + dday * 86400 + 3 * H, 0.5, 1)
    hour("A", day0 + 8 * 86400 + 10 * H, 2.5, 2)  # the 2.5 SOL hour in the window: p90 over [0.5 x 6, 2.5] = 1.3 SOL
    burst = day0 + 9 * 86400 + 5 * H
    B0 = SLOT0 + 900_000
    # A's burst legs (slot offset, tx, trader, SOL, depth, farmer): running volume / distinct traders in the comment
    legs = [(0, 1, "a", 6, 100, True),      # 6 / 1
            (0, 2, "b", 4.5, 100, True),    # 10.5 / 2: the D10 crossing (t_d_slot = B0), below 13
            (2, 1, "c", 1, 100, False),     # 11.5 / 3
            (4, 1, "d", 1.5, 100, False),   # 13 / 4: >= 13
            (5, 1, "a", 0.5, 100, False),   # 13.5 / 4: a again (no new trader)
            (6, 1, "e", 1, 100, False),     # 14.5 / 5: D10_T5 fires here (t = B0 + 6, 6 trades, 2 farmer)
            (10, 1, "f", 9.5, 100, False),  # 24 / 6: D20 crossing and D20_T5
            (12, 1, "g", 0.5, 100, False),  # 24.5 / 7
            (14, 1, "h", 0.5, 100, False),  # 25 / 8
            (16, 1, "i", 0.5, 100, False),  # 25.5 / 9
            (18, 1, "j", 0.5, 120, False)]  # 26 / 10: D10_T10 and D20_T10 (depth 120)
    if truncate:
        legs = legs[:6]
    vol, trd, n = sum(l[3] for l in legs), len({l[2] for l in legs}), len(legs)
    hour("A", burst, vol, trd, trades=n, farmer_trades=2, first=B0, last=B0 + legs[-1][0])
    store_legs = [("buy", "A", B0 + o, tx, 0, 0, t, True, int(v * SOL), d * SOL, f) for o, tx, t, v, d, f in legs]
    # a second burst the same day (15 traders x 8 SOL = 120 SOL, threshold 95.5: crossed at leg 12, 96 SOL, 12 traders; the
    # 50 SOL crossing at leg 7): D50_T5 / D50_T10 only, the D10 / D20 cells are deduplicated by the day; and a 4 SOL hour on day 10
    if not truncate:
        hour("A", burst + 2 * H, 120, 15, trades=15, first=B0 + 10_000, last=B0 + 10_014)
        store_legs += [("buy", "A", B0 + 10_000 + i, 1, 0, 0, f"k{i}", True, 8 * SOL, 100 * SOL, False) for i in range(15)]
    hour("A", day0 + 10 * 86400 + 5 * H, 4, 6)
    for h in range(0, 11 * 24):  # B: active every hour, never sleeping
        hour("B", day0 + h * H, 50, 20)
    hour("C", burst, 25, 12, first=SLOT0 + 950_000)  # C: in-window, created 2 days before: too young
    for dday in range(9):
        hour("M", day0 + dday * 86400 + 3 * H, 0.5, 1)
    hour("M", burst, 25, 12, first=SLOT0 + 960_000)  # M: mayhem, excluded
    pq.write_table(pa.table({k: [r[k] for r in rows] for k in rows[0]}), agg / "pool_hour.parquet")
    pools = {"pool": ["A", "B", "C", "M"], "orientation": ["reversed", "normal", "reversed", "reversed"], "quote_class": ["reversed", "sol", "reversed", "reversed"],
             "created_in_period": [False, False, True, False], "create_slot": [None, None, SLOT0 + 900_000 - 2 * 323_000, None],
             "mayhem": [False, False, False, True], "coin_creator": ["c"] * 4, "b0": [1] * 4, "q0": [2] * 4}
    pq.write_table(pa.table(pools), agg / "pools.parquet")
    first_day = str(__import__("datetime").date(1970, 1, 1) + __import__("datetime").timedelta(days=(day0 + 9 * 86400) // 86400))
    st_path = tmp_path / ("mini_cut.duckdb" if truncate else "mini.duckdb")
    st = duckdb.connect(str(st_path))
    st.execute("CREATE TABLE pools (pool VARCHAR, mint VARCHAR)")
    st.execute("INSERT INTO pools VALUES ('A', 'MA'), ('B', 'MB'), ('C', 'MC'), ('M', 'MM')")
    st.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, trader VARCHAR, "
               "token_buy BOOLEAN, sol_amount BIGINT, sol_depth BIGINT, farmer BOOLEAN)")
    st.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", store_legs)
    st.execute("INSERT INTO pool VALUES ('withdraw', 'A', ?, 3, 0, 0, 'lp', NULL, NULL, 50000000000, NULL)", [B0 + 3])  # not a trade
    st.close()
    return agg, st_path, first_day, B0


def test_detect(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path))
    agg, st_path, first_day, B0 = _detect_fixture(tmp_path)
    out = tmp_path / "det"
    info = revival.detect(str(agg), str(out), first_day=first_day, store=str(st_path))
    con = duckdb.connect()
    sig = {(r[0]): r[1:] for r in con.execute(f"SELECT variant, pool, t, t_tx, traders_at_t, depth_at_t, volume_at_t, trades_at_t, farm, segment, day, "
                                               f"thr_sol, t_d_slot, shift_slots FROM read_parquet('{out}/signals.parquet') ORDER BY 1").fetchall()}
    # the first burst fires D10 and D20 (26 SOL >= max(D, 10 x p90 = 13)) for T5 and T10 (10 traders), under S1 and S5; the
    # second burst D50 (threshold 95.5) for T5 and T10 (15 traders); never T20
    assert sorted(sig) == sorted(f"S{S}_D{D}_T{T}" for S in (1, 5) for D in (10, 20, 50) for T in (5, 10))
    assert all(v[0] == "A" and v[7] == "organic" and v[8] == "pool_reversed" and v[9] == first_day for v in sig.values())
    for S in (1, 5):
        # D10_T5: the 13 SOL threshold is crossed at leg 4 (13 SOL), the fifth trader arrives at leg 6: t = B0 + 6, after
        # 6 trades of 14.5 SOL; the D crossing itself was at B0 (shift 6 slots)
        assert sig[f"S{S}_D10_T5"][1:7] == (B0 + 6, 1, 5, 100 * SOL, int(14.5 * SOL), 6) and sig[f"S{S}_D10_T5"][10:] == (13.0, B0, 6)
        # D20_T5: 20 SOL at the 9.5 SOL leg (6 traders): t = B0 + 10 = the D crossing (shift 0)
        assert sig[f"S{S}_D20_T5"][1:5] == (B0 + 10, 1, 6, 100 * SOL) and sig[f"S{S}_D20_T5"][10:] == (20.0, B0 + 10, 0)
        # T10: the tenth trader is the last leg (depth 120 SOL there)
        assert sig[f"S{S}_D10_T10"][1:5] == (B0 + 18, 1, 10, 120 * SOL) and sig[f"S{S}_D10_T10"][12] == 18
        assert sig[f"S{S}_D20_T10"][1:5] == (B0 + 18, 1, 10, 120 * SOL) and sig[f"S{S}_D20_T10"][12] == 8
        # D50 from the second burst: the 95.5 SOL threshold at leg 12 (96 SOL, 12 traders), the 50 SOL crossing at leg 7
        for T in (5, 10):
            assert sig[f"S{S}_D50_T{T}"][1:7] == (B0 + 10_011, 1, 12, 100 * SOL, 96 * SOL, 12) and sig[f"S{S}_D50_T{T}"][10:] == (95.5, B0 + 10_006, 5)
    assert con.execute(f"SELECT DISTINCT mint FROM read_parquet('{out}/signals.parquet')").fetchall() == [("MA",)]
    assert info["signals"] == 12 and info["cells"] == 18 and info["candidate_hours"] == 2 and info["candidate_legs"] == 26
    assert info["per_cell"]["S1_D10_T5"] == {"signals": 1, "pools": 1, "days": 1, "reversed": 1, "shifted": 1, "shift_slots_p50": 6.0, "shift_slots_p90": 6.0}
    assert info["per_cell"]["S1_D20_T5"]["shifted"] == 0
    assert info["consistency"] == {"candidate_hours": 2, "hours_without_legs": 0, "trades_mismatch": 0, "volume_mismatch": 0, "traders_mismatch": 0}
    assert con.execute(f"SELECT hour_sol_volume, hour_traders, hour_trades FROM read_parquet('{out}/signals.parquet') WHERE variant = 'S1_D10_T5'").fetchone() == (26 * SOL, 10, 11)
    assert info["signals_without_mint"] == 0 and info["mayhem_excluded"] is True
    # the report without a markout: frequency, sleeping pools, liquidity (depth 100 SOL: pool share 0.5 % at 0.5 SOL, average
    # fill 0.5 %, 1 % at 1.01 SOL), the shift columns and the legend
    md = revival.report(str(out / "revival.md"), str(out / "signals.parquet"), None, str(out / "run-detect.json"))
    assert "| S1_D10_T5 | 1 | 1 |" in md and "sleeping pools (S1" in md
    liq = [l for l in md.splitlines() if l.startswith("| S1_D10_T5 | 1 | 100.0")]
    assert len(liq) == 1 and "| 0% | 0.5% / 2.0% / 4.8% | 0.5% / 2.0% / 5.0% | 1.01 SOL | 6 / 6 | 100% | 100% | 0% |" in liq[0]
    assert "| 1.21 SOL | 18 / 18 | 100% |" in md  # D10_T10: depth 120 SOL at t
    assert "Point in time by construction" in md and "mayhem pools excluded" in md and "dev 1033-1046" in md
    assert (out / "revival.json").exists()
    # require_mint drops a pool without a mint in the store
    st = duckdb.connect(str(st_path))
    st.execute("UPDATE pools SET mint = NULL WHERE pool = 'A'")
    st.close()
    info2 = revival.detect(str(agg), str(tmp_path / "det2"), first_day=first_day, store=str(st_path), require_mint=True)
    assert info2["signals_without_mint"] == 12 and info2["pools_without_mint"] == 1
    assert con.execute(f"SELECT count(*) FROM read_parquet('{tmp_path}/det2/signals.parquet')").fetchone()[0] == 0
    info3 = revival.detect(str(agg), str(tmp_path / "det3"), first_day=first_day, store=str(st_path))
    assert con.execute(f"SELECT count(*), sum(mint_known::INT) FROM read_parquet('{tmp_path}/det3/signals.parquet')").fetchone() == (12, 0)


def test_detect_is_point_in_time(tmp_path, monkeypatch):
    """Two histories identical up to the signal leg of D10_T5 and different after it (one stops there) give the same D10_T5
    signal; the cells whose rule is met only by later legs exist only in the full history."""
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path))
    agg, st_path, first_day, B0 = _detect_fixture(tmp_path)
    agg_cut, st_cut, _, _ = _detect_fixture(tmp_path, truncate=True)
    full = revival.detect(str(agg), str(tmp_path / "full"), first_day=first_day, store=str(st_path))
    cut = revival.detect(str(agg_cut), str(tmp_path / "cut"), first_day=first_day, store=str(st_cut))
    con = duckdb.connect()
    cols = "variant, pool, t, t_tx, traders_at_t, depth_at_t, volume_at_t, trades_at_t, farm, thr_sol, t_d_slot, shift_slots, day"
    f = con.execute(f"SELECT {cols} FROM read_parquet('{tmp_path}/full/signals.parquet') WHERE variant LIKE '%_D10_T5' ORDER BY 1").fetchall()
    c = con.execute(f"SELECT {cols} FROM read_parquet('{tmp_path}/cut/signals.parquet') ORDER BY 1").fetchall()
    assert f == c and len(c) == 2 and c[0][2] == B0 + 6
    assert full["signals"] == 12 and cut["signals"] == 2 and cut["candidate_hours"] == 1 and cut["candidate_legs"] == 6


def test_detect_red_on_inconsistent_store(tmp_path, monkeypatch):
    """The legs read for a candidate hour must reproduce the aggregate's hour totals; a store with one leg less stops RED."""
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path))
    agg, st_path, first_day, B0 = _detect_fixture(tmp_path)
    st = duckdb.connect(str(st_path))
    st.execute("DELETE FROM pool WHERE pool = 'A' AND slot = ? AND trader = 'j'", [B0 + 18])
    st.close()
    with pytest.raises(SystemExit, match="RED.*trades_mismatch': 1.*volume_mismatch': 1.*traders_mismatch': 1"):
        revival.detect(str(agg), str(tmp_path / "red"), first_day=first_day, store=str(st_path))


def test_candidate_legs_plan():
    """The candidate legs join is a hash join on pool with the slot range as a filter, never a nested loop (the DuckDB lesson
    of 99zp: a range-only predicate plans a blockwise NL join)."""
    con = duckdb.connect()
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, trader VARCHAR, "
                "sol_amount BIGINT, sol_depth BIGINT, farmer BOOLEAN)")
    con.execute("CREATE TABLE cand (pool VARCHAR, hour BIGINT, first_slot BIGINT, last_slot BIGINT)")
    plan = "\n".join(str(r) for r in con.execute("EXPLAIN " + revival.candidate_legs_sql()).fetchall())
    assert "HASH_JOIN" in plan and "NESTED_LOOP" not in plan and "BLOCKWISE" not in plan and "CROSS_PRODUCT" not in plan
