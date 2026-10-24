"""The racing cost (backtest.racing_cost, E65): the q guard (G1), the ms per slot at d 0, the race table's count of t + 1
token buys and the first buyer's fee, the landing draws and the exact expectation, the gates G2-G5 on a synthetic
dev run and E63's own report, the break-even delay, and W8-B's cost decomposition."""

import argparse
import json
import os

import duckdb
import numpy as np
import pytest

from backtest import capital_ceiling as cc
from backtest import racing_cost as rc

LO = cc.DEV[0]


def day_of_pairs(pairs):
    """A SlotDays from (slot, day) pairs in a fixture's own day labels (its change points)."""
    starts, days = [], []
    for slot, day in sorted(set(pairs)):
        if not days or day != days[-1]:
            starts.append(slot)
            days.append(day)
    return cc.SlotDays(starts, days)
DAYS = ("2026-09-12", "2026-09-12", "2026-09-13", "2026-09-14")
POOLS = ("A", "B", "C", "D")
N_T1 = {"A": 0, "B": 1, "C": 2, "D": 3}  # token-buying transactions in t + 1 (the store below)
COLS = ("family", "segment", "variant", "q", "overlay", "tau", "size_sol", "d", "t", "day", "pool", "farm", "skipped",
        "entry_failed", "cost", "proceeds", "exit_slot", "exit_decision_slot", "exit_attempts", "d_ms", "depth", "exit_reason",
        "tokens", "entry_slot")
TYPES = ("VARCHAR", "VARCHAR", "VARCHAR", "DOUBLE", "VARCHAR", "VARCHAR", "DOUBLE", "INTEGER", "UBIGINT", "VARCHAR", "VARCHAR",
         "VARCHAR", "BOOLEAN", "BOOLEAN", "BIGINT", "BIGINT", "UBIGINT", "UBIGINT", "INTEGER", "DOUBLE", "DOUBLE", "VARCHAR",
         "BIGINT", "UBIGINT")


def trig(i):
    return LO + 10_000 + i * 1000


def write_parquet(path, rows, extra=()):
    con = duckdb.connect()
    cols = COLS + tuple(c for c, _ in extra)
    types = TYPES + tuple(t for _, t in extra)
    con.execute(f"CREATE TABLE x ({', '.join(f'{c} {t}' for c, t in zip(cols, types))})")
    con.executemany(f"INSERT INTO x VALUES ({', '.join('?' * len(cols))})", rows)
    con.execute(f"COPY x TO '{path}' (FORMAT parquet)")


def write_run(root, name, rows, slot_range=None, f7_grid="v1,exits,big", variants=("tp4_T600",) + rc.VARIANTS):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "parts"))
    with open(os.path.join(d, "config.json"), "w") as f:
        json.dump({"engine_commit": "abc", "store": {"slot_start": cc.DEV[0], "slot_end_exclusive": cc.DEV[1], "git_commit": "s"},
                   "slot_range": slot_range, "families": ["F7"], "segments": ["pool_reversed"], "f7_grid": f7_grid, "tau": ["empirical"],
                   "variants": list(variants), "sizes_sol": [0.5, 1.0, 1.25, 5.0]}, f)
    write_parquet(os.path.join(d, "parts", "part-000000.parquet"), rows)
    return d


def row(variant, size, d, i, ret, q=None, ms=270.0, failed=False):
    t = trig(i)
    cost = int(size * 1e9)
    farm = "flagged" if POOLS[i] == "B" else "organic"
    return ("F7", "pool_reversed", variant, (1.0 if d == 0 else 0.5) if q is None else q, "none", "empirical", size, d, t,
            DAYS[i], POOLS[i], farm, False, failed, 0 if failed else cost, 0 if failed else int(cost * (1 + ret)),
            None if failed else t + 100 + d, t + 90 + d, 1, round(d * ms, 1), 200e9, "entry_slippage" if failed else "time",
            0 if failed else 1000 + i, t + d)


def rows_for(ds, ret_of, q=None, ms=270.0):
    return [row(v, s, d, i, ret_of(v, s, d), q=q, ms=ms) for v in rc.VARIANTS for s in rc.SIZES for d in ds for i in range(4)]


def ret(v, s, d):
    return (0.30 if v == "flow300_tp100_H6" else 0.10) - 0.02 * d - 0.01 * s


def write_store(path):
    con = duckdb.connect(path)
    meta = {"slot_start": cc.DEV[0], "slot_end_exclusive": cc.DEV[1], "git_commit": "storecommit",
            "ms_per_slot": {str(e): 270.0 for e in range(1033, 1047)}}
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps(meta)])
    con.execute("""CREATE TABLE pool (pool VARCHAR, slot UBIGINT, tx_index BIGINT, outer_ix INTEGER, inner_ix INTEGER, kind VARCHAR,
                   token_buy BOOLEAN, priority_fee UBIGINT, jito_tip UBIGINT, tx_fee UBIGINT, lp_bps INTEGER, protocol_bps INTEGER,
                   creator_bps INTEGER)""")
    legs = []
    for i, p in enumerate(POOLS):
        t = trig(i)
        legs.append((p, t, 5, 0, -1, "buy", True, 0, 0, 5000, 20, 5, 30))  # the trigger slot (the sandwich flag's fee)
        n = N_T1[p]
        for k in range(n):
            tx = 10 + k
            fee = 1000 * (i + 1) * (k + 1)
            legs.append((p, t + 1, tx, 0, -1, "sell", True, fee, 100 * k, 5000 + fee, 20, 5, 30))  # reversed pool: a sell buys tokens
            legs.append((p, t + 1, tx, 1, -1, "sell", True, fee, 100 * k, 5000 + fee, 20, 5, 30))  # its fee repeats on every leg
        if p == "C":
            legs.append((p, t + 1, 11, 2, -1, "buy", False, 2000 * 3, 0, 5000, 20, 5, 30))  # tx 11 is a round trip: once
        if p == "D":
            legs.append((p, t + 1, 3, 0, -1, "buy", False, 9_999_999, 0, 5000, 20, 5, 30))  # a token sell: not a buy
            legs.append((p, t + 2, 20, 0, -1, "sell", True, 1, 0, 5000, 20, 5, 30))  # t + 2: outside the count
    con.executemany(f"INSERT INTO pool VALUES ({', '.join('?' * 13)})", legs)
    # Block times: each trigger's slot on its DAYS day (the daily loss limit's exit days).
    con.execute("CREATE TABLE blocks (slot UBIGINT, block_time BIGINT)")
    con.executemany("INSERT INTO blocks VALUES (?, ?)", [(trig(i), int(np.datetime64(DAYS[i], "s").astype(np.int64)) + 3600)
                                                         for i in range(4)])
    con.close()
    return path


@pytest.fixture
def lab(tmp_path):
    root = str(tmp_path)
    a = write_run(root, "a", rows_for((0,), ret))
    b = write_run(root, "b", rows_for((1,), ret))
    e = write_run(root, "e", rows_for((2, 3), ret))
    keys = os.path.join(root, "keys.parquet")
    duckdb.connect().execute(f"""COPY (SELECT * FROM (VALUES ('A', {trig(0)}::UBIGINT, 2, '5m', 3), ('C', {trig(2)}::UBIGINT, 2, '5m', 2),
                    ('D', {trig(3)}::UBIGINT, 2, '5m', 1)) t(pool, t, d, h, paid_buyers_60s)) TO '{keys}' (FORMAT parquet)""")
    store = write_store(os.path.join(root, "dev2.duckdb"))
    # Route (ii)'s rows (E46): D1both = the d 1 rows, E0 = the d 0 fills one slot later, K1-K3 capped by the store's n.
    rii = []
    for i in range(4):
        r1 = row("flow300_tp100_H6", 0.5, 1, i, ret("flow300_tp100_H6", 0.5, 1))
        r0 = row("flow300_tp100_H6", 0.5, 0, i, 0.5)
        rii.append(r1 + ("D1both", None))
        rii.append(r0[:-1] + (r0[-1] + 1, "E0", False))
        for k in (1, 2, 3):
            rii.append(r1 + (f"K{k}", N_T1[POOLS[i]] < k))
    route = os.path.join(root, "route-ii.parquet")
    write_parquet(route, rii, extra=(("entry", "VARCHAR"), ("capped", "BOOLEAN")))
    # E63's report on run e (capital_ceiling itself).
    con = duckdb.connect()
    cc.load(con, [e], keys)
    days, cells = cc.compute(con, False, day_of_pairs(con.execute("SELECT t, day FROM r").fetchall()))
    e63 = os.path.join(root, "ceiling.json")
    with open(e63, "w") as f:
        json.dump({"cells": cells, "days": days, "daily_loss_book": "exit_day"}, f, default=str)
    return {"root": root, "a": a, "b": b, "e": e, "keys": keys, "store": store, "route": route, "e63": e63}


def test_p_land_and_the_draws():
    assert [rc.p_land(n) for n in range(6)] == [1.0, 0.5, 0.25, 0.1, 0.1, 0.1]
    u = rc.draws_u("A", trig(0), 50)
    assert u.shape == (50,) and ((0 <= u) & (u < 1)).all()
    assert (rc._draws("A", trig(0), 50) == u).all()  # the same u in every cell
    assert not (rc.draws_u("A", trig(1), 50) == u).all()
    assert abs(float(np.mean(rc._draws("Z", 1, 4000))) - 0.5) < 0.03


def test_g1_refuses_a_d0_row_inside_the_trigger_slot(tmp_path, lab):
    bad = write_run(str(tmp_path), "bad", rows_for((0,), ret, q=0.5))
    with pytest.raises(cc.Refused, match="G1 RED"):
        rc.load(duckdb.connect(), [bad], lab["keys"], lab["store"])
    bad1 = write_run(str(tmp_path), "bad1", rows_for((1,), ret, q=1.0))
    with pytest.raises(cc.Refused, match="G1 RED"):
        rc.load(duckdb.connect(), [bad1], lab["keys"], lab["store"])


def test_the_ms_per_slot_must_be_the_stores(tmp_path, lab):
    con = duckdb.connect()
    rc.load(con, [lab["a"], lab["b"]], lab["keys"], lab["store"])
    r0 = rc.cell_rows(con, "hold300", None, False, 0.5, 0, "base")
    r1 = rc.cell_rows(con, "hold300", None, False, 0.5, 1, "base")
    assert {r[10] for r in r0} == {270.0} and {r[10] for r in r1} == {270.0}  # d 0: the store; d 1: d_ms / d
    off = write_run(str(tmp_path), "off", rows_for((1,), ret, ms=300.0))
    with pytest.raises(cc.Refused, match="ms per slot"):
        rc.load(duckdb.connect(), [off], lab["keys"], lab["store"])


def test_the_race_table_counts_token_buying_transactions_once(lab):
    out = os.path.join(lab["root"], "race")
    info = rc.table(duckdb.connect(), [lab["a"], lab["b"], lab["e"]], lab["store"], out)
    got = dict(((p, t), (n, f)) for p, t, n, f in duckdb.connect().execute(
        f"SELECT pool, t, n_t1, first_fee FROM read_parquet('{out}/race.parquet')").fetchall())
    assert {p: got[(p, trig(i))][0] for i, p in enumerate(POOLS)} == N_T1
    # The first buyer (lowest tx_index) pays priority + tip: B 2000 + 0, C 3000 + 0, D 4000 + 0; A has none.
    assert [got[(p, trig(i))][1] for i, p in enumerate(POOLS)] == [None, 2000, 3000, 4000]
    assert info["keys"] == 4 and info["txs"] == 6
    tx = duckdb.connect().execute(f"SELECT count(*), count(DISTINCT (pool, t, tx_index)) FROM read_parquet('{out}/race_txs.parquet')").fetchone()
    assert tx == (6, 6)  # one row per transaction, not per leg


def test_the_expectation_is_exact():
    days = ["2026-09-12"]
    rows = [(1, 10, 5, 0, 100, 1000, False, "2026-09-12", "A", 1100, 270.0), (2, 10, 5, 0, 100, 2000, False, "2026-09-12", "B", 2100, 270.0)]
    ns = [0, 2]
    _, e = rc.expected(rows, ns, 10, lambda r: None, 500, days)
    # A lands for sure: 1000 - 10; B lands with 0.25: 0.25 (2000 - 10) + 0.75 (-500 - 10)
    assert abs(e["sol_day"] - (990 + 0.25 * 1990 + 0.75 * -510) / 1e9) < 1e-15
    assert (e["attempts_per_day"], e["landed_per_day"], e["missed_per_day"]) == (2, 1.25, 0.75)
    alt = (2, 11, 6, 1, 100, 300, False, "2026-09-12", "B", 400, 270.0)
    _, e1 = rc.expected(rows, ns, 10, lambda r: alt if r[8] == "B" else None, 500, days)
    assert abs(e1["sol_day"] - (990 + 0.25 * 1990 + 0.75 * 290) / 1e9) < 1e-15


def test_breakeven():
    assert rc.breakeven([(0, 9, 9), (1, 3, 1), (2, 2, -1), (3, -1, -2), (5, 1, -3)]) == {
        "grid": [1, 2, 3, 5], "first_non_positive_d": 3, "beyond_grid": False, "lower_bound_positive_up_to_d": 1}
    assert rc.breakeven([(1, 3, 1), (2, 2, 1)])["beyond_grid"]


def test_the_report_and_its_gates(lab):
    race = os.path.join(lab["root"], "race")
    rc.table(duckdb.connect(), [lab["a"], lab["b"], lab["e"]], lab["store"], race)
    out = os.path.join(lab["root"], "out")
    a = argparse.Namespace(parts=[lab["a"], lab["b"], lab["e"]], race=race, c3_keys=lab["keys"], store=lab["store"],
                           route_ii=lab["route"], e63_json=lab["e63"], out=out, draws=40, workers=1, memory="1GB", threads=1)
    assert rc.report(a) == 0
    res = json.load(open(os.path.join(out, "racing.json")))
    assert {k: v["status"] for k, v in res["gates"].items()} == {"G0": "GREEN", "G1": "GREEN", "G2": "GREEN", "G3": "GREEN", "G4": "GREEN",
                                                                 "G5": "GREEN"}
    assert res["gates"]["G4"]["cells"] == 2 * 3 * 2 * 2 and res["gates"]["G5"]["mismatch"] == 0
    h5 = res["leads"]["H5"]
    x = h5["sizes"]["0.5"]["scenarios"]["base"]
    # H5 is organic: A, C, D (B is flagged), n 0 / 2 / 3.
    assert x["cells"]["0"]["n_t1"] == {"0": 1, "1": 0, "2": 1, "3": 1}
    assert x["cells"]["0"]["e_p_land"] == round((1 + 0.25 + 0.1) / 3, 4)
    assert h5["fees"]["first_n"] == 2 and h5["fees"]["first_p50"] == 3500
    assert [k for k, _ in h5["f_grid"]] == ["0", "p50", "p90", "0.001", "0.005", "0.01"]
    # F is paid on every attempt: S(F) = S(0) - F x attempts per day, exactly.
    ex = x["cells"]["0"]["expected"]
    for fkey, F in h5["f_grid"]:
        assert abs(ex[f"{fkey}|none"]["sol_day"] - (ex["0|none"]["sol_day"] - F / 1e9 * ex["0|none"]["attempts_per_day"])) < 1e-12
    fs = x["f_star_sol"]["none_vs_d3"]
    assert abs(fs - (ex["0|none"]["sol_day"] - x["cells"]["3"]["unlimited"]["sol_day"]) / ex["0|none"]["attempts_per_day"]) < 1e-12
    assert set(x["cells"]["0"]["drawn"]) == {f"{k}|{m}" for k, _ in h5["f_grid"] for m in rc.MISSES}
    assert {"fastest_Fp90_none_vs_d3", "oracle_vs_d3", "fast_d1_vs_d3", "fastest_Fp90_d1_vs_d2"} <= set(x["speed"])
    assert x["breakeven"]["limited"]["grid"] == [1, 2, 3]
    assert len(x["infra"]["fastest_net_sol_day"]) == 2
    c3 = res["leads"]["C3/hold300"]["sizes"]["0.5"]["scenarios"]["base"]["cells"]["0"]
    assert c3["n_t1"] == {"0": 1, "1": 0, "2": 1, "3": 0}  # the arm keys A and C (D has one paid buyer)
    md = open(os.path.join(out, "racing.md")).read()
    assert "DRY RUN" not in md and "Gates: G0 GREEN; G1 GREEN" in md and "Landed means first in t + 1" in md


def test_a_run_on_another_grid_than_e63s_is_refused(tmp_path, lab):
    assert rc.grid_check([lab["a"], lab["b"], lab["e"]])["status"] == "GREEN"
    other = write_run(str(tmp_path), "other", rows_for((1,), ret), f7_grid="v1")
    with pytest.raises(cc.Refused, match="f7_grid"):
        rc.grid_check([lab["a"], other, lab["e"]])
    narrow = write_run(str(tmp_path), "narrow", rows_for((1,), ret), variants=("hold300",))
    with pytest.raises(cc.Refused, match="variants"):
        rc.grid_check([narrow, lab["e"]])
    loose = write_run(str(tmp_path), "loose", rows_for((1,), ret))
    cfg = json.load(open(os.path.join(loose, "config.json")))
    json.dump(dict(cfg, slippage_tol=0.10), open(os.path.join(loose, "config.json"), "w"))
    with pytest.raises(cc.Refused, match="slippage_tol"):  # the tournament's name for --tol
        rc.grid_check([loose, lab["e"]])


def test_a_differing_e63_cell_is_red(lab):
    race = os.path.join(lab["root"], "race")
    rc.table(duckdb.connect(), [lab["a"], lab["b"], lab["e"]], lab["store"], race)
    e63 = json.load(open(lab["e63"]))
    next(c for c in e63["cells"] if c["candidate"] == "H5" and c["d"] == 3)["unlimited"]["sol_day"] += 1e-9
    with open(lab["e63"], "w") as f:
        json.dump(e63, f)
    a = argparse.Namespace(parts=[lab["a"], lab["b"], lab["e"]], race=race, c3_keys=lab["keys"], store=lab["store"],
                           route_ii=lab["route"], e63_json=lab["e63"], out=os.path.join(lab["root"], "out"), draws=5, workers=1,
                           memory="1GB", threads=1)
    assert rc.report(a) == 1


def test_w8b_scales_e1s_own_cost(tmp_path):
    p = os.path.join(str(tmp_path), "e1.parquet")
    duckdb.connect().execute(f"""COPY (SELECT * FROM (VALUES (446300000, 1033, false, 100.0, 1.05, 0.03, 1.02, 0.03, 1.01, 0.03),
                                 (446700000, 1033, true, 100.0, 0.98, 0.02, 0.97, 0.02, 0.99, 0.02))
                                 t(cs, ep, late_mig, fp, g1, c1, g2, c2, g3, c3)) TO '{p}' (FORMAT parquet)""")
    out = os.path.join(str(tmp_path), "w")
    assert rc.w8b(argparse.Namespace(e1=p, out=out)) == 1  # the E51 check is RED on synthetic rows
    res = json.load(open(os.path.join(out, "w8b.json")))
    cell = {(x["d"], x["size"], x["extra"], x["group"]): x for x in res["cells"]}
    # At 0.25 SOL the decomposition gives back g - 1 - c: ((0.05 - 0.03) + (-0.02 - 0.02)) / 2 = -1.0 % at d 1.
    assert cell[(1, 0.25, 0.0, "all")]["capped_mean_pct"] == -1.0
    # At 5 SOL: imp = c - 0.02 - 0.004, scaled x 20, and 0.001 / 5 instead of 0.004.
    imp = 0.03 - 0.02 - 0.004
    want = 100 * (0.05 - 0.02 - imp * 20 - 0.0002)
    assert abs(cell[(1, 5.0, 0.0, "same_slot (post hoc)")]["capped_mean_pct"] - round(want, 2)) < 1e-9
    assert cell[(1, 0.25, 0.006, "all")]["capped_mean_pct"] == -1.6  # E51's +0.6 pt extras
