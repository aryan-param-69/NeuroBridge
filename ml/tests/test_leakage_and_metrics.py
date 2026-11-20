"""Leakage guards and metric correctness. Synthetic arrays here are TEST FIXTURES only."""

import numpy as np
import pytest
from sklearn.exceptions import NotFittedError
from sklearn.utils.validation import check_is_fitted

from nbml.cv import assert_subject_disjoint, fit_predict, outer_folds, split_holdout
from nbml.metrics import chance_upper_bound, evaluate, expected_calibration_error
from nbml.models import MODELS
from nbml.select import choose_model, paired_bootstrap, subject_counts

SUBJECTS = np.arange(1, 106)


def test_holdout_is_disjoint_deterministic_and_sized():
    dev, hold = split_holdout(np.repeat(SUBJECTS, 45))
    assert not set(dev) & set(hold)
    assert set(dev) | set(hold) == set(SUBJECTS)
    assert len(hold) == round(0.2 * len(SUBJECTS))
    dev2, hold2 = split_holdout(np.repeat(SUBJECTS, 45))
    assert (hold == hold2).all() and (dev == dev2).all()


def test_outer_folds_partition_development_subjects_and_never_touch_holdout():
    dev, hold = split_holdout(np.repeat(SUBJECTS, 45))
    folds = outer_folds(dev)
    tests = np.concatenate([te for _, te in folds])
    assert sorted(tests) == sorted(dev)  # every dev subject is tested exactly once
    for tr, te in folds:
        assert not set(tr) & set(te)
        assert not (set(tr) | set(te)) & set(hold)


def test_overlap_is_detected():
    subject = np.array([1, 1, 2, 2, 3, 3])
    with pytest.raises(AssertionError, match="Data leakage"):
        assert_subject_disjoint(subject, np.array([0, 1, 2]), np.array([3, 4]))  # subject 2 in both


@pytest.mark.parametrize("name", list(MODELS))
def test_every_model_is_built_unfitted(name):
    """Scalers/CSP/Riemannian means must not be fitted before cross-validation starts."""
    est = MODELS[name].build()
    with pytest.raises(NotFittedError):
        check_is_fitted(est)


def test_pipeline_on_pure_noise_scores_near_chance():
    """Sanity check on random features and random labels: a leak-free pipeline must sit near 50 %."""
    rng = np.random.default_rng(0)
    n_sub, per = 30, 40
    subject = np.repeat(np.arange(n_sub), per)
    y = np.tile(np.r_[np.zeros(per // 2), np.ones(per // 2)], n_sub).astype(int)
    X = rng.normal(size=(len(y), 192))  # synthetic noise, no signal
    scores = []
    for fold in range(3):
        te_s = np.arange(fold * 10, fold * 10 + 10)
        te = np.where(np.isin(subject, te_s))[0]
        tr = np.where(~np.isin(subject, te_s))[0]
        r = fit_predict("logreg", X, y, subject, tr, te, fold)
        scores.append(evaluate(y[te], r.proba)["balanced_accuracy"])
    assert abs(np.mean(scores) - 0.5) < 0.06


def test_a_real_signal_is_detected_when_present():
    rng = np.random.default_rng(1)
    n_sub, per = 24, 40
    subject = np.repeat(np.arange(n_sub), per)
    y = np.tile(np.r_[np.zeros(per // 2), np.ones(per // 2)], n_sub).astype(int)
    X = rng.normal(size=(len(y), 192))
    X[:, :5] += y[:, None] * 1.5  # planted effect shared across subjects
    te = np.where(subject < 6)[0]
    tr = np.where(subject >= 6)[0]
    r = fit_predict("logreg", X, y, subject, tr, te, 0)
    assert evaluate(y[te], r.proba)["balanced_accuracy"] > 0.9


def test_ece_zero_when_calibrated_and_large_when_overconfident():
    rng = np.random.default_rng(2)
    p = rng.uniform(size=20000)
    y = (rng.uniform(size=20000) < p).astype(int)
    assert expected_calibration_error(y, p) < 0.02
    assert expected_calibration_error(y, np.where(p > 0.5, 0.99, 0.01)) > 0.2


def test_chance_bound_matches_published_value():
    assert chance_upper_bound(1000) == pytest.approx(0.527, abs=0.003)
    assert chance_upper_bound(100) == pytest.approx(0.58, abs=0.01)


def test_selection_rule_prefers_calibrated_model_among_statistical_ties():
    rng = np.random.default_rng(3)
    n_sub = 60
    subject = np.repeat(np.arange(n_sub), 40)
    y = np.tile([0, 1], n_sub * 20)
    strong = np.clip(y * 0.6 + 0.2 + rng.normal(0, 0.3, len(y)), 0, 1)
    weak = np.clip(y * 0.2 + 0.4 + rng.normal(0, 0.3, len(y)), 0, 1)
    probas = {"logreg": strong, "random_forest": strong + rng.normal(0, 0.01, len(y)), "xgboost": weak}
    counts = {m: subject_counts(y, p, subject)[1] for m, p in probas.items()}
    boot = paired_bootstrap(counts, n_boot=500)
    point = {m: float(boot[m].mean()) for m in probas}
    brier = {"logreg": 0.10, "random_forest": 0.20, "xgboost": 0.30}
    sel = choose_model(point, brier, boot)
    assert "xgboost" not in sel["tied"]  # clearly worse model is not a tie
    assert sel["chosen"] == "logreg"  # tie broken by calibration
