"""The runner report: the day-pair AUC equals the plain Mann-Whitney AUC (ties at 1/2), its day bootstrap is
reproducible and brackets the point estimate, and the rendered report finds the planted lead."""

import os

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from wallets import runners_report as R


def _plain_auc(x, y):
    pos, neg = x[y], x[~y]
    return float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()) / (len(pos) * len(neg)))


def test_day_pair_auc_matches_plain_auc_with_ties():
    rng = np.random.default_rng(1)
    x = np.round(rng.normal(size=600), 1)          # rounded: plenty of ties
    y = (x + rng.normal(size=600) * 1.5) > 0.3
    day = rng.integers(0, 7, size=600)
    a, lo, hi = R.auc_ci(x, y, day, 7, boot=500)
    assert abs(a - _plain_auc(x, y)) < 1e-12
    assert lo is not None and lo <= a <= hi and hi - lo < 0.2
    a2, lo2, hi2 = R.auc_ci(x, y, day, 7, boot=500)
    assert (a2, lo2, hi2) == (a, lo, hi)             # seed fixed
    assert R.auc_ci(x, np.zeros(600, bool), day, 7, 10) == (None, None, None)
    assert R.auc_ci(x, y, day, 7, boot=0)[1] is None


def _entries(path, n=1200, seed=3):
    rng = np.random.default_rng(seed)
    days = [f"2026-09-{d:02d}" for d in range(15, 29)]
    rows = []
    for i in range(n):
        strong = rng.normal()                        # the planted lead: higher -> runner
        run = rng.uniform() < 1 / (1 + np.exp(-(2.5 * strong - 2.0)))
        mult = rng.uniform(5, 12) if run else rng.uniform(0.1, 2.5)
        end = mult * rng.uniform(0, 1)
        moment = ("c1", "c5", "m1")[i % 3]
        regime = "320ms" if i % 2 else "270ms"
        rows.append({"key": f"K{i // 3}", "mint": f"K{i // 3}", "moment": moment, "t": 1000 + i, "entry_slot": 1002 + i, "entry_ord": 0, "entry_venue": "curve",
                     "segment": "curve" if moment != "m1" else "pool", "regime": regime, "day": days[i % len(days)], "depth_sol": rng.uniform(1, 50),
                     "tokens": 1.0, "cost_sol": 0.5007, "min_since_create": float(i % 30), "create_slot": 500 + i // 3, "creator": f"C{(i // 3) % 40}", "mayhem": False,
                     "has_curve": True, "n_marks": 10, "n_curve": 5, "n_pool": 5, "mult_1h": mult * 0.8, "mult_6h": mult, "t_max_min": rng.uniform(0, 300),
                     "end_6h": end, "dd_6h": rng.uniform(0.3, 1), "last_min": 350.0, "h6_slots": 80_000, "cut": False,
                     "x_tp2": 2.0 if mult >= 2 else end, "x_tr30": max(end, 0.7 * mult), "x_tr50": max(end, 0.5 * mult),
                     "all_inflow": strong, "all_buyers": int(rng.integers(1, 50)), "top5_share": rng.uniform(0.2, 1), "holders": int(rng.integers(1, 200)),
                     "payer_hub": rng.uniform(0, 0.3), "pre5_inflow": rng.normal(), "post5_inflow": None if i % 5 == 0 else rng.normal()})
    os.makedirs(path)
    pq.write_table(pa.Table.from_pylist(rows), os.path.join(path, "entries-1.parquet"))


def test_report_finds_the_planted_lead(tmp_path):
    _entries(str(tmp_path / "ent"))
    out = tmp_path / "runners.md"
    R.main(["--entries", str(tmp_path / "ent"), "--boot", "300", "--out", str(out)])
    md = out.read_text()
    assert "## 1. Entries per moment" in md and "| c1 | 400 | 400 |" in md
    assert "### days 10-18" in md and "| c1 | 270ms | curve |" in md
    # both regimes occur on days 10-18 here: each is scored there
    assert "270ms scored on days 10-18; 320ms scored on days 10-18" in md
    # the planted feature leads in every regime for every moment; a noise feature does not
    leads = md.split("## 4. Leads")[1].split("## 5.")[0]
    assert "c1: `all_inflow`" in leads and "c5: `all_inflow`" in leads and "m1: `all_inflow`" in leads
    assert "`pre5_inflow`" not in leads and "`top5_share`" not in leads
    # the AUC table marks the lead bold with a CI and the runner count; features absent from the entries are skipped
    row = next(ln for ln in md.splitlines() if ln.startswith("| all_inflow |"))
    assert "**0." in row and "…" in row and "pre1_inflow" not in md.split("## 3.")[1].split("## 4.")[0]
    assert "| creator_prev_n |" in md
    # quintiles of the strongest feature per moment, with the net SOL of the simple exits per entry and per day
    assert "**c1 · all_inflow**" in md and "| 5 |" in md
    q5 = next(ln for ln in md.split("**c1 · all_inflow**")[1].splitlines() if ln.startswith("| 5 |"))
    cells = q5.split(" | ")
    per_entry = [float(x) for x in cells[6].split(" / ")]
    import duckdb
    want = duckdb.connect().execute(f"""WITH q AS (SELECT ntile(5) OVER (ORDER BY all_inflow) AS qt, cost_sol, end_6h, x_tp2, x_tr30, x_tr50
                                            FROM '{tmp_path / 'ent' / 'entries-1.parquet'}' WHERE moment = 'c1' AND day >= '2026-09-20' AND day <= '2026-09-30')
        SELECT avg((end_6h - 1) * cost_sol), avg((x_tp2 - 1) * cost_sol), avg((x_tr30 - 1) * cost_sol), avg((x_tr50 - 1) * cost_sol) FROM q WHERE qt = 5""").fetchone()
    assert per_entry == [round(w, 4) for w in want]
    assert "net SOL / entry: hold 6h / tp ×2 / trail 30 / trail 50" in md


def test_creator_history_is_point_in_time(tmp_path):
    """A creator with two tokens 10 min apart: the second sees the first create (count 1) but not its mult_6h, which is
    known only 6 h after the first token's entry; a third token a day later sees both tokens and the first two multiples."""
    import duckdb
    ms = 400.0
    h6 = round(6 * 3_600_000 / ms)          # 54,000 slots
    ten_min = round(600_000 / ms)           # 1,500 slots
    day = round(86_400_000 / ms)
    base = {"moment": "c5", "entry_venue": "curve", "segment": "curve", "regime": "270ms", "depth_sol": 1.0, "tokens": 1.0, "cost_sol": 0.5,
            "min_since_create": 5.0, "mayhem": False, "has_curve": True, "n_marks": 3, "n_curve": 3, "n_pool": 0, "mult_1h": 1.0, "t_max_min": 1.0,
            "end_6h": 0.5, "dd_6h": 0.9, "last_min": 300.0, "cut": False, "all_inflow": 1.0, "h6_slots": h6, "creator": "C", "entry_ord": 0}
    rows = [dict(base, key="A", mint="A", create_slot=1000, t=1000 + 800, entry_slot=1000 + 802, day="2026-09-21", mult_6h=7.0),
            dict(base, key="B", mint="B", create_slot=1000 + ten_min, t=1000 + ten_min + 800, entry_slot=1000 + ten_min + 802, day="2026-09-21", mult_6h=1.0),
            dict(base, key="D", mint="D", create_slot=1000 + day, t=1000 + day + 800, entry_slot=1000 + day + 802, day="2026-09-22", mult_6h=1.5)]
    os.makedirs(tmp_path / "ent")
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "ent" / "entries-1.parquet")
    con = duckdb.connect()
    R.load(con, str(tmp_path / "ent"))
    got = {r[0]: (r[1], r[2]) for r in con.execute("SELECT key, creator_prev_n, creator_prev_mult6 FROM ent").fetchall()}
    assert got["A"] == (0, None)
    assert got["B"] == (1, None)            # A was created before B's moment, but A's mult_6h is not known yet
    assert got["D"] == (2, 4.0)             # a day later: both earlier tokens, median of 7.0 and 1.0
