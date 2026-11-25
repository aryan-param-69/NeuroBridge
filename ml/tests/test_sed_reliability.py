"""Reliability machinery for the sedation model: no leakage between people, honest calibration / conformal / unfamiliar-EEG behaviour.
Synthetic data are test fixtures only."""

import numpy as np
import pytest

from nbml import sed_analyse as A
from nbml import sed_ood as ood
from nbml import sed_predict as P
from nbml.sed_cv import labels, task_mask


def test_labels_and_task_mask():
    level = np.array([1, 2, 3, 4, 3])
    assert task_mask(level).tolist() == [True, False, True, True, True]  # mild sedation never enters the primary task
    assert labels(level).tolist() == [0, 0, 1, 0, 1]  # moderate = sedated; baseline and recovery = awake


def test_to_recording_averages_epochs_of_one_recording():
    rec = np.array([0, 0, 0, 1, 1])
    y = np.array([1, 1, 1, 0, 0])
    s = np.array([5, 5, 5, 5, 5])
    p = np.array([0.2, 0.4, 0.9, 0.1, 0.3])
    ids, yy, ss, pp = A.to_recording(rec, y, s, p)
    assert ids.tolist() == [0, 1] and yy.tolist() == [1, 0] and np.allclose(pp, [0.5, 0.2])


def test_platt_loso_never_uses_the_held_out_person():
    """The calibrator for person s must be identical whatever person s's own labels are."""
    rng = np.random.default_rng(0)
    n_sub, per = 6, 40
    subject = np.repeat(np.arange(n_sub), per)
    y = rng.integers(0, 2, n_sub * per)
    p = np.clip(0.5 + (y - 0.5) * 0.3 + rng.normal(0, 0.15, y.size), 0.01, 0.99)
    mask = np.ones_like(y, dtype=bool)
    a, params_a = A.platt_loso(p, y, subject, mask)
    y2 = y.copy()
    y2[subject == 3] = 1 - y2[subject == 3]  # flip ONLY person 3's labels
    b, params_b = A.platt_loso(p, y2, subject, mask)
    assert params_a[3] == params_b[3]  # person 3's calibrator ignored person 3's labels
    assert np.allclose(a[subject == 3], b[subject == 3])
    assert params_a[2] != params_b[2]  # other people's calibrators did change


def test_platt_fixes_an_overconfident_scorer():
    rng = np.random.default_rng(1)
    subject = np.repeat(np.arange(8), 200)
    y = rng.integers(0, 2, subject.size)
    z = (y - 0.5) * 1.0 + rng.normal(0, 1.0, y.size)  # weak signal ...
    p = 1 / (1 + np.exp(-4 * z))  # ... made extremely over-confident
    cal, _ = A.platt_loso(p, y, subject, np.ones_like(y, dtype=bool))
    from nbml.metrics import expected_calibration_error as ece

    assert ece(y, cal) < 0.5 * ece(y, p) and ece(y, cal) < 0.08


def test_conformal_coverage_is_close_to_the_target_for_new_people():
    rng = np.random.default_rng(2)
    n_sub = 40
    subj = np.repeat(np.arange(n_sub), 3)
    y = np.tile([0, 1, 0], n_sub)
    z = (y - 0.5) * 2.0 + rng.normal(0, 1.0, y.size)
    p = 1 / (1 + np.exp(-z))  # a reasonably calibrated scorer
    sets = A.conformal_loso(p, y, subj, alpha=0.2)
    cover = np.mean([int(t) in s for t, s in zip(y, sets, strict=True)])
    assert 0.74 < cover < 0.9  # target 0.80 (finite sample, 120 recordings)
    wide = A.conformal_loso(p, y, subj, alpha=0.05)
    assert np.mean([len(s) for s in wide]) > np.mean([len(s) for s in sets])  # asking for more coverage gives larger sets


def test_conformal_shows_both_classes_when_the_scorer_is_useless():
    rng = np.random.default_rng(3)
    subj = np.repeat(np.arange(30), 3)
    y = np.tile([0, 1, 0], 30)
    p = np.clip(rng.normal(0.5, 0.05, y.size), 0.01, 0.99)  # no information
    sets = A.conformal_loso(p, y, subj, alpha=0.2)
    # theory: 80 % coverage with a no-information scorer needs both-class sets about 60 % of the time (f + (1-f)/2 = 0.8)
    assert np.mean([len(s) == 2 for s in sets]) > 0.45
    assert np.mean([int(t) in s for t, s in zip(y, sets, strict=True)]) > 0.7


def test_ood_detector_flags_a_shifted_population_but_not_new_samples_of_the_training_one():
    rng = np.random.default_rng(4)
    mix = rng.normal(size=(10, 10))
    train = rng.normal(size=(2000, 10)) @ mix
    new_same = rng.normal(size=(500, 10)) @ mix
    shifted = rng.normal(size=(500, 10)) @ mix + 4.0
    m = A.ood_fit(train)
    assert ood.flags(m, new_same).mean() < 0.05
    assert ood.flags(m, shifted).mean() > 0.9  # also catches a shift into the directions the PCA discarded (needs the SPE statistic)
    t2_only = (ood._stats(m, shifted)[0] > m["thr"]).mean()
    assert t2_only < ood.flags(m, shifted).mean()  # the second statistic adds real detection power


def test_prediction_set_logic_and_verdicts():
    class Fake:
        def predict_proba(self, F):
            return np.stack([1 - F[:, 0], F[:, 0]], axis=1)

    names = ["a"]
    bundle = {"model": Fake(), "features": names, "platt": (1.0, 0.0), "conformal_q": 0.3, "alpha": 0.2,
              "ood": {"mu": np.zeros(1), "sd": np.ones(1), "comp": np.ones((1, 1)), "lam": np.ones(1), "thr": 100.0, "thr_spe": 100.0}}
    import nbml.sed_predict as mod

    orig = mod.extract
    try:
        for val, expect in ((0.95, "sedated-like"), (0.05, "awake-like"), (0.5, "uncertain")):
            mod.extract = lambda X, fs, v=val: (np.full((len(X), 1), v), names)
            out = P.predict_epochs(bundle, np.zeros((4, 19, 100)), 250.0)
            assert out["verdict"] == expect, (val, out["prediction_set"])
    finally:
        mod.extract = orig
    with pytest.raises(ValueError):
        mod.extract = lambda X, fs: (np.zeros((len(X), 1)), ["other"])
        try:
            P.predict_epochs(bundle, np.zeros((4, 19, 100)), 250.0)
        finally:
            mod.extract = orig


def test_ood_detector_treats_non_finite_features_as_unfamiliar():
    rng = np.random.default_rng(5)
    m = A.ood_fit(rng.normal(size=(500, 5)))
    X = rng.normal(size=(4, 5))
    X[1, 2] = np.nan
    X[2, 0] = np.inf
    fl = ood.flags(m, X)
    assert fl[1] and fl[2]  # a dead channel can give NaN or infinity; such an epoch must never pass as familiar
    assert not np.isnan(ood._stats(m, np.where(np.isfinite(X), X, 0.0))[0]).any()
