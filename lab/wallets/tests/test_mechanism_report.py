"""mechanism_report on synthetic markout rows: the pool is the unit (a pool with many triggers weighs once), the three
outcomes and 'not judged', the tradable flag apart from the outcome, the N2 pairing, the consistency horizons."""
import os

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from wallets import mechanism_report as mr

HS = (("1m", 60_000), ("5m", 300_000), ("15m", 900_000))


def _rows(pools, trig_per_pool, gross_fn, net_fn=None, n2_fn=lambda p, k, h: 0.0, days=6, twins=2, seed=1):
    """F7 pool_reversed triggers of `pools` pools with `trig_per_pool` (int or per-pool list) triggers each, over `days` days,
    d 1 and 2, every horizon; gross per trigger from gross_fn(pool, k, h), net = gross − 0.05 unless net_fn; N2 twins per trigger."""
    rng = np.random.default_rng(seed)
    out = []
    for p in range(pools):
        n = trig_per_pool if isinstance(trig_per_pool, int) else trig_per_pool[p]
        for k in range(n):
            t = 455_760_000 + 10_000 * (p * 50 + k)
            day = f"2026-10-{20 + (p + k) % days:02d}"
            for d in (1, 2):
                for h, ms in HS:
                    g = gross_fn(p, k, h)
                    nt = net_fn(p, k, h) if net_fn else g - 0.05
                    base = {"family": "F7", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": f"M{p}", "pool": f"P{p}",
                            "t": t, "day": day, "ms_per_slot": 218.0, "regime": "220ms", "farm": "organic" if p % 2 else "flagged",
                            "ref_mint": None, "ref_t": None, "d": d, "h": h, "h_ms": ms, "target": t + d + 10, "cut": False,
                            "mid": 1.0, "gross": g, "net": nt, "impact": 0.01}
                    out.append(base)
                    for j in range(twins):
                        out.append(base | {"family": "N2_F7", "mint": f"X{p}{k}{j}", "pool": f"Q{p}{k}{j}", "t": t + 1 + j, "ref_mint": f"P{p}",
                                           "ref_t": t, "farm": "n/a", "gross": n2_fn(p, k, h) + 0.001 * j, "net": n2_fn(p, k, h) - 0.05})
                # the entry row
                out.append({"family": "F7", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": f"M{p}", "pool": f"P{p}",
                            "t": t, "day": day, "ms_per_slot": 218.0, "regime": "220ms", "farm": "organic" if p % 2 else "flagged", "ref_mint": None,
                            "ref_t": None, "d": d, "h": "", "h_ms": 0, "target": t + d, "cut": False, "mid": 1.0, "gross": None, "net": None, "impact": 0.01})
    return out


def _write(tmp_path, rows, name="clean-e20"):
    d = tmp_path / "mk"
    d.mkdir(exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), d / f"markout-{name}.parquet")
    return str(d)


def _run(tmp_path, rows, boot=300):
    con = duckdb.connect()
    mr.load(con, _write(tmp_path, rows))
    return con, mr.analyse(con, boot)


def test_confirmed_and_tradable_flag_apart(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    rng = np.random.default_rng(3)
    noise = {}

    def g(p, k, h):
        return noise.setdefault((p, k, h), 0.03 + 0.01 * rng.standard_normal())

    # 150 pools, gross +3 % with small noise, twins at 0: P1 and P2 both clearly > 0; net = gross − 5 pt < 0 → tradable no
    con, res = _run(tmp_path, _rows(150, 2, g))
    v = res["verdict"]
    assert v["outcome"] == "CONFIRMED" and v["P1_lo_above_0"] and v["P2_lo_above_0"]
    assert v["tradable"] == "no" and res["secondary"]["1m"]["net"]["hi"] < 0
    assert v["consistency_same_sign"] == {"5m": True, "15m": True}
    assert res["input"]["pools"] == 150 and res["input"]["paired_triggers"] == 300
    p1 = res["primary"]["1m"]["P1_lg"]
    assert abs(p1["mean"] - np.log(1.03)) < 0.005 and p1["lo_999"] is not None and p1["lo_999"] <= p1["lo"]
    # tradable yes when the net is positive, without touching the outcome
    con, res = _run(tmp_path, _rows(150, 2, g, net_fn=lambda p, k, h: g(p, k, h) - 0.005))
    assert res["verdict"]["outcome"] == "CONFIRMED" and res["verdict"]["tradable"] == "yes"


def test_rejected_inconclusive_not_judged(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    # P1 positive but the twins bounce as much: P2 mean 0 → rejected on P2
    con, res = _run(tmp_path, _rows(150, 2, lambda p, k, h: 0.02, n2_fn=lambda p, k, h: 0.02))
    assert res["verdict"]["outcome"] == "REJECTED" and res["primary"]["1m"]["P2_diff"]["mean"] <= 0
    # P1 at or below 0 → rejected whatever P2
    con, res = _run(tmp_path, _rows(150, 2, lambda p, k, h: -0.01, n2_fn=lambda p, k, h: -0.05))
    assert res["verdict"]["outcome"] == "REJECTED" and res["primary"]["1m"]["P2_diff"]["lo"] > 0
    # positive point estimates with wide intervals: inconclusive
    con, res = _run(tmp_path, _rows(120, 1, lambda p, k, h: 0.02 if p % 2 else -0.017))
    p1 = res["primary"]["1m"]["P1_lg"]
    assert p1["mean"] > 0 and p1["lo"] < 0 and res["verdict"]["outcome"] == "INCONCLUSIVE"
    # too few pools: not judged, no flags
    con, res = _run(tmp_path, _rows(20, 3, lambda p, k, h: 0.03))
    assert res["verdict"]["outcome"] == "NOT JUDGED" and res["verdict"]["P1_lo_above_0"] is None
    # too few days
    con, res = _run(tmp_path, _rows(150, 1, lambda p, k, h: 0.03, days=3))
    assert res["input"]["days"] == 3 and res["verdict"]["outcome"] == "NOT JUDGED"


def test_pool_is_the_unit(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    # 120 pools at +2 % and one pool with 500 triggers at −30 % (log −0.36): per trigger the mean is deep negative, per pool it is
    # positive and the one pool is one draw of the bootstrap
    counts = [1] * 120 + [500]
    con, res = _run(tmp_path, _rows(121, counts, lambda p, k, h: -0.30 if p == 120 else 0.02, twins=1))
    p1 = res["primary"]["1m"]["P1_lg"]
    assert p1["pools"] == 121 and p1["mean"] > 0 and res["descriptive"]["1m"]["per_trigger"]["lg"]["mean"] < -0.2
    # the pool means equal the same data with the big pool's 500 triggers averaged: identical statistic
    con2, res2 = _run(tmp_path, _rows(121, [1] * 121, lambda p, k, h: -0.30 if p == 120 else 0.02, twins=1))
    q1 = res2["primary"]["1m"]["P1_lg"]
    assert abs(q1["mean"] - p1["mean"]) < 1e-12 and abs(q1["lo"] - p1["lo"]) < 1e-12 and abs(q1["hi"] - p1["hi"]) < 1e-12


def test_markdown_and_main(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    mk = _write(tmp_path, _rows(150, 2, lambda p, k, h: 0.03))
    out = tmp_path / "mechanism.md"
    monkeypatch.setattr("sys.argv", ["mechanism_report", "--markout", mk, "--boot", "200", "--out", str(out)])
    mr.main()
    md = out.read_text()
    assert "**Outcome: CONFIRMED**" in md and "tradable (net 0.5 SOL at 1m, lower bound > 0): **no**" in md
    assert "| P1 log F7 @ 1m | 150 |" in md and "| P2 log F7 − N2 @ 15m | 150 |" in md
    assert os.path.exists(tmp_path / "mechanism.json")
