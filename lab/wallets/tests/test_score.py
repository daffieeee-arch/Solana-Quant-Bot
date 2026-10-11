"""Scoring arithmetic: Benjamini-Hochberg, empirical-Bayes shrinkage, labels."""

import numpy as np

from wallets import score as S


def test_bh_matches_known_example():
    p = np.array([0.01, 0.04, 0.03, 0.20, np.nan])
    q = S.bh(p)
    # sorted: 0.01, 0.03, 0.04, 0.20 -> 0.04, 0.06, 0.0533.., 0.20 -> monotone from the end: 0.04, 0.0533, 0.0533, 0.20
    assert np.allclose(q[:4], [0.04, 0.0533333, 0.0533333, 0.20], atol=1e-6) and np.isnan(q[4])


def test_shrinkage_pulls_small_n_towards_prior():
    means = np.array([0.5, 0.0, -0.2, 0.1, 0.9])
    ns = np.array([50, 50, 50, 50, 2], dtype=float)
    sds = np.array([0.3, 0.3, 0.3, 0.3, 0.3])
    shr, prior, k = S.shrink(means, ns, sds)
    assert abs(prior - 0.1) < 1e-9  # weighted mean of the n >= MIN_N wallets
    assert abs(shr[4] - 0.9) > abs(shr[0] - 0.5)  # the n = 2 wallet moves much more than an n = 50 wallet
    assert prior < shr[4] < 0.9 and 0.1 < shr[0] < 0.5


def test_shrinkage_with_too_few_wallets_returns_prior():
    shr, prior, k = S.shrink(np.array([0.2, -0.1]), np.array([10.0, 10.0]), np.array([0.1, 0.1]))
    assert k == float("inf") and np.allclose(shr, prior) and abs(prior - 0.05) < 1e-9


def test_labels_in_leaderboard():
    leaders = {"w": {"n": 1, "mean_ret_marked": 0.1, "mean_ret_realized": 0.0, "median_hold_s": 5.0,
                     "create_slot_share": 1.0, "closed_share": 1.0, "days": 1, "sol_in": 2.0, "curve_share": 1.0,
                     "reversed_share": 0.0},
               "b": {"n": 800, "mean_ret_marked": 0.0, "mean_ret_realized": 0.0, "median_hold_s": 10.0,
                     "create_slot_share": 0.0, "closed_share": 1.0, "days": 2, "sol_in": 900.0, "curve_share": 0.5,
                     "reversed_share": 0.5}}
    board = S.leaderboard("2026-09-27", [], leaders, creators={"w"}, farmer={"b": 0.8}, memberships={"b": 5},
                          split={"b": 2}, days_in_window=2)
    by = {r["wallet"]: r for r in board}
    assert by["w"]["label_text"] == "creator,sniper,one_shot"
    assert by["b"]["label_text"] == "hf_bot,farmer,bundler,split_buyer"
    assert by["w"]["follow_ret_d2"] is None and by["w"]["rank_d2"] is None
