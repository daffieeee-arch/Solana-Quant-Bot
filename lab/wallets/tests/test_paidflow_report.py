"""paidflow_report on synthetic markout rows: the arm is >= 2 distinct paying buyers in the 60 s before the trigger, the pool
is the unit, the three outcomes plus NOT JUDGED, the frozen rule, d from the run JSON, the window / floor refusals, the
missing-column refusal, the payer classes, the workflow band and the depth cross, markdown and main."""
import json
import os

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import paidflow_report as pr

SOL = 10**9
COLS = ("family", "segment", "venue", "orientation", "mint", "pool", "t", "day", "ms_per_slot", "regime", "farm", "ref_mint", "ref_t", "variants",
        "d", "h", "h_ms", "target", "cut", "mid", "gross", "net", "impact", "capped", "depth_entry", "fee_bps", "creator_prior_pools", "paid_buyers_60s", "paid_max_60s")


def _rows(pools, trig_per_pool, paid_fn, net_fn, days=("2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"),
          seed=1, ds=(2,), creator=None, depth_fn=lambda i, j, rng: 120 * SOL, max_fn=None):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(pools):
        pool = f"POOL{i}"
        for j in range(trig_per_pool):
            t = 455_760_000 + 1000 * (i * trig_per_pool + j)
            day = days[(i * trig_per_pool + j) % len(days)]
            paid = paid_fn(i, j, rng)
            pmax = max_fn(i, j, rng) if max_fn else (None if paid == 0 else 10_000)
            for d in ds:
                base = {"family": "F7", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": f"M{i}", "pool": pool, "t": t,
                        "day": day, "ms_per_slot": 268.0, "regime": "268ms", "farm": "organic" if i % 2 else "flagged", "ref_mint": None, "ref_t": None,
                        "variants": ["tp4_T600"], "d": d, "target": t + d, "cut": False, "mid": 1e-6, "impact": 0.006, "capped": None,
                        "depth_entry": depth_fn(i, j, rng), "fee_bps": 30, "creator_prior_pools": creator(i) if creator else None, "paid_buyers_60s": paid, "paid_max_60s": pmax}
                out.append(base | {"h": "", "h_ms": 0, "gross": None, "net": None})
                for h, ms in (("1m", 60_000), ("5m", 300_000)):
                    net = net_fn(i, j, h, rng)
                    out.append(base | {"h": h, "h_ms": ms, "target": t + d + ms // 268, "gross": net + 0.01, "net": net})
    return out


def _write(tmp_path, rows, run="clean-c3", run_json=None, cols=COLS):
    d = tmp_path / "mk"
    d.mkdir(exist_ok=True)
    table = pa.table({c: pa.array([r[c] for r in rows], type=pa.list_(pa.string()) if c == "variants" else None) for c in cols})
    pq.write_table(table, d / f"markout-{run}.parquet")
    if run_json is not None:
        json.dump(run_json, open(d / f"run-markout-{run}.json", "w"))
    return str(d)


RUN_JSON = {"size_sol": 0.5, "delay_ms": 600, "delay_slots_per_epoch": {"1055": 2}, "delay_slots_default": 2, "paid_window_ms": 60_000, "paid_min_lamports": 5_000}


def _analyse(tmp_path, rows, boot=300, d=None, run_json=RUN_JSON):
    con = duckdb.connect()
    d_info = pr.load(con, _write(tmp_path, rows, run_json=run_json), "clean-c3", d)
    return con, pr.analyse(con, boot, d_info)


def test_arm_is_two_or_more_paying_buyers_and_the_pool_is_the_unit(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    # 120 pools: 100 in the arm (paying buyers 2 .. 12, one pool at exactly 2), 10 at exactly 1 (outside), 10 at 0 (outside)
    def paid(i, j, rng):
        return 2 if i == 0 else (2 + i % 11 if i < 100 else (1 if i < 110 else 0))
    # arm pools win +5 % on every trigger, one arm pool has 50 triggers at +80 % (it must weigh once), outside pools lose
    def net(i, j, h, rng):
        return 0.8 if i == 2 else (0.05 if i < 100 else -0.3)
    rows = _rows(120, 2, paid, net) + [r | {"pool": "POOLX", "mint": "MX"} for r in _rows(1, 50, lambda i, j, r: 4, lambda i, j, h, r: 0.8, seed=2)]
    con, res = _analyse(tmp_path, rows)
    assert res["input"]["arm_pools"] == 101 and res["input"]["all_pools"] == 121
    in_arm = dict(con.execute("SELECT pool, bool_and(in_arm) FROM f GROUP BY 1").fetchall())
    assert in_arm["POOL0"] is True and in_arm["POOL100"] is False and in_arm["POOL110"] is False
    p1 = res["primary"]["5m"]["P1_net"]
    # 99 pools at +5 % and 2 at +80 %: the trimmed mean over pools is close to +6.5 %, nowhere near the trigger-weighted mean
    assert p1["pools"] == 101 and 0.05 < p1["mean"] < 0.08 and p1["lo"] > 0
    assert res["verdict"]["outcome"] == "CONFIRMED" and res["verdict"]["consistency_same_sign"]["1m"] is True
    assert res["secondary"]["5m"]["complement_net"]["mean"] < 0
    c = res["classes"]["5m"]
    assert c["0"]["triggers"] == 20 and c["1"]["triggers"] == 20 and c["2-3"]["triggers"] > 0 and c[">=9"]["triggers"] > 0
    assert sum(v["triggers"] for v in c.values()) == 290


def test_rejected_inconclusive_and_not_judged(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    paid = lambda i, j, rng: 3
    _, res = _analyse(tmp_path, _rows(120, 2, paid, lambda i, j, h, rng: -0.02))
    assert res["verdict"]["outcome"] == "REJECTED" and res["verdict"]["P1_lo_above_0"] is False
    _, res = _analyse(tmp_path, _rows(120, 2, paid, lambda i, j, h, rng: 0.02 if i % 2 else -0.017))
    assert res["verdict"]["outcome"] == "INCONCLUSIVE" and res["primary"]["5m"]["P1_net"]["mean"] > 0 and res["primary"]["5m"]["P1_net"]["lo"] <= 0
    # fewer than MIN_POOLS pools in the arm (the others have one paying buyer): no outcome
    _, res = _analyse(tmp_path, _rows(120, 2, lambda i, j, rng: 3 if i < 50 else 1, lambda i, j, h, rng: 0.05))
    assert res["verdict"]["outcome"] == "NOT JUDGED" and res["input"]["arm_pools"] == 50 and res["verdict"]["consistency_same_sign"]["1m"] is None
    _, res = _analyse(tmp_path, _rows(120, 2, paid, lambda i, j, h, rng: 0.05, days=("2026-11-01", "2026-11-02")))
    assert res["verdict"]["outcome"] == "NOT JUDGED" and res["input"]["arm_days"] == 2


def test_wiped_share_creator_class_report_band_and_depth_cross(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    # every pool: one trigger wiped (−100 %), one at +30 %; creator classes −1 / 0 / 5 / 50 / 500 / NULL; the highest paid buy
    # alternates inside and outside the workflow band; the depth alternates 120 and 180 SOL
    creators = [-1, 0, 5, 50, 500, None]
    rows = _rows(120, 2, lambda i, j, rng: 2 + (i % 3), lambda i, j, h, rng: -1.0 if j == 0 else 0.3, creator=lambda i: creators[i % 6],
                 depth_fn=lambda i, j, rng: (120 if i % 2 else 180) * SOL, max_fn=lambda i, j, rng: 20_000 if i % 2 else 900_000)
    _, res = _analyse(tmp_path, rows)
    w = res["secondary"]["5m"]["arm_wiped"]
    assert abs(w["mean"] - 0.5) < 1e-9 and abs(res["descriptive"]["per_trigger"]["wiped"] - 0.5) < 1e-9
    assert abs(res["primary"]["5m"]["P1_net"]["mean"] - (-0.35)) < 1e-9
    assert set(res["descriptive"]["creator_class"]) == {"creator onbekend (system-program)", "eerste in de store-window", "creator met 1-9 eerdere pools",
                                                        "creator met 10-99 eerdere pools", "creator met >= 100 eerdere pools", "pool niet in de store"}
    assert all(v["net"]["pools"] == 20 for v in res["descriptive"]["creator_class"].values())
    rb = res["descriptive"]["report_band"]
    assert rb["band & arm"]["triggers"] == 120 and rb["arm & not band"]["triggers"] == 120 and rb["band & not arm"]["triggers"] == 0 and rb["neither"]["triggers"] == 0
    db = res["descriptive"]["day_blocks"]
    assert db["first"]["days"] == ["2026-11-01", "2026-11-02", "2026-11-03"] and db["second"]["days"] == ["2026-11-04", "2026-11-05", "2026-11-06"]
    assert db["first"]["arm"]["triggers"] == 120 and db["second"]["arm"]["triggers"] == 120 and db["first"]["complement"]["triggers"] == 0
    dp = res["descriptive"]["depth"]
    assert dp["100-150"]["arm"]["triggers"] == 120 and dp["150-200"]["arm"]["triggers"] == 120 and dp["100-150"]["complement"]["triggers"] == 0 and dp[">=500"]["arm"]["triggers"] == 0


def test_d_comes_from_the_run_json_and_the_run_settings_are_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    rows = _rows(120, 2, lambda i, j, rng: 3, lambda i, j, h, rng: 0.05, ds=(2, 3))
    with pytest.raises(SystemExit, match="delay_slots_per_epoch"):
        _analyse(tmp_path, rows, run_json={"size_sol": 0.5})
    con, res = _analyse(tmp_path, rows, run_json=RUN_JSON | {"delay_slots_per_epoch": {"1055": 3}, "delay_slots_default": 3})
    assert res["rule"]["d_rows"] == {"3": 240} and res["verdict"]["outcome"] == "CONFIRMED"
    with pytest.raises(SystemExit, match="delay_ms 500"):
        _analyse(tmp_path, rows, run_json=RUN_JSON | {"delay_ms": 500})
    with pytest.raises(SystemExit, match="0.25 SOL"):
        _analyse(tmp_path, rows, run_json=RUN_JSON | {"size_sol": 0.25})
    with pytest.raises(SystemExit, match="paid_window_ms 30000"):
        _analyse(tmp_path, rows, run_json=RUN_JSON | {"paid_window_ms": 30_000})
    with pytest.raises(SystemExit, match="paid_min_lamports 1000"):
        _analyse(tmp_path, rows, run_json=RUN_JSON | {"paid_min_lamports": 1_000})
    # a run JSON without the paid settings (older markout) is accepted when the column is there
    _, res = _analyse(tmp_path, rows, run_json={"size_sol": 0.5, "delay_ms": 600, "delay_slots_per_epoch": {"1055": 2}, "delay_slots_default": 2})
    assert res["verdict"]["outcome"] == "CONFIRMED"


def test_refuses_a_markout_without_paid_buyers(tmp_path):
    rows = _rows(5, 1, lambda i, j, rng: 3, lambda i, j, h, rng: 0.05)
    cols = tuple(c for c in COLS if c not in ("paid_buyers_60s", "paid_max_60s"))
    d = _write(tmp_path, rows, run="x", cols=cols)
    with pytest.raises(SystemExit, match="paid_buyers_60s"):
        pr.load(duckdb.connect(), d, "x", 2)


def test_rule_is_frozen():
    assert pr.RULE == {"delay_ms": 600, "h": "5m", "consistency": ("1m",), "min_paid_buyers": 2, "paid_min_lamports": 5_000, "paid_window_ms": 60_000, "size_sol": 0.5,
                       "min_pools": 100, "min_days": 5, "boot": 10_000, "seed": 7, "trim": 0.005, "wiped": -0.999, "unit": "pool", "family": "F7", "segment": "pool_reversed"}
    assert pr.CLASSES == ((0, 0), (1, 1), (2, 3), (4, 8), (9, None)) and pr.REPORT_BAND == (5_000, 500_000)
    from wallets import markout
    assert markout.PAID_WINDOW_MS == pr.RULE["paid_window_ms"] and markout.PAID_MIN_LAMPORTS == pr.RULE["paid_min_lamports"]


def test_markdown_and_main(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    rows = _rows(120, 2, lambda i, j, rng: 3 if i < 110 else 0, lambda i, j, h, rng: 0.05)
    mk = _write(tmp_path, rows, run_json=RUN_JSON)
    out = tmp_path / "paidflow.md"
    monkeypatch.setattr("sys.argv", ["paidflow_report", "--markout", mk, "--run", "clean-c3", "--boot", "200", "--out", str(out)])
    pr.main()
    md = out.read_text()
    assert "**Outcome: CONFIRMED**" in md and "| P1 net in arm @ 5m | 110 |" in md and "| **2-3** | 220 | 110 |" in md and "| 0 | 20 | 10 |" in md
    assert "net outside the arm @ 5m" in md and "per trigger (unweighted) in the arm at 5m: n 220" in md and "depth 100-150 SOL at entry: arm 220 triggers" in md
    assert "day block first (2026-11-01 … 2026-11-03, 3 days): arm 111 triggers" in md  # pools 0, 3, 6 … fill days 1-2, pools 1, 4, … day 3
    j = json.load(open(tmp_path / "paidflow.json"))
    assert j["verdict"]["outcome"] == "CONFIRMED" and j["rule"]["d"]["source"].startswith("run JSON") and os.path.exists(out)
