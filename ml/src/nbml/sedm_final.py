"""Stage 3 of the multi-site experiment: fit the pre-specified model on all 40 people, attach calibration / conformal / unfamiliar-EEG statistics,
save `ml/models/sedation_v2` with a model card, and run the observational check on clinical EEG (ds004504).
Run:  uv run python -m nbml.sedm_final
"""

import ast
import hashlib
import json
import platform
import time

import joblib
import mne
import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GridSearchCV, StratifiedGroupKFold

from . import config as C
from . import dementia_data as DM
from . import sed_ood as ood
from . import sed_predict as P
from . import sedm_data as S
from .sed_analyse import ALPHA, to_recording
from .sed_cv import labels, task_mask
from .sed_features import STANDARD_19
from .sed_models import ZOO
from .sedm_cv import OUT
from .store import Store

MODEL_DIR = C.ML_DIR / "models" / "sedation_v2"


def main(featureset: str = "primary"):
    res = json.loads((OUT / f"results_{featureset}.json").read_text(encoding="utf-8"))
    chosen = res["chosen"]
    z = np.load(OUT / f"oof_{featureset}.npz")
    d = S.pooled(featureset)
    X, level, subject, rec = d["X"], d["level"], d["subject"], d["recording"]
    y, tm = labels(level), task_mask(level)
    spec = ZOO[chosen]
    t0 = time.time()
    gs = GridSearchCV(spec.build(), spec.grid, scoring="balanced_accuracy", n_jobs=10, refit=True,
                      cv=StratifiedGroupKFold(n_splits=C.INNER_FOLDS, shuffle=True, random_state=C.SEED))
    gs.fit(X[tm], y[tm], groups=subject[tm])
    model = gs.best_estimator_

    p_oof = np.clip(z[f"p_task__{chosen}"][tm], 1e-4, 1 - 1e-4)
    lg = np.log(p_oof / (1 - p_oof))
    lr = LogisticRegression(C=1e6, max_iter=1000).fit(lg[:, None], y[tm])
    platt = (float(lr.coef_[0, 0]), float(lr.intercept_[0]))
    cal_full = np.full(len(y), np.nan)
    cal_full[np.where(tm)[0]] = 1 / (1 + np.exp(-(platt[0] * lg + platt[1])))
    _, yr, _, pr = to_recording(rec[tm], y[tm], subject[tm], cal_full[tm])
    score = 1 - np.where(yr == 1, pr, 1 - pr)
    n = len(score)
    q = float(np.quantile(score, min(1.0, np.ceil((n + 1) * (1 - ALPHA)) / n), method="higher"))
    ood_model = ood.fit(X[tm])
    bundle = {"version": "sedation_v2", "chosen": chosen, "model": model, "features": list(d["features"]), "platt": platt, "conformal_q": q, "alpha": ALPHA,
              "ood": ood_model, "best_params": gs.best_params_, "classes": P.LABELS}
    if hasattr(model, "named_steps"):
        bundle["background_scaled"] = model.named_steps["sc"].transform(X[tm][np.random.default_rng(0).choice(tm.sum(), 200, replace=False)])
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = MODEL_DIR / "model.joblib"
    joblib.dump(bundle, path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()

    # ---- observational check on clinical EEG: ds004504 (nobody is sedated) ----
    dm = DM.build()
    names = [str(x) for x in dm["features"]]
    keep = [names.index(n) for n in bundle["features"]]
    F = dm["X"][:, keep].astype(np.float64)
    raw_p = model.predict_proba(F)[:, 1]
    cal = 1 / (1 + np.exp(-(platt[0] * np.log(np.clip(raw_p, 1e-4, 1 - 1e-4) / (1 - np.clip(raw_p, 1e-4, 1 - 1e-4))) + platt[1])))
    fl = ood.flags(ood_model, F)
    rows = []
    for line in dm["meta"]:
        m = ast.literal_eval(str(line))
        if "n_rows" not in m:
            continue
        sl = slice(m["first_row"], m["first_row"] + m["n_rows"])
        p = float(cal[sl].mean())
        pset = [k for k, pk in ((0, 1 - p), (1, p)) if 1 - pk <= q]
        rows.append({"id": m["id"], "group": m["group"], "mmse": m["mmse"], "age": m["age"], "n_epochs": m["n_rows"], "p_sedated": p,
                     "verdict": P.LABELS[pset[0]] if len(pset) == 1 else "uncertain", "unfamiliar_fraction": float(fl[sl].mean()),
                     "epochs_rejected": m["n_epochs_total"] - m["n_epochs_kept"], "epochs_total": m["n_epochs_total"]})
    from scipy.stats import spearmanr

    summary = {}
    for g in ("C", "A", "F"):
        r = [x for x in rows if x["group"] == g]
        summary[DM.GROUPS[g]] = {"n": len(r), "mean_p_sedated": float(np.mean([x["p_sedated"] for x in r])), "median_p_sedated": float(np.median([x["p_sedated"] for x in r])),
                                 "frac_called_sedated": float(np.mean([x["p_sedated"] >= 0.5 for x in r])),
                                 "frac_verdict_awake": float(np.mean([x["verdict"] == "awake-like" for x in r])),
                                 "frac_verdict_sedated": float(np.mean([x["verdict"] == "sedated-like" for x in r])),
                                 "frac_verdict_uncertain": float(np.mean([x["verdict"] == "uncertain" for x in r])),
                                 "mean_unfamiliar_fraction": float(np.mean([x["unfamiliar_fraction"] for x in r])),
                                 "frac_recordings_unfamiliar": float(np.mean([x["unfamiliar_fraction"] > 0.25 for x in r]))}
    rho = spearmanr([x["mmse"] for x in rows], [x["p_sedated"] for x in rows])
    res["dementia_check"] = {"n_recordings": len(rows), "by_group": summary, "spearman_mmse": float(rho[0]), "spearman_mmse_p": float(rho[1]),
                             "rejected_epoch_fraction": float(sum(x["epochs_rejected"] for x in rows) / max(1, sum(x["epochs_total"] for x in rows))), "rows": rows}
    res["final"] = {"best_params": gs.best_params_, "platt": platt, "conformal_q": q, "ood_components": ood_model["k"], "fit_seconds": round(time.time() - t0, 1), "model_sha256": sha}
    (OUT / f"results_{featureset}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

    card = {"name": "sedation_level", "version": "sedation_v2", "status": "staging",
            "intended_use": "Research and decision SUPPORT: how much does a resting EEG resemble awake vs propofol-sedated EEG of healthy volunteers (two laboratories).",
            "not_for": ["diagnosis", "detecting consciousness or covert awareness in patients", "measuring recovery", "any clinical decision on its own"],
            "training_data": {"datasets": ["Cambridge (Chennu 2016, doi:10.17863/CAM.68959)", "Oslo (OpenNeuro ds005620)"], "subjects": int(len(np.unique(subject))),
                              "recordings_used": int(len(np.unique(rec[tm]))), "epochs": int(tm.sum()), "population": "healthy adults under propofol, two laboratories"},
            "labels": "sedated = moderate sedation (Cambridge) or rest under propofol before an awakening (Oslo); awake = baseline/recovery/eyes-closed awake; Cambridge mild not used",
            "model": chosen, "features": len(d["features"]), "feature_set": featureset, "channels": STANDARD_19, "epoch_seconds": 10,
            "validation": {"scheme": "pooled leave-one-person-out (40 people) + cross-site tests", "balanced_accuracy": res["models"][chosen]["balanced_accuracy"],
                           "ba_ci95": res["models"][chosen]["ba_ci95"], "auc": res["models"][chosen]["roc_auc"], "permutation_p": res["permutation"]["p_value"],
                           "cross_site": [{k: c[k] for k in ("train", "test", "balanced_accuracy", "ba_ci95")} for c in res["cross_site"] if c["model"] == chosen]},
            "known_limits": ["40 healthy people in two laboratories; wide intervals", "never validated on patients; EEG slowing in dementia can look sedated-like",
                             "other devices/montages may look unfamiliar", "Oslo sedation depth ungraded"],
            "software": {"python": platform.python_version(), "sklearn": sklearn.__version__, "mne": mne.__version__}, "model_sha256": sha}
    (MODEL_DIR / "model_card.json").write_text(json.dumps(card, indent=1), encoding="utf-8")
    st = Store()
    if st.ok:
        st.upsert("model_registry", "sedation_level/sedation_v2", {"name": "sedation_level", "version": "sedation_v2", "status": "staging", **card})
    print("saved", path, sha[:12], json.dumps(summary)[:300])


if __name__ == "__main__":
    main()
