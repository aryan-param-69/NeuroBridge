"""Stage 2: everything computed from the leave-one-subject-out predictions (reports/sedation/oof.npz).

Order follows reports/sedation/PRESPEC.md: model comparison + pre-specified selection, then for the chosen model: permutation test,
Platt calibration, conformal prediction sets, mild-sedation dose-response, SHAP ranking, unfamiliar-EEG (OOD) detector.
Run:  uv run python -m nbml.sed_analyse
"""

import json

import numpy as np
from joblib import Parallel, delayed
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression

from . import sed_data as D
from . import sed_ood as ood
from .metrics import calibration_curve, evaluate
from .sed_cv import OUT, labels, task_mask
from .sed_features import plain_name
from .sed_models import ZOO
from .select import choose_model, paired_bootstrap, subject_counts

ALPHA = 0.2  # conformal miscoverage (80 % coverage)
N_PERM = 100


def logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def to_recording(rec: np.ndarray, y: np.ndarray, subject: np.ndarray, p: np.ndarray):
    """Mean epoch probability per recording. Returns recording ids, y, subject, p."""
    ids = np.unique(rec)
    yy = np.array([y[rec == i][0] for i in ids])
    ss = np.array([subject[rec == i][0] for i in ids])
    pp = np.array([np.nanmean(p[rec == i]) for i in ids])
    return ids, yy, ss, pp


def ci_bootstrap(y, p, subj, n_boot=4000, seed=11):
    """95 % CI over subjects (people are resampled, not recordings) for balanced accuracy and AUC."""
    from sklearn.metrics import balanced_accuracy_score, roc_auc_score

    rng = np.random.default_rng(seed)
    subs = np.unique(subj)
    ba, auc = [], []
    for _ in range(n_boot):
        pick = rng.choice(subs, len(subs))
        m = np.concatenate([np.where(subj == s)[0] for s in pick])
        if len(np.unique(y[m])) < 2:
            continue
        ba.append(balanced_accuracy_score(y[m], (p[m] >= 0.5).astype(int)))
        auc.append(roc_auc_score(y[m], p[m]))
    return [float(x) for x in np.percentile(ba, [2.5, 97.5])], [float(x) for x in np.percentile(auc, [2.5, 97.5])]


def platt_loso(p_epoch: np.ndarray, y: np.ndarray, subject: np.ndarray, mask: np.ndarray):
    """Calibrated epoch probabilities where the calibrator for person s is fitted on the OTHER people's out-of-fold scores.
    (Approximation: those scores came from models that had seen person s; the leak is second-order and stated in the report.)"""
    out = np.full_like(p_epoch, np.nan)
    params = {}
    for s in np.unique(subject[mask]):
        fit = mask & (subject != s) & np.isfinite(p_epoch)
        lr = LogisticRegression(C=1e6, max_iter=1000).fit(logit(p_epoch[fit])[:, None], y[fit])
        params[int(s)] = (float(lr.coef_[0, 0]), float(lr.intercept_[0]))
        out[subject == s] = lr.predict_proba(logit(np.nan_to_num(p_epoch[subject == s], nan=0.5))[:, None])[:, 1]
    return out, params


def conformal_loso(p_rec_cal: np.ndarray, y_rec: np.ndarray, subj_rec: np.ndarray, alpha=ALPHA):
    """Split-conformal sets for each recording, calibrated on the other people's recordings."""
    sets = []
    for i in range(len(y_rec)):
        cal = subj_rec != subj_rec[i]
        score = 1 - np.where(y_rec[cal] == 1, p_rec_cal[cal], 1 - p_rec_cal[cal])
        n = cal.sum()
        q = np.quantile(score, min(1.0, np.ceil((n + 1) * (1 - alpha)) / n), method="higher")
        sets.append({k for k, pk in ((0, 1 - p_rec_cal[i]), (1, p_rec_cal[i])) if 1 - pk <= q})
    return sets


def perm_job(model, params, X, y, subject, level, perm_seed):
    """One label permutation: labels shuffled among each person's own 3 recordings; full LOSO with fixed hyper-parameters."""
    rec = D.build()["recording"]
    rng = np.random.default_rng(perm_seed)
    yp = y.copy()
    tm = task_mask(level)
    for s in np.unique(subject):
        ids = np.unique(rec[tm & (subject == s)])
        lab = np.array([y[rec == i][0] for i in ids])
        sh = rng.permutation(lab)
        for i, lab_i in zip(ids, sh, strict=True):
            yp[rec == i] = lab_i
    p = np.full(len(y), np.nan)
    for s in np.unique(subject):
        tr, te = np.where(tm & (subject != s))[0], np.where(tm & (subject == s))[0]
        est = ZOO[model].build().set_params(**params).fit(X[tr], yp[tr])
        p[te] = est.predict_proba(X[te])[:, 1]
    ids, yr, _, pr = to_recording(rec[tm], yp[tm], subject[tm], p[tm])
    return float(0.5 * ((pr[yr == 1] >= 0.5).mean() + (pr[yr == 0] < 0.5).mean()))


ood_fit = ood.fit  # kept for callers; see sed_ood


def main(n_jobs: int = 10):
    z = np.load(OUT / "oof.npz")
    y, level, subject, rec = z["y"], z["level"], z["subject"], z["recording"]
    tm = task_mask(level)
    d = D.build()
    X, names = d["X"].astype(np.float64), list(d["features"])
    res: dict = {"n_subjects": int(len(np.unique(subject))), "n_recordings_primary": int(len(np.unique(rec[tm]))), "alpha": ALPHA}

    # ---- 1. comparison and pre-specified selection (recording level) ----
    per, counts, point, brier, epoch_level = {}, {}, {}, {}, {}
    for m in ZOO:
        p = z[f"p_task__{m}"]
        ids, yr, sr, pr = to_recording(rec[tm], y[tm], subject[tm], p[tm])
        ev = evaluate(yr, pr)
        ba_ci, auc_ci = ci_bootstrap(yr, pr, sr)
        per[m] = {**ev, "ba_ci95": ba_ci, "auc_ci95": auc_ci}
        epoch_level[m] = {k: v for k, v in evaluate(y[tm], p[tm]).items() if k in ("balanced_accuracy", "roc_auc", "brier", "ece")}
        _, c = subject_counts(yr, pr, sr)
        counts[m], point[m], brier[m] = c, ev["balanced_accuracy"], ev["brier"]
    boot = paired_bootstrap(counts)
    sel = choose_model(point, brier, boot)
    res.update(models=per, epoch_level=epoch_level, selection=sel)
    chosen = sel["chosen"]
    print("chosen:", chosen, {m: round(point[m], 3) for m in point})

    # ---- 2. chosen model: calibration, conformal, dose-response ----
    p_ep = z[f"p_task__{chosen}"]
    cal_ep, _ = platt_loso(p_ep, y, subject, tm)
    ids, yr, sr, pr_raw = to_recording(rec[tm], y[tm], subject[tm], p_ep[tm])
    _, _, _, pr = to_recording(rec[tm], y[tm], subject[tm], cal_ep[tm])
    res["calibration"] = {"raw": {k: evaluate(yr, pr_raw)[k] for k in ("brier", "ece", "log_loss")},
                          "platt": {k: evaluate(yr, pr)[k] for k in ("brier", "ece", "log_loss", "balanced_accuracy", "roc_auc")},
                          "curve_platt": calibration_curve(yr, pr, bins=5), "curve_raw": calibration_curve(yr, pr_raw, bins=5)}
    sets = conformal_loso(pr, yr, sr)
    covered = np.array([int(t) in s for t, s in zip(yr, sets, strict=True)])
    single = np.array([len(s) == 1 for s in sets])
    res["conformal"] = {"coverage": float(covered.mean()), "target": 1 - ALPHA, "n": int(len(sets)),
                        "frac_single": float(single.mean()), "frac_both": float(np.mean([len(s) == 2 for s in sets])),
                        "frac_empty": float(np.mean([len(s) == 0 for s in sets])),
                        "acc_when_single": float(np.mean([next(iter(s)) == t for t, s in zip(yr, sets, strict=True) if len(s) == 1])) if single.any() else None}

    pm = z[f"p_mild__{chosen}"]
    # Platt for mild epochs: reuse each person's calibrator (fitted on others) by recomputing the same fit
    mild_cal = np.full_like(pm, np.nan)
    for s in np.unique(subject):
        fit = tm & (subject != s) & np.isfinite(p_ep)
        lr = LogisticRegression(C=1e6, max_iter=1000).fit(logit(p_ep[fit])[:, None], y[fit])
        sel_m = (subject == s) & (level == 2)
        mild_cal[sel_m] = lr.predict_proba(logit(pm[sel_m])[:, None])[:, 1]
    all_cal = np.where(level == 2, mild_cal, cal_ep)
    by_level, rec_cal = {}, {}
    for L in (1, 2, 3, 4):
        ids_l = np.unique(rec[level == L])
        vals = np.array([np.nanmean(all_cal[rec == i]) for i in ids_l])
        rec_cal.update(dict(zip(ids_l.tolist(), vals.tolist(), strict=True)))
        by_level[L] = {"mean": float(vals.mean()), "sd": float(vals.std(ddof=1)), "n": int(len(vals))}
    rid = np.array(sorted(rec_cal))
    prop = np.array([d["propofol"][d["recording"] == i][0] for i in rid])
    corr = np.array([d["correct"][d["recording"] == i][0] for i in rid])
    pv = np.array([rec_cal[i] for i in rid])
    mild_ids = [i for i in rid if level[rec == i][0] == 2]
    base_ids = [i for i in rid if level[rec == i][0] == 1]
    sub_of = {i: int(subject[rec == i][0]) for i in rid}
    paired = [(rec_cal[next(j for j in mild_ids if sub_of[j] == sub_of[b])] - rec_cal[b]) for b in base_ids]
    res["dose_response"] = {"by_level": by_level, "spearman_propofol": float(spearmanr(pv, prop)[0]),
                            "spearman_correct_responses": float(spearmanr(pv, corr)[0]),
                            "mild_minus_baseline_mean": float(np.mean(paired)), "mild_gt_baseline_fraction": float(np.mean(np.array(paired) > 0))}

    # ---- 3. SHAP (out-of-sample, per held-out person) ----
    if f"shap_abs__{chosen}" in z.files:
        sa = z[f"shap_abs__{chosen}"].mean(0)
        sg = z[f"shap_sgn__{chosen}"]
        Xs = (X - X[tm].mean(0)) / (X[tm].std(0) + 1e-9)
        order = np.argsort(sa)[::-1]
        top = []
        for i in order[:15]:
            direction = float(Xs[tm & (y == 1), i].mean() - Xs[tm & (y == 0), i].mean())
            top.append({"feature": names[i], "plain": plain_name(names[i]), "mean_abs_shap": float(sa[i]),
                        "sedated_minus_awake_z": direction, "stable_in_folds": float(np.mean(np.sign(sg[:, i]) == np.sign(sg[:, i].mean())))})
        res["shap"] = {"available": True, "top": top, "amplitude_only_in_top5": any("log_power" in t["feature"] for t in top[:5])}
    else:
        res["shap"] = {"available": False, "reason": f"{chosen} has no fast exact explainer; the best tree/linear model would be explained instead"}

    # ---- 4. OOD detector, leave-one-subject-out ----
    flagged_in, n_in = [], []
    for s in np.unique(subject):
        tr = tm & (subject != s)
        m = ood_fit(X[tr])
        for L in (1, 3, 4):
            sel_e = (subject == s) & (level == L)
            fl = ood.flags(m, X[sel_e])
            flagged_in.append(float(fl.mean()))
            n_in.append(int(sel_e.sum()))
    res["ood_within_dataset"] = {"mean_flagged_epoch_fraction": float(np.mean(flagged_in)), "max_recording": float(np.max(flagged_in)),
                                "recordings_flagged_unfamiliar_over_25pct": float(np.mean(np.array(flagged_in) > 0.25)),
                                "recordings_flagged_unfamiliar_over_50pct": float(np.mean(np.array(flagged_in) > 0.5)),
                                "n_recordings": int(len(flagged_in)),
                                "threshold_percentile": 99.0, "note": "held-out people from the same dataset; ~1-3 % is what the 99th percentile predicts"}

    # ---- 5. permutation test for the chosen model ----
    best = {}
    for row in json.loads((OUT / "cv_params.json").read_text())[chosen]:
        key = json.dumps(row["best"], sort_keys=True)
        best[key] = best.get(key, 0) + 1
    params = json.loads(max(best, key=best.get))
    print("permutation test with", params)
    y_all = labels(level)
    null = Parallel(n_jobs=n_jobs, verbose=2)(delayed(perm_job)(chosen, params, X, y_all, subject, level, 1000 + k) for k in range(N_PERM))
    obs = res["models"][chosen]["balanced_accuracy"]
    res["permutation"] = {"n": N_PERM, "params": params, "observed": obs, "null_mean": float(np.mean(null)), "null_p95": float(np.percentile(null, 95)),
                          "p_value": float((1 + np.sum(np.array(null) >= obs)) / (N_PERM + 1)), "null": [float(v) for v in null]}
    res["chosen"] = chosen
    res["feature_names"] = names
    (OUT / "results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    np.savez_compressed(OUT / "cal.npz", cal_ep=cal_ep, all_cal=all_cal)
    print("saved results.json")


if __name__ == "__main__":
    main()
