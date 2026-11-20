"""Subject-independent nested cross-validation (no participant ever appears in train and test).

Layout (see docs/04-research-datasets-ml-xai.md section 4):
    all kept subjects -> locked HOLD-OUT subjects (touched once, at the very end)
                      -> DEVELOPMENT subjects -> outer K folds (by subject) -> inner folds (tuning only)
Anything fitted on data (scalers, CSP filters, Riemannian mean, hyper-parameters) is fitted on the
training subjects of the current fold only, inside an sklearn Pipeline / GridSearchCV.
"""

import time
from dataclasses import dataclass

import numpy as np
from joblib import Parallel, delayed
from sklearn.base import clone
from sklearn.model_selection import GridSearchCV, StratifiedGroupKFold

from . import config as C
from .metrics import evaluate
from .models import MODELS


def split_holdout(subjects: np.ndarray, fraction: float = C.HOLDOUT_FRACTION, seed: int = C.SEED):
    """Deterministic subject-level split. Returns (development_subjects, holdout_subjects)."""
    uniq = np.array(sorted(set(subjects.tolist())))
    rng = np.random.default_rng(seed)
    perm = rng.permutation(uniq)
    n_hold = int(round(len(uniq) * fraction))
    return np.sort(perm[n_hold:]), np.sort(perm[:n_hold])


def outer_folds(dev_subjects: np.ndarray, k: int = C.OUTER_FOLDS, seed: int = C.SEED):
    """K disjoint groups of subjects; yields (train_subjects, test_subjects)."""
    rng = np.random.default_rng(seed + 1)
    groups = np.array_split(rng.permutation(dev_subjects), k)
    return [(np.concatenate([g for j, g in enumerate(groups) if j != i]), groups[i]) for i in range(k)]


def assert_subject_disjoint(subject: np.ndarray, train_idx: np.ndarray, test_idx: np.ndarray) -> None:
    shared = set(subject[train_idx].tolist()) & set(subject[test_idx].tolist())
    if shared:
        raise AssertionError(f"Data leakage: subjects in both train and test: {sorted(shared)}")


@dataclass
class FitResult:
    model: str
    fold: int
    test_idx: np.ndarray
    proba: np.ndarray
    best_params: dict
    inner_score: float | None
    seconds: float


def _inner_cv(seed: int):
    return StratifiedGroupKFold(n_splits=C.INNER_FOLDS, shuffle=True, random_state=seed)


def fit_predict(model: str, X: np.ndarray, y: np.ndarray, subject: np.ndarray, train_idx: np.ndarray,
                test_idx: np.ndarray, fold: int, tune: bool = True, params: dict | None = None,
                seed: int = C.SEED, zoo: dict | None = None) -> FitResult:
    """Tune on the training subjects only (inner CV), refit, predict the unseen test subjects."""
    assert_subject_disjoint(subject, train_idx, test_idx)
    spec = (zoo or MODELS)[model]
    t0 = time.perf_counter()
    est = spec.build()
    if tune:
        gs = GridSearchCV(est, spec.grid, scoring="balanced_accuracy", cv=_inner_cv(seed + fold),
                          n_jobs=1, refit=True)
        gs.fit(X[train_idx], y[train_idx], groups=subject[train_idx])
        best, inner, fitted = gs.best_params_, float(gs.best_score_), gs.best_estimator_
    else:
        fitted = clone(est).set_params(**(params or {}))
        fitted.fit(X[train_idx], y[train_idx])
        best, inner = params or {}, None
    proba = fitted.predict_proba(X[test_idx])[:, 1]
    return FitResult(model, fold, test_idx, proba, best, inner, time.perf_counter() - t0)


def nested_cv(model_names: list[str], inputs: dict[str, np.ndarray], y: np.ndarray, subject: np.ndarray,
              dev_subjects: np.ndarray, n_jobs: int = 10, verbose: int = 5):
    """Run every model on every outer fold. Returns {model: [FitResult per fold]}."""
    folds = outer_folds(dev_subjects)
    # Slowest models first so the worker pool stays busy.
    order = sorted(model_names, key=lambda m: -MODELS[m].cost)
    jobs = []
    for i, (tr_s, te_s) in enumerate(folds):
        tr = np.where(np.isin(subject, tr_s))[0]
        te = np.where(np.isin(subject, te_s))[0]
        for m in order:
            jobs.append(delayed(fit_predict)(m, inputs[MODELS[m].input], y, subject, tr, te, i))
    results = Parallel(n_jobs=n_jobs, verbose=verbose)(jobs)
    by_model: dict[str, list[FitResult]] = {m: [] for m in model_names}
    for r in results:
        by_model[r.model].append(r)
    for m in by_model:
        by_model[m].sort(key=lambda r: r.fold)
    return by_model


def fold_metrics(results: list[FitResult], y: np.ndarray) -> list[dict]:
    return [{"fold": r.fold, "best_params": r.best_params, "inner_balanced_accuracy": r.inner_score,
             "seconds": round(r.seconds, 1), **evaluate(y[r.test_idx], r.proba)} for r in results]


def pooled_oof(results: list[FitResult]):
    idx = np.concatenate([r.test_idx for r in results])
    proba = np.concatenate([r.proba for r in results])
    return idx, proba
