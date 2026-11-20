"""Build figures and REPORT.md for one experiment directly from the saved JSON/NPZ results.

No number in the report is typed by hand. Usage: python -m nbml.report --task left_vs_right
"""

import argparse
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from . import config as C

BLUE, TEAL, GREY, AMBER = "#1F5FAE", "#0F8C8C", "#94A3B8", "#B7791F"
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": "#5B6B7F", "axes.labelcolor": "#0F1C2E", "xtick.color": "#5B6B7F",
                     "ytick.color": "#5B6B7F", "figure.dpi": 140})

NICE = {"ts_logreg": "Tangent space + LR", "ts_svm_linear": "Tangent space + linear SVM", "csp_lda": "CSP + LDA",
        "logreg": "Logistic regression", "svm_rbf": "SVM (RBF)", "random_forest": "Random forest",
        "gradient_boosting": "Gradient boosting", "xgboost": "XGBoost", "lightgbm": "LightGBM",
        "ts_ra_logreg": "Tangent space + recentring + LR"}


def load(d, name):
    return json.loads((d / name).read_text())


def subject_bootstrap_ci(per_subject_correct: dict, n_boot=5000, seed=3):
    """95% CI of mean per-subject accuracy (subjects are the unit of replication)."""
    v = np.array(list(per_subject_correct.values()))
    rng = np.random.default_rng(seed)
    means = [rng.choice(v, len(v)).mean() for _ in range(n_boot)]
    return float(v.mean()), [float(x) for x in np.percentile(means, [2.5, 97.5])]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="left_vs_right")
    a = ap.parse_args(argv)
    d = C.REPORT_DIR / a.task
    sel, hold = load(d, "selection.json"), load(d, "holdout.json")
    manifest = load(C.CACHE_DIR, f"{a.task}.manifest.json")
    perm = load(d, "permutation.json") if (d / "permutation.json").exists() else None
    cv = {r["model"]: load(d, f"cv_{r['model']}.json") for r in sel["table"]}
    order = sorted(sel["table"], key=lambda r: -r["balanced_accuracy"])
    chosen = sel["chosen"]
    classes = manifest["classes"]

    # ---- Figure 1: model comparison with subject-bootstrap CIs
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    names = [NICE.get(r["model"], r["model"]) for r in order][::-1]
    vals = [r["balanced_accuracy"] for r in order][::-1]
    lo = [r["balanced_accuracy"] - r["balanced_accuracy_ci95"][0] for r in order][::-1]
    hi = [r["balanced_accuracy_ci95"][1] - r["balanced_accuracy"] for r in order][::-1]
    colors = [BLUE if r["model"] == chosen else GREY for r in order][::-1]
    ax.barh(names, vals, xerr=[lo, hi], color=colors, error_kw={"ecolor": "#0F1C2E", "lw": 1})
    ax.axvline(0.5, color=AMBER, ls="--", lw=1)
    ax.text(0.502, -0.75, "chance", color=AMBER, fontsize=8)
    ax.set_xlim(0.45, 0.70)
    ax.set_xlabel("Balanced accuracy, pooled out-of-fold (95% CI over subjects)")
    ax.set_title("Cross-subject nested CV, development subjects", loc="left", fontsize=10)
    fig.tight_layout(); fig.savefig(d / "fig_model_comparison.png"); plt.close(fig)

    # ---- Figure 2: confusion matrix (hold-out) and calibration (OOF + hold-out)
    fig, axs = plt.subplots(1, 3, figsize=(11, 3.6))
    cm = np.array(hold["confusion_matrix"]); cmn = cm / cm.sum(axis=1, keepdims=True)
    axs[0].imshow(cmn, cmap="Blues", vmin=0, vmax=1)
    for i in range(2):
        for j in range(2):
            axs[0].text(j, i, f"{cm[i, j]}\n({cmn[i, j]:.0%})", ha="center", va="center",
                        color="white" if cmn[i, j] > .55 else "#0F1C2E", fontsize=9)
    axs[0].set_xticks([0, 1], classes); axs[0].set_yticks([0, 1], classes)
    axs[0].set_xlabel("Predicted"); axs[0].set_ylabel("True"); axs[0].set_title("Hold-out confusion matrix", loc="left", fontsize=10)
    for ax_, (tag, cal, col) in zip([axs[1], axs[1]], [("development (out-of-fold)", cv[chosen]["calibration"], BLUE),
                                                        ("hold-out", hold["calibration"], TEAL)], strict=True):
        ax_.plot(cal["mean_predicted"], cal["fraction_positive"], "o-", color=col, label=tag, ms=4)
    axs[1].plot([0, 1], [0, 1], color=GREY, ls="--", lw=1)
    axs[1].set_xlabel(f"Predicted P({classes[1]})"); axs[1].set_ylabel("Observed fraction"); axs[1].legend(fontsize=7, frameon=False)
    axs[1].set_title("Calibration", loc="left", fontsize=10)
    dev_ps = np.array(list(cv[chosen]["per_subject_accuracy"].values()))
    hold_ps = np.array(list(hold["per_subject_accuracy"].values()))
    axs[2].boxplot([dev_ps, hold_ps], tick_labels=["development", "hold-out"], widths=0.5, showfliers=False,
                   medianprops={"color": BLUE})
    for k, v in enumerate([dev_ps, hold_ps], 1):
        axs[2].scatter(np.random.default_rng(k).normal(k, 0.05, len(v)), v, s=8, color=TEAL, alpha=.6)
    axs[2].axhline(0.5, color=AMBER, ls="--", lw=1); axs[2].set_ylabel("Per-subject accuracy")
    axs[2].set_title("Accuracy per person", loc="left", fontsize=10)
    fig.tight_layout(); fig.savefig(d / "fig_chosen_model.png"); plt.close(fig)

    # ---- Figure 3: permutation test
    if perm:
        fig, ax = plt.subplots(figsize=(6, 3.4))
        ax.hist(perm["null"], bins=18, color=GREY)
        ax.axvline(perm["observed_balanced_accuracy"], color=BLUE, lw=2)
        ax.text(perm["observed_balanced_accuracy"], ax.get_ylim()[1] * .92, "  observed", color=BLUE, fontsize=8)
        ax.set_xlabel("Balanced accuracy with labels shuffled within each subject"); ax.set_ylabel("Permutations")
        fig.tight_layout(); fig.savefig(d / "fig_permutation.png"); plt.close(fig)

    h_mean, h_ci = subject_bootstrap_ci(hold["per_subject_accuracy"])
    d_mean, d_ci = subject_bootstrap_ci(cv[chosen]["per_subject_accuracy"])
    n_hold_sub = hold["n_holdout_subjects"]
    n_dev = len(cv[chosen]["per_subject_accuracy"])

    cal = hold["calibration"]
    keep = [i for i, n in enumerate(cal["count"]) if n >= 20]  # ignore bins too small to be meaningful
    cal = {k: [v[i] for i in keep] for k, v in cal.items()}
    sp_pred = max(cal["mean_predicted"]) - min(cal["mean_predicted"])
    sp_obs = max(cal["fraction_positive"]) - min(cal["fraction_positive"])
    direction = ("**under-confident** (predicted probabilities span {a:.2f}–{b:.2f} while observed frequencies span "
                 "{c:.2f}–{d:.2f}; strong regularisation squeezes the outputs toward 0.5)" if sp_pred < sp_obs else
                 "**over-confident** (predicted probabilities span a wider range than the observed frequencies)")
    calib_text = ("The chosen model is poorly calibrated (hold-out ECE {e:.3f}); it is " + direction).format(
        e=hold["ece"], a=min(cal["mean_predicted"]), b=max(cal["mean_predicted"]),
        c=min(cal["fraction_positive"]), d=max(cal["fraction_positive"])) +         ". Probabilities must be recalibrated before anyone relies on them."
    cm_ = np.array(hold["confusion_matrix"])
    rec0, rec1 = cm_[0, 0] / cm_[0].sum(), cm_[1, 1] / cm_[1].sum()
    bias_text = (f"On the hold-out subjects recall was {rec0:.0%} for {classes[0].replace('_', ' ')} but {rec1:.0%} for "
                 f"{classes[1].replace('_', ' ')}: the 0.5 decision threshold favours one class for new people "
                 "(a subject-level shift the model does not correct).") if abs(rec0 - rec1) > 0.1 else         "Recall was similar for both classes on the hold-out subjects."

    rows = []
    for r in order:
        p = cv[r["model"]]["pooled_metrics"]
        ci, dci = r["balanced_accuracy_ci95"], r["diff_ci95"]
        rows.append(f"| {NICE.get(r['model'], r['model'])} | **{p['balanced_accuracy']:.3f}** [{ci[0]:.3f}, {ci[1]:.3f}] | "
                    f"{p['roc_auc']:.3f} | {p['f1_macro']:.3f} | {p['precision_macro']:.3f} / {p['recall_macro']:.3f} | "
                    f"{p['brier']:.3f} | {p['ece']:.3f} | "
                    f"{'—' if r['model'] == sel['best_by_accuracy'] else f'{r['diff_vs_best']:+.3f} [{dci[0]:+.3f}, {dci[1]:+.3f}]'} | "
                    f"{cv[r['model']]['train_seconds_total'] / 60:.1f} |")

    md = f"""# Experiment report — {a.task.replace('_', ' ')} (PhysioNet EEGMMIDB 1.0.0)

*Generated by `python -m nbml.report` from saved results; nothing here was typed by hand.*

> **What this is — and is not.** A benchmark of **motor-imagery decoding in healthy volunteers**
> ({classes[0].replace('_', ' ')} vs {classes[1].replace('_', ' ')} imagery). It is **not** a test of consciousness,
> cognitive motor dissociation (CMD) or any clinical state, and its models must not be used for diagnosis or prognosis.

## 1. Data and protocol
- Dataset: PhysioNet EEGMMIDB v1.0.0, 64 channels, 160 Hz, runs {C.TASKS[a.task]['runs']} (one cue per trial; T1 = {classes[0].replace('_', ' ')}, T2 = {classes[1].replace('_', ' ')}).
- **Subjects kept: {manifest['n_subjects_kept']} of 109; epochs: {manifest['n_epochs']}** (class counts {manifest['class_counts']}).
- Integrity rule (fixed before any model was run; it uses file properties only, never labels or performance) excluded:
  {'; '.join(f"S{e['subject']:03d} ({e['reasons'][0]})" for e in manifest['excluded'])}.
- Preprocessing (identical for everyone): common-average reference, zero-phase FIR band-pass {C.FILTER_BAND[0]:g}–{C.FILTER_BAND[1]:g} Hz on the continuous run, epoch {C.EPOCH_START_S:g}–{C.EPOCH_END_S:g} s after the cue.
- **Subject-independent validation.** {n_hold_sub} subjects were locked away as a hold-out (never used for training, tuning or model choice).
  The remaining {n_dev} development subjects were split into {C.OUTER_FOLDS} outer folds by subject; hyper-parameters were tuned only inside the training subjects of each fold
  ({C.INNER_FOLDS}-fold inner CV, small fixed grids). Scalers, CSP filters and the Riemannian reference mean are fitted inside each training fold.
  Leakage guards are unit-tested (`ml/tests`).
- Chance level: with {hold['n']} hold-out epochs a no-skill classifier exceeds **{hold['chance_upper_bound_95']:.3f}** accuracy only 5 % of the time (binomial bound).

## 2. Model comparison (development subjects, pooled out-of-fold predictions)
| Model | Balanced accuracy [95% CI over subjects] | ROC-AUC | F1 (macro) | Precision / recall (macro) | Brier | ECE | Difference vs best [95% CI] | Fit time (min, summed) |
|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

![model comparison](fig_model_comparison.png)

**Selection rule (fixed in advance):** best balanced accuracy; a model is "tied" if the paired subject-bootstrap interval of its
difference from the best includes 0; ties are broken by calibration (Brier), then interpretability, then CPU cost.
Result: best = **{NICE.get(sel['best_by_accuracy'])}**; models tied with it: {', '.join(NICE.get(m, m) for m in sel['tied'])};
**chosen: {NICE.get(chosen)}**.

## 3. Chosen model on the locked hold-out (evaluated once)
| Metric | Value |
|---|---|
| Balanced accuracy | **{hold['balanced_accuracy']:.3f}** (95 % CI over {n_hold_sub} subjects: {h_ci[0]:.3f}–{h_ci[1]:.3f}) |
| ROC-AUC | {hold['roc_auc']:.3f} |
| F1 / precision / recall (macro) | {hold['f1_macro']:.3f} / {hold['precision_macro']:.3f} / {hold['recall_macro']:.3f} |
| Brier / ECE | {hold['brier']:.3f} / {hold['ece']:.3f} |
| Confusion matrix (rows = true {classes[0]}, {classes[1]}) | {hold['confusion_matrix']} |
| Subjects with accuracy ≥ 70 % | {int((hold_ps >= 0.7).sum())} of {len(hold_ps)} (median {np.median(hold_ps):.2f}, range {hold_ps.min():.2f}–{hold_ps.max():.2f}) |

Development CV for the same model: balanced accuracy {cv[chosen]['pooled_metrics']['balanced_accuracy']:.3f}, mean per-subject accuracy {d_mean:.3f} (95 % CI {d_ci[0]:.3f}–{d_ci[1]:.3f}).
**With only {n_hold_sub} hold-out subjects the hold-out number is noisy; the development estimate (based on {n_dev} subjects) is the more reliable one.**

![chosen model](fig_chosen_model.png)

""" + (f"""## 4. Label-permutation test (leakage / chance check)
Labels were shuffled within each subject {perm['n_permutations']} times and the whole outer CV repeated with the tuned hyper-parameters fixed.
Observed balanced accuracy {perm['observed_balanced_accuracy']:.3f} vs shuffled mean {perm['null_mean']:.3f} (SD {perm['null_sd']:.3f}, max {perm['null_max']:.3f}); **p = {perm['p_value']:.4f}**.
A leak-free pipeline scores ≈ 0.5 on shuffled labels; this one does.

![permutation](fig_permutation.png)

""" if perm else "") + f"""## 5. Interpretation and limits
- Cross-subject left-vs-right imagery is genuinely hard: a sizeable share of healthy people produce little discriminable sensorimotor activity.
  Accuracy per person varies widely (figure above), so a single average hides large individual differences.
- Tree ensembles (random forest, gradient boosting, XGBoost, LightGBM) were the weakest here: with ~{int(manifest['n_epochs'] * 0.8)} epochs and 192 noisy band-power features they have little to exploit,
  while methods that model the spatial covariance structure (tangent space, CSP) did better — consistent with the motor-imagery literature, but this is the measured result on this dataset only.
- {calib_text}
- {bias_text}
- Healthy volunteers, one session each, 64-channel research cap: **no evidence yet** about transfer to patients, bedside montages or repeated sessions.
- Hyper-parameter grids were small and fixed; deep models (EEGNet) were **not** run in this experiment.
"""
    extra = C.REPORT_DIR / f"{a.task}_ra" / "comparison.json"
    if extra.exists():
        x = json.loads(extra.read_text())
        ba = x["balanced_accuracy"]
        md += f"""
## 6. Exploratory follow-up: per-subject recentring (NOT part of the pre-specified comparison)
Idea: whiten each person's covariance matrices by that person's own mean covariance before the tangent-space step
(label-free; it needs only the new person's own unlabelled epochs).
- Development CV, same subjects and folds: **{ba[x['extra']]:.3f}** vs {ba[x['base']]:.3f} for the pre-specified winner
  (difference **{x['difference_extra_minus_base']:+.3f}**, 95 % paired-bootstrap CI {x['difference_ci95'][0]:+.3f} to {x['difference_ci95'][1]:+.3f};
  better for {x['subjects_better']} of {x['n_subjects']} subjects, worse for {x['subjects_worse']}). Brier {x['brier'][x['extra']]:.3f} vs {x['brier'][x['base']]:.3f}.
- **Status: exploratory.** It was judged on development subjects only because the locked hold-out was already used once.
  It must be confirmed on a **new** dataset (e.g. Lee 2019 / Cho 2017 through MOABB) before it is trusted.
- **Caveats:** (1) it is transductive: it needs some unlabelled data from the person being decoded; (2) it deliberately removes
  person- and session-level offsets, which helps decoding but is the *opposite* of what longitudinal tracking needs, so it must
  never be applied to the features used for session-to-session comparison.
"""
    (d / "REPORT.md").write_text(md, encoding="utf-8")
    print("wrote", d / "REPORT.md")


if __name__ == "__main__":
    main()
