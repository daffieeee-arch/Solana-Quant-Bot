"""Markout report: the weighted trimmed mean equals the plain one at unit weights, the day CI contains
the statistic, N2 pairing per trigger, the K rows and the advantage verdict on synthetic markout rows."""

import json
import os

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import markout_report as R


def test_trimmed_means_and_day_ci():
    rng = np.random.default_rng(3)
    x = rng.normal(size=1000)
    x[:5] = 1e6  # outliers the 1 % trim removes
    xs = np.sort(x)
    assert abs(R.weighted_trimmed_mean(xs, np.ones(len(xs))) - R.trimmed_mean(x)) < 1e-9
    assert abs(R.trimmed_mean(x)) < 0.2
    # doubling every weight changes nothing; zero weight on the outliers removes them
    assert abs(R.weighted_trimmed_mean(xs, 2 * np.ones(len(xs))) - R.trimmed_mean(x)) < 1e-9
    days = np.array([f"d{i % 10}" for i in range(1000)])
    lo, hi = R.day_ci(x, days, boot=300)
    assert lo is not None and lo <= R.trimmed_mean(x) <= hi
    assert R.day_ci(x[:10], np.array(["d0"] * 10), boot=10) == (None, None)


def _rows():
    rows = []
    hs = (("1s", 1000), ("10s", 10000), ("1m", 60000), ("5m", 300000), ("15m", 900000), ("1h", 3600000), ("6h", 21600000))
    for i in range(40):
        day = f"2026-09-{11 + i % 18:02d}"
        farm = "organic" if i % 2 else "flagged"
        for d in (1, 2):
            entry = {"family": "F7", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": f"M{i}", "pool": f"P{i}",
                     "t": 446_300_000 + i * 1000, "day": day, "regime": "267ms", "farm": farm, "ref_mint": None, "ref_t": None, "variants": ["tp4_T600"],
                     "d": d, "h": "", "h_ms": 0, "target": 446_300_000 + i * 1000 + d, "cut": False, "mid": 1.0, "gross": None, "net": None, "impact": 0.01}
            rows.append(entry)
            for h, ms in hs:
                up = 0.05 if h in ("1s", "10s") else -0.5  # early horizons positive, later ones a dead pool
                rows.append({**entry, "h": h, "h_ms": ms, "target": entry["target"] + ms // 400, "cut": h == "6h", "mid": 1 + up,
                             "gross": up, "net": up - 0.02 if h != "6h" else -1.0})
                # the N2 twin: two matched entries per trigger, flat
                for k in range(2):
                    rows.append({"family": "N2_F7", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": f"X{i}{k}", "pool": f"Q{i}{k}",
                                 "t": 446_300_000 + i * 1000 + 50 + k, "day": day, "regime": "267ms", "farm": "n/a", "ref_mint": f"M{i}", "ref_t": 446_300_000 + i * 1000,
                                 "variants": ["tp4_T600"], "d": d, "h": h, "h_ms": ms, "target": 0, "cut": False, "mid": 1.0, "gross": 0.0, "net": -0.02, "impact": 0.01})
    return rows


def test_pairing_k_rows_and_verdict(tmp_path):
    out = tmp_path / "mk"
    os.makedirs(out)
    pq.write_table(pa.Table.from_pylist(_rows()), out / "markout-t.parquet")
    with open(out / "run-markout-t.json", "w") as f:
        json.dump({"run": "t", "signals": 40}, f)
    con = duckdb.connect()
    con.execute("SET memory_limit = '200MB'; SET threads TO 1")
    assert R.load(con, str(out)) == 1
    assert con.execute("SELECT count(*), avg(n_n2) FROM pairs").fetchone() == (40 * 2 * 7, 2.0)
    p = R.paired(con, "family = 'F7' AND d = 1 AND h = '1s'", "net", boot=200)
    assert p["n"] == 40 and abs(p["n2_trim"] + 0.02) < 1e-9 and abs(p["diff"] - 0.05) < 1e-9 and p["lo"] > 0
    md = R.render(con, str(out), 200)
    assert "### F7 · pool_reversed · d = 1" in md and "### K1/K2 (F7 pool_reversed, organic) · d = 1" in md and "### K3 (F7 pool_reversed, all pools) · d = 2" in md
    assert "advantage first at **1s**" in md  # 1 s and 10 s are positive with CI > 0, N2's own net is not
    assert "| 267ms | 1s |" in md and "| 316ms | 1s |" not in md and "Entry impact > 5 %" in md
    line = next(l for l in md.splitlines() if l.startswith("| days 10-18 | 6h |"))
    assert "| 100% | 100% |" in line  # cut and dead
    assert R.fmt(None) == "–" and R.fmt(0.1234, 1, True, True) == "+12.3%" and R.fmt(12345) == "12,345"


if __name__ == "__main__":
    pytest.main([__file__])
