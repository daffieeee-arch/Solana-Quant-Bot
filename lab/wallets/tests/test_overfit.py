"""DSR and PBO: the normal helpers, the closed-form checks from the papers, noise versus a real edge,
and the per-day matrix from synthetic parts."""

import math
import os

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import overfit


def test_normal_helpers_roundtrip():
    for x in (-3.0, -0.5, 0.0, 0.9, 2.5):
        assert abs(overfit.norm_ppf(overfit.norm_cdf(x)) - x) < 1e-8
    assert overfit.norm_ppf(0.0) == -math.inf and overfit.norm_ppf(1.0) == math.inf
    assert abs(overfit.norm_cdf(1.959964) - 0.975) < 1e-6


def test_psr_and_expected_max_sharpe_closed_forms():
    # PSR against 0 for SR = 0.1 per day over 100 days, normal returns (kurtosis 3): Phi(0.1 sqrt(99) / sqrt(1 + 0.005))
    assert abs(overfit.psr(0.1, 0.0, 100) - overfit.norm_cdf(0.1 * math.sqrt(99) / math.sqrt(1.005))) < 1e-12
    assert abs(overfit.psr(0.1, 0.0, 100) - 0.8395) < 1e-3
    # one trial: nothing to deflate
    assert overfit.expected_max_sharpe(1, 1.0) == 0.0
    # two trials with unit Sharpe variance: (1 - g) Z(1/2) + g Z(1 - 1/(2e)) = 0.5772 * 0.9004
    assert abs(overfit.expected_max_sharpe(2, 1.0) - overfit.EULER_GAMMA * overfit.norm_ppf(1 - 1 / (2 * math.e))) < 1e-9
    assert abs(overfit.expected_max_sharpe(2, 1.0) - 0.52) < 0.01
    # more trials, higher bar; variance scales as sqrt
    assert overfit.expected_max_sharpe(100, 1.0) > overfit.expected_max_sharpe(10, 1.0) > 0
    assert abs(overfit.expected_max_sharpe(10, 4.0) - 2 * overfit.expected_max_sharpe(10, 1.0)) < 1e-12


def test_sharpe_and_moments():
    assert overfit.sharpe([1.0, 1.0, 1.0]) == 0.0 and overfit.sharpe([1.0]) == 0.0
    assert abs(overfit.sharpe([1.0, 3.0]) - 2 / math.sqrt(2)) < 1e-12
    s, k = overfit.moments(np.array([0.0, 0.0, 0.0]))
    assert s == 0.0 and k == 3.0
    cols = overfit.sharpe_columns(np.array([[1.0, 0.0], [3.0, 0.0], [2.0, 0.0]]))
    assert abs(cols[0] - 2.0) < 1e-12 and cols[1] == 0.0


def test_dsr_noise_is_deflated_and_a_real_edge_survives():
    rng = np.random.default_rng(7)
    noise = rng.normal(0.0, 1.0, size=(200, 60))
    d = overfit.dsr(noise)
    assert d["n_trials"] == 60 and d["t"] == 200 and d["sr"] > 0 and d["sr0"] > 0
    assert d["dsr"] < d["psr0"]  # deflating can only lower the probability
    assert d["dsr"] < 0.95  # the best of 60 noise trials does not pass
    edge = noise.copy()
    edge[:, 5] += 0.5  # one trial with a true daily Sharpe of about 0.5
    e = overfit.dsr(edge)
    assert e["selected"] == 5 and e["dsr"] > 0.99
    # deflating a named trial uses the same SR0; the null-variance SR0 is sqrt(1/T) x the N factor
    assert overfit.dsr(edge, 5)["sr0"] == e["sr0"]
    assert abs(e["sr0_null"] - overfit.expected_max_sharpe(60, 1 / 200)) < 1e-12 and e["dsr_null"] > 0.99


def test_pbo_noise_near_half_and_edge_near_zero():
    rng = np.random.default_rng(11)
    noise = rng.normal(0.0, 1.0, size=(64, 20))
    p = overfit.pbo(noise, blocks=8)
    assert p["n_splits"] == math.comb(8, 4) and p["blocks"] == 8
    assert 0.3 <= p["pbo"] <= 0.7
    edge = noise.copy()
    edge[:, 3] += 0.8
    e = overfit.pbo(edge, blocks=8)
    assert e["pbo"] < 0.1 and e["prob_loss"] < 0.1
    # degenerate inputs
    assert math.isnan(overfit.pbo(noise[:, :1], blocks=8)["pbo"])
    assert math.isnan(overfit.pbo(noise[:1], blocks=8)["pbo"])
    # odd block counts are rounded down to even; max_splits bounds the work
    q = overfit.pbo(noise, blocks=7, max_splits=5)
    assert q["blocks"] == 6 and q["n_splits"] == 5


def _cfg():
    return {"costs": {"ata_rent_lamports": 2_039_280,
                      "scenarios": {"base": {"fee_per_tx_sol": 0.000005, "tip_sol": 0.0, "failed_tx_sol": 0.000005, "land_prob": 1.0,
                                             "ata_rent": "refunded", "adverse_exec_per_side": 0.0}}}}


def test_day_matrix_and_render(tmp_path):
    sol = 10**9
    rows = []
    for i, day in enumerate(f"2026-09-{11 + k:02d}" for k in range(16)):
        for variant, win in (("good", i % 4 != 0), ("bad", i % 4 == 0)):
            for j in range(3):
                rows.append({"family": "F7", "variant": variant, "size_sol": 0.5, "d": 2, "q": 0.5, "farm": "organic", "overlay": "none",
                             "day": day, "skipped": False, "entry_failed": False, "exit_attempts": 1, "exit_slot": 1000 + i,
                             "exit_reason": "time", "cost": sol // 2,
                             "proceeds": int(sol // 2 * (1.1 if win else 0.9))})
    rows.append({**rows[-1], "skipped": True})  # a skipped position is ignored
    pdir = tmp_path / "parts"
    os.makedirs(pdir)
    pq.write_table(pa.Table.from_pylist(rows), pdir / "part-0.parquet")
    con = duckdb.connect()
    con.execute("SET memory_limit = '200MB'; SET threads TO 1")
    fams = overfit.day_matrix(con, str(pdir), _cfg(), "base", where="d = 2")
    labels, days, m = fams["F7"]
    assert len(days) == 16 and m.shape == (16, 2) and labels[0][1] == "bad" and labels[1][1] == "good"
    assert abs(m[1, 1] - 3 * 0.05) < 1e-3 and m[0, 1] < 0  # three wins of +10% on 0.5 SOL minus fees; a losing day
    md = overfit.render(str(pdir), "base", 8, None, overfit.KEY, "variant=good,d=2", fams, "d = 2")
    assert "| F7 | 2 | 16 | variant=good" in md and "trials where d = 2" in md and "Selected trial" in md and "rank 1 of 2" in md
    assert overfit.select_index(labels, overfit.KEY, "variant=nope") is None


if __name__ == "__main__":
    pytest.main([__file__])
