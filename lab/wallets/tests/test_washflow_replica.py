"""washflow_replica on a mini store: the fit-half selection from wallet-days, the scale factor, the price in SOL per token
(normal e / b, reversed b / e), ppost from the wallet's own post-state, p_k as the first leg at slot >= s + k, the three
replica variants with fees, the day bootstrap and the outcome rule."""
import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets.explore import washflow_replica as wr

SOL = 10**9
EPOCH0 = 1033 * 432_000
BT0 = 1789084800  # 2026-09-11 00:00 UTC


def _blocks(con, n_days=21, slots_per_day=1000):
    # 21 UTC days of `slots_per_day` slots each from EPOCH0; block_time 86400 / slots_per_day s apart
    con.execute(f"CREATE TABLE blocks AS SELECT {EPOCH0} + i AS slot, ({BT0} + i * (86400.0 / {slots_per_day}))::BIGINT AS block_time FROM range(0, {n_days * slots_per_day}) t(i)")


def _store(path):
    con = duckdb.connect(str(path))
    _blocks(con)
    con.execute("CREATE TABLE pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR, mint VARCHAR, create_slot BIGINT)")
    con.execute("INSERT INTO pools VALUES ('N', 'sol', 'normal', 'MN', ?), ('R', 'reversed', 'reversed', 'MR', ?)", [EPOCH0, EPOCH0])
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, trader VARCHAR, token_buy BOOLEAN, "
                "sol_amount BIGINT, b BIGINT, e BIGINT, base BIGINT, e_delta BIGINT, farmer BOOLEAN)")
    test0 = EPOCH0 + 10 * 1000  # first slot of 2026-09-21
    rows = []
    # fit half: wallet A buys with p90 leg 0.5 SOL (f_w = 0.5), wallet B with 0.2 SOL (f_w = 1)
    for i in range(10):
        rows.append(("buy", "N", EPOCH0 + 100 + i, 1, 0, 0, "A", True, int(0.5 * SOL) if i < 9 else int(0.1 * SOL), 10**12, 100 * SOL, 10**9, int(0.5 * SOL), True))
        rows.append(("buy", "N", EPOCH0 + 200 + i, 1, 0, 0, "B", True, int(0.2 * SOL), 10**12, 100 * SOL, 10**9, int(0.2 * SOL), True))
    # test half, normal pool N (price e/b in lamports per token unit): A buys 1 SOL at slot t (pre e 100 SOL, b 1e12 -> p0 0.1; post e 101, b 0.99e12 -> ppost 0.10202)
    t = test0 + 50
    rows.append(("buy", "N", t, 1, 0, 0, "A", True, 1 * SOL, 10**12, 100 * SOL, 10**10, 1 * SOL, True))
    # other legs in N: slot t + 1 (pre = A's post-state), slot t + 2 (price 0.105), slot t + 3 (price 0.110)
    rows.append(("buy", "N", t + 1, 1, 0, 0, "X", True, 1 * SOL, 99 * 10**10, 101 * SOL, 10**9, 1 * SOL, False))
    rows.append(("sell", "N", t + 2, 1, 0, 0, "X", False, 1 * SOL, 10**12, 105 * SOL, 10**9, -1 * SOL, False))
    rows.append(("sell", "N", t + 3, 1, 0, 0, "X", False, 1 * SOL, 10**12, 110 * SOL, 10**9, -1 * SOL, False))
    # A sells 1 SOL at slot t + 20 (pre e 110, b 1e12), next leg at t + 25 with price 0.1
    rows.append(("sell", "N", t + 20, 1, 0, 0, "A", False, 1 * SOL, 10**12, 110 * SOL, 10**10, -1 * SOL, True))
    rows.append(("buy", "N", t + 25, 1, 0, 0, "X", True, 1 * SOL, 10**12, 100 * SOL, 10**9, 1 * SOL, False))
    # reversed pool R (price b/e): B buys 0.2 SOL (token buy = pool sell of WSOL: b + base) at slot t on day 1; the next leg at t + 3 has price 2x
    rows.append(("sell", "R", t, 2, 0, 0, "B", True, int(0.2 * SOL), 100 * SOL, 10**15, int(0.2 * SOL), -2 * 10**12, True))
    rows.append(("sell", "R", t + 3, 1, 0, 0, "Y", True, 1 * SOL, 200 * SOL, 10**15, 10**9, -10**13, False))
    # B on every other test day: one buy and a sell that nets +0.01 SOL, so the per-day series has 10 days
    for d in range(1, 10):
        s = test0 + d * 1000 + 10
        rows.append(("buy", "N", s, 1, 0, 0, "B", True, int(0.2 * SOL), 10**12, 100 * SOL, 10**9, int(0.2 * SOL), True))
        rows.append(("sell", "N", s + 5, 1, 0, 0, "B", False, int(0.21 * SOL), 10**12, 100 * SOL, 10**9, -int(0.21 * SOL), True))
        rows.append(("buy", "N", s + 6, 1, 0, 0, "X", True, 1 * SOL, 10**12, 100 * SOL, 10**9, 1 * SOL, False))
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.close()


def _money(tmp_path):
    d = tmp_path / "money"
    d.mkdir()
    rows = []
    for day in range(11, 21):  # fit half: A and B qualify (4+ farmer-pool days, 10k legs, margin 1 %); C fails the margin; D the legs
        for w, legs, cash in (("A", 3000, 10 * SOL), ("B", 3000, 10 * SOL), ("C", 3000, -1 * SOL), ("D", 100, 10 * SOL)):
            rows.append({"wallet": w, "day": f"2026-09-{day:02d}", "cash": float(cash), "fees": 1e6, "buy_sol": float(100 * SOL), "sell_sol": float(100 * SOL),
                         "n_pool_legs": float(legs), "n_farmer_legs": float(legs), "median_buy_sol": float(0.3 * SOL)})
    rows.append({"wallet": "E", "day": "2026-09-25", "cash": 1e12, "fees": 0.0, "buy_sol": 1e12, "sell_sol": 1e11, "n_pool_legs": 50000.0, "n_farmer_legs": 50000.0, "median_buy_sol": 0.3e9})  # test half only: never selected
    pq.write_table(pa.Table.from_pylist(rows), d / "wallet-day-0.parquet")
    return str(d)


def test_replica_on_the_mini_store(tmp_path, monkeypatch, capfd):
    _store(tmp_path / "s.duckdb")
    money = _money(tmp_path)
    monkeypatch.setattr(wr, "STORE", str(tmp_path / "s.duckdb"))
    monkeypatch.setattr(wr, "MONEY", money)
    monkeypatch.setenv("BOOT", "300")
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["washflow_replica", str(out)])
    wr.main()
    sel = {r["wallet"]: r for r in pq.read_table(str(out / "selected.parquet")).to_pylist()}
    assert set(sel) == {"A", "B"} and sel["A"]["rank_fit"] == 1 and sel["B"]["rank_fit"] == 2
    assert sel["A"]["f_w"] == pytest.approx(0.5) and sel["B"]["f_w"] == pytest.approx(1.0)  # p90 of A's fit buys 0.5 SOL, B's 0.2
    sh = {(r["wallet"], r["pool"], r["slot"]): r for r in pq.read_table(str(out / "legs-shift.parquet")).to_pylist()}
    t = EPOCH0 + 10 * 1000 + 50
    a = sh[("A", "N", t)]
    assert a["p0"] == pytest.approx(0.1) and a["ppost"] == pytest.approx(101 * SOL / 0.99e12) and a["sg"] == -1 and a["s"] == pytest.approx(0.5)
    # p_k = the pre-trade price of the first leg at slot >= t + k: k 1 -> X's leg at t + 1 (= A's post-state), k 2 -> 1.05e-10, k 3 -> 1.10e-10
    assert a["p1"] == pytest.approx(a["ppost"]) and a["p2"] == pytest.approx(0.105) and a["p3"] == pytest.approx(0.110) and a["d3"] == pytest.approx(110.0)
    b = sh[("B", "R", t)]
    assert b["p0"] == pytest.approx(100 * SOL / 1e15) and b["ppost"] == pytest.approx(100.2 * SOL / (1e15 - 2e12)) and b["p3"] == pytest.approx(200 * SOL / 1e15)
    s_a = sh[("A", "N", t + 20)]
    assert s_a["sg"] == 1 and s_a["p3"] == pytest.approx(0.1)  # the sell at t + 20: next leg at t + 25
    # replica cash of A's buy at k 3: (i) -0.5 x 0.110 / 0.1 = -0.55; (ii) -0.5 x 0.110 / ppost; (iii) = (ii) - 0.25 / 110 - fee
    assert -a["s"] * a["p3"] / a["p0"] == pytest.approx(-0.55)
    res = json.load(open(out / "washflow.json"))
    c = res["cells"]["k3_i_base_all"]
    assert c["days"] == 10 and c["legs"] == len(sh) and res["input"]["wallets"] == 2
    assert "legs per day over 2 wallets" in (out / "washflow.md").read_text() and "own later legs" in (out / "washflow.md").read_text()
    assert res["verdict"]["outcome"] in ("LEAD LIVES", "CLOSED") and set(res["verdict"]) == {"outcome", "net_per_leg_positive", "lower_bound_positive", "pos_days_ok", "without_top5_positive"}
    md = (out / "washflow.md").read_text()
    assert "**Outcome:" in md and "| 3 | iii | base | all |" in md and "| 1 | A" in md
    assert "selected 2 wallets" in capfd.readouterr().err


def test_guards_red_on_missing_scale_factor_short_test_half_and_row_loss(tmp_path, monkeypatch):
    _store(tmp_path / "s.duckdb")
    money = _money(tmp_path)
    monkeypatch.setattr(wr, "STORE", str(tmp_path / "s.duckdb"))
    monkeypatch.setattr(wr, "MONEY", money)
    monkeypatch.setenv("BOOT", "100")
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    # a selected wallet without buy legs in the fit half: no scale factor, RED (C qualifies on the wallet-days once its margin is positive)
    con.execute("UPDATE pool SET trader = 'Z' WHERE trader = 'B' AND slot < ?", [EPOCH0 + 10 * 1000])
    con.close()
    monkeypatch.setattr("sys.argv", ["washflow_replica", str(tmp_path / "o1")])
    with pytest.raises(SystemExit, match="without buy legs in the fit half"):
        wr.main()
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    con.execute("UPDATE pool SET trader = 'B' WHERE trader = 'Z'")
    # a leg in a pool without a pools row: silent row loss, RED
    con.execute("INSERT INTO pool VALUES ('buy', 'Q', ?, 1, 0, 0, 'A', true, 100000000, 1000000000000, 100000000000, 1000000000, 100000000, true)", [EPOCH0 + 10 * 1000 + 300])
    con.close()
    monkeypatch.setattr("sys.argv", ["washflow_replica", str(tmp_path / "o2")])
    with pytest.raises(SystemExit, match="silent row loss"):
        wr.main()
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    con.execute("DELETE FROM pool WHERE pool = 'Q'")
    # fewer than 10 test days with legs: RED (rule 3 needs all ten)
    con.execute("DELETE FROM pool WHERE slot >= ? AND slot < ?", [EPOCH0 + 19 * 1000, EPOCH0 + 20 * 1000])
    con.close()
    monkeypatch.setattr("sys.argv", ["washflow_replica", str(tmp_path / "o3")])
    with pytest.raises(SystemExit, match="the rule needs all 10"):
        wr.main()


def test_value_sql_and_day_boot():
    assert "p3 / p0" in wr.value_sql(3, "i", 5000) and "p2 / ppost" in wr.value_sql(2, "ii", 5000) and "s * s / greatest" in wr.value_sql(3, "iii", 5000)
    lo, hi = wr.day_boot([1.0] * 10, 200)
    assert lo == pytest.approx(1.0) and hi == pytest.approx(1.0)
    assert wr.day_boot([1.0], 200) == (None, None)
    assert wr.RULE["primary"] == {"variant": "iii", "k": 3, "fees": "base"} and wr.RULE["size_sol"] == 0.25 and wr.RULE["margin"] == 0.005
