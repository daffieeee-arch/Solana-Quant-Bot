"""Case selection: the self-funded flag and the daily / weekly lists from saved aggregates (no store)."""

import duckdb

from wallets import cases


def _con():
    con = duckdb.connect()
    con.execute("SET memory_limit = '200MB'; SET threads TO 1")
    return con


def _daily_rows():
    # key, mint, trades, traders, volume_sol, p_first, p_last, p_max, peak_slot, first_slot, last_slot, vault_end, buy_sol,
    # top_buyer_share, grad_in_create, curve_buyers, self_funded(stale)
    base = dict(trades=1000, traders=500, volume_sol=5000.0, peak_slot=10, first_slot=1, last_slot=100, vault_end=200.0,
                buy_sol=3000.0, top_buyer_share=0.05, self_funded=False)
    rows = [
        dict(base, key="A", mint="A", p_first=1.0, p_last=5.0, p_max=5.0, grad_in_create=True, curve_buyers=4),     # create-slot bundle
        dict(base, key="B", mint="B", p_first=1.0, p_last=4.0, p_max=6.0, grad_in_create=False, curve_buyers=300),  # organic riser
        dict(base, key="C", mint="C", p_first=1.0, p_last=3.0, p_max=4.0, grad_in_create=False, curve_buyers=None), # curve before the store
        dict(base, key="D", mint="D", p_first=1.0, p_last=2.0, p_max=3.0, grad_in_create=False, curve_buyers=2),    # 2 curve wallets
        dict(base, key="E", mint="E", p_first=1.0, p_last=0.05, p_max=10.0, grad_in_create=False, curve_buyers=50), # dumper
        dict(base, key="F", mint="F", p_first=1.0, p_last=2.0, p_max=2.0, grad_in_create=False, curve_buyers=400, traders=50),  # too few
    ]
    return rows


def test_self_funded_flag_and_daily_lists():
    con = _con()
    rows = _daily_rows()
    cols = list(rows[0].keys())
    con.execute(f"CREATE TABLE d ({', '.join(f'{c} ' + ('VARCHAR' if c in ('key', 'mint') else 'BOOLEAN' if c in ('grad_in_create', 'self_funded') else 'DOUBLE' if isinstance(rows[0][c], float) else 'BIGINT') for c in cols)})")
    con.executemany(f"INSERT INTO d VALUES ({', '.join('?' * len(cols))})", [[r[c] for c in cols] for r in rows])
    sel = cases.daily_select(con, "2026-09-11").to_pylist()
    flags = dict(con.execute("SELECT mint, self_funded FROM d").fetchall())
    assert flags == {"A": True, "B": False, "C": False, "D": True, "E": False, "F": False}
    lists = {}
    for r in sel:
        lists.setdefault(r["list"], []).append(r["mint"])
    assert lists["trending_24h"][:2] == ["A", "B"]           # ranked by (24 h return x volume)
    assert lists["trending_24h_organic"] == ["B", "C"]       # the bundles A and D are out, the small F too
    assert lists["dumpers_24h"] == ["E"]
    r = next(r for r in sel if r["list"] == "trending_24h_organic" and r["mint"] == "B")
    assert abs(r["ret_24h"] - 3.0) < 1e-9 and abs(r["peak_mult"] - 6.0) < 1e-9 and abs(r["end_of_peak"] - 4 / 6) < 1e-9
    assert all(r["day"] == "2026-09-11" for r in sel)


def test_weekly_select_from_saved_aggregate():
    con = _con()
    con.execute("""CREATE TABLE w2 AS SELECT * FROM (VALUES
        ('A', 'pA', 10, 10, true, 1.0, 1000000000, 'A', 'A', 5.0, 20, 5.0, 5000.0, 400, 100.0, 1, false),
        ('B', 'pB', 10, 5, false, 1.0, 1000000000, 'B', 'B', 6.0, 20, 3.0, 5000.0, 400, 100.0, 250, false),
        ('C', 'pC', 10, 5, false, 1.0, 1000000000, 'C', 'C', 9.0, 20, 0.1, 5000.0, 400, 100.0, 4, false),
        ('D', 'pD', 10, 5, false, 1.0, 1000000000, 'D', 'D', 8.0, 20, 0.2, 5000.0, 400, 100.0, 80, false))
        t(mint, pool, open_slot, create_slot, grad_in_create, p_open, supply, symbol, name, p_peak, peak_slot, p_7d,
          volume_7d, traders_7d, vault_7d, curve_buyers, self_funded)""")
    sel = cases.weekly_select(con).to_pylist()
    lists = {}
    for r in sel:
        lists.setdefault(r["list"], []).append(r["mint"])
    assert lists["stayers_7d"] == ["A", "B", "D", "C"]
    assert lists["stayers_7d_organic"] == ["B", "D"]
    assert lists["collapsed_7d"] == ["C", "D"]
    assert lists["collapsed_7d_organic"] == ["D"]
    assert cases.SELF_FUNDED_SQL.endswith("<= 5, false)")
