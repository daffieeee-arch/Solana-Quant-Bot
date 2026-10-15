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
        moment = ("c1", "c5", "m1")[i % 3]
        regime = "320ms" if i % 2 else "270ms"
        rows.append({"key": f"K{i // 3}", "mint": f"K{i // 3}", "moment": moment, "t": 1000 + i, "entry_slot": 1002 + i, "entry_ord": 0, "entry_venue": "curve",
                     "segment": "curve" if moment != "m1" else "pool", "regime": regime, "day": days[i % len(days)], "depth_sol": rng.uniform(1, 50),
                     "tokens": 1.0, "cost_sol": 0.5007, "min_since_create": float(i % 30), "create_slot": 500 + i // 3, "creator": f"C{(i // 3) % 40}", "mayhem": False,
                     "has_curve": True, "n_marks": 10, "n_curve": 5, "n_pool": 5, "mult_1h": mult * 0.8, "mult_6h": mult, "t_max_min": rng.uniform(0, 300),
                     "end_6h": mult * rng.uniform(0, 1), "dd_6h": rng.uniform(0.3, 1), "last_min": 350.0, "cut": False,
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
    # the planted feature leads in every regime for every moment; a noise feature does not
    leads = md.split("## 4. Leads")[1].split("## 5.")[0]
    assert "c1: `all_inflow`" in leads and "c5: `all_inflow`" in leads and "m1: `all_inflow`" in leads
    assert "`pre5_inflow`" not in leads and "`top5_share`" not in leads
    # the AUC table marks the lead bold with a CI and the runner count; features absent from the entries are skipped
    row = next(ln for ln in md.splitlines() if ln.startswith("| all_inflow |"))
    assert "**0." in row and "…" in row and "pre1_inflow" not in md.split("## 3.")[1].split("## 4.")[0]
    # the creator history is joined: K1 (second token of creator C1 ... ) gets creator_prev_n 1 -> the feature appears in the table
    assert "| creator_prev_n |" in md
    # quintiles of the strongest feature per moment
    assert "**c1 · all_inflow**" in md and "| 5 |" in md
