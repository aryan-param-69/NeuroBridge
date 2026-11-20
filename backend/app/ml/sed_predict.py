# VENDORED from ml/src/nbml (parity is tested in tests/test_ai_sedation.py). Change both copies together.
"""Inference for the sedation-level model: epochs of a 19-channel recording in, an honest answer out.

The answer is NOT a diagnosis. It says how much a recording resembles awake or moderately propofol-sedated EEG *of healthy volunteers*,
how uncertain that is, and whether the recording looks unlike anything the model was trained on.
"""

import numpy as np

from . import sed_ood
from .sed_features import STANDARD_19, extract, plain_name

LABELS = {0: "awake-like", 1: "sedated-like"}


def _logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def predict_epochs(bundle: dict, X19: np.ndarray, fs: float) -> dict:
    """X19: (n_epochs, 19 channels in STANDARD_19 order, samples), microvolts, 0.5-45 Hz filtered, average-referenced, 10 s epochs."""
    F, names = extract(X19, fs)
    want = list(bundle["features"])  # a model may use a subset of the extracted features (e.g. without amplitude-dependent ones)
    missing = [n for n in want if n not in names]
    if missing:
        raise ValueError(f"feature set does not match the model: missing {missing[:3]}")
    names = list(names)
    F = F[:, [names.index(n) for n in want]]
    names = want
    raw = bundle["model"].predict_proba(F)[:, 1]
    a, b = bundle["platt"]
    cal = 1 / (1 + np.exp(-(a * _logit(raw) + b)))
    fl = sed_ood.flags(bundle["ood"], F)
    p = float(cal.mean())
    q = bundle["conformal_q"]
    pset = [k for k, pk in ((0, 1 - p), (1, p)) if 1 - pk <= q]
    flagged = float(fl.mean())
    return {
        "p_sedated": p, "p_epoch": cal.tolist(), "prediction_set": [LABELS[k] for k in pset],
        "verdict": LABELS[pset[0]] if len(pset) == 1 else "uncertain",
        "coverage_target": 1 - bundle["alpha"], "n_epochs": int(len(cal)),
        "unfamiliar_fraction": flagged, "unfamiliar": bool(flagged > 0.25),
        "epoch_spread_sd": float(cal.std(ddof=1)) if len(cal) > 1 else None,
        "feature_matrix": F, "feature_names": names,
    }


def explain(bundle: dict, F: np.ndarray, top: int = 6) -> list[dict]:
    """SHAP explanation of the recording-mean prediction (tree and linear models), plain-language."""
    import shap

    est = bundle["model"]
    if hasattr(est, "named_steps"):
        sc, clf = est.named_steps["sc"], est.named_steps["clf"]
        ex = shap.LinearExplainer(clf, bundle["background_scaled"])
        sv = np.asarray(ex.shap_values(sc.transform(F)))
    else:
        v = np.asarray(shap.TreeExplainer(est).shap_values(F))
        sv = v[..., 1] if v.ndim == 3 else v
    mean = sv.mean(0)
    order = np.argsort(np.abs(mean))[::-1][:top]
    return [{"feature": bundle["features"][i], "plain": plain_name(bundle["features"][i]), "shap": float(mean[i]),
             "pushes_towards": "sedated-like" if mean[i] > 0 else "awake-like"} for i in order]


__all__ = ["predict_epochs", "explain", "STANDARD_19", "LABELS"]
