"""runner_census on synthetic pool-hours: the dense carried series, the SOL conversion of an 'other' pool through its quote's
SOL pool, the runner rule at both floors (3x the minimum over [u-72, u-24] with the depth at that minimum), one runner per
movement, seen / F7 trigger, the momentum entry and its net markouts with a wiped case, the unit pool and the outputs."""
import json
import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets.explore import runner_census as rc

H0 = 496968  # 2026-09-11 00:00 UTC
BT0 = H0 * 3600
USDC = rc.USDC


def _hours(tmp_path, pools, series, parts_pools=()):
    d = tmp_path / "hours"
    d.mkdir()
    pq.write_table(pa.Table.from_pylist([{"pool": p, "quote_class": qc, "orientation": o, "quote_mint": qm, "mint": m, "create_slot": 0} for p, qc, o, qm, m in pools]), d / "pools.parquet")
    pq.write_table(pa.Table.from_pylist([{"hour": H0 + i, "from_slot": 1000 * i, "first_block_time": BT0 + 3600 * i} for i in range(200)]), d / "hour_bounds.parquet")
    rows = []
    for pool, pts in series.items():
        for i, (close, depth) in pts.items():
            rows.append({"pool": pool, "hour": H0 + i, "close": float(close), "depth": float(depth), "fee_bps": 30, "trades": 5, "buys": 3, "farmer_trades": 0, "volume": 1.0, "last_slot": 1000 * i})
    pq.write_table(pa.Table.from_pylist(rows), d / "hours.parquet")
    parts = tmp_path / "parts"
    parts.mkdir()
    pq.write_table(pa.Table.from_pylist([{"family": "F7", "segment": "pool_reversed", "pool": p, "t": 1} for p in parts_pools] or [{"family": "F7", "segment": "pool_reversed", "pool": "none", "t": 1}]), parts / "part-0.parquet")
    return str(d), str(parts)


def test_census_on_synthetic_hours(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    pools = [("A", "sol", "normal", "So1", "MA"), ("B", "reversed", "reversed", None, "MB"), ("Q", "sol", "normal", "So1", "MQ"),
             ("C", "other", "normal", "MQ", "MC"), ("D", "other", "normal", USDC, "MD"), ("E", "sol", "normal", "So1", "ME"), ("F", "other", "normal", "XYZ", "MF")]
    # A: price 1 for hours 0..80 at depth 600 SOL, then 4 from hour 100 on (a runner at both floors), entry at hour 100 (2x the hour-76 close), no later data
    a = {i: (1.0, 600.0) for i in range(0, 81)} | {i: (4.0, 650.0) for i in range(100, 130)}
    # B: reversed, depth 150: only at the secondary floor; its 24 h markout is wiped (depth 0.1 at hour 124)
    b = {i: (1.0, 150.0) for i in range(0, 81)} | {i: (3.5, 160.0) for i in range(100, 124)} | {124: (3.5, 0.1)}
    # Q: the quote token's SOL pool (0.5 SOL per Q, constant); C: 'other' pool in Q units, depth 1,500 Q = 750 SOL, triples -> a runner outside the universe
    q = {i: (0.5, 1000.0) for i in range(0, 130)}
    c = {i: (10.0, 1500.0) for i in range(0, 81)} | {i: (30.0, 1500.0) for i in range(100, 130)}
    # D: USDC pool, depth 150k USDC, triples -> a runner at the primary USDC floor; E: flat; F: unknown quote without a SOL pool, triples (no floor)
    d = {i: (1.0, 150_000.0) for i in range(0, 81)} | {i: (3.0, 150_000.0) for i in range(100, 130)}
    e = {i: (1.0, 600.0) for i in range(0, 130)}
    f = {i: (1.0, 5.0) for i in range(0, 81)} | {i: (3.0, 5.0) for i in range(100, 130)}
    hours, parts = _hours(tmp_path, pools, {"A": a, "B": b, "Q": q, "C": c, "D": d, "E": e, "F": f}, parts_pools=("B",))
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["runner_census", hours, str(out), "--parts", parts, "--boot", "200"])
    rc.main()
    res = json.load(open(out / "census.json"))
    prim, sec = res["runners"]["primary"], res["runners"]["secondary"]
    assert set(prim) == {"sol/normal/SOL", "other/normal/SOL", "other/normal/USDC"} and prim["sol/normal/SOL"]["pools"] == 1 and prim["other/normal/SOL"]["seen"] is False
    assert prim["other/normal/USDC"]["runners"] == 1 and "reversed/reversed/SOL" not in prim  # B's 150 SOL is below the primary floor
    assert sec["reversed/reversed/SOL"]["runners"] == 1 and sec["reversed/reversed/SOL"]["pools_with_f7_trigger"] == 1 and sec["sol/normal/SOL"]["runners"] == 1
    assert res["counts"]["primary"]["runners"] == 3 and res["counts"]["primary"]["seen"] == 1 and res["counts"]["secondary"]["runners"] == 4
    runners = pq.read_table(str(out / "runners.parquet")).to_pylist()
    assert all(r["pool"] != "E" and r["pool"] != "F" for r in runners)  # flat, and no floor for an unknown quote
    assert [r["hour"] - H0 for r in runners if r["pool"] == "A"] == [100, 100]  # one movement, at both floors, first qualifying hour
    ent = {r["pool"]: r for r in pq.read_table(str(out / "entries.parquet")).to_pylist()}
    assert set(ent) == {"A", "C", "D"} and ent["A"]["hour"] - H0 == 100  # B's depth 160 < 500: no entry; the entry needs the 2x over 24 h at the primary floor
    # A's markouts: the price stays 4.0: net = (1-f)(1-s/650) / ((1+f)(1+s/650)) - 1 - network, about -1.0 %
    f, s = 0.003, 0.5
    expect = (1 - f) * (1 - s / 650) / ((1 + f) * (1 + s / 650)) - 1 - 2 * rc.BASE_LAMPORTS / 1e9 / s  # entry at hour 100: depth 650
    assert ent["A"]["net_1h"] == pytest.approx(expect, abs=1e-9) and ent["A"]["net_24h"] == pytest.approx(expect, abs=1e-9)
    assert ent["C"]["unit"] == "SOL" and ent["C"]["price"] == pytest.approx(15.0) and ent["C"]["depth"] == pytest.approx(750.0)  # converted through Q
    m = res["markouts"]
    assert m["SOL_inside"]["pools"] == 1 and m["SOL_outside"]["pools"] == 1 and m["USDC_outside"]["pools"] == 1 and m["USDC_inside"]["entries"] == 0
    assert m["SOL_inside"]["net_24h"]["mean"] == pytest.approx(expect, abs=1e-9) and m["SOL_inside"]["net_24h"]["wiped"] == 0.0
    md = (out / "census.md").read_text()
    assert "**primary floor:** 3 runners" in md and "| sol/normal/SOL | 1 | 1 | True | 0 |" in md and "| SOL_inside | 1 | 1 |" in md


def test_wiped_markout_and_movement_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    pools = [("W", "sol", "normal", "So1", "MW")]
    # W: 1.0 at depth 600 for 80 h; 4.0 from hour 100 (entry 100), the pool is drained at hour 124 (depth 0.2 -> wiped at 24 h);
    # the price falls back to 1.2 at hours 130-160 (below 1.5x the minimum: re-armed) and rises to 4.0 again at hour 200 -> a second movement
    w = {i: (1.0, 600.0) for i in range(0, 81)} | {i: (4.0, 600.0) for i in range(100, 124)} | {124: (4.0, 0.2)} | {i: (1.2, 600.0) for i in range(130, 161)} | {i: (4.0, 600.0) for i in range(199, 200)}
    hours, parts = _hours(tmp_path, pools, {"W": w})
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["runner_census", hours, str(out), "--boot", "100"])
    rc.main()
    runners = [r for r in pq.read_table(str(out / "runners.parquet")).to_pylist() if r["floor"] == "primary"]
    assert [r["hour"] - H0 for r in runners] == [100, 199]  # two movements
    ent = pq.read_table(str(out / "entries.parquet")).to_pylist()
    assert [e["hour"] - H0 for e in ent] == [100, 199] and ent[0]["net_24h"] == -1.0 and ent[0]["net_1h"] > -0.02
    res = json.load(open(out / "census.json"))
    assert res["markouts"]["SOL_inside"]["net_24h"]["wiped"] == 1.0  # the one pool's mean at 24 h is the wiped entry averaged with the second entry (None -> excluded)
