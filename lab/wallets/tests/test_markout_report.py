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
                     "t": 446_300_000 + i * 1000, "day": day, "regime": "270ms", "ms_per_slot": 267.1, "farm": farm, "ref_mint": None, "ref_t": None, "variants": ["tp4_T600"],
                     "d": d, "h": "", "h_ms": 0, "target": 446_300_000 + i * 1000 + d, "cut": False, "mid": 1.0, "gross": None, "net": None, "impact": 0.01}
            rows.append(entry)
            for h, ms in hs:
                up = 0.05 if h in ("1s", "10s") else -0.5  # early horizons positive, later ones a dead pool
                rows.append({**entry, "h": h, "h_ms": ms, "target": entry["target"] + ms // 400, "cut": h == "6h", "mid": 1 + up,
                             "gross": up, "net": up - 0.02 if h != "6h" else -1.0})
                # the N2 twin: two matched entries per trigger, flat except at 10 s, where N2 is positive too (no advantage there)
                for k in range(2):
                    rows.append({"family": "N2_F7", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": f"X{i}{k}", "pool": f"Q{i}{k}",
                                 "t": 446_300_000 + i * 1000 + 50 + k, "day": day, "regime": "270ms", "ms_per_slot": 267.1, "farm": "n/a", "ref_mint": f"P{i}", "ref_t": 446_300_000 + i * 1000,
                                 "variants": ["tp4_T600"], "d": d, "h": h, "h_ms": ms, "target": 0, "cut": False, "mid": 1.0, "gross": 0.0,
                                 "net": 0.04 if h == "10s" else -0.02, "impact": 0.01})
    return rows


def test_pairing_k_rows_and_verdict(tmp_path):
    out = tmp_path / "mk"
    os.makedirs(out)
    rows = _rows()
    pq.write_table(pa.Table.from_pylist(rows), out / "markout-t.parquet")
    pq.write_table(pa.Table.from_pylist(rows), out / "markout-u.parquet")  # the same triggers from a second run: deduplicated
    for run in ("t", "u"):
        with open(out / f"run-markout-{run}.json", "w") as f:
            json.dump({"run": run, "signals": 40}, f)
    con = duckdb.connect()
    con.execute("SET memory_limit = '200MB'; SET threads TO 1")
    assert R.load(con, str(out)) == 2
    assert con.execute("SELECT count(*) FROM m WHERE family = 'F7'").fetchone()[0] == 40 * 2 * 7  # not doubled
    assert con.execute("SELECT count(*) FROM entries WHERE family = 'F7'").fetchone()[0] == 40 * 2
    assert con.execute("SELECT count(*), avg(n_n2) FROM pairs").fetchone() == (40 * 2 * 7, 2.0)  # paired on the trigger's pool key
    p = R.paired(con, "family = 'F7' AND d = 1 AND h = '1s'", "net", boot=200)
    assert p["n"] == 40 and abs(p["n2_trim"] + 0.02) < 1e-9 and abs(p["diff"] - 0.05) < 1e-9 and p["lo"] > 0 and p["n2_hi"] < 0
    md = R.render(con, str(out), 200)
    assert "### F7 · pool_reversed · d = 1" in md and "### K1/K2 (F7 pool_reversed, organic) · d = 1" in md and "### K3 (F7 pool_reversed, all pools) · d = 2" in md
    assert "advantage first at **1s**" in md  # 1 s is positive with CI > 0 and N2's own net is not
    assert "| 270ms | 1s |" in md and "| 320ms | 1s |" not in md and "Entry impact > 5 %" in md  # regime blocks come from the rows
    b = R.BINDING
    assert b == "days 10-18 (2026-09-20 … 09-30)"
    # at 10 s the trigger is positive but so is N2 (its own CI > 0): no advantage, in the family table and in the K1/K2 row alike
    k = md.index("### K1/K2 (F7 pool_reversed, organic) · d = 1")
    for section in (md[:k], md[k:]):
        row = next(l for l in section.splitlines() if l.startswith(f"| {b} | 10s |"))
        assert row.endswith("**no** |") and "+4.0% (" in row
        row1 = next(l for l in section.splitlines() if l.startswith(f"| {b} | 1s |"))
        assert row1.endswith("**yes** |")
    line = next(l for l in md.splitlines() if l.startswith(f"| {b} | 6h |"))
    assert "| 100% | 100% |" in line  # cut and dead
    assert R.fmt(None) == "–" and R.fmt(0.1234, 1, True, True) == "+12.3%" and R.fmt(12345) == "12,345"


def test_entry_delay_section(tmp_path):
    """Spec M-a: the --tx-delays codes are paired per signal against d = 2; the entry mid advantage and the net Δ get
    day CIs; the screen fires only for a causal code (0 / t+1 first tx) at 1m / 5m / 15m on days 10-18 with Δ >= 0.2 pt
    and the CI above 0; k-codes are an upper bound; the capped share and the coverage are shown."""
    out = tmp_path / "mk"
    os.makedirs(out)
    rows = []
    hs = (("1s", 1000), ("1m", 60000), ("5m", 300000))
    for i in range(40):
        day = f"2026-09-{11 + i % 18:02d}"
        for d in (2, 0, 2001, 1000):
            # d = 0 enters 1 % cheaper than d = 2 (mid 0.99) and nets +1 pt more at every horizon; t+1 first tx +0.1 pt (below the threshold);
            # the trigger tx (k0) +3 pt (upper bound); signal 39 has no t+1 entry (coverage 39/40 there)
            if d == 2001 and i == 39:
                continue
            gain = {2: 0.0, 0: 0.01, 2001: 0.001, 1000: 0.03}[d]
            entry = {"family": "F7", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": f"M{i}", "pool": f"P{i}",
                     "t": 446_300_000 + i * 1000, "day": day, "regime": "270ms", "ms_per_slot": 267.1, "farm": "organic" if i % 2 else "flagged",
                     "ref_mint": None, "ref_t": None, "variants": ["tp4_T600"], "d": d, "h": "", "h_ms": 0, "target": 446_300_000 + i * 1000,
                     "cut": False, "mid": 1.0 - gain, "gross": None, "net": None, "impact": 0.01, "capped": (i % 4 == 0) if d >= 1000 else None}
            rows.append(entry)
            for h, ms in hs:
                rows.append({**entry, "h": h, "h_ms": ms, "mid": 1.02, "gross": 0.02 + gain, "net": 0.01 + gain + (0.001 * (i % 3))})
    pq.write_table(pa.Table.from_pylist(rows), out / "markout-tx.parquet")
    with open(out / "run-markout-tx.json", "w") as f:
        json.dump({"run": "tx", "signals": 40}, f)
    con = duckdb.connect()
    con.execute("SET memory_limit = '200MB'; SET threads TO 1")
    R.load(con, str(out))
    assert R.delay_codes(con) == [0, 1000, 2001]
    md = R.delay_section(con, boot=200)
    assert "## Entry delay (spec M-a): paired per signal against d = 2" in md
    assert "### Entry delay vs d = 2 · F7 · pool_reversed" in md and "### Entry delay vs d = 2 · K1/K2 (F7 pool_reversed, organic)" in md
    b = R.BINDING
    lines = md.splitlines()
    k = lines.index("### Entry delay vs d = 2 · F7 · pool_reversed")
    fam = lines[k:lines.index("### Entry delay vs d = 2 · K1/K2 (F7 pool_reversed, organic)")]
    r0 = next(l for l in fam if l.startswith(f"| {b} | 0 slots | 1m |"))
    assert "| 100% | 0% | +1.01% |" in r0 and "**+1.00%**" in r0 and r0.endswith("**yes** |")  # mid 0.99 → 1/0.99 − 1 = +1.01 %; net Δ +1 pt
    r1 = next(l for l in fam if l.startswith(f"| {b} | t+1, first tx | 1m |"))
    assert "**+0.10%**" in r1 and r1.endswith("**no** |")  # below 0.2 pt
    rk = next(l for l in fam if l.startswith(f"| {b} | t, trigger tx + 0 | 1m |"))
    assert "**+3.00%**" in rk and rk.endswith("upper bound |")
    rka = next(l for l in fam if l.startswith("| all days | t, trigger tx + 0 | 1m |"))
    assert "| 40 | 100% | 25% |" in rka  # capped: every fourth signal
    r1s = next(l for l in fam if l.startswith(f"| {b} | 0 slots | 1s |"))
    assert r1s.endswith("|  |")  # 1 s is no screen horizon
    allrow = next(l for l in fam if l.startswith("| all days | t+1, first tx | 1m |"))
    assert "| 39 | 98% |" in allrow  # coverage: 39 of 40 reference signals have a t+1 entry
    assert "screen (net Δ ≥ 0.2 pt" in md and "positive at 0 slots / 1m, 0 slots / 5m" in md
    full = R.render(con, str(out), 200)
    assert "## Entry delay (spec M-a)" in full and "### K3 (F7 pool_reversed, all pools) · d = t+1, first tx" in full


if __name__ == "__main__":
    pytest.main([__file__])


def test_day_ci_parallel_is_bit_identical(monkeypatch):
    """LAB_REPORT_WORKERS splits the draws into ordered blocks over a process pool: the CI must equal the serial
    loop bit for bit (same seed, same draw order), and the input cache must answer an identical input the same."""
    from wallets import markout_report as r
    rng = np.random.default_rng(3)
    v = rng.standard_t(3, size=5000) / 100
    days = rng.integers(0, 12, size=5000).astype(str)
    r._CI_CACHE.clear()
    serial = r.day_ci(v, days, boot=600, n_workers=1)
    r._CI_CACHE.clear()
    par = r.day_ci(v, days, boot=600, n_workers=3)
    assert serial == par and serial[0] is not None and serial[0] < serial[1]
    assert r.day_ci(v.copy(), days.copy(), boot=600, n_workers=1) == par  # cache: same values/days/boot/seed
    monkeypatch.setenv("LAB_REPORT_WORKERS", "2")
    r._CI_CACHE.clear()
    assert r.day_ci(v, days, boot=600) == serial
    assert r.day_ci(v, days, boot=600, seed=8) != serial  # another seed is another key, not a cache hit
    r.close_pool()
    assert r._POOL == [None, 0]


def test_table_without_twin_renders_na():
    """twin=False: a positive group shows 'n/a' in the advantage column and verdict, never a free 'yes'."""
    import duckdb
    from wallets import markout_report as mr
    con = duckdb.connect()
    con.execute("CREATE TABLE m (family VARCHAR, segment VARCHAR, venue VARCHAR, mint VARCHAR, pool VARCHAR, t BIGINT, d INTEGER, h VARCHAR, "
                "day VARCHAR, regime VARCHAR, farm VARCHAR, gross DOUBLE, net DOUBLE, cut BOOLEAN, variants VARCHAR[])")
    rows = [("REV", "pool_normal", "pool", None, f"P{i}", 1000 + i, 2, h, f"2026-09-{21 + i % 5:02d}", "270ms", "organic", 0.1, 0.05 + 0.001 * i, False, ["S5_D10_T5"])
            for i in range(40) for h in mr.H_ORDER]
    con.executemany("INSERT INTO m VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute("CREATE TABLE entries AS SELECT *, 0.01 AS impact FROM m WHERE h = '1m'")
    con.execute("CREATE TABLE pairs (family VARCHAR, segment VARCHAR, mint VARCHAR, t BIGINT, d INTEGER, h VARCHAR, day VARCHAR, regime VARCHAR, "
                "farm VARCHAR, gross DOUBLE, net DOUBLE, n2_gross DOUBLE, n2_net DOUBLE, n_n2 BIGINT)")
    where = "family = 'REV' AND d = 2 AND list_contains(variants, 'S5_D10_T5')"
    md = mr.table(con, "REV test", where, 200, twin=False)
    assert "**n/a** |" in md and "n/a without an N2 twin" in md and "**yes**" not in md
    md2 = mr.table(con, "REV test", "family = 'REV' AND d = 2", 200)  # twin=True on a run without twins: the old 'yes' path
    assert "**yes**" in md2
