"""Markouts on a mini store: a normal pool, a reversed pool (b / e, the token buy is a pool sell of WSOL)
and a curve; the horizon conversion, the end-of-slot state, the round trip through the engine, the
cut flag and the N2 reference columns. Needs the store-v2 engine (LAB_ENGINE); skipped without it."""

import json
import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ENGINE = os.environ.get("LAB_ENGINE") or "/home/chupa/Solana-project/data-old-faithful-one/lab/backtests/f7rev-1033-1046/engine"
if not os.path.exists(os.path.join(ENGINE, "backtest", "venues.py")):
    pytest.skip("store-v2 engine (LAB_ENGINE) not available", allow_module_level=True)
os.environ.setdefault("LAB_ENGINE", ENGINE)

from wallets import markout  # noqa: E402

SLOT0 = 446_256_000
A, B, D = SLOT0 + 1000, SLOT0 + 2000, SLOT0 + 3000
SOL = 10**9


def _cfg(tmp_path):
    p = tmp_path / "defaults.yaml"
    p.write_text("costs:\n  ata_rent_lamports: 2039280\n  scenarios:\n    base: {fee_per_tx_sol: 0.000605, tip_sol: 0.0001, failed_tx_sol: 0.000505, "
                 "land_prob: 0.97, ata_rent: refunded_on_full_exit, adverse_exec_per_side: 0.0}\n")
    return str(p)


def _store(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": SLOT0, "slot_end_exclusive": SLOT0 + 432_000,
                                                            "ms_per_slot": {str(SLOT0 // 432_000): 400.0}})])
    con.execute("CREATE TABLE pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR, mint VARCHAR, create_slot BIGINT)")
    con.execute("INSERT INTO pools VALUES ('P', 'sol', 'normal', 'MP', ?), ('R', 'reversed', 'reversed', 'MR', ?), ('PC', 'sol', 'normal', 'C', ?)",
                [A - 500, B - 500, D + 120])
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, b BIGINT, e BIGINT, "
                "vault BIGINT, vq BIGINT, base BIGINT, e_delta BIGINT, quote_user BIGINT, lp_bps BIGINT, protocol_bps BIGINT, creator_bps BIGINT, cashback_bps BIGINT, trader VARCHAR)")
    rows = [
        # normal pool P (base token, quote WSOL): pre-trade reserves; post = b -/+ base, e + e_delta
        ("buy", "P", A, 5, 0, 0, 10**12, 100 * SOL, 100 * SOL, 0, 5 * 10**10, 5 * SOL, 5 * SOL, 20, 5, 0, 0),      # post b 9.5e11, e 105
        ("buy", "P", A + 3, 1, 0, 0, 95 * 10**10, 105 * SOL, 105 * SOL, 0, 5 * 10**10, 6 * SOL, 6 * SOL, 20, 5, 0, 0),  # post b 9e11, e 111
        ("sell", "P", A + 20, 1, 0, 0, 9 * 10**11, 111 * SOL, 111 * SOL, 0, 2 * 10**11, -25 * SOL, 25 * SOL, 20, 5, 0, 0),  # post b 1.1e12, e 86
        # a boost at A + 30 adds 10 SOL of virtual quote (post b 1.1e12, e 96, virt 10); the LP withdraw at A + 40 (1/11 of the tokens, 7.818 SOL)
        # must take that virt: post b 1.0e12, e = vault 86 + virt 10 - 7.818 = 88.182 (with virt from the last trade it would be 78.182)
        ("boost", "P", A + 30, 1, 0, 0, 11 * 10**11, 86 * SOL, 86 * SOL, 10 * SOL, 0, 10 * SOL, None, None, None, None, None),
        ("withdraw", "P", A + 40, 1, 0, 0, 11 * 10**11, 86 * SOL, 86 * SOL, None, 10**11, None, 7_818_000_000, None, None, None, None),
        # reversed pool R (base WSOL, quote token): a token buy is a pool sell of WSOL: b + base, e + e_delta (negative)
        ("sell", "R", B, 2, 0, 0, 100 * SOL, 10**15, 10**15, 0, 10 * SOL, -9 * 10**13, 9 * 10**13, 25, 5, 0, 0),        # post b 110, e 9.1e14
        ("sell", "R", B + 3, 1, 0, 0, 110 * SOL, 91 * 10**13, 91 * 10**13, 0, 10 * SOL, -8 * 10**13, 8 * 10**13, 25, 5, 0, 0),  # post b 120, e 8.3e14
        # migration pool PC of curve token C, first leg at D + 120 (pre b 2e14, e 85 SOL): post b 1.9e14, e 90
        ("buy", "PC", D + 120, 1, 0, 0, 2 * 10**14, 85 * SOL, 85 * SOL, 0, 10**13, 5 * SOL, 5 * SOL, 20, 5, 0, 0),
    ]
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)", rows)
    con.execute("CREATE TABLE curve (mint VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, vt BIGINT, vq BIGINT, rt BIGINT, rq BIGINT, "
                "fee_bps BIGINT, creator_bps BIGINT)")
    con.executemany("INSERT INTO curve VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, 95, 30)", [
        ("C", D, 3, 10**15, 31 * SOL, 79 * 10**13, 1 * SOL),          # post-trade state
        ("C", D + 4, 1, 98 * 10**13, 316 * SOL // 10, 77 * 10**13, 2 * SOL),
        ("C", D + 100, 1, 21 * 10**13, 150 * SOL, 0, 85 * SOL),        # the curve completes (rt = 0)
        ("E", D, 1, 10**15, 31 * SOL, 79 * 10**13, 1 * SOL),
        ("E", D + 10, 1, 21 * 10**13, 150 * SOL, 0, 85 * SOL)])        # completes too, but never gets a pool
    con.close()


def _parts(path):
    rows = [{"family": "F7", "variant": v, "segment": "pool_normal", "venue": "pool", "orientation": "normal", "mint": "MP", "pool": "P",
             "t": A, "day": "2026-09-11", "farm": "organic", "ref_mint": None, "ref_t": None} for v in ("tp4_T600", "tp6_T600")]
    rows.append({"family": "F7", "variant": "tp4_T600", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": "MR",
                 "pool": "R", "t": B, "day": "2026-09-11", "farm": "flagged", "ref_mint": None, "ref_t": None})
    rows.append({"family": "F7", "variant": "tp4_T600", "segment": "curve", "venue": "curve", "orientation": "normal", "mint": "C", "pool": None,
                 "t": D, "day": "2026-09-11", "farm": "n/a", "ref_mint": None, "ref_t": None})
    rows.append({"family": "F7", "variant": "tp4_T600", "segment": "curve", "venue": "curve", "orientation": "normal", "mint": "E", "pool": None,
                 "t": D, "day": "2026-09-11", "farm": "n/a", "ref_mint": None, "ref_t": None})
    rows.append({"family": "N2_F7", "variant": "tp4_T600", "segment": "pool_normal", "venue": "pool", "orientation": "normal", "mint": "MP",
                 "pool": "P", "t": A + 2, "day": "2026-09-11", "farm": "n/a", "ref_mint": "MR", "ref_t": B})
    os.makedirs(path)
    pq.write_table(pa.Table.from_pylist(rows), os.path.join(path, "part-0.parquet"))


def test_markouts_on_the_mini_store(tmp_path, capfd):
    _store(tmp_path / "s.duckdb")
    _parts(str(tmp_path / "parts"))
    info = markout.run(str(tmp_path / "parts"), "t", str(tmp_path / "out"), store=str(tmp_path / "s.duckdb"), cfg_path=_cfg(tmp_path))
    assert info["signals"] == 5 and info["pool_states"] > 0 and info["curve_states"] > 0  # states at the target slots only
    # one timed progress line per phase on stderr (the NAS log must not stay empty), and the phases in the run json
    err = capfd.readouterr().err
    for phase in ("signals", "targets", "pool_at", "curve_at", "matched", "round trips", "parquet written"):
        assert phase in err and phase.split(" (")[0] in " ".join(info["phases_seconds"]), phase
    assert err.index("signals") < err.index("pool_at") < err.index("matched") < err.index("round trips")
    con = duckdb.connect()
    rows = con.execute(f"SELECT * FROM '{tmp_path / 'out' / 'markout-t.parquet'}'").fetchall()
    cols = [c[0] for c in con.description]
    R = [dict(zip(cols, r)) for r in rows]
    assert len(R) == 5 * 2 * 8  # signals x delays x (entry + 7 horizons)
    assert all(r["regime"] == "400ms" and abs(r["ms_per_slot"] - 400.0) < 1e-9 for r in R)  # the test store's epoch runs at 400 ms per slot

    # exit variants share the signal
    p = next(r for r in R if r["pool"] == "P" and r["family"] == "F7" and r["d"] == 1 and r["h"] == "")
    assert list(p["variants"]) == ["tp4_T600", "tp6_T600"]
    # horizons at 400 ms per slot: 1 s = 3 slots, 10 s = 25, 1 min = 150, 6 h = 54000
    hs = {r["h"]: r["target"] for r in R if r["pool"] == "P" and r["family"] == "F7" and r["d"] == 1}
    assert hs[""] == A + 1 and hs["1s"] == A + 4 and hs["10s"] == A + 26 and hs["1m"] == A + 151 and hs["6h"] == A + 1 + 54000
    # normal pool: entry = end of slot A + 1 = post-state of the trade in A (e 105 / b 9.5e11); 1 s later the buy in A + 3 lifted it
    assert abs(p["mid"] - 105 * SOL / 9.5e11) < 1e-15
    m1 = next(r for r in R if r["pool"] == "P" and r["family"] == "F7" and r["d"] == 1 and r["h"] == "1s")
    assert abs(m1["gross"] - ((111 * SOL / 9e11) / (105 * SOL / 9.5e11) - 1)) < 1e-12 and m1["gross"] > 0
    assert m1["net"] is not None and 0 < m1["net"] < m1["gross"] and not m1["cut"]
    m10 = next(r for r in R if r["pool"] == "P" and r["family"] == "F7" and r["d"] == 1 and r["h"] == "10s")
    assert m10["gross"] < 0  # the sell in A + 20 pushed the price below the entry
    m1m = next(r for r in R if r["pool"] == "P" and r["family"] == "F7" and r["d"] == 1 and r["h"] == "1m")
    assert abs(m1m["mid"] - 88_182_000_000 / 1.0e12) < 1e-15  # after the boost and the LP withdraw: virt carried over the boost
    assert m1m["net"] is not None and m1m["net"] > -0.99  # the pool can still pay (fee tiers from the last trade)
    m6 = next(r for r in R if r["pool"] == "P" and r["family"] == "F7" and r["d"] == 1 and r["h"] == "6h")
    assert m6["cut"] and abs(m6["mid"] - m1m["mid"]) < 1e-18  # past the venue's last leg: last known state, flagged
    # reversed pool: mid = b / e; the token buys (pool sells of WSOL) raised it, so the long-token markout is positive
    r0 = next(r for r in R if r["pool"] == "R" and r["d"] == 1 and r["h"] == "")
    assert abs(r0["mid"] - 110 * SOL / 9.1e14) < 1e-18 and r0["impact"] is not None and r0["impact"] > 0
    r1 = next(r for r in R if r["pool"] == "R" and r["d"] == 1 and r["h"] == "1s")
    assert r1["gross"] > 0.15 and r1["net"] is not None and 0 < r1["net"] < r1["gross"]
    # curve: vq / vt, d = 2 entry at D + 2 (post-state of D); the buy in D + 4 lifted it by the 1 s horizon
    c0 = next(r for r in R if r["mint"] == "C" and r["d"] == 2 and r["h"] == "")
    c1 = next(r for r in R if r["mint"] == "C" and r["d"] == 2 and r["h"] == "1s")
    assert abs(c0["mid"] - 31 * SOL / 1e15) < 1e-18 and c1["gross"] > 0 and c1["net"] is not None and c1["net"] < c1["gross"]
    # graduation is not death: C completes at D + 100; the 1 min horizon (D + 2 + 150) is valued on the migration pool PC (post-state of D + 120)
    c1m = next(r for r in R if r["mint"] == "C" and r["d"] == 2 and r["h"] == "1m")
    assert abs(c1m["mid"] - 90 * SOL / 1.9e14) < 1e-18 and c1m["net"] is not None and c1m["net"] > -0.99 and c1m["cut"]  # past the pool's last leg (D + 120): last known state, flagged
    # E completes at D + 10 and never gets a pool: no market from the 10 s horizon on
    e10 = next(r for r in R if r["mint"] == "E" and r["d"] == 1 and r["h"] == "10s")
    assert e10["mid"] is None and e10["net"] is None and e10["gross"] is None
    # N2 keeps its reference to the trigger
    n2 = next(r for r in R if r["family"] == "N2_F7" and r["h"] == "")
    assert n2["ref_mint"] == "MR" and n2["ref_t"] == B and n2["farm"] == "n/a"


def test_buckets_do_not_change_the_markouts(tmp_path, monkeypatch, capfd):
    """The venue-state match per key-hash bucket (LAB_MARKOUT_BUCKETS, the NAS setting for the big runs) and a
    one-row flush of the round trips give the same parquet as the unbucketed run."""
    _store(tmp_path / "s.duckdb")
    _parts(str(tmp_path / "parts"))
    markout.run(str(tmp_path / "parts"), "one", str(tmp_path / "out"), store=str(tmp_path / "s.duckdb"), cfg_path=_cfg(tmp_path))
    monkeypatch.setenv("LAB_MARKOUT_BUCKETS", "3")
    monkeypatch.setattr(markout, "FLUSH_ROWS", 7)
    info = markout.run(str(tmp_path / "parts"), "three", str(tmp_path / "out"), store=str(tmp_path / "s.duckdb"), cfg_path=_cfg(tmp_path))
    assert info["buckets"] == 3 and "bucket 3/3" in capfd.readouterr().err
    con = duckdb.connect()
    one, three = (con.execute(f"SELECT * FROM '{tmp_path / 'out' / f'markout-{n}.parquet'}' ORDER BY ALL").fetchall() for n in ("one", "three"))
    assert len(one) == 5 * 2 * 8 and one == three


def test_guard_refuses_the_holdout(tmp_path):
    with pytest.raises(ValueError):
        markout.guard(markout.__dict__["guard"].__globals__["HOLDOUT_START"] + 1)


if __name__ == "__main__":
    pytest.main([__file__])


def test_sql_targets_equal_the_python_formula_across_an_epoch_boundary():
    """build_targets (SQL) reproduces entry + ceil(h_ms / ms) with the ms of the entry slot's epoch, also when t + d
    crosses into the next epoch and for an epoch the clock has not measured (last earlier epoch)."""
    from wallets.common import EPOCH_SLOTS, Clock
    con = duckdb.connect()
    clock = Clock({1036: 316.4, 1037: 267.1})  # 1038 unmeasured -> 267.1; 1035 -> REF_MS
    ts = [1035 * EPOCH_SLOTS + 5, 1036 * EPOCH_SLOTS + 10, 1037 * EPOCH_SLOTS - 2, 1037 * EPOCH_SLOTS - 1, 1037 * EPOCH_SLOTS + 3, 1038 * EPOCH_SLOTS + 7]
    con.execute("CREATE TEMP TABLE sig AS SELECT * FROM (VALUES " + ", ".join(f"({i + 1}, {t})" for i, t in enumerate(ts)) + ") v(sid, t)")
    n = markout.build_targets(con, clock, 10**12)
    got = {(r[0], r[1], r[2]): r[4] for r in con.execute("SELECT sid, d, h, h_ms, target FROM targets").fetchall()}
    want = {}
    for sid, t in enumerate(ts, 1):
        for d in markout.DELAYS:
            entry = t + d
            ms = clock.ms_per_slot(entry)
            want[(sid, d, "")] = entry
            for name, h_ms in markout.HORIZONS_MS:
                want[(sid, d, name)] = entry + markout.horizon_slots(ms, h_ms)
    assert n == len(want) == len(got) and got == want


def test_tx_level_entries(tmp_path):
    """Spec M-a (--tx-delays): the entry state is a leg's post-state inside the trigger slot or the first leg of
    t + 1, chosen by the trigger wallet; horizons still run from the entry slot."""
    store = tmp_path / "s.duckdb"
    con = duckdb.connect(str(store))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": SLOT0, "slot_end_exclusive": SLOT0 + 432_000, "ms_per_slot": {str(SLOT0 // 432_000): 400.0}})])
    con.execute("CREATE TABLE pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR, mint VARCHAR, create_slot BIGINT)")
    con.execute("INSERT INTO pools VALUES ('P', 'sol', 'normal', 'MP', ?)", [A - 500])
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, b BIGINT, e BIGINT, "
                "vault BIGINT, vq BIGINT, base BIGINT, e_delta BIGINT, quote_user BIGINT, lp_bps BIGINT, protocol_bps BIGINT, creator_bps BIGINT, cashback_bps BIGINT, trader VARCHAR)")
    # slot A: tx 2 (someone), tx 5 = the trigger (wallet W), tx 7 and tx 9 after it; slot A + 1: tx 1; slot A + 2: tx 1
    legs = [("buy", A, 2, 10**12, 100 * SOL, 10**10, 1 * SOL, "X"), ("buy", A, 5, 99 * 10**10, 101 * SOL, 4 * 10**10, 4 * SOL, "W"),
            ("buy", A, 7, 95 * 10**10, 105 * SOL, 10**10, 1 * SOL, "Y"), ("buy", A, 9, 94 * 10**10, 106 * SOL, 10**10, 1 * SOL, "Z"),
            ("buy", A + 1, 1, 93 * 10**10, 107 * SOL, 10**10, 1 * SOL, "Q"), ("buy", A + 2, 1, 92 * 10**10, 108 * SOL, 10**10, 1 * SOL, "Q")]
    con.executemany("INSERT INTO pool VALUES (?, 'P', ?, ?, 0, 0, ?, ?, ?, 0, ?, ?, ?, 20, 5, 0, 0, ?)",
                    [(k, sl, tx, b, e, e, base, dq, dq, w) for k, sl, tx, b, e, base, dq, w in legs])
    con.execute("CREATE TABLE curve (mint VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, vt BIGINT, vq BIGINT, rt BIGINT, rq BIGINT, fee_bps BIGINT, creator_bps BIGINT)")
    con.close()
    parts = tmp_path / "parts"
    os.makedirs(parts)
    pq.write_table(pa.Table.from_pylist([{"family": "F7", "variant": "tp4_T600", "segment": "pool_normal", "venue": "pool", "orientation": "normal", "mint": "MP",
                                          "pool": "P", "t": A, "day": "2026-09-11", "farm": "organic", "ref_mint": None, "ref_t": None, "trigger_trader": "W"}]),
                   os.path.join(parts, "part-0.parquet"))
    info = markout.run(str(parts), "tx", str(tmp_path / "out"), store=str(store), cfg_path=_cfg(tmp_path), tx_delays=True)
    assert info["tx_delays"] == {1000: "t, trigger tx + 0", 1001: "t, trigger tx + 1", 1003: "t, trigger tx + 3", 1010: "t, trigger tx + 10", 2001: "t+1, first tx"}
    R = pq.read_table(str(tmp_path / "out" / "markout-tx.parquet")).to_pylist()
    ent = {r["d"]: r for r in R if r["h"] == ""}
    post = lambda b, e: e / b  # mid of a normal pool = e / b (virt 0)
    assert ent[1000]["mid"] == pytest.approx(post(95 * 10**10, 105 * SOL))   # the trigger leg's post-state (b 9.9e11 - 4e10, e 101 + 4)
    assert ent[1001]["mid"] == pytest.approx(post(94 * 10**10, 106 * SOL))   # one leg after it
    assert ent[1003]["mid"] == pytest.approx(post(93 * 10**10, 107 * SOL))   # capped at the slot's last leg (tx 9)
    assert ent[1010]["mid"] == ent[1003]["mid"]
    assert ent[2001]["mid"] == pytest.approx(post(92 * 10**10, 108 * SOL))   # the first leg of A + 1
    assert ent[1]["mid"] == ent[2001]["mid"]                                  # d = 1 = the end of A + 1: the same single leg
    assert ent[2001]["target"] == A + 1 and ent[1000]["target"] == A and ent[2]["target"] == A + 2
    # horizons of a tx-level entry run from its entry slot: the 1 s horizon (400 ms slots → 3 slots) of 2001 ends at A + 4
    h1 = {r["d"]: r["target"] for r in R if r["h"] == "1s"}
    assert h1[2001] == A + 1 + 3 and h1[1000] == A + 3 and h1[1] == A + 1 + 3
