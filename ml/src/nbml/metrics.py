import numpy as np
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


def expected_calibration_error(y: np.ndarray, p1: np.ndarray, bins: int = 10) -> float:
    """ECE of the positive-class probability: weighted gap between confidence and accuracy per bin."""
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p1, edges[1:-1]), 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(p1[m].mean() - y[m].mean())
    return float(ece)


def calibration_curve(y: np.ndarray, p1: np.ndarray, bins: int = 10) -> dict:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p1, edges[1:-1]), 0, bins - 1)
    out = {"mean_predicted": [], "fraction_positive": [], "count": []}
    for b in range(bins):
        m = idx == b
        if m.any():
            out["mean_predicted"].append(float(p1[m].mean()))
            out["fraction_positive"].append(float(y[m].mean()))
            out["count"].append(int(m.sum()))
    return out


def evaluate(y: np.ndarray, p1: np.ndarray) -> dict:
    """All headline metrics from true labels and positive-class probabilities."""
    pred = (p1 >= 0.5).astype(int)
    p1c = np.clip(p1, 1e-6, 1 - 1e-6)
    return {
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "accuracy": float(accuracy_score(y, pred)),
        "precision_macro": float(precision_score(y, pred, average="macro", zero_division=0)),
        "recall_macro": float(recall_score(y, pred, average="macro", zero_division=0)),
        "f1_macro": float(f1_score(y, pred, average="macro", zero_division=0)),
        "roc_auc": float(roc_auc_score(y, p1)) if len(np.unique(y)) == 2 else float("nan"),
        "brier": float(brier_score_loss(y, p1)),
        "log_loss": float(log_loss(y, np.stack([1 - p1c, p1c], axis=1), labels=[0, 1])),
        "ece": expected_calibration_error(y, p1),
        "confusion_matrix": confusion_matrix(y, pred, labels=[0, 1]).tolist(),
        "n": len(y),
    }


def chance_upper_bound(n: int, alpha: float = 0.05) -> float:
    """Accuracy a no-skill classifier would exceed with probability alpha on n balanced trials
    (binomial, Combrisson & Jerbi 2015). Accuracies below this are not distinguishable from chance."""
    from scipy.stats import binom

    return float(binom.ppf(1 - alpha, n, 0.5) / n)


def per_subject_accuracy(y: np.ndarray, p1: np.ndarray, subject: np.ndarray) -> dict[int, float]:
    pred = (p1 >= 0.5).astype(int)
    return {int(s): float((pred[subject == s] == y[subject == s]).mean()) for s in np.unique(subject)}
