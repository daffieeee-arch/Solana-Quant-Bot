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


def _store(path, shift=0):
    """shift: every slot moved by this many slots (a whole number of epochs for the clean-lock test past the hold-out)."""
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": SLOT0 + shift, "slot_end_exclusive": SLOT0 + shift + 432_000,
                                                            "ms_per_slot": {str((SLOT0 + shift) // 432_000): 400.0}})])
    # coin_creator: P and PC share a creator (PC is the creator's second pool), R's creator is the system program (unknown)
    con.execute("CREATE TABLE pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR, mint VARCHAR, create_slot BIGINT, coin_creator VARCHAR)")
    con.execute("INSERT INTO pools VALUES ('P', 'sol', 'normal', 'MP', ?, 'CR'), ('R', 'reversed', 'reversed', 'MR', ?, ?), ('PC', 'sol', 'normal', 'C', ?, 'CR')",
                [A - 500 + shift, B - 500 + shift, markout.SYSTEM_PROGRAM, D + 120 + shift])
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, b BIGINT, e BIGINT, "
                "vault BIGINT, vq BIGINT, base BIGINT, e_delta BIGINT, quote_user BIGINT, lp_bps BIGINT, protocol_bps BIGINT, creator_bps BIGINT, cashback_bps BIGINT, trader VARCHAR, "
                "token_buy BOOLEAN, sol_amount BIGINT, sol_depth BIGINT)")
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
    rows = [r[:2] + (r[2] + shift,) + r[3:] for r in rows]
    con.executemany("INSERT INTO pool VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)", rows)
    con.execute("CREATE TABLE curve (mint VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, vt BIGINT, vq BIGINT, rt BIGINT, rq BIGINT, "
                "fee_bps BIGINT, creator_bps BIGINT)")
    con.executemany("INSERT INTO curve VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, 95, 30)", [(r[0], r[1] + shift) + r[2:] for r in [
        ("C", D, 3, 10**15, 31 * SOL, 79 * 10**13, 1 * SOL),          # post-trade state
        ("C", D + 4, 1, 98 * 10**13, 316 * SOL // 10, 77 * 10**13, 2 * SOL),
        ("C", D + 100, 1, 21 * 10**13, 150 * SOL, 0, 85 * SOL),        # the curve completes (rt = 0)
        ("E", D, 1, 10**15, 31 * SOL, 79 * 10**13, 1 * SOL),
        ("E", D + 10, 1, 21 * 10**13, 150 * SOL, 0, 85 * SOL)]])        # completes too, but never gets a pool
    con.close()


def _parts(path, shift=0):
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
    for r in rows:
        r["t"] += shift
        if r["ref_t"] is not None:
            r["ref_t"] += shift
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
    # the entry state's SOL side and fee tier (E49): normal pool P = real quote vault e (105 SOL after the trade in A), 20 + 5 bps;
    # reversed pool R = the WSOL base b (110 SOL), 25 + 5 bps; curve C = the real quote rq (1 SOL after D), 95 + 30 bps;
    # the horizon rows carry the entry's values (the 1 min row of P after the boost and the withdraw still says 105 SOL)
    assert p["depth_entry"] == 105 * SOL and p["fee_bps"] == 25 and m1m["depth_entry"] == 105 * SOL and m1m["fee_bps"] == 25
    assert r0["depth_entry"] == 110 * SOL and r0["fee_bps"] == 30 and r1["depth_entry"] == 110 * SOL
    assert c0["depth_entry"] == 1 * SOL and c0["fee_bps"] == 125 and c1m["depth_entry"] == 1 * SOL
    assert e10["depth_entry"] == 1 * SOL  # E's entry had a market; the horizon without one keeps the entry's values
    # creator_prior_pools: the creator's earlier pools in the store at the pool's creation (P first → 0, PC second → 1), the
    # system program → -1, a curve signal → NULL
    assert p["creator_prior_pools"] == 0 and r0["creator_prior_pools"] == -1 and c0["creator_prior_pools"] is None
    pc = next(r for r in R if r["pool"] == "PC") if any(r["pool"] == "PC" for r in R) else None
    assert pc is None or pc["creator_prior_pools"] == 1


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


CLEAN_SHIFT = 455_760_000 - SLOT0  # 22 epochs: the mini store then starts at the clean window's first slot, past the hold-out


def _clean_run(tmp_path):
    """E20's shape: <run>/parts, <run>/config.json, <run>/engine (the snapshot: this engine's backtest/ + COMMIT), the store next
    to its clean.lock (the keys backtest.clean_test writes)."""
    run = tmp_path / "clean-run"
    _parts(run / "parts", CLEAN_SHIFT)
    (run / "config.json").write_text('{"engine_commit": "abc1234def", "delay_ms": 600}')
    (run / "engine").mkdir()
    os.symlink(os.path.join(ENGINE, "backtest"), run / "engine" / "backtest")
    (run / "engine" / "COMMIT").write_text("abc1234def0000\n")
    (tmp_path / "store").mkdir()
    store = tmp_path / "store" / "clean.duckdb"
    _store(store, CLEAN_SHIFT)
    lock = tmp_path / "store" / "clean.lock"
    lock.write_text(json.dumps({"store": str(store), "store_sha256": markout._sha256(str(store)), "test_run": str(run),
                                "test_config_sha256": markout._sha256(str(run / "config.json")), "engine_commit": "abc1234def",
                                "trading_days": 7, "matrix_run": str(run) + "-matrix", "locked_at_unix": 1}))
    return run, store, lock


def test_clean_lock_admits_only_the_locked_run(tmp_path, monkeypatch):
    run, store, lock = _clean_run(tmp_path)
    cfg = _cfg(tmp_path)
    parts, out = str(run / "parts"), str(tmp_path / "out")
    # past the hold-out without the lock: the guard refuses, with or without a window
    with pytest.raises(ValueError, match="hold-out"):
        markout.run(parts, "c", out, store=str(store), cfg_path=cfg)
    with pytest.raises(ValueError, match="hold-out"):
        markout.run(parts, "c", out, lo=SLOT0 + CLEAN_SHIFT, hi=SLOT0 + CLEAN_SHIFT + 5000, store=str(store), cfg_path=cfg)
    # the locked run: admitted; the store and the engine snapshot come from the lock
    monkeypatch.delenv("LAB_ENGINE", raising=False)
    info = markout.run(parts, "c", out, cfg_path=cfg, clean=str(lock))
    assert info["rows"] > 0 and info["signals"] == 5 and info["store"] == str(store)
    assert info["clean_lock"]["engine_commit"] == "abc1234def" and os.environ["LAB_ENGINE"] == str(run / "engine")
    assert os.path.exists(os.path.join(out, "markout-c.parquet"))
    # and a run on the same signals from before the hold-out gives the same markouts (the shift changes nothing)
    _store(tmp_path / "dev.duckdb")
    _parts(tmp_path / "dev-parts")
    monkeypatch.setenv("LAB_ENGINE", ENGINE)
    markout.run(str(tmp_path / "dev-parts"), "dev", out, store=str(tmp_path / "dev.duckdb"), cfg_path=cfg)
    con = duckdb.connect()
    same, n = con.execute(f"""SELECT count(*) FILTER (WHERE (a.gross, a.net, a.cut) IS NOT DISTINCT FROM (b.gross, b.net, b.cut)), count(*)
        FROM read_parquet('{out}/markout-c.parquet') a JOIN read_parquet('{out}/markout-dev.parquet') b
        ON a.family = b.family AND a.mint = b.mint AND a.t = b.t + {CLEAN_SHIFT} AND a.d = b.d AND a.h = b.h""").fetchone()
    assert n == info["rows"] and same == n


def test_clean_lock_refuses_anything_else(tmp_path, monkeypatch):
    run, store, lock = _clean_run(tmp_path)
    cfg = _cfg(tmp_path)
    parts, out = str(run / "parts"), str(tmp_path / "out")
    monkeypatch.delenv("LAB_ENGINE", raising=False)
    held = json.loads(lock.read_text())

    def relock(**changes):
        p = tmp_path / "other.lock"
        p.write_text(json.dumps(held | changes))
        return str(p)

    # a sub-window, other parts (a copy of the same rows elsewhere), another LAB_ENGINE
    with pytest.raises(ValueError, match="from-slot"):
        markout.run(parts, "c", out, lo=SLOT0 + CLEAN_SHIFT, cfg_path=cfg, clean=str(lock))
    _parts(tmp_path / "copy-parts", CLEAN_SHIFT)
    with pytest.raises(ValueError, match="admits only"):
        markout.run(str(tmp_path / "copy-parts"), "c", out, cfg_path=cfg, clean=str(lock))
    monkeypatch.setenv("LAB_ENGINE", ENGINE)
    with pytest.raises(ValueError, match="LAB_ENGINE"):
        markout.run(parts, "c", out, cfg_path=cfg, clean=str(lock))
    monkeypatch.delenv("LAB_ENGINE")
    # a lock that names another store, another store sha, another config sha, another engine commit, no trading days, a missing key
    with pytest.raises(ValueError, match="no store"):
        markout.run(parts, "c", out, cfg_path=cfg, clean=relock(store=str(tmp_path / "nope.duckdb")))
    with pytest.raises(ValueError, match="sha256"):
        markout.run(parts, "c", out, cfg_path=cfg, clean=relock(store_sha256="0" * 64))
    with pytest.raises(ValueError, match="config.json"):
        markout.run(parts, "c", out, cfg_path=cfg, clean=relock(test_config_sha256="0" * 64))
    with pytest.raises(ValueError, match="engine snapshot"):
        markout.run(parts, "c", out, cfg_path=cfg, clean=relock(engine_commit="fffffff"))
    with pytest.raises(ValueError, match="trading days"):
        markout.run(parts, "c", out, cfg_path=cfg, clean=relock(trading_days=0))
    with pytest.raises(ValueError, match="lacks"):
        markout.run(parts, "c", out, cfg_path=cfg, clean=relock_missing(tmp_path, held))
    # a tampered config.json or store after the lock
    (run / "config.json").write_text('{"engine_commit": "abc1234def", "delay_ms": 601}')
    with pytest.raises(ValueError, match="config.json"):
        markout.run(parts, "c", out, cfg_path=cfg, clean=str(lock))
    assert not os.path.exists(os.path.join(out, "markout-c.parquet"))


def relock_missing(tmp_path, held):
    p = tmp_path / "missing.lock"
    p.write_text(json.dumps({k: v for k, v in held.items() if k != "store_sha256"}))
    return str(p)


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
    t + 1, chosen by the trigger wallet's FIRST leg of the trigger kind (the backtest's first qualifying leg, not the
    wallet's last leg); horizons still run from the entry slot; d = 0 and 3 are added; capped entries and the counts
    (wallet with more than one leg, creator sell after the trigger) are reported."""
    store = tmp_path / "s.duckdb"
    con = duckdb.connect(str(store))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": SLOT0, "slot_end_exclusive": SLOT0 + 432_000, "ms_per_slot": {str(SLOT0 // 432_000): 400.0}})])
    con.execute("CREATE TABLE pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR, mint VARCHAR, create_slot BIGINT, coin_creator VARCHAR)")
    con.execute("INSERT INTO pools VALUES ('P', 'sol', 'normal', 'MP', ?, 'CR')", [A - 500])
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, b BIGINT, e BIGINT, "
                "vault BIGINT, vq BIGINT, base BIGINT, e_delta BIGINT, quote_user BIGINT, lp_bps BIGINT, protocol_bps BIGINT, creator_bps BIGINT, cashback_bps BIGINT, trader VARCHAR, "
                "token_buy BOOLEAN, sol_amount BIGINT, sol_depth BIGINT)")
    # normal pool: a token sell is kind 'sell' (post b + base, e + e_delta); the store's SOL-side columns say sol_depth 150 SOL
    # (the trigger needs >= 150 and a sell >= max(3 SOL, 4 % of it) = 6 SOL). Slot A: tx 2 W buys (not a token sell), tx 3 W
    # sells 1 SOL (below the size), tx 5 = the trigger (W sells 8 SOL), tx 7 Y buys, tx 8 W sells 1 SOL again (the wallet's
    # last leg: the v1 choice), tx 9 the creator CR sells (the slot rule's creator veto); slot A + 1: tx 1; slot A + 2: tx 1.
    # (kind, slot, tx, pre b, pre e, base, e_delta, trader)
    legs = [("buy", A, 2, 10**12, 100 * SOL, 10**10, 1 * SOL, "W"), ("sell", A, 3, 99 * 10**10, 101 * SOL, 10**10, -1 * SOL, "W"),
            ("sell", A, 5, 100 * 10**10, 100 * SOL, 8 * 10**10, -8 * SOL, "W"),
            ("buy", A, 7, 108 * 10**10, 92 * SOL, 10**10, 1 * SOL, "Y"), ("sell", A, 8, 107 * 10**10, 93 * SOL, 10**10, -1 * SOL, "W"),
            ("sell", A, 9, 108 * 10**10, 92 * SOL, 10**10, -1 * SOL, "CR"),
            ("buy", A + 1, 1, 109 * 10**10, 91 * SOL, 10**10, 1 * SOL, "Q"), ("buy", A + 2, 1, 108 * 10**10, 92 * SOL, 10**10, 1 * SOL, "Q")]
    con.executemany("INSERT INTO pool VALUES (?, 'P', ?, ?, 0, 0, ?, ?, ?, 0, ?, ?, ?, 20, 5, 0, 0, ?, ?, ?, ?)",
                    [(k, sl, tx, b, e, e, base, dq, abs(dq), w, k == "buy", abs(dq), 150 * SOL) for k, sl, tx, b, e, base, dq, w in legs])
    # slot A + 1, tx 1 is a round trip: Q buys (above) and sells again in the same transaction (inner_ix 1); the first
    # transaction of A + 1 ends after the sell, so the t+1 entry (2001) takes that state, not the state between the legs.
    con.execute("INSERT INTO pool VALUES ('sell', 'P', ?, 1, 0, 1, ?, ?, ?, 0, ?, ?, ?, 20, 5, 0, 0, 'Q', false, ?, ?)",
                [A + 1, 108 * 10**10, 92 * SOL, 92 * SOL, 10**10, -1 * SOL, 1 * SOL, 1 * SOL, 150 * SOL])
    con.execute("CREATE TABLE curve (mint VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, vt BIGINT, vq BIGINT, rt BIGINT, rq BIGINT, fee_bps BIGINT, creator_bps BIGINT)")
    con.close()
    parts = tmp_path / "parts"
    os.makedirs(parts)
    pq.write_table(pa.Table.from_pylist([{"family": "F7", "variant": "tp4_T600", "segment": "pool_normal", "venue": "pool", "orientation": "normal", "mint": "MP",
                                          "pool": "P", "t": A, "day": "2026-09-11", "farm": "organic", "ref_mint": None, "ref_t": None, "trigger_trader": "W"},
                                         {"family": "F7", "variant": "tp4_T600", "segment": "curve", "venue": "curve", "orientation": "normal", "mint": "C",
                                          "pool": None, "t": A, "day": "2026-09-11", "farm": "n/a", "ref_mint": None, "ref_t": None, "trigger_trader": "V"}]),
                   os.path.join(parts, "part-0.parquet"))
    info = markout.run(str(parts), "tx", str(tmp_path / "out"), store=str(store), cfg_path=_cfg(tmp_path), tx_delays=True)
    assert info["tx_delays"] == {1000: "t, trigger tx + 0", 1001: "t, trigger tx + 1", 1003: "t, trigger tx + 3", 1010: "t, trigger tx + 10", 2001: "t+1, first tx"}
    assert info["delays"] == [0, 1, 2, 3, 1000, 1001, 1003, 1010, 2001]
    assert info["tx_stats"] == {"pool_signals": 1, "with_leg_in_t": 1, "trigger_trader_multi_leg": 1, "trigger_kind_missing": 0, "trigger_size_missing": 0,
                                "trigger_smaller_sell_before": 1, "creator_sell_after_trigger": 1, "capped": {1000: 0, 1001: 0, 1003: 0, 1010: 1, 2001: 0},
                                "tx_end": {1000: 0, 1001: 0, 1003: 0, 1010: 0, 2001: 1}}
    R = pq.read_table(str(tmp_path / "out" / "markout-tx.parquet")).to_pylist()
    assert {r["d"] for r in R if r["segment"] == "curve"} == {1, 2}            # curve signals keep the slot delays
    R = [r for r in R if r["segment"] != "curve"]
    ent = {r["d"]: r for r in R if r["h"] == ""}
    post = lambda b, e: e / b  # mid of a normal pool = e / b (virt 0)
    assert ent[1000]["mid"] == pytest.approx(post(108 * 10**10, 92 * SOL))   # the trigger leg's post-state (tx 5: b 1e12 + 8e10, e 100 - 8): not W's small sell (tx 3), not its last leg (tx 8)
    assert ent[1001]["mid"] == pytest.approx(post(107 * 10**10, 93 * SOL))   # one leg after it (tx 7)
    assert ent[1003]["mid"] == pytest.approx(post(109 * 10**10, 91 * SOL))   # three legs after it = the slot's last leg (tx 9)
    assert ent[1010]["mid"] == ent[1003]["mid"] and ent[1010]["capped"] and not ent[1003]["capped"] and ent[1]["capped"] is None
    assert ent[0]["mid"] == ent[1003]["mid"]                                  # d = 0 = the end of slot A: the same state
    assert ent[2001]["mid"] == pytest.approx(post(109 * 10**10, 91 * SOL))   # the first transaction of A + 1 ends after its second leg (the sell)
    assert ent[1]["mid"] == ent[2001]["mid"]                                  # d = 1 = the end of A + 1: the same single transaction
    assert ent[2001]["target"] == A + 1 and ent[1000]["target"] == A and ent[0]["target"] == A and ent[3]["target"] == A + 3
    assert ent[3]["mid"] == pytest.approx(post(107 * 10**10, 93 * SOL))      # d = 3 = the end of A + 3: the last leg (A + 2) carried forward
    # horizons of a tx-level entry run from its entry slot: the 1 s horizon (400 ms slots → 3 slots) of 2001 ends at A + 4
    h1 = {r["d"]: r["target"] for r in R if r["h"] == "1s"}
    assert h1[2001] == A + 1 + 3 and h1[1000] == A + 3 and h1[1] == A + 1 + 3 and h1[0] == A + 3
    assert all(r["capped"] == ent[r["d"]]["capped"] for r in R)               # the horizon rows carry the entry's flag


def test_delay_ms_adds_the_clock_delay(tmp_path, monkeypatch):
    """--delay-ms: the slot delay of that many ms per epoch through the Clock beside DELAYS (400 ms on the mini store: 1,300 ms
    = 3 slots), listed per epoch in the run JSON, with the parts' sha256s."""
    _store(tmp_path / "s.duckdb")
    _parts(tmp_path / "parts")
    monkeypatch.setenv("LAB_ENGINE", ENGINE)
    info = markout.run(str(tmp_path / "parts"), "dm", str(tmp_path / "out"), store=str(tmp_path / "s.duckdb"), cfg_path=_cfg(tmp_path), delay_ms=1300)
    assert info["delay_ms"] == 1300 and info["delay_slots_per_epoch"] == {str(SLOT0 // 432_000): 3} and info["delays"] == [1, 2]
    assert list(info["parts_sha256"]) == ["part-0.parquet"] and len(info["parts_sha256"]["part-0.parquet"]) == 64
    con = duckdb.connect()
    ds = [r[0] for r in con.execute(f"SELECT DISTINCT d FROM read_parquet('{tmp_path}/out/markout-dm.parquet') ORDER BY 1").fetchall()]
    assert ds == [1, 2, 3]
    # d 3 rows: the entry at t + 3, one per signal and horizon, same count as d 2
    n2, n3 = con.execute(f"SELECT count(*) FILTER (WHERE d = 2), count(*) FILTER (WHERE d = 3) FROM read_parquet('{tmp_path}/out/markout-dm.parquet')").fetchone()
    assert n2 == n3 > 0
