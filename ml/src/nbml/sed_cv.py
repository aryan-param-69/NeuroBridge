"""Stage 1: nested leave-one-subject-out evaluation of the six models on the sedation task.

For every person (fold) and model: tune on the other 19 people with an inner 3-fold CV *grouped by person*, refit, then predict
  - the held-out person's awake/moderate epochs (the primary task),
  - the held-out person's MILD-sedation epochs (never used for training: dose-response check),
and compute SHAP values of that fold's model on the held-out epochs. Results go to reports/sedation/oof.npz (+ json summary).
Run:  uv run python -m nbml.sed_cv
"""

import json
import time

import numpy as np
from joblib import Parallel, delayed
from sklearn.model_selection import GridSearchCV, StratifiedGroupKFold

from . import config as C
from . import sed_data as D
from .sed_models import ZOO

OUT = C.REPORT_DIR / "sedation"
PRIMARY_LEVELS = (1, 3, 4)


def task_mask(level: np.ndarray) -> np.ndarray:
    return np.isin(level, PRIMARY_LEVELS)


def labels(level: np.ndarray) -> np.ndarray:
    return (level == 3).astype(int)  # 1 = moderate sedation, 0 = awake (baseline or recovery)


def shap_values(model_name: str, fitted, Xtr: np.ndarray, Xte: np.ndarray) -> np.ndarray | None:
    """SHAP values of the positive class (sedated) on Xte, or None for models without a fast exact explainer."""
    import shap

    if model_name == "svm_rbf":
        return None
    if model_name == "logreg":
        sc, clf = fitted.named_steps["sc"], fitted.named_steps["clf"]
        ex = shap.LinearExplainer(clf, sc.transform(Xtr))
        return np.asarray(ex.shap_values(sc.transform(Xte)))
    ex = shap.TreeExplainer(fitted)
    v = ex.shap_values(Xte)
    v = np.asarray(v)
    return v[..., 1] if v.ndim == 3 else v


def fold_job(model: str, held: int, X: np.ndarray, y: np.ndarray, subject: np.ndarray, level: np.ndarray, seed: int) -> dict:
    t0 = time.perf_counter()
    spec = ZOO[model]
    tm = task_mask(level)
    tr = np.where(tm & (subject != held))[0]
    te = np.where(tm & (subject == held))[0]
    mild = np.where((level == 2) & (subject == held))[0]
    inner = StratifiedGroupKFold(n_splits=C.INNER_FOLDS, shuffle=True, random_state=seed + held)
    gs = GridSearchCV(spec.build(), spec.grid, scoring="balanced_accuracy", cv=inner, n_jobs=1, refit=True)
    gs.fit(X[tr], y[tr], groups=subject[tr])
    fitted = gs.best_estimator_
    sv = shap_values(model, fitted, X[tr], X[te])
    return {"model": model, "held": held, "te": te, "mild": mild, "p_te": fitted.predict_proba(X[te])[:, 1],
            "p_mild": fitted.predict_proba(X[mild])[:, 1] if len(mild) else np.empty(0), "best_params": gs.best_params_, "inner": float(gs.best_score_),
            "shap_abs": None if sv is None else np.abs(sv).mean(0), "shap_signed": None if sv is None else sv.mean(0),
            "seconds": time.perf_counter() - t0}


def main(n_jobs: int = 10):
    OUT.mkdir(parents=True, exist_ok=True)
    d = D.build()
    X, level, subject = d["X"].astype(np.float64), d["level"], d["subject"]
    y = labels(level)
    subs = sorted(set(subject.tolist()))
    jobs = [delayed(fold_job)(m, s, X, y, subject, level, C.SEED) for s in subs for m in sorted(ZOO, key=lambda m: -ZOO[m].cost)]
    t0 = time.time()
    res = Parallel(n_jobs=n_jobs, verbose=5)(jobs)
    n = len(y)
    p_task = {m: np.full(n, np.nan) for m in ZOO}
    p_mild = {m: np.full(n, np.nan) for m in ZOO}
    shap_abs = {m: np.zeros((len(subs), X.shape[1])) for m in ZOO}
    shap_sgn = {m: np.zeros((len(subs), X.shape[1])) for m in ZOO}
    have_shap = {m: False for m in ZOO}
    params = {m: [] for m in ZOO}
    for r in res:
        p_task[r["model"]][r["te"]] = r["p_te"]
        p_mild[r["model"]][r["mild"]] = r["p_mild"]
        params[r["model"]].append({"held": r["held"], "best": r["best_params"], "inner_ba": r["inner"], "seconds": round(r["seconds"], 1)})
        if r["shap_abs"] is not None:
            shap_abs[r["model"]][r["held"]], shap_sgn[r["model"]][r["held"]] = r["shap_abs"], r["shap_signed"]
            have_shap[r["model"]] = True
    np.savez_compressed(OUT / "oof.npz", y=y, level=level, subject=subject, recording=d["recording"],
                        **{f"p_task__{m}": p_task[m] for m in ZOO}, **{f"p_mild__{m}": p_mild[m] for m in ZOO},
                        **{f"shap_abs__{m}": shap_abs[m] for m in ZOO if have_shap[m]},
                        **{f"shap_sgn__{m}": shap_sgn[m] for m in ZOO if have_shap[m]})
    (OUT / "cv_params.json").write_text(json.dumps(params, indent=1), encoding="utf-8")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
