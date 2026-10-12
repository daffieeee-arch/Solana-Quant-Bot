"""AUC and the ridge-logistic fit used by the runner report."""

import numpy as np

from wallets import runners_report as R


def test_auc_perfect_and_random():
    y = np.array([0, 0, 1, 1], dtype=float)
    assert R.auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert R.auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert abs(R.auc(y, np.array([0.5, 0.5, 0.5, 0.5])) - 0.5) < 1e-9


def test_logistic_recovers_sign_and_separates():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(2000, 2))
    y = (1 / (1 + np.exp(-(1.5 * X[:, 0] - 1.0 * X[:, 1]))) > rng.uniform(size=2000)).astype(float)
    w = R.fit_logistic(X, y, l2=0.1)
    assert w[1] > 0.8 and w[2] < -0.5
    s = np.hstack([np.ones((2000, 1)), X]) @ w
    assert R.auc(y, s) > 0.75
