"""Stage 3: fit the chosen model on all 20 people, attach calibration / conformal / unfamiliar-EEG statistics, save it with a model card,
and run an external sanity check on a different dataset (EEGMMIDB: 109 AWAKE healthy people, other device and site).

Run:  uv run python -m nbml.sed_final
"""

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
from . import sed_data as D
from . import sed_predict as P
from .sed_analyse import ALPHA, ood_fit, to_recording
from .sed_cv import OUT, labels, task_mask
from .sed_features import STANDARD_19
from .sed_models import ZOO
from .store import Store

MODEL_DIR = C.ML_DIR / "models" / "sedation_v1"
mne.set_log_level("ERROR")


def eegmmidb_epochs(subject: int, run: int):
    f = C.DATASET_DIR / f"S{subject:03d}" / f"S{subject:03d}R{run:02d}.edf"
    raw = mne.io.read_raw_edf(f, preload=True, verbose=False)
    raw.rename_channels({c: c.strip(".").strip() for c in raw.ch_names})
    raw.pick(STANDARD_19)
    raw.reorder_channels(STANDARD_19)
    raw.filter(0.5, 45.0, method="iir", iir_params={"order": 4, "ftype": "butter", "output": "sos"}, phase="zero", verbose=False)
    x = raw.get_data() * 1e6
    x -= x.mean(axis=0, keepdims=True)  # average reference over the 19 channels
    L = int(10 * raw.info["sfreq"])
    n = x.shape[1] // L
    return np.stack([x[:, i * L:(i + 1) * L] for i in range(n)]), float(raw.info["sfreq"])


def main():
    res = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    chosen = res["chosen"]
    z = np.load(OUT / "oof.npz")
    d = D.build()
    X, level, subject, rec = d["X"].astype(np.float64), d["level"], d["subject"], d["recording"]
    y, tm = labels(level), task_mask(level)
    spec = ZOO[chosen]

    t0 = time.time()
    gs = GridSearchCV(spec.build(), spec.grid, scoring="balanced_accuracy", n_jobs=10, refit=True,
                      cv=StratifiedGroupKFold(n_splits=C.INNER_FOLDS, shuffle=True, random_state=C.SEED))
    gs.fit(X[tm], y[tm], groups=subject[tm])
    model = gs.best_estimator_

    # calibration (Platt) on every person's out-of-fold epoch scores
    p_oof = z[f"p_task__{chosen}"]
    lg = np.log(np.clip(p_oof[tm], 1e-4, 1 - 1e-4) / (1 - np.clip(p_oof[tm], 1e-4, 1 - 1e-4)))
    lr = LogisticRegression(C=1e6, max_iter=1000).fit(lg[:, None], y[tm])
    platt = (float(lr.coef_[0, 0]), float(lr.intercept_[0]))
    cal = 1 / (1 + np.exp(-(platt[0] * lg + platt[1])))
    # conformal threshold from the recording-level calibrated out-of-fold scores of all 20 people
    cal_full = np.full(len(y), np.nan)
    cal_full[np.where(tm)[0]] = cal
    _, yr, _, pr = to_recording(rec[tm], y[tm], subject[tm], cal_full[tm])
    score = 1 - np.where(yr == 1, pr, 1 - pr)
    n = len(score)
    q = float(np.quantile(score, min(1.0, np.ceil((n + 1) * (1 - ALPHA)) / n), method="higher"))
    ood = ood_fit(X[tm])
    bundle = {"version": "sedation_v1", "chosen": chosen, "model": model, "features": list(d["features"]), "platt": platt, "conformal_q": q,
              "alpha": ALPHA, "ood": ood, "best_params": gs.best_params_, "classes": P.LABELS}
    if hasattr(model, "named_steps"):
        bundle["background_scaled"] = model.named_steps["sc"].transform(X[tm][np.random.default_rng(0).choice(tm.sum(), 200, replace=False)])
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = MODEL_DIR / "model.joblib"
    joblib.dump(bundle, path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()

    # ---- external sanity check: awake healthy people, different device ----
    rows = []
    for s in range(1, 110):
        for run, cond in ((1, "eyes_open"), (2, "eyes_closed")):
            try:
                ep, fs = eegmmidb_epochs(s, run)
            except Exception:  # missing file: skip, counted below
                continue
            out = P.predict_epochs(bundle, ep, fs)
            rows.append({"subject": s, "condition": cond, "p_sedated": out["p_sedated"], "unfamiliar_fraction": out["unfamiliar_fraction"],
                         "verdict": out["verdict"]})
    ext_p = np.array([r["p_sedated"] for r in rows])
    ext = {"n_recordings": len(rows), "truth": "all awake healthy volunteers (eyes open / eyes closed rest)",
           "fraction_called_sedated": float(np.mean(ext_p >= 0.5)), "fraction_verdict_awake": float(np.mean([r["verdict"] == "awake-like" for r in rows])),
           "fraction_verdict_sedated": float(np.mean([r["verdict"] == "sedated-like" for r in rows])),
           "fraction_verdict_uncertain": float(np.mean([r["verdict"] == "uncertain" for r in rows])),
           "mean_p_sedated": float(ext_p.mean()), "unfamiliar_recordings": float(np.mean([r["unfamiliar_fraction"] > 0.25 for r in rows])),
           "mean_unfamiliar_epoch_fraction": float(np.mean([r["unfamiliar_fraction"] for r in rows])),
           "by_condition": {c: {"mean_p_sedated": float(np.mean([r["p_sedated"] for r in rows if r["condition"] == c])),
                                "frac_called_sedated": float(np.mean([r["p_sedated"] >= 0.5 for r in rows if r["condition"] == c]))}
                            for c in ("eyes_open", "eyes_closed")}}
    res["external_eegmmidb"] = ext
    res["final"] = {"best_params": gs.best_params_, "platt": platt, "conformal_q": q, "ood_threshold_t2": ood["thr"], "ood_threshold_spe": ood["thr_spe"], "ood_components": ood["k"],
                    "fit_seconds": round(time.time() - t0, 1), "model_sha256": sha}
    (OUT / "results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

    card = {
        "name": "sedation_level", "version": "sedation_v1", "status": "staging",
        "intended_use": "Research and decision SUPPORT: how much does a resting EEG resemble awake vs moderately propofol-sedated EEG of healthy volunteers.",
        "not_for": ["diagnosis", "detecting consciousness or covert awareness in patients", "measuring recovery", "any clinical decision on its own"],
        "training_data": {"dataset": "Chennu et al. 2016, Cambridge repository, DOI 10.17863/CAM.68959, CC BY 2.0 UK", "subjects": 20,
                          "recordings_used": int(len(np.unique(rec[tm]))), "epochs": int(tm.sum()), "population": "healthy adults under propofol"},
        "labels": "sedated = moderate sedation; awake = baseline or recovery; mild sedation not used for training",
        "model": chosen, "features": len(d["features"]), "channels": STANDARD_19, "epoch_seconds": 10,
        "validation": {"scheme": "leave-one-subject-out, nested", "balanced_accuracy": res["models"][chosen]["balanced_accuracy"],
                       "ba_ci95": res["models"][chosen]["ba_ci95"], "auc": res["models"][chosen]["roc_auc"],
                       "permutation_p": res["permutation"]["p_value"]},
        "known_limits": ["20 healthy people only; wide intervals", "never validated on patients or on any clinical EEG",
                         "healthy propofol sedation differs from brain injury", "other devices/montages may look unfamiliar to it"],
        "software": {"python": platform.python_version(), "sklearn": sklearn.__version__, "mne": mne.__version__}, "model_sha256": sha,
    }
    (MODEL_DIR / "model_card.json").write_text(json.dumps(card, indent=1), encoding="utf-8")
    st = Store()
    if st.ok:
        st.upsert("model_registry", "sedation_level/sedation_v1", {"name": "sedation_level", "version": "sedation_v1", "status": "staging", **card})
    print("saved", path, "sha256", sha[:12], "| external:", {k: ext[k] for k in ("n_recordings", "fraction_called_sedated", "unfamiliar_recordings")})


if __name__ == "__main__":
    main()
