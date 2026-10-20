"""entry_report on synthetic route (ii) parts: Σ/day with days without a position counting 0, the paired day bootstrap
of the difference (identical series → a zero interval; the same day draws for every series), busy-only counts, same state
on the fill, capped share, the contiguous competition margin, and the decision rule on days 10-18 with the days 1-9 sign
check. No store, no engine: settle_sql only needs the parts' columns."""
import os

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import entry_report as er

SOL = 10**9
DAYS_EARLY = ["2026-09-13", "2026-09-14", "2026-09-15"]
DAYS_LATE = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]


def _cfg(tmp_path):
    p = tmp_path / "defaults.yaml"
    p.write_text("costs:\n  ata_rent_lamports: 2039280\n  scenarios:\n    base: {fee_per_tx_sol: 0.000605, tip_sol: 0.0001, failed_tx_sol: 0.000505, "
                 "land_prob: 0.97, ata_rent: refunded_on_full_exit, adverse_exec_per_side: 0.0}\n")
    return str(p)


def _row(entry, variant, farm, day, pool, t, tokens, proceeds, capped=None, k_tx=0, skipped=False, failed=False, reason="take_profit"):
    return {"family": "F7", "segment": "pool_reversed", "size_sol": 0.5, "entry": entry, "variant": variant, "farm": farm,
            "regime": "270ms", "day": day, "pool": pool, "t": t, "entry_slot": t + 1, "exit_slot": t + 50, "exit_reason": reason,
            "skipped": skipped, "entry_failed": failed, "capped": capped, "k_tx": k_tx, "cost": SOL // 2, "tokens": tokens,
            "proceeds": proceeds, "exit_attempts": 1, "vault_capped": False}


def _parts(tmp_path, rows):
    d = tmp_path / "parts"
    d.mkdir()
    cols = {k: [r[k] for r in rows] for k in rows[0]}
    pq.write_table(pa.table(cols), d / "part-000000.parquet")
    return str(d)


def _fixture(tmp_path):
    rows = []
    # K3 group (tp4_T600, farm mixed). Per late day: 3 triggers. R earns +0.01 SOL per trade, E0 +0.02 (better fill: more
    # tokens), K1 = R's fill on two of three (same state) and +0.015 on the third; K2 loses.
    for i, day in enumerate(DAYS_LATE):
        for j in range(3):
            pool, t = f"P{i}{j}", 1000 * (i + 1) + 10 * j
            farm = "organic" if j == 0 else "flagged"
            rows.append(_row("R", "tp4_T600", farm, day, pool, t, 1000, SOL // 2 + int(0.01 * SOL)))
            rows.append(_row("E0", "tp4_T600", farm, day, pool, t, 1100, SOL // 2 + int(0.02 * SOL), capped=False, k_tx=0))
            same = j < 2
            rows.append(_row("K1", "tp4_T600", farm, day, pool, t, 1000 if same else 1050, SOL // 2 + int((0.01 if same else 0.0105) * SOL),
                             capped=same, k_tx=0 if same else 1))
            rows.append(_row("K2", "tp4_T600", farm, day, pool, t, 900, SOL // 2 - int(0.01 * SOL), capped=True, k_tx=2))
    # a trigger only in E0 (busy in R) on the last day, and one only in R
    rows.append(_row("E0", "tp4_T600", "flagged", DAYS_LATE[-1], "PX", 9000, 1100, SOL // 2 + int(0.02 * SOL), capped=False))
    rows.append(_row("R", "tp4_T600", "flagged", DAYS_LATE[-1], "PY", 9100, 1000, SOL // 2 + int(0.01 * SOL)))
    # early days: E0 better than R too (same sign), K1 equal
    for i, day in enumerate(DAYS_EARLY):
        pool, t = f"E{i}", 100 * (i + 1)
        rows.append(_row("R", "tp4_T600", "flagged", day, pool, t, 1000, SOL // 2 + int(0.01 * SOL)))
        rows.append(_row("E0", "tp4_T600", "flagged", day, pool, t, 1100, SOL // 2 + int(0.03 * SOL), capped=False))
        rows.append(_row("K1", "tp4_T600", "flagged", day, pool, t, 1000, SOL // 2 + int(0.01 * SOL), capped=True))
        rows.append(_row("K2", "tp4_T600", "flagged", day, pool, t, 900, SOL // 2 - int(0.01 * SOL), capped=True))
    # H5 group: organic only, E0 and K1 both clearly better than R on every late day, early too
    for i, day in enumerate(DAYS_LATE + DAYS_EARLY):
        pool, t = f"H{i}", 5000 + 10 * i
        rows.append(_row("R", "flow300_tp100_H6", "organic", day, pool, t, 1000, SOL // 2 + int(0.02 * SOL)))
        rows.append(_row("E0", "flow300_tp100_H6", "organic", day, pool, t, 1200, SOL // 2 + int(0.05 * SOL), capped=False))
        rows.append(_row("K1", "flow300_tp100_H6", "organic", day, pool, t, 1150, SOL // 2 + int(0.04 * SOL), capped=False, k_tx=1))
        rows.append(_row("K2", "flow300_tp100_H6", "organic", day, pool, t, 1100, SOL // 2 + int(0.03 * SOL), capped=False, k_tx=2))
    return _parts(tmp_path, rows)


def _run(tmp_path, boot=400):
    con = duckdb.connect()
    n, codes = er.load(con, _fixture(tmp_path), _cfg(tmp_path))
    res = er.analyse(con, codes, boot, log=lambda m: None)
    return n, codes, res


def test_paired_bootstrap_and_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    n, codes, res = _run(tmp_path)
    k3 = res["groups"]["K3"]["blocks"][er.BINDING]
    assert k3["days"] == 4
    r, e0, k1, k2 = (k3["codes"][c] for c in ("R", "E0", "K1", "K2"))
    # R against itself: a zero difference with a zero interval; the same draws make it exact
    assert r["diff_day"] == 0.0 and r["diff_day_lo"] == 0.0 and r["diff_day_hi"] == 0.0
    # E0 earns 0.01 SOL more per trade than R on 3 trades a day, plus the busy-only trigger on the last day
    assert e0["only_in_code"] == 1 and e0["only_in_R"] == 1 and r["only_in_R"] == 0
    assert e0["diff_day"] > 0 and e0["diff_day_lo"] > 0
    assert e0["pairs"] == 12 and e0["same_state"] == 0.0 and e0["capped_share"] == 0.0
    assert abs(e0["dret_mean"] - 0.02) < 5e-4  # (0.02 − 0.01) SOL on a cost of 0.5 SOL plus network cost and rent ≈ +1.99 pt
    assert k1["pairs"] == 12 and abs(k1["same_state"] - 8 / 12) < 1e-9 and abs(k1["capped_share"] - 8 / 12) < 1e-9
    assert k2["diff_day"] < 0 and k2["diff_day_hi"] < 0
    # the margin is contiguous from k = 0: E0 above 0; K1 adds +0.0005 on three days but misses R's busy-only trigger (−0.01)
    # on the last day, so its paired interval spans 0 → k = 0; H5 (E0, K1 above 0, K2 above 0 too) → k = 2
    assert k3["competition_margin_k"] == 0
    assert res["groups"]["H5"]["blocks"][er.BINDING]["competition_margin_k"] == 2
    # a day without a position counts 0 in Σ/day: the early block has 3 days with one trade each
    early = res["groups"]["K3"]["blocks"][er.EARLY]
    assert early["days"] == 3 and early["codes"]["R"]["rows"] == 3
    # the organic subset is H4/H5's farm filter: K3's organic rows are not in H5 (different variant), and H5 sees 7 days
    assert res["groups"]["H5"]["blocks"]["all days"]["days"] == 7
    assert "H4" in res["groups"] and res["groups"]["H4"]["blocks"] == {}


def test_decision_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    _, _, res = _run(tmp_path)
    v = res["verdict"]
    assert v["per_group"]["H5"]["E0"]["sum_day_diff_ci_above_0"] and v["per_group"]["H5"]["K1"]["sum_day_diff_ci_above_0"]
    assert v["per_group"]["K3"]["E0"]["sum_day_diff_ci_above_0"] and v["per_group"]["K3"]["E0"]["days_1_9_same_sign"]
    # K3's K1: Σ/day − R spans 0 (the busy-only trigger of R on the last day) and the paired Δ return (≈ +0.03 pt) is below
    # 0.2 pt → not positive on both counts
    assert not v["per_group"]["K3"]["K1"]["sum_day_diff_ci_above_0"]
    assert v["per_group"]["K3"]["K1"]["dret_trim_pt"] < er.MIN_DELTA_PT and not v["per_group"]["K3"]["K1"]["delta_ret_ge_0.2pt"]
    assert v["positive"] is False


def test_markdown_and_main(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_REPORT_WORKERS", "1")
    parts = _fixture(tmp_path)
    out = tmp_path / "entry_report.md"
    monkeypatch.setattr("sys.argv", ["entry_report", "--parts", parts, "--cfg", _cfg(tmp_path), "--boot", "200", "--out", str(out)])
    er.main()
    md = out.read_text()
    assert "### K3 · tp4_T600 · farm all" in md and "### H5 · flow300_tp100_H6 · farm organic" in md
    assert "NOT positive" in md and "competition margin: k = 0" in md and "competition margin: k = 2" in md
    assert os.path.exists(tmp_path / "entry_report.json")


def test_mean_ci_shared_draws():
    idx = er.boot_idx(5, 300)
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    m, lo, hi = er.mean_ci(a - a, idx)
    assert (m, lo, hi) == (0.0, 0.0, 0.0)
    m2, lo2, hi2 = er.mean_ci(a, idx)
    assert lo2 <= m2 <= hi2 and m2 == 3.0
