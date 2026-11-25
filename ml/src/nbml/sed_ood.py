"""Unfamiliar-EEG detector: PCA novelty detection on the standardised feature vector of each epoch.

Two standard statistics are used, because each one alone has a blind spot:
  - Hotelling T^2: distance inside the subspace of the main components (unusual *combinations* of the usual features);
  - SPE (squared prediction error): how much of the vector lies OUTSIDE that subspace (unusual *new directions*).
An epoch is flagged if either exceeds the 99th percentile seen in training.
"""

import numpy as np


def fit(Xtr: np.ndarray, var: float = 0.95, pct: float = 99.0) -> dict:
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    Z = (Xtr - mu) / sd
    _, S, Vt = np.linalg.svd(Z, full_matrices=False)
    ev = S**2 / (len(Z) - 1)
    k = int(np.searchsorted(np.cumsum(ev) / ev.sum(), var) + 1)
    comp, lam = Vt[:k], ev[:k]
    t2, spe = _stats({"mu": mu, "sd": sd, "comp": comp, "lam": lam}, Xtr)
    return {"mu": mu, "sd": sd, "comp": comp, "lam": lam, "k": k, "thr": float(np.percentile(t2, pct)),
            "thr_spe": float(np.percentile(spe, pct))}


def _stats(m: dict, X: np.ndarray):
    Z = (X - m["mu"]) / m["sd"]
    sc = Z @ m["comp"].T
    t2 = (sc**2 / m["lam"]).sum(1)
    spe = ((Z - sc @ m["comp"]) ** 2).sum(1)
    return t2, spe


def flags(m: dict, X: np.ndarray) -> np.ndarray:
    """True for an unfamiliar epoch. A feature vector that is not finite (e.g. from a dead channel) can never be familiar."""
    bad = ~np.isfinite(X).all(axis=1)
    t2, spe = _stats(m, np.where(np.isfinite(X), X, 0.0))
    return (t2 > m["thr"]) | (spe > m["thr_spe"]) | bad
