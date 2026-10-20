"""depthband_report on synthetic markout rows: the band is the entry state's SOL side (100 inclusive, 150 exclusive), the
pool is the unit, the three outcomes plus NOT JUDGED, the frozen rule, d from the run JSON, the missing-column refusal,
markdown and main."""
import json
import os

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import depthband_report as dr

SOL = 10**9
COLS = ("family", "segment", "venue", "orientation", "mint", "pool", "t", "day", "ms_per_slot", "regime", "farm", "ref_mint", "ref_t", "variants",
        "d", "h", "h_ms", "target", "cut", "mid", "gross", "net", "impact", "capped", "depth_entry", "fee_bps", "creator_prior_pools")


def _rows(pools, trig_per_pool, depth_fn, net_fn, days=("2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"),
          seed=1, ds=(2,), creator=None):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(pools):
        pool = f"POOL{i}"
        for j in range(trig_per_pool):
            t = 455_760_000 + 1000 * (i * trig_per_pool + j)
            day = days[(i * trig_per_pool + j) % len(days)]
            depth = depth_fn(i, j, rng)
            for d in ds:
                base = {"family": "F7", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": f"M{i}", "pool": pool, "t": t,
                        "day": day, "ms_per_slot": 268.0, "regime": "268ms", "farm": "organic" if i % 2 else "flagged", "ref_mint": None, "ref_t": None,
                        "variants": ["tp4_T600"], "d": d, "target": t + d, "cut": False, "mid": 1e-6, "impact": 0.006, "capped": None, "depth_entry": depth,
                        "fee_bps": 30, "creator_prior_pools": creator(i) if creator else None}
                out.append(base | {"h": "", "h_ms": 0, "gross": None, "net": None})
                for h, ms in (("1m", 60_000), ("5m", 300_000)):
                    net = net_fn(i, j, h, rng)
                    out.append(base | {"h": h, "h_ms": ms, "target": t + d + ms // 268, "gross": net + 0.01, "net": net})
    return out


def _write(tmp_path, rows, run="clean-e49", run_json=None):
    d = tmp_path / "mk"
    d.mkdir(exist_ok=True)
    table = pa.table({c: pa.array([r[c] for r in rows], type=pa.list_(pa.string()) if c == "variants" else None) for c in COLS})
    pq.write_table(table, d / f"markout-{run}.parquet")
    if run_json is not None:
        json.dump(run_json, open(d / f"run-markout-{run}.json", "w"))
    return str(d)


RUN_JSON = {"size_sol": 0.5, "delay_ms": 600, "delay_slots_per_epoch": {"1055": 2}, "delay_slots_default": 2}


def _analyse(tmp_path, rows, boot=300, d=None, run_json=RUN_JSON):
    con = duckdb.connect()
    d_info = dr.load(con, _write(tmp_path, rows, run_json=run_json), "clean-e49", d)
    return con, dr.analyse(con, boot, d_info)


def test_band_is_inclusive_at_100_exclusive_at_150_and_the_pool_is_the_unit(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    # 120 pools: 100 in the band (one pool at exactly 100 SOL, one at 149.999), 10 at exactly 150 SOL (outside), 10 at 80 SOL (outside)
    def depth(i, j, rng):
        if i < 100:
            return 100 * SOL if i == 0 else (150 * SOL - 1 if i == 1 else int(rng.uniform(101, 149) * SOL))
        return 150 * SOL if i < 110 else 80 * SOL
    # band pools win +5 % on every trigger, one band pool has 50 triggers at +80 % (it must weigh once), outside pools lose
    def net(i, j, h, rng):
        if i < 100:
            return 0.8 if i == 2 else 0.05
        return -0.3
    rows = _rows(120, 2, depth, net) + [r | {"pool": "POOLX", "mint": "MX"} for r in _rows(1, 50, lambda i, j, r: 120 * SOL, lambda i, j, h, r: 0.8, seed=2)]
    con, res = _analyse(tmp_path, rows)
    assert res["input"]["band_pools"] == 101 and res["input"]["all_pools"] == 121
    in_band = dict(con.execute("SELECT pool, bool_and(in_band) FROM f GROUP BY 1").fetchall())
    assert in_band["POOL0"] is True and in_band["POOL1"] is True and in_band["POOL100"] is False and in_band["POOL110"] is False
    p1 = res["primary"]["5m"]["P1_net"]
    # 99 pools at +5 % and 2 at +80 %: the trimmed mean over pools is close to +6.5 %, nowhere near the trigger-weighted mean (the
    # 50-trigger pool would pull a per-trigger mean to about +30 %)
    assert p1["pools"] == 101 and 0.05 < p1["mean"] < 0.08 and p1["lo"] > 0
    assert res["verdict"]["outcome"] == "CONFIRMED" and res["verdict"]["consistency_same_sign"]["1m"] is True
    assert res["secondary"]["5m"]["complement_net"]["mean"] < 0 and res["bands"]["5m"]["100-150"]["triggers"] == 250
    assert res["bands"]["5m"][">=500"]["triggers"] == 0 and res["bands"]["5m"]["150-200"]["triggers"] == 20


def test_rejected_inconclusive_and_not_judged(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    depth = lambda i, j, rng: 120 * SOL
    _, res = _analyse(tmp_path, _rows(120, 2, depth, lambda i, j, h, rng: -0.02))
    assert res["verdict"]["outcome"] == "REJECTED" and res["verdict"]["P1_lo_above_0"] is False
    # half the pools +2 %, half −1.7 %: a positive mean whose interval spans 0
    _, res = _analyse(tmp_path, _rows(120, 2, depth, lambda i, j, h, rng: 0.02 if i % 2 else -0.017))
    assert res["verdict"]["outcome"] == "INCONCLUSIVE" and res["primary"]["5m"]["P1_net"]["mean"] > 0 and res["primary"]["5m"]["P1_net"]["lo"] <= 0
    # fewer than MIN_POOLS pools in the band (the others sit at 200 SOL): no outcome
    _, res = _analyse(tmp_path, _rows(120, 2, lambda i, j, rng: 120 * SOL if i < 50 else 200 * SOL, lambda i, j, h, rng: 0.05))
    assert res["verdict"]["outcome"] == "NOT JUDGED" and res["input"]["band_pools"] == 50 and res["verdict"]["consistency_same_sign"]["1m"] is None
    # fewer than MIN_DAYS days
    _, res = _analyse(tmp_path, _rows(120, 2, depth, lambda i, j, h, rng: 0.05, days=("2026-11-01", "2026-11-02")))
    assert res["verdict"]["outcome"] == "NOT JUDGED" and res["input"]["band_days"] == 2


def test_wiped_share_and_creator_class(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    # every pool: one trigger wiped (−100 %), one at +30 %; creator classes −1 / 0 / 5 / 50 / 500 / NULL
    creators = [-1, 0, 5, 50, 500, None]
    rows = _rows(120, 2, lambda i, j, rng: 120 * SOL, lambda i, j, h, rng: -1.0 if j == 0 else 0.3, creator=lambda i: creators[i % 6])
    _, res = _analyse(tmp_path, rows)
    w = res["secondary"]["5m"]["band_wiped"]
    assert abs(w["mean"] - 0.5) < 1e-9 and abs(res["descriptive"]["per_trigger"]["wiped"] - 0.5) < 1e-9
    assert abs(res["primary"]["5m"]["P1_net"]["mean"] - (-0.35)) < 1e-9
    assert set(res["descriptive"]["creator_class"]) == {"creator onbekend (system-program)", "eerste pool van de creator", "creator met 1-9 eerdere pools",
                                                        "creator met 10-99 eerdere pools", "creator met >= 100 eerdere pools", "pool niet in de store"}
    assert all(v["net"]["pools"] == 20 for v in res["descriptive"]["creator_class"].values())


def test_d_comes_from_the_run_json_and_the_size_is_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    rows = _rows(120, 2, lambda i, j, rng: 120 * SOL, lambda i, j, h, rng: 0.05, ds=(2, 3))
    with pytest.raises(SystemExit, match="delay_slots_per_epoch"):
        _analyse(tmp_path, rows, run_json={"size_sol": 0.5})
    # the json says d 3 for every epoch: only the d 3 rows are read
    con, res = _analyse(tmp_path, rows, run_json={"size_sol": 0.5, "delay_ms": 600, "delay_slots_per_epoch": {"1055": 3}, "delay_slots_default": 3})
    assert res["rule"]["d_rows"] == {"3": 240} and res["verdict"]["outcome"] == "CONFIRMED"
    with pytest.raises(SystemExit, match="delay_ms 500"):
        _analyse(tmp_path, rows, run_json={"size_sol": 0.5, "delay_ms": 500, "delay_slots_per_epoch": {"1055": 2}, "delay_slots_default": 2})
    with pytest.raises(SystemExit, match="0.25 SOL"):
        _analyse(tmp_path, rows, run_json={"size_sol": 0.25, "delay_ms": 600, "delay_slots_per_epoch": {"1055": 2}, "delay_slots_default": 2})


def test_refuses_a_markout_without_depth_entry(tmp_path):
    rows = _rows(5, 1, lambda i, j, rng: 120 * SOL, lambda i, j, h, rng: 0.05)
    d = tmp_path / "mk"
    d.mkdir()
    cols = [c for c in COLS if c not in ("depth_entry", "fee_bps", "creator_prior_pools")]
    pq.write_table(pa.table({c: pa.array([r[c] for r in rows], type=pa.list_(pa.string()) if c == "variants" else None) for c in cols}), d / "markout-x.parquet")
    with pytest.raises(SystemExit, match="depth_entry"):
        dr.load(duckdb.connect(), str(d), "x", 2)


def test_rule_is_frozen():
    assert dr.RULE == {"delay_ms": 600, "h": "5m", "consistency": ("1m",), "depth_band_sol": (100, 150), "size_sol": 0.5, "min_pools": 100, "min_days": 5,
                       "boot": 10_000, "seed": 7, "trim": 0.005, "wiped": -0.999, "unit": "pool", "family": "F7", "segment": "pool_reversed"}
    assert dr.BANDS == ((0, 100), (100, 150), (150, 200), (200, 300), (300, 500), (500, None))


def test_markdown_and_main(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    rows = _rows(120, 2, lambda i, j, rng: 120 * SOL if i < 110 else 300 * SOL, lambda i, j, h, rng: 0.05)
    mk = _write(tmp_path, rows, run_json=RUN_JSON)
    out = tmp_path / "depthband.md"
    monkeypatch.setattr("sys.argv", ["depthband_report", "--markout", mk, "--run", "clean-e49", "--boot", "200", "--out", str(out)])
    dr.main()
    md = out.read_text()
    assert "**Outcome: CONFIRMED**" in md and "| P1 net in band @ 5m | 110 |" in md and "| **100-150** | 220 | 110 |" in md and "| 300-500 | 20 | 10 |" in md
    assert "net outside the band @ 5m" in md and "per trigger (unweighted) at 5m: n 220" in md
    j = json.load(open(tmp_path / "depthband.json"))
    assert j["verdict"]["outcome"] == "CONFIRMED" and j["rule"]["d"]["source"].startswith("run JSON") and os.path.exists(out)
