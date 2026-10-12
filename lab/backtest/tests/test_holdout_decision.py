"""The hold-out decision rule (pre-registration §4) on fixed numbers."""

from backtest.analysis import _holdout_decision

CONFIG = {"decision_group": "B+C", "candidates": [{"id": "K1", "role": "main"}, {"id": "K2", "role": "variant"},
                                                  {"id": "K3", "role": "control"}]}


def out(**groups):
    """groups: K -> {group: (mean, lo)}"""
    return {"candidates": {k: {"groups": {g: {"main": {"mean": m, "lo": lo}} for g, (m, lo) in v.items()}}
                           for k, v in groups.items()}}


def test_the_control_does_not_win_on_a_narrower_ci_alone():
    # The dev pseudo-parts case: K3 has the higher lower bound (0.247 vs 0.244) but the lower mean.
    o = out(K1={"B+C": (0.0078, 0.00244)}, K2={"B+C": (0.0070, 0.00229)}, K3={"B+C": (0.0062, 0.00247)})
    assert _holdout_decision(o, CONFIG)["pick"] == "K1"


def test_the_control_wins_with_a_higher_lower_bound_and_mean():
    o = out(K1={"B+C": (0.0050, 0.0010)}, K2={"B+C": (0.0040, 0.0005)}, K3={"B+C": (0.0055, 0.0020)})
    assert _holdout_decision(o, CONFIG)["pick"] == "K3"


def test_highest_lower_bound_among_the_candidates_and_drop_outs():
    o = out(K1={"A": (0.01, 0.0), "B+C": (0.006, -0.001)}, K2={"A": (0.01, 0.0), "B+C": (-0.001, -0.004)},
            K3={"B+C": (0.004, -0.002)})
    d = _holdout_decision(o, CONFIG)
    assert d["pick"] == "K1"  # the only eligible one (mean > 0), even with its lower bound below 0
    assert d["dropped_after_part_A"] == ["K2"]


def test_no_candidate_with_a_positive_mean():
    o = out(K1={"B+C": (-0.001, -0.004)}, K2={"B+C": (-0.002, -0.005)}, K3={"B+C": (-0.0005, -0.003)})
    d = _holdout_decision(o, CONFIG)
    assert d["pick"] is None and d["no_positive_candidate"] and d["highest_mean_if_none"] == "K3"
