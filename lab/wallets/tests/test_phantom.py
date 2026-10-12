"""Phantom cases on a mini store: the hold-out refusal, the most-traded mint per symbol, the timeline
buckets, the dev-bundle flag, insiders, big traders, and the Dutch report (strategies off: the
engine is not needed for these)."""

import json
import os

import duckdb
import pytest

from wallets import phantom, phantom_report
from wallets.common import HOLDOUT_START, Clock


def _mini_store(path, slot0=454_464_000):
    """Token T: created at slot0 by CR, 3 curve buyers (one insider), graduates 100 slots later into pool P,
    then 24 h of pool trades. Token U (same symbol, copycat) has 2 curve trades and no pool."""
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (json VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [json.dumps({"slot_start": slot0, "slot_end_exclusive": slot0 + 432_000,
                                                            "ms_per_slot": {str(slot0 // 432_000): 400.0}})])
    con.execute("CREATE TABLE blocks (slot BIGINT, block_time BIGINT)")
    con.execute(f"INSERT INTO blocks SELECT {slot0} + i, 1791440881 + i * 0.4 FROM range(0, 300000) t(i)")
    con.execute("CREATE TABLE mints (mint VARCHAR, create_slot BIGINT, creator VARCHAR, mayhem BOOLEAN, quote_mint VARCHAR, supply BIGINT, "
                "name VARCHAR, symbol VARCHAR, complete_slot BIGINT, migrate_slot BIGINT, pool VARCHAR)")
    con.execute("INSERT INTO mints VALUES ('T', ?, 'CR', false, 'So11111111111111111111111111111111111111112', 1000000, 'Token T', 'TOK', ?, ?, 'P'), "
                "('U', ?, 'CU', false, 'So11111111111111111111111111111111111111112', 1000000, 'Token U', 'tok', NULL, NULL, NULL)",
                [slot0, slot0 + 100, slot0 + 100, slot0 + 50])
    con.execute("CREATE TABLE pools (pool VARCHAR, quote_class VARCHAR, orientation VARCHAR, mint VARCHAR, mayhem BOOLEAN, create_slot BIGINT, "
                "coin_creator VARCHAR, b0 BIGINT, q0 BIGINT, created_in_period BOOLEAN)")
    con.execute("INSERT INTO pools VALUES ('P', 'sol', 'normal', 'T', false, ?, 'CR', 1000, 1000, true)", [slot0 + 100])
    con.execute("CREATE TABLE pool_stats (pool VARCHAR, n_buy BIGINT, n_sell BIGINT)")
    con.execute("INSERT INTO pool_stats VALUES ('P', 3, 2)")
    con.execute("CREATE TABLE curve (mint VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, trader VARCHAR, is_buy BOOLEAN, "
                "t BIGINT, q BIGINT, vt BIGINT, vq BIGINT, rq BIGINT)")
    sol = 10**9
    con.executemany("INSERT INTO curve VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?)", [
        ("T", slot0 + 1, 0, "IN", True, 100, 2 * sol, 10_000, 30 * sol, 2 * sol),
        ("T", slot0 + 60, 0, "W1", True, 100, 10 * sol, 9_900, 40 * sol, 12 * sol),
        ("T", slot0 + 100, 0, "W2", True, 100, 73 * sol, 9_800, 113 * sol, 85 * sol),
        ("U", slot0 + 50, 0, "X1", True, 100, 1 * sol, 10_000, 31 * sol, 1 * sol),
        ("U", slot0 + 70, 0, "X1", False, 100, 1 * sol, 10_100, 30 * sol, 0)])
    con.execute("CREATE TABLE pool (kind VARCHAR, pool VARCHAR, slot BIGINT, tx_index BIGINT, outer_ix BIGINT, inner_ix BIGINT, trader VARCHAR, "
                "token_buy BOOLEAN, sol_amount BIGINT, token_amount BIGINT, farmer BOOLEAN, b BIGINT, e BIGINT, e_delta BIGINT, base BIGINT, "
                "sol_depth BIGINT, vault BIGINT)")
    o = slot0 + 100
    legs = [("buy", o + 10, "W3", True, 5 * sol, 50, False, 1000, 1000 * sol // 1000, 0, 0, 85 * sol),
            ("buy", o + 20, "IN", True, 20 * sol, 100, False, 950, 1100 * sol // 1000, 0, 0, 90 * sol),
            ("sell", o + 9000, "IN", False, 30 * sol, 150, False, 850, 1500 * sol // 1000, 0, 0, 120 * sol),
            ("buy", o + 9100, "W4", True, 1 * sol, 5, True, 1000, 1200 * sol // 1000, 0, 0, 90 * sol),
            ("sell", o + 200000, "W3", False, 2 * sol, 50, False, 995, 1210 * sol // 1000, 0, 0, 91 * sol)]
    con.executemany("INSERT INTO pool VALUES (?, 'P', ?, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(k, s, w, tb, a, ta, f, b, e, ed, ba, sd, sd) for k, s, w, tb, a, ta, f, b, e, ed, ba, sd in legs])
    con.close()


def test_refuses_a_store_in_the_holdout(tmp_path):
    _mini_store(tmp_path / "h.duckdb", slot0=HOLDOUT_START)
    con = duckdb.connect(str(tmp_path / "h.duckdb"), read_only=True)
    with pytest.raises(SystemExit):
        phantom.check_store(con)


def test_describe_and_report(tmp_path):
    p = tmp_path / "s.duckdb"
    _mini_store(p)
    con = duckdb.connect(str(p), read_only=True)
    meta, lo, hi = phantom.check_store(con)
    clock = Clock.from_store(con)
    cand = phantom.candidates(con, ["tok", "nope"])
    assert [c["mint"] for c in cand["tok"]] == ["T", "U"] and cand["tok"][0]["n_pool"] == 5 and "nope" not in cand
    tl = phantom.describe(con, None, "T", clock, lo, hi)
    assert tl["pool"] == "P" and tl["pool_normal_sol"] and tl["p_open"] == 1.0
    assert tl["curve_buyers"] == 3 and tl["self_funded"] and not tl["grad_in_create"]  # <= 5 curve buyers
    assert tl["n_insiders"] == 2 and tl["window_24h"]["trades"] == 5 and tl["window_24h"]["traders"] == 3
    assert [r["wallet"] for r in tl["big_buyers"]][:2] == ["IN", "W3"] and tl["big_buyers"][0]["insider"]
    assert any(r["from_min"] is None for r in tl["series"]) and sum(r["trades"] for r in tl["series"] if r["from_min"] is not None) == 5
    first = next(r for r in tl["series"] if r["from_min"] == 0)
    assert first["trades"] == 2 and first["buyers"] == 2 and abs(first["net_sol"] - 25) < 1e-9
    assert tl["create_time"].startswith("2026-10-08")
    assert tl["f7"]["checked"] is False and tl["m1"]["checked"] is False
    tl["f7"] = {"checked": True, "n_triggers": 0, "first": []}
    tl["m1"] = {"checked": False, "reason": "test"}
    run = {"tag": "t", "slot_range": [lo, hi], "time_range": ["a", "b"], "commit": "0000000", "symbols": ["tok", "nope"],
           "chosen": {"tok": "T"}, "missing": ["nope"], "candidates": cand, "timelines": {"T": tl}}
    os.makedirs(tmp_path / "out")
    with open(tmp_path / "out" / "timelines-phantom-t.json", "w") as f:
        json.dump(run, f, default=str)
    md = phantom_report.render(*phantom_report.load(str(tmp_path / "out")))
    assert "| [TOK](https://dexscreener.com/solana/T) | Token T |" in md and "niet gevonden" in md
    assert "`U`" not in md and "Token U" in md and "**ja**" in md  # the copycat is an alternative, T is chosen
    assert "geen F7" in md and "M1: niet van toepassing (test)" in md and "+0–5 min" in md


if __name__ == "__main__":
    pytest.main([__file__])
