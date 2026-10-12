"""Step-1 report: settled returns per group, day bootstrap and the Markdown, on synthetic run files."""

import os

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import step1_report


def _cfg():
    return {"costs": {"ata_rent_lamports": 2_039_280,
                      "scenarios": {"base": {"fee_per_tx_sol": 0.000005, "tip_sol": 0.0, "failed_tx_sol": 0.000005, "land_prob": 1.0,
                                             "ata_rent": "refunded", "adverse_exec_per_side": 0.0}}}}


def _follow_rows():
    rows = []
    sol = 10**9
    for day, wallets in (("2026-09-25", ("A", "B", "C")), ("2026-09-26", ("A", "B", "C"))):
        for w in wallets:
            for i in range(6):
                win = (w == "A") or (i % 3 == 0)  # A wins always, B and C one in three
                rows.append({"wallet": w, "key": f"k{i}", "venue": "pool", "orientation": "normal", "t0": 100 + i, "day": day,
                             "control": w == "C", "size_sol": 0.5, "d": 2, "variant": "W_half", "leader_ret_marked": 0.1,
                             "leader_ret_realized": 0.05, "leader_hold_slots": 50, "leader_closed": True, "create_slot": False,
                             "entry_failed": False, "skipped": False, "tokens": 1000, "cost": sol // 2,
                             "proceeds": int(sol // 2 * (1.2 if win else 0.8)), "exit_reason": "leader_sold" if i % 2 else "time",
                             "entry_slot": 101 + i, "exit_decision_slot": 101 + i + (5 if i == 0 else 40), "exit_slot": 103 + i + 40,
                             "exit_attempts": 1, "cf_graduation": False, "seed_pool": False, "vault_capped": False,
                             "hist_mid_entry": 1.0, "hist_mid_exit": 1.0})
    return rows


def test_groups_and_markdown(tmp_path):
    fdir, edir, sdir = tmp_path / "follow", tmp_path / "episodes", tmp_path / "scores"
    for d in (fdir, edir, sdir):
        os.makedirs(d)
    pq.write_table(pa.Table.from_pylist(_follow_rows()), fdir / "follow-100.parquet")
    pq.write_table(pa.Table.from_pylist([{"wallet": "A", "key": "k", "venue": "pool", "orientation": "normal", "t0": 100, "day": "2026-09-25",
                                          "cost_sol": 10**9, "hold_slots": 5, "end_slot": 120, "create_slot": False, "closed": True,
                                          "leader_ret_realized": 0.1, "leader_ret_marked": 0.1}]), edir / "episodes-100.parquet")
    pq.write_table(pa.Table.from_pylist([{"wallet": "A", "variant": "W_half", "size_sol": 0.5, "d": 2, "control": False, "n": 6, "filled": 6,
                                          "mean_ret": 0.19, "sd_ret": 0.01, "mean_pnl_sol": 0.09, "win": 1.0, "median_hold_slots": 42.0,
                                          "leader_exit_share": 0.5, "failed_share": 0.0, "leader_sold_fast_share": 0.0,
                                          "median_slots_to_leader_sell": 40.0, "shrunk_ret": 0.1, "prior": -0.1, "k": 5.0, "p_value": 0.001,
                                          "q_value": 0.001, "top": True, "rank": 1, "day": "2026-09-26"}]), sdir / "scores-2026-09-26.parquet")
    pq.write_table(pa.Table.from_pylist([{"day": "2026-09-26", "wallet": "A", "creator": False, "sniper": False, "hf_bot": False, "farmer": False,
                                          "one_shot": False, "bundler": False, "split_buyer": False, "label_text": "", "leader_n": 6,
                                          "leader_positions_per_day": 3.0, "leader_ret_marked": 0.1, "leader_ret_realized": 0.05,
                                          "leader_median_hold_s": 13.0, "follow_n": 6, "control": False, "follow_ret_d1": 0.2, "follow_ret_d2": 0.19,
                                          "follow_shrunk_d2": 0.1, "q_d2": 0.001, "top_d2": True, "leader_sold_fast_share_d2": 0.0,
                                          "copyable": True, "rank_d2": 1}]), sdir / "leaderboard-2026-09-26.parquet")
    con = duckdb.connect()
    con.execute("SET memory_limit = '200MB'; SET threads TO 1")
    assert step1_report.load(con, str(fdir), _cfg()) == 1
    g = step1_report.groups(con, boot=200)
    cand = next(r for r in g if not r["control"])
    ctrl = next(r for r in g if r["control"])
    assert cand["n"] == 24 and cand["wallets"] == 2 and ctrl["n"] == 12 and ctrl["wallets"] == 1
    # A: +20% gross on every position; B: one in three. Net is a little below gross (network fee), never above.
    assert 0.0 < cand["mean_ret"] < 0.2 and ctrl["mean_ret"] < 0
    assert cand["ci_lo"] is not None and cand["ci_lo"] - 1e-9 <= cand["mean_ret"] <= cand["ci_hi"] + 1e-9
    assert abs(cand["led"] - 0.5) < 1e-9 and cand["days"] == 2
    sc = step1_report.scores(con, str(sdir))
    assert sc["last"] == "2026-09-26" and sc["per_day"][0][2] == 1  # one top wallet
    wf = sc["wf"][0]
    assert wf[0] == "2026-09-26" and wf[1] == 6 and wf[3] == 1  # wallet A, ranked 1, followed on its day: 6 positions

    class A:
        follow, episodes, scores = str(fdir), str(edir), str(sdir)

    md = step1_report.render(A, 1, g, step1_report.venues(con), step1_report.days(con), step1_report.wallet_level(con),
                             step1_report.exits(con), sc, step1_report.episodes_summary(con, str(edir)), 200)
    assert "| W_half | 2 | candidates | 24 | 2 | 2 |" in md
    assert "0 of 2 days negative" in md and "Walk-forward" in md and "`A`" in md


def test_day_bootstrap_contains_the_mean_and_handles_one_day():
    lo, hi = step1_report.day_bootstrap([10.0, -5.0, 2.0], [100, 100, 100], 500)
    assert lo <= (10 - 5 + 2) / 300 <= hi
    assert step1_report.day_bootstrap([1.0], [10], 10) == (None, None)
    rng_a = step1_report.day_bootstrap([1.0, 2.0], [10, 10], 100)
    assert rng_a == step1_report.day_bootstrap([1.0, 2.0], [10, 10], 100)  # deterministic seed


def test_fmt():
    assert step1_report.fmt(None) == "–" and step1_report.fmt(0.1234, pct=True, sign=True) == "+12.3%"
    assert step1_report.fmt(12345) == "12,345" and step1_report.fmt(np.float64(0.5), 0, pct=True) == "50%"
    assert step1_report.fmt(True) == "yes"


if __name__ == "__main__":
    pytest.main([__file__])
