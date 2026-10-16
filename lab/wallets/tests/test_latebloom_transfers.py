"""E22 addendum on the transfers mini store and chunk (test_transfers): holder concentration, the insider share and the
transfer-fed holdings at t, the transfer-fed selling in the post-entry windows, Q3 as a reference, and a trigger whose
mint was created before the window (no ledger: NULL features, counted)."""

import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from wallets import latebloom_transfers
from wallets.tests.test_transfers import LO, M, S, U, _chunk, _store

T = S + 65          # the F7 trigger on pool P (created at S + 60), before the pool's trades at S + 70
MS = 267.3


def _inputs(tmp_path):
    parts = tmp_path / "parts"
    os.makedirs(parts)
    base = {"family": "F7", "variant": "tp4_T600", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "day": "2026-09-22",
            "farm": "organic", "regime": "270ms", "trigger_trader": "X", "trigger_farmer": False, "depth": 150 * 10**9, "d": 1}
    pq.write_table(pa.Table.from_pylist([{**base, "mint": M, "pool": "P", "t": T}, {**base, "mint": "Old", "pool": "POLD", "t": T}]), parts / "part-0.parquet")
    mk = []
    for p in ("P", "POLD"):
        b = {"family": "F7", "segment": "pool_reversed", "pool": p, "t": T, "d": 1, "ms_per_slot": MS, "cut": False, "impact": 0.006}
        mk.append({**b, "h": "", "target": T + 1, "mid": 0.1, "net": None})
        for h, n, ms in (("1m", 0.02, 60_000), ("5m", 0.08, 300_000), ("15m", 0.1, 900_000)):
            mk.append({**b, "h": h, "target": T + 1 + int(ms / MS), "mid": 1.0, "net": n})
    pq.write_table(pa.Table.from_pylist(mk), tmp_path / "markout.parquet")
    pq.write_table(pa.Table.from_pylist([{"pool": p, "t": T, "drain_slot": None, "event": False, "first_withdraw_slot": None, "last_trade_slot": T + 100}
                                         for p in ("P", "POLD")]), tmp_path / "survival.parquet")
    return str(parts), str(tmp_path / "markout.parquet"), str(tmp_path / "survival.parquet")


def test_holders_and_transfers_at_t(tmp_path, monkeypatch):
    _store(tmp_path / "s.duckdb")
    root = _chunk(tmp_path / "chunks")
    parts, mk, sv = _inputs(tmp_path)
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    out = tmp_path / "out"
    info = latebloom_transfers.run(root, parts, mk, sv, str(out), boot=50, store=str(tmp_path / "s.duckdb"))
    assert info["triggers"] == 2 and info["triggers_with_ledger"] == 1 and info["lo"] == LO
    con = duckdb.connect()
    rows = {r["pool"]: r for r in con.execute(f"SELECT * FROM '{out / 'latebloom-transfers.parquet'}'").arrow().read_all().to_pylist()}
    p, old = rows["P"], rows["POLD"]
    # the old mint has no ledger in the window: counted, features NULL
    assert not old["in_window"] and old["holders_t"] is None and old["share_c_t"] is None and old["late_5"]
    # at t = S + 65 the ledger of test_transfers after X's last curve sell (S + 50): circulating 360 = CR 300, I 40, W 19, FEE 1
    # (X: 60 received, 50 + 10 sold; R, PDA1 and Z net 0), insiders CR + I
    assert p["in_window"] and p["circ_t"] == 360 * U and p["holders_t"] == 4 and p["n_insiders"] == 2
    assert abs(p["share_c_t"] - 340 / 360) < 1e-12 and abs(p["top1_share_t"] - 300 / 360) < 1e-12 and p["top10_share_t"] == 1.0
    assert abs(p["hhi_t"] - sum((x / 360) ** 2 for x in (300, 40, 19, 1))) < 1e-12
    # transfer-fed holdings: W received 19 and FEE 1 from R by non-trade transfer; CR and I bought on the curve
    assert p["fed_holders_t"] == 2 and abs(p["fed_share_t"] - 20 / 360) < 1e-12 and abs(p["fed_holder_share_t"] - 0.5) < 1e-12
    # post-entry: X (not an insider) sells 5 on pool P at S + 70, inside both windows; it received 60 from I at S + 20 -> fully fed
    assert p["sellers_30"] == 1 and p["fed_sellers_30"] == 1 and p["fed_sell_share_30"] == 1.0
    assert p["sellers_60"] == 1 and p["fed_sell_share_60"] == 1.0
    assert old["sellers_30"] is None or old["sellers_30"] == 0
    # Q3 reference: the pool's first hour (test_transfers: 1 seller, fully fed)
    assert p["q3_fed_share_1h"] == 1.0 and p["q3_sellers_1h"] == 1
    md = (out / "latebloom-transfers.md").read_text()
    assert "E22 addendum" in md and "`fed_share_t`" in md and "| days 10-18 | 2 |" in md and "| fed_share_t | 1 |" in md


def test_no_nested_loop_join_in_any_query(tmp_path, monkeypatch):
    """Every table of the addendum is built with hash joins (test_transfers: a nested-loop plan sat 6 h on one epoch)."""
    from wallets.tests.test_transfers import _Explained

    _store(tmp_path / "s.duckdb")
    root = _chunk(tmp_path / "chunks")
    parts, mk, sv = _inputs(tmp_path)
    monkeypatch.setenv("LAB_SPILL_DIR", str(tmp_path / "spill"))
    seen = {}
    real = latebloom_transfers.connect

    def wrapped(*a, **k):
        px = _Explained(real(*a, **k))
        seen["plans"] = px.plans
        return px
    monkeypatch.setattr(latebloom_transfers, "connect", wrapped)
    latebloom_transfers.run(root, parts, mk, sv, str(tmp_path / "out"), boot=20, store=str(tmp_path / "s.duckdb"))
    plans = seen["plans"]
    assert {"tw", "hold", "holdr", "feat_t", "sells", "ps", "feat_post", "lb"} <= set(plans)
    bad = {t: ops for t, ops in plans.items() if any("NESTED_LOOP" in op or "BLOCKWISE" in op or "CROSS_PRODUCT" in op for op in ops)}
    assert not bad, bad
