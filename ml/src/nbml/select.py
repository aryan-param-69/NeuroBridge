"""Model selection by a rule fixed BEFORE seeing results (docs/04 section 3.3).

1. Pool each model's out-of-fold predictions (every development subject is predicted exactly once,
   by a model that never saw that subject).
2. Best model = highest pooled balanced accuracy.
3. A model is "statistically tied" with the best if the 95 % subject-level paired-bootstrap interval of
   (best - model) balanced accuracy includes 0. Subjects, not epochs, are resampled: epochs of one
   person are not independent.
4. Among tied models choose, in order: (a) lowest Brier score (best calibrated), (b) most interpretable,
   (c) cheapest on CPU.
"""

import numpy as np

from .models import MODELS


def subject_counts(y: np.ndarray, p1: np.ndarray, subject: np.ndarray):
    """Per-subject [TP, FN, TN, FP] so bootstraps over subjects are fast."""
    pred = (p1 >= 0.5).astype(int)
    subs = np.unique(subject)
    c = np.zeros((len(subs), 4))
    for i, s in enumerate(subs):
        m = subject == s
        c[i] = [((pred == 1) & (y == 1) & m).sum(), ((pred == 0) & (y == 1) & m).sum(),
                ((pred == 0) & (y == 0) & m).sum(), ((pred == 1) & (y == 0) & m).sum()]
    return subs, c


def _bal_acc(c: np.ndarray) -> np.ndarray:
    c = np.atleast_2d(c)
    return 0.5 * (c[:, 0] / np.maximum(c[:, 0] + c[:, 1], 1) + c[:, 2] / np.maximum(c[:, 2] + c[:, 3], 1))


def paired_bootstrap(counts: dict[str, np.ndarray], n_boot: int = 5000, seed: int = 7) -> dict[str, np.ndarray]:
    """Bootstrap distribution of balanced accuracy per model, using the SAME subject resamples."""
    n_sub = next(iter(counts.values())).shape[0]
    rng = np.random.default_rng(seed)
    w = rng.multinomial(n_sub, np.full(n_sub, 1 / n_sub), size=n_boot)  # (B, S) subject weights
    return {m: _bal_acc(w @ c) for m, c in counts.items()}


def choose_model(point: dict[str, float], brier: dict[str, float], boot: dict[str, np.ndarray],
                 candidates: list[str] | None = None) -> dict:
    names = candidates or list(point)
    best = max(names, key=lambda m: point[m])
    table, tied = [], []
    for m in names:
        d = boot[best] - boot[m]
        lo, hi = np.percentile(d, [2.5, 97.5])
        is_tied = m == best or lo <= 0
        if is_tied:
            tied.append(m)
        table.append({"model": m, "balanced_accuracy": point[m], "brier": brier[m],
                      "diff_vs_best": float(point[best] - point[m]),
                      "diff_ci95": [float(lo), float(hi)], "tied_with_best": bool(is_tied)})
    chosen = min(tied, key=lambda m: (brier[m], MODELS[m].interpretability, MODELS[m].cost))
    return {"best_by_accuracy": best, "tied": tied, "chosen": chosen, "table": table,
            "rule": "tied if 95% paired subject-bootstrap CI of (best - model) includes 0; "
                    "then lowest Brier, then most interpretable, then cheapest"}
