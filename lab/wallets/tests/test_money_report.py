"""Money report on synthetic wallet-day rows: the label window and hierarchy, the day bootstrap and Holm,
the placebo rank, the measurement window of a late wallet, and the zero-sum table."""

import os

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import money_report as R

SOL = 10**9


def _rows():
    rows = []
    days = [f"2026-09-{d:02d}" for d in range(11, 31)]

    def add(wallet, day, pnl, n_opened=2, n_snipe=0, median_buy=0.2, n_pool=4, n_farmer=0, hold=1000, n_closed=2, n_legs=4):
        rows.append({"wallet": wallet, "day": day, "ms_per_slot": 316.4 if day < "2026-09-18" else 267.1, "regime": "320ms" if day < "2026-09-18" else "270ms",
                     "cash": pnl, "fees": 0, "paid_by_other": 0, "value_end": 0.0, "dvalue": 0.0, "pnl": float(pnl),
                     "buy_sol": 10 * SOL, "sell_sol": 10 * SOL, "n_legs": n_legs, "n_buys": n_legs // 2, "n_pool_legs": n_pool, "n_farmer_legs": n_farmer,
                     "venue_fees": 0, "n_mints": 2, "n_opened": n_opened, "n_snipe": n_snipe, "median_buy_sol": median_buy * SOL, "n_closed": n_closed,
                     "median_hold_slots": hold})
    rng = np.random.default_rng(1)
    for i in range(40):  # snipers: half their positions in the create slot, win 0.02 SOL a day on days 10-18
        for day in days:
            add(f"S{i}", day, (0.02 * SOL + rng.normal(0, 0.002 * SOL)) if day >= "2026-09-20" else 0.0, n_opened=4, n_snipe=2)
    for i in range(40):  # bots: 60 positions a day, 5 s holds, lose
        for day in days:
            add(f"B{i}", day, -0.01 * SOL, n_opened=60, hold=20, n_legs=120)
    for i in range(200):  # retail: noise around 0
        for day in days:
            add(f"R{i}", day, rng.normal(0, 0.01 * SOL), n_opened=2)
    for day in days[:9]:  # a farmer labelled on days 1-9, inactive later
        add("F0", day, 0.0, n_pool=40, n_farmer=30)
    for day in days[10:]:  # a late wallet: first seen 09-21, labelled on 09-22/23, measured from 09-24
        add("L0", day, 0.05 * SOL, n_opened=4, n_snipe=4)
    return rows


def _dir(tmp_path):
    out = tmp_path / "money"
    os.makedirs(out)
    pq.write_table(pa.Table.from_pylist(_rows()), out / "wallet-day-0.parquet")
    pq.write_table(pa.Table.from_pylist([{"fee_payer": "PAY", "wallet": "R0", "mint": "M", "n": 3, "first_slot": 1, "first_day": "2026-09-12"},
                                          {"fee_payer": "PAY", "wallet": "R1", "mint": "M", "n": 1, "first_slot": 1, "first_day": "2026-09-12"}]), out / "payers.parquet")
    pq.write_table(pa.Table.from_pylist([{"wallet": "R1", "mint": "M", "create_slot": 1, "day": "2026-09-12"},
                                          {"wallet": "R2", "mint": "M2", "create_slot": 9, "day": "2026-09-25"}]), out / "creators.parquet")  # R2 creates during the measurement
    pq.write_table(pa.Table.from_pylist([{"venue": "pool", "venue_fees": 10 * SOL, "network_fees": 2 * SOL, "legs": 1000, "gross_sol": 1000 * SOL,
                                          "cash_all": -110 * SOL, "sol_start": 500 * SOL, "sol_end": 650 * SOL, "external_in": 50 * SOL}]), out / "venue_flows.parquet")
    pq.write_table(pa.Table.from_pylist([{"mint": "M", "day": "2026-09-11", "price": 1.0, "depth": SOL, "venue": "pool", "last_slot": 100, "traded": True}]),
                   out / "marks.parquet")
    # LP legs: pool A is drained 100 slots after its trigger, pool B only partially (50 %), pool C never
    pq.write_table(pa.Table.from_pylist([
        {"pool": "A", "mint": "MA", "orientation": "reversed", "slot": 446_300_100, "tx_index": 1, "kind": "withdraw", "sol_in": -95 * SOL, "depth_pre": 100 * SOL, "depth_post": 5 * SOL},
        {"pool": "B", "mint": "MB", "orientation": "reversed", "slot": 446_300_100, "tx_index": 1, "kind": "withdraw", "sol_in": -50 * SOL, "depth_pre": 100 * SOL, "depth_post": 50 * SOL},
        {"pool": "A", "mint": "MA", "orientation": "reversed", "slot": 446_299_000, "tx_index": 1, "kind": "withdraw", "sol_in": -95 * SOL, "depth_pre": 100 * SOL, "depth_post": 5 * SOL}]),
        out / "lp_legs.parquet")  # the earlier drain of A (before the trigger) must not count
    return str(out)


def _parts(tmp_path):
    pdir = tmp_path / "parts"
    os.makedirs(pdir)
    rows = []
    for pool, reg, ms in (("A", "270ms", 267.1), ("B", "270ms", 267.1), ("C", "320ms", 316.4)):
        for v in ("tp4_T600", "tp6_T600"):
            rows.append({"family": "F7", "variant": v, "segment": "pool_reversed", "pool": pool, "mint": "M" + pool, "t": 446_300_000, "day": "2026-09-12", "regime": reg,
                         "d": 2, "d_ms": 2 * ms})
    rows.append({"family": "F7", "variant": "tp4_T600", "segment": "pool_reversed", "pool": "A", "mint": "MA", "t": 446_300_050, "day": "2026-09-12", "regime": "270ms", "d": 1, "d_ms": 267.1})
    rows.append({"family": "N2_F7", "variant": "tp4_T600", "segment": "pool_reversed", "pool": "B", "mint": "MB", "t": 446_300_000, "day": "2026-09-12", "regime": "270ms", "d": 1, "d_ms": 267.1})
    pq.write_table(pa.Table.from_pylist(rows), pdir / "part-0.parquet")
    return str(pdir)


def test_labels_groups_holm_and_placebo(tmp_path):
    con = duckdb.connect()
    con.execute("SET memory_limit = '300MB'; SET threads TO 1")
    assert R.load(con, _dir(tmp_path)) == 1
    labels = dict(con.execute("SELECT wallet, label FROM labels").fetchall())
    assert labels["S0"] == "sniper" and labels["B0"] == "bot" and labels["R5"] == "retail" and labels["F0"] == "farmer"
    assert labels["R1"] == "creator" and labels["R0"] == "creator"  # R1 created on day 2; R0 shares its fee payer with R1 (link from day 2)
    assert labels["R2"] == "retail" and labels["L0"] == "sniper"  # R2 only creates on day 15: not a creator for the measurement; the late wallet on its first two full days
    assert con.execute("SELECT creator_any, creator_before FROM labels WHERE wallet = 'R2'").fetchone() == (True, False)
    assert con.execute("SELECT measure_from FROM labels WHERE wallet = 'L0'").fetchone()[0] == "2026-09-24"
    assert con.execute("SELECT min(day) FROM meas WHERE wallet = 'L0'").fetchone()[0] == "2026-09-24"
    s = R.group_stats(con, "label = 'sniper'", 500)
    assert s["wallets"] == 41 and s["mean"] > 0.015 and s["lo"] > 0 and s["p"] < 0.01 and s["days"] == 11
    b = R.group_stats(con, "label = 'bot'", 500)
    assert b["mean"] < 0 and b["hi"] < 0 and b["share_pos"] == 0.0
    assert R.group_stats(con, "label = 'whale'", 100) is None
    assert R.holm([0.001, 0.2, 0.04, None, 0.0001, 0.3, 0.5]) == {0, 4}  # 0.0001 <= 0.05/7, 0.001 <= 0.05/6, 0.04 > 0.05/5 stops
    lo, hi, p = R.day_boot([1.0, 2.0, 3.0], [1, 1, 1], 200)
    assert lo <= 2.0 <= hi and p < 0.2
    assert R.day_boot([1.0], [1], 10) == (None, None, None)
    q = R.placebo(con, "sniper", 20, 100)
    assert q["draws"] == 20 and q["below"] == 20  # the snipers beat every placebo set of retail-like wallets
    md = R.render(con, 300, 20)
    assert "| sniper |" in md and "**earns**" in md and "| bot |" in md and "with replacement" in md
    bind = md.index("### Binding"); ctrl = md.index("### Control: days 1-9"); r267 = md.index("### Control: regime 270ms"); r316 = md.index("### Control: regime 320ms")
    assert bind < ctrl < r267 < r316  # regime blocks in label order, from the rows
    line = next(l for l in md[bind:ctrl].splitlines() if l.startswith("| bot |"))
    assert line.endswith("**no** |")
    ctrl_bot = next(l for l in md[ctrl:r267].splitlines() if l.startswith("| bot |"))
    assert "| 9 |" in ctrl_bot and "verdict" not in md[ctrl:r267].splitlines()[2]  # 9 label days, no verdict on a control table
    assert "realised Σ (cash − network fees)" in md and "marked Σ value_end" in md
    # the numpy rows reproduce the per-group mean
    rows = R.Rows(con)
    ids = np.array([rows.wallet_id[f"S{i}"] for i in range(40)] + [rows.wallet_id["L0"]])
    assert abs(rows.mean_per_wallet_day(ids) - s["mean"]) < 1e-9
    d = R.drains(con, _parts(tmp_path))
    rows = {l.split(" | ")[0].strip("| "): l for l in d.splitlines() if l.startswith("| F7") or l.startswith("| N2")}
    allrow = rows["F7 triggers, all"].split(" | ")
    assert allrow[1] == "4" and allrow[2] == "3" and allrow[3] == "50%"  # 4 triggers on 3 pools; A's two triggers drained, B partial, C never
    assert allrow[4] == "50%" and allrow[8] == "50%"  # 100 slots at 267 ms = 0.45 min: within 1 min
    assert rows["F7 pools (first trigger per pool)"].split(" | ")[3] == "33%" and rows["N2_F7 twins' pools, all"].split(" | ")[3] == "0%"
    md2 = R.render(con, 300, 20, False, _parts(tmp_path / "again"))
    assert "### LP drain after an F7 trigger" in md2
    z = R.zero_sum(con)[0]
    assert z["venue"] == "pool" and abs(z["resid"]) < 1e-9 and "+0.0" in md  # -110 + 10 + (650 - 500) - 50 = 0


if __name__ == "__main__":
    pytest.main([__file__])
