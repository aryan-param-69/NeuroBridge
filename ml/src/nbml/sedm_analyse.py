"""Stage 2 of the multi-site experiment (protocol: reports/sedation_multisite/PRESPEC.md).

Reads the pooled leave-one-person-out predictions and computes: model comparison and pre-specified selection (pooled and per site), cross-site
tests (train on one site, test on the other), and for the chosen model the permutation test, calibration, conformal sets, SHAP, Cambridge
dose-response and the unfamiliar-EEG detector within and across sites.
Run:  uv run python -m nbml.sedm_analyse [primary|full]
"""

import json
import sys

import numpy as np
from joblib import Parallel, delayed
from scipy.stats import spearmanr
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.model_selection import GridSearchCV, StratifiedGroupKFold

from . import config as C
from . import sed_ood as ood
from . import sedm_data as S
from .metrics import calibration_curve, evaluate
from .sed_analyse import ALPHA, N_PERM, ci_bootstrap, conformal_loso, logit, platt_loso, to_recording
from .sed_cv import task_mask
from .sed_features import plain_name
from .sed_models import ZOO
from .select import choose_model, paired_bootstrap, subject_counts

OUT = C.REPORT_DIR / "sedation_multisite"
SITES = {0: "Cambridge", 1: "Oslo"}


def site_metrics(yr, pr, sr, site_r, which):
    m = site_r == which
    if m.sum() == 0 or len(np.unique(yr[m])) < 2:
        return None
    ba_ci, auc_ci = ci_bootstrap(yr[m], pr[m], sr[m])
    return {"n_recordings": int(m.sum()), "n_people": int(len(np.unique(sr[m]))), "balanced_accuracy": float(balanced_accuracy_score(yr[m], (pr[m] >= 0.5).astype(int))),
            "ba_ci95": ba_ci, "roc_auc": float(roc_auc_score(yr[m], pr[m])), "auc_ci95": auc_ci, "frac_called_sedated": float((pr[m] >= 0.5).mean())}


def cross_site_job(model, train_site, X, y, subject, level, site, rec, seed):
    tm = task_mask(level)
    tr = np.where(tm & (site == train_site))[0]
    te = np.where(tm & (site != train_site))[0]
    spec = ZOO[model]
    gs = GridSearchCV(spec.build(), spec.grid, scoring="balanced_accuracy", cv=StratifiedGroupKFold(n_splits=C.INNER_FOLDS, shuffle=True, random_state=seed),
                      n_jobs=1, refit=True)
    gs.fit(X[tr], y[tr], groups=subject[tr])
    p = gs.best_estimator_.predict_proba(X[te])[:, 1]
    ids, yr, sr, pr = to_recording(rec[te], y[te], subject[te], p)
    ba_ci, auc_ci = ci_bootstrap(yr, pr, sr)
    return {"model": model, "train": SITES[train_site], "test": SITES[1 - train_site], "n_recordings": int(len(yr)), "n_people": int(len(np.unique(sr))),
            "balanced_accuracy": float(balanced_accuracy_score(yr, (pr >= 0.5).astype(int))), "ba_ci95": ba_ci, "roc_auc": float(roc_auc_score(yr, pr)),
            "auc_ci95": auc_ci, "frac_called_sedated": float((pr >= 0.5).mean()), "frac_called_sedated_if_awake": float((pr[yr == 0] >= 0.5).mean()),
            "frac_called_sedated_if_sedated": float((pr[yr == 1] >= 0.5).mean()), "best_params": gs.best_params_}


def perm_job(model, params, X, y, subject, level, rec, perm_seed):
    rng = np.random.default_rng(perm_seed)
    yp, tm = y.copy(), task_mask(level)
    for s in np.unique(subject):
        ids = np.unique(rec[tm & (subject == s)])
        lab = np.array([y[rec == i][0] for i in ids])
        for i, lab_i in zip(ids, rng.permutation(lab), strict=True):
            yp[rec == i] = lab_i
    p = np.full(len(y), np.nan)
    for s in np.unique(subject):
        tr, te = np.where(tm & (subject != s))[0], np.where(tm & (subject == s))[0]
        p[te] = ZOO[model].build().set_params(**params).fit(X[tr], yp[tr]).predict_proba(X[te])[:, 1]
    _, yr, _, pr = to_recording(rec[tm], yp[tm], subject[tm], p[tm])
    return float(0.5 * ((pr[yr == 1] >= 0.5).mean() + (pr[yr == 0] < 0.5).mean()))


def main(featureset: str = "primary", n_jobs: int = 10):
    z = np.load(OUT / f"oof_{featureset}.npz")
    y, level, subject, rec, site = z["y"], z["level"], z["subject"], z["recording"], z["site"]
    tm = task_mask(level)
    d = S.pooled(featureset)
    X, names = d["X"], d["features"]
    res: dict = {"featureset": featureset, "n_features": len(names), "n_people": int(len(np.unique(subject))),
                 "n_recordings_primary": int(len(np.unique(rec[tm]))), "alpha": ALPHA}

    # ---- 1. pooled comparison, per-site breakdown, pre-specified selection ----
    per, counts, point, brier, per_site = {}, {}, {}, {}, {}
    for m in ZOO:
        p = z[f"p_task__{m}"]
        ids, yr, sr, pr = to_recording(rec[tm], y[tm], subject[tm], p[tm])
        site_r = np.array([site[rec == i][0] for i in ids])
        ev = evaluate(yr, pr)
        ba_ci, auc_ci = ci_bootstrap(yr, pr, sr)
        per[m] = {**ev, "ba_ci95": ba_ci, "auc_ci95": auc_ci}
        per_site[m] = {SITES[k]: site_metrics(yr, pr, sr, site_r, k) for k in SITES}
        _, c = subject_counts(yr, pr, sr)
        counts[m], point[m], brier[m] = c, ev["balanced_accuracy"], ev["brier"]
    sel = choose_model(point, brier, paired_bootstrap(counts))
    chosen = sel["chosen"]
    res.update(models=per, per_site=per_site, selection=sel, chosen=chosen)
    print("chosen:", chosen, {m: round(point[m], 3) for m in point})

    # ---- 2. cross-site tests, all six models, both directions ----
    cs = Parallel(n_jobs=n_jobs, verbose=2)(delayed(cross_site_job)(m, ts, X, y, subject, level, site, rec, C.SEED) for m in ZOO for ts in (0, 1))
    res["cross_site"] = cs

    # ---- 3. chosen model: calibration, conformal, dose-response, SHAP, OOD ----
    p_ep = z[f"p_task__{chosen}"]
    cal_ep, _ = platt_loso(p_ep, y, subject, tm)
    ids, yr, sr, pr_raw = to_recording(rec[tm], y[tm], subject[tm], p_ep[tm])
    _, _, _, pr = to_recording(rec[tm], y[tm], subject[tm], cal_ep[tm])
    res["calibration"] = {"raw": {k: evaluate(yr, pr_raw)[k] for k in ("brier", "ece")}, "platt": {k: evaluate(yr, pr)[k] for k in ("brier", "ece", "balanced_accuracy")},
                          "curve_platt": calibration_curve(yr, pr, bins=5), "curve_raw": calibration_curve(yr, pr_raw, bins=5)}
    sets = conformal_loso(pr, yr, sr)
    cover = np.array([int(t) in s for t, s in zip(yr, sets, strict=True)])
    single = np.array([len(s) == 1 for s in sets])
    res["conformal"] = {"coverage": float(cover.mean()), "target": 1 - ALPHA, "n": int(len(sets)), "frac_single": float(single.mean()),
                        "frac_uncertain": float((~single).mean()),
                        "acc_when_single": float(np.mean([next(iter(s)) == t for t, s in zip(yr, sets, strict=True) if len(s) == 1])) if single.any() else None}
    pm = z[f"p_mild__{chosen}"]
    cam = (level == 2) & (site == 0)
    mild_cal = np.full_like(pm, np.nan)
    for s in np.unique(subject[cam]):
        fit = tm & (subject != s) & np.isfinite(p_ep)
        from sklearn.linear_model import LogisticRegression

        lr = LogisticRegression(C=1e6, max_iter=1000).fit(logit(p_ep[fit])[:, None], y[fit])
        sel_m = cam & (subject == s)
        mild_cal[sel_m] = lr.predict_proba(logit(pm[sel_m])[:, None])[:, 1]
    all_cal = np.where(cam, mild_cal, cal_ep)
    by_level, rec_cal = {}, {}
    for L in (1, 2, 3, 4):
        ids_l = np.unique(rec[(level == L) & (site == 0)])
        vals = np.array([np.nanmean(all_cal[rec == i]) for i in ids_l])
        rec_cal.update(dict(zip(ids_l.tolist(), vals.tolist(), strict=True)))
        by_level[L] = {"mean": float(vals.mean()), "sd": float(vals.std(ddof=1)), "n": int(len(vals))}
    rid = np.array(sorted(rec_cal))
    pv = np.array([rec_cal[i] for i in rid])
    pr_prop = np.array([d["propofol"][rec == i][0] for i in rid])
    res["dose_response_cambridge"] = {"by_level": by_level, "spearman_propofol": float(spearmanr(pv, pr_prop)[0])}
    oslo_awake = np.array([np.nanmean(all_cal[rec == i]) for i in np.unique(rec[(site == 1) & (level == 1)])])
    oslo_sed = np.array([np.nanmean(all_cal[rec == i]) for i in np.unique(rec[(site == 1) & (level == 3)])])
    res["oslo_levels"] = {"awake_mean": float(oslo_awake.mean()), "sedated_mean": float(oslo_sed.mean()), "n_awake": int(len(oslo_awake)), "n_sedated": int(len(oslo_sed))}

    if f"shap_abs__{chosen}" in z.files:
        sa, sg = z[f"shap_abs__{chosen}"].mean(0), z[f"shap_sgn__{chosen}"]
        Xs = (X - X[tm].mean(0)) / (X[tm].std(0) + 1e-9)
        top = []
        for i in np.argsort(sa)[::-1][:15]:
            top.append({"feature": names[i], "plain": plain_name(names[i]), "mean_abs_shap": float(sa[i]),
                        "sedated_minus_awake_z": float(Xs[tm & (y == 1), i].mean() - Xs[tm & (y == 0), i].mean()),
                        "stable_in_folds": float(np.mean(np.sign(sg[:, i]) == np.sign(sg[:, i].mean())))})
        res["shap"] = {"available": True, "top": top}
    else:
        res["shap"] = {"available": False}

    flagged = []
    for s in np.unique(subject):
        m_ = ood.fit(X[tm & (subject != s)])
        for L in (1, 3):
            sel_e = (subject == s) & (level == L)
            if sel_e.any():
                flagged.append(float(ood.flags(m_, X[sel_e]).mean()))
    res["ood_within"] = {"mean_flagged_epoch_fraction": float(np.mean(flagged)), "recordings_over_25pct": float(np.mean(np.array(flagged) > 0.25)), "n": len(flagged)}
    res["ood_across_sites"] = {}
    for tr_site in (0, 1):
        m_ = ood.fit(X[tm & (site == tr_site)])
        res["ood_across_sites"][f"train_{SITES[tr_site]}_flags_{SITES[1 - tr_site]}"] = float(ood.flags(m_, X[tm & (site != tr_site)]).mean())

    # ---- 4. permutation test (chosen model, fixed hyper-parameters) ----
    best: dict = {}
    for row in json.loads((OUT / f"cv_params_{featureset}.json").read_text())[chosen]:
        k = json.dumps(row["best"], sort_keys=True)
        best[k] = best.get(k, 0) + 1
    params = json.loads(max(best, key=best.get))
    null = Parallel(n_jobs=n_jobs, verbose=2)(delayed(perm_job)(chosen, params, X, y, subject, level, rec, 5000 + k) for k in range(N_PERM))
    obs = per[chosen]["balanced_accuracy"]
    res["permutation"] = {"n": N_PERM, "params": params, "observed": obs, "null_mean": float(np.mean(null)), "null_p95": float(np.percentile(null, 95)),
                          "p_value": float((1 + np.sum(np.array(null) >= obs)) / (N_PERM + 1)), "null": [float(v) for v in null]}
    res["feature_names"] = names
    (OUT / f"results_{featureset}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    np.savez_compressed(OUT / f"cal_{featureset}.npz", cal_ep=cal_ep, all_cal=all_cal)
    print("saved", featureset)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "primary")
