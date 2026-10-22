"""x10 stage C on a synthetic features table: the column rule, a planted relation the model finds (precision above the
base rate on test), censored rows dropped, the capacity rule, Holm, the trade-count choice of X*, and a byte-identical
second run (T4)."""
import datetime
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets.x10 import model as X

SLOTS = 432_000


def _features(path, n_per_block=3000, seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    blocks = (("train", 1033, 1040), ("val", 1041, 1043), ("test", 1044, 1046))
    for name, e0, e1 in blocks:
        for i in range(n_per_block):
            epoch = int(rng.integers(e0, e1 + 1))
            slot = epoch * SLOTS + int(rng.integers(0, SLOTS))
            f1, f2, noise = rng.normal(), rng.normal(), rng.uniform()
            p = 1 / (1 + np.exp(-(-2.5 + 2.0 * f1)))     # the planted relation: f1 drives x2
            hit2 = noise < p
            hit5 = hit2 and rng.uniform() < 0.3
            hit10 = hit5 and rng.uniform() < 0.3
            # returns per exit: a hit pays, a miss loses; tp2 pays exactly x2 on a hit
            ret = {"tp2": 1.0 if hit2 else -0.6, "tp5": 4.0 if hit5 else -0.6, "tp10": 9.0 if hit10 else -0.6,
                   "trail30": 0.8 if hit2 else -0.55, "half2": 0.9 if hit2 else -0.58, "half5": 2.0 if hit5 else -0.6}
            censored = epoch in (1040, 1043, 1046) and i % 50 == 0   # stage B's flag; the model must drop these rows
            day = datetime.date(2026, 9, 11) + datetime.timedelta(days=int((slot - 1033 * SLOTS) // (SLOTS * 1.33)))
            r = {"key": f"K{name}{i}", "mint": f"K{name}{i}", "moment": "e10" if i % 10 else "mig", "t": slot - 2, "entry_slot": slot, "entry_ord": slot * 1000,
                 "entry_venue": "curve", "segment": "curve", "regime": "268ms", "day": day, "epoch": epoch, "create_slot": slot - 100, "creator": "C",
                 "pool": None, "block": name, "next_boundary": (epoch + 1) * SLOTS, "censored_24": censored, "censored_72": censored, "tokens": 1, "cost_sol": 0.5,
                 "h6_slots": 967_000, "d_slots": 2, "cut": False, "has_curve": True, "mayhem": bool(i % 5 == 0), "depth_sol": float(rng.uniform(5, 50)),
                 "f1": float(f1), "f2": float(f2), "all_inflow_org": 1.0, "pre1_buyers_nofarm": 0.5, "post5_buyers": 3, "hit_2_24": hit2, "hit_5_24": hit5,
                 "hit_10_24": hit10, "hit_2_72": hit2, "stop_first_24": not hit2, "peak_24": 2.0, "t_peak_24": 1.0, "end_24": 0.5, "dd_24": 0.4, "n_marks_24": 10,
                 "mult_6h": 1.0, "x_tr30": 1.0, "cr_hist_days": 1.0, "eb_hit_share": float(rng.uniform())}
            for rule, v in ret.items():
                r[f"x_{rule}_24"] = 1 + v
                r[f"x_{rule}_72"] = 1 + v
                r[f"trig_{rule}_24"] = True
            rows.append(r)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return rows


def test_feature_rule_excludes_labels_exits_post_windows_and_farmer_columns():
    cols = [("key", "VARCHAR"), ("f1", "DOUBLE"), ("hit_2_24", "BOOLEAN"), ("x_tp2_24", "DOUBLE"), ("post5_buyers", "BIGINT"), ("all_inflow_org", "DOUBLE"),
            ("pre1_buyers_nofarm", "DOUBLE"), ("mayhem", "BOOLEAN"), ("depth_sol", "DOUBLE"), ("epoch", "INTEGER"), ("cr_hist_days", "DOUBLE"), ("entry_venue", "VARCHAR"),
            ("eb_hit_share", "DOUBLE"), ("n_marks_24", "BIGINT"), ("peak_72", "DOUBLE"), ("trig_tp2_24", "BOOLEAN")]
    assert X.feature_columns(cols) == ["depth_sol", "eb_hit_share", "f1", "mayhem", "entry_venue_pool"]


def test_holm_and_capacity_and_choice():
    # step-down: 0.01 x 3 = 0.03; 0.03 x 2 = 0.06; 0.04 x 1 = 0.04 but never below the earlier 0.06
    assert X.holm([0.01, 0.04, 0.03]) == [pytest.approx(0.03), pytest.approx(0.06), pytest.approx(0.06)]
    assert X.holm([None, 0.02]) == [None, 0.02]
    # capacity: X = 1 SOL -> 2 positions of 0.5 SOL; signals at slots 0, 10, 20 within one 24 h window (100 slots): the third is skipped
    taken = X.capacity_take(np.array([0, 10, 20, 150]), np.array([100, 100, 100, 100]), 1.0)
    assert taken.tolist() == [True, True, False, True]
    assert X.choose_x({"trades": 10, "taken_per_day": {"10": 6.0, "25": 5.0, "100": 4.9}}) == 25
    assert X.choose_x({"trades": 10, "taken_per_day": {"10": 1.0, "25": 1.0, "100": 1.0}}) is None


def test_planted_relation_is_found_and_the_run_is_repeatable(tmp_path):
    rows = _features(tmp_path / "features.parquet")
    res = X.run(str(tmp_path / "features.parquet"), str(tmp_path / "out1"), boot=300, moments=("e10", "mig"), leaves=(15,))
    assert res["rows"]["train"] == sum(1 for r in rows if r["block"] == "train" and not r["censored_24"]) and res["censored_24"]["train"]["censored_24"] > 0
    assert "f1" in res["feature_columns"] and "x_tp2_24" not in res["feature_columns"] and "post5_buyers" not in res["feature_columns"]
    t2 = res["targets"]["x2"]
    assert t2["cells"] > 0 and t2["chosen"] is not None and t2["chosen"]["model"] in ("lgbm", "logit")
    te = t2["test"]
    # the model ranks on f1: the selected coins hit far more often than the base rate
    assert te["hit"] > 1.5 * t2["base_rate"]["test"] and te["precision_vs_base"] > 1.5 and te["auc"] > 0.7
    # the ratio uses the chosen moment's own test base rate, not the all-moments rate
    assert te["precision_vs_base"] == pytest.approx(te["hit"] / te["base_rate_moment"]) and te["base_rate_moment"] == pytest.approx(t2["all_coins_test"]["hit"])
    if t2.get("logit_reference"):
        assert t2["logit_reference"]["test"]["precision_vs_base"] == pytest.approx(t2["logit_reference"]["test"]["hit"] / t2["logit_reference"]["test"]["base_rate_moment"])
    assert set(te["boot"]) == {"10", "25", "100"} and te["boot"]["100"]["days"] == res["days"]["test"]
    assert sum(te["trades_per_test_day"].values()) == te["trades"] and te["dsr"]["trials"] == t2["cells"]
    assert t2["cscv"]["splits"] == 12870 and 0 <= t2["cscv"]["pbo"] <= 1
    assert "gate" in t2 and set(t2["gate"]) >= {"holm_p_below_alpha", "lower_bound_positive", "trades_per_day_ok", "top1_share_ok", "precision_ok", "passed"}
    assert (tmp_path / "out1" / "model.md").exists() and (tmp_path / "out1" / "cells-val.parquet").exists() and (tmp_path / "out1" / "scores-x2.parquet").exists()
    if t2["chosen"]["model"] == "lgbm":
        assert t2["importance_gain"][0]["feature"] == "f1" and (tmp_path / "out1" / "lgbm-x2.txt").exists()
    # T4: a second run from the same parquet is byte-identical (json apart from the wall time)
    res2 = X.run(str(tmp_path / "features.parquet"), str(tmp_path / "out2"), boot=300, moments=("e10", "mig"), leaves=(15,))
    j1 = json.load(open(tmp_path / "out1" / "model.json"))
    j2 = json.load(open(tmp_path / "out2" / "model.json"))
    j1.pop("seconds"), j2.pop("seconds")
    assert j1 == j2
    assert open(tmp_path / "out1" / "scores-x2.parquet", "rb").read() == open(tmp_path / "out2" / "scores-x2.parquet", "rb").read()
