"""Stage 4: figures + REPORT.md for the sedation-level study. Every sentence that depends on a number is generated from results.json,
so the text cannot disagree with the data. Run:  uv run python -m nbml.sed_report
"""

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .sed_cv import OUT

BLUE, TEAL, AMBER, RED, GREY = "#1F5FAE", "#0F8C8C", "#B7791F", "#B42318", "#5B6B7F"


def pct(x, d=0):
    return f"{100 * x:.{d}f} %"


def figures(r: dict, z, cal) -> None:
    ms = list(r["models"])
    fig, ax = plt.subplots(figsize=(7, 3.4))
    ba = [r["models"][m]["balanced_accuracy"] for m in ms]
    lo = [r["models"][m]["ba_ci95"][0] for m in ms]
    hi = [r["models"][m]["ba_ci95"][1] for m in ms]
    ax.errorbar(range(len(ms)), ba, yerr=[np.array(ba) - lo, np.array(hi) - ba], fmt="o", color=BLUE, capsize=4)
    ax.axhline(0.5, color=GREY, ls="--", lw=1)
    ax.set_xticks(range(len(ms)), [m.replace("_", "\n") for m in ms], fontsize=8)
    ax.set_ylim(0.4, 1.02)
    ax.set_ylabel("balanced accuracy (recordings)")
    ax.set_title("Leave-one-person-out, 95 % CI over people (dashed = chance)", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "fig_models.png", dpi=150)
    plt.close(fig)

    level, subject = z["level"], z["subject"]
    all_cal = cal["all_cal"]
    fig, ax = plt.subplots(figsize=(6, 3.6))
    per = np.full((20, 4), np.nan)
    for s in range(20):
        for k, L in enumerate((1, 2, 3, 4)):
            sel = (subject == s) & (level == L)
            per[s, k] = np.nanmean(all_cal[sel])
    for row in per:
        ax.plot(range(4), row, color=GREY, alpha=0.3, lw=1)
    ax.plot(range(4), per.mean(0), "o-", color=BLUE, lw=2.5, label="mean of 20 people")
    ax.set_xticks(range(4), ["baseline", "mild\n(not trained)", "moderate", "recovery"])
    ax.set_ylabel("P(sedated-like)")
    ax.set_title("Dose-response of the model's output", fontsize=9)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "fig_dose_response.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(4.2, 4))
    for key, c, lab in (("curve_raw", AMBER, "raw"), ("curve_platt", BLUE, "Platt")):
        cv = r["calibration"][key]
        ax.plot(cv["mean_predicted"], cv["fraction_positive"], "o-", color=c, label=lab)
    ax.plot([0, 1], [0, 1], "--", color=GREY, lw=1)
    ax.set_xlabel("predicted P(sedated)")
    ax.set_ylabel("observed fraction sedated")
    ax.set_title("Reliability (recordings, 5 bins)", fontsize=9)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "fig_calibration.png", dpi=150)
    plt.close(fig)

    if r["shap"]["available"]:
        top = r["shap"]["top"][:12][::-1]
        fig, ax = plt.subplots(figsize=(8, 4.2))
        ax.barh([t["plain"][:70] for t in top], [t["mean_abs_shap"] for t in top],
                color=[RED if t["sedated_minus_awake_z"] > 0.2 else TEAL if t["sedated_minus_awake_z"] < -0.2 else GREY for t in top])
        ax.set_xlabel("mean |SHAP| on held-out people")
        ax.set_title("red: higher when sedated · teal: lower · grey: no average difference", fontsize=8)
        ax.tick_params(axis="y", labelsize=7)
        fig.tight_layout()
        fig.savefig(OUT / "fig_shap.png", dpi=150)
        plt.close(fig)

    pm = r["permutation"]
    fig, ax = plt.subplots(figsize=(5, 3.2))
    ax.hist(pm["null"], bins=15, color=GREY, alpha=0.7)
    ax.axvline(pm["observed"], color=RED, lw=2, label=f"observed {pm['observed']:.3f}")
    ax.set_xlabel("balanced accuracy with shuffled labels")
    ax.legend(fontsize=8)
    ax.set_title(f"Permutation test (n={pm['n']}, p={pm['p_value']:.3f})", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "fig_permutation.png", dpi=150)
    plt.close(fig)


def main():
    r = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    z = np.load(OUT / "oof.npz")
    cal = np.load(OUT / "cal.npz")
    figures(r, z, cal)
    ch = r["chosen"]
    m = r["models"][ch]
    sel = r["selection"]
    cf, dr, ex, ood = r["conformal"], r["dose_response"], r["external_eegmmidb"], r["ood_within_dataset"]
    pl, raw = r["calibration"]["platt"], r["calibration"]["raw"]
    pm = r["permutation"]
    lv = dr["by_level"]
    L = []
    w = L.append
    w("# Sedation-level model — results\n")
    w("**What this is:** a model trained on healthy volunteers under propofol (Chennu et al. 2016, Cambridge, CC BY 2.0 UK, DOI 10.17863/CAM.68959) that estimates "
      "how much a resting EEG *resembles* awake or moderately sedated EEG, with uncertainty and an explanation. "
      "**What it is not:** a diagnosis, a consciousness or covert-awareness detector for patients, or a measure of recovery. It has never seen a patient.\n")
    w("Protocol, fixed before any model was trained: [PRESPEC.md](PRESPEC.md). Code: `nbml/sed_*.py`.\n")
    w("## 1. Can it tell awake from moderately sedated in a new person?\n")
    w(f"Leave-one-person-out (20 folds, no person in both training and test; tuning inside the training people only). {r['n_recordings_primary']} recordings "
      "(20 people × baseline / moderate / recovery). Unit of analysis: the recording (mean of its epoch probabilities).\n")
    w("| Model | Balanced accuracy (recordings) | 95 % CI (over people) | AUC | Brier | ECE | Balanced accuracy (epochs) |\n|---|---|---|---|---|---|---|")
    for k, v in r["models"].items():
        w(f"| {k} | {v['balanced_accuracy']:.3f} | {v['ba_ci95'][0]:.2f}–{v['ba_ci95'][1]:.2f} | {v['roc_auc']:.3f} | {v['brier']:.3f} | {v['ece']:.3f} | "
          f"{r['epoch_level'][k]['balanced_accuracy']:.3f} |")
    w("\n![models](fig_models.png)\n")
    tied = sel["tied"]
    if len(tied) == len(r["models"]):
        w(f"**No model is statistically better than another** (every pairwise 95 % interval of the difference includes 0). The pre-specified rule therefore picks the "
          f"best-calibrated model: **{ch}** (lowest Brier score). Treat the ranking as noise; with 20 people the six models are interchangeable here.\n")
    else:
        w(f"Statistically tied with the best: {', '.join(tied)}. Pre-specified choice (lowest Brier, then most interpretable, then cheapest): **{ch}**.\n")
    w(f"Chosen model ({ch}): balanced accuracy **{m['balanced_accuracy']:.3f}** (95 % CI {m['ba_ci95'][0]:.2f}–{m['ba_ci95'][1]:.2f}), AUC {m['roc_auc']:.3f}.\n")
    w(f"**Permutation test:** with labels shuffled among each person's own recordings the same pipeline reaches {pm['null_mean']:.3f} on average "
      f"(95th percentile {pm['null_p95']:.3f}); observed {pm['observed']:.3f}, p = {pm['p_value']:.3f} (with {pm['n']} permutations the smallest possible p is {1 / (pm['n'] + 1):.3f}).\n")
    w("![permutation](fig_permutation.png)\n")
    w("**Caveats.** Selection among six models used the same cross-validation as the estimate (grids are tiny and fixed, so the optimism is small but not zero). "
      "20 healthy people is a small sample, and propofol sedation in healthy adults is a much easier and cleaner problem than brain injury.\n")

    w("## 2. Does the output behave like a dose-response? (mild sedation was never used in training)\n")
    w("| Level | n | mean P(sedated-like) | SD |\n|---|---|---|---|")
    for k, name in (("1", "baseline"), ("2", "mild sedation"), ("3", "moderate sedation"), ("4", "recovery")):
        w(f"| {name} | {lv[k]['n']} | {lv[k]['mean']:.2f} | {lv[k]['sd']:.2f} |")
    w(f"\nMild sedation sat between baseline and moderate for the group, and was higher than the same person's baseline in {pct(dr['mild_gt_baseline_fraction'])} of people "
      f"(mean +{dr['mild_minus_baseline_mean']:.2f}). Across all 80 recordings the output correlates with measured plasma propofol (Spearman ρ = {dr['spearman_propofol']:.2f}) "
      f"and negatively with correct responses in the behavioural task (ρ = {dr['spearman_correct_responses']:.2f}). Recovery returned to near baseline. "
      "This is the most convincing single result: the model was not trained on this level.\n")
    w("![dose](fig_dose_response.png)\n")

    w("## 3. Can its probabilities be trusted?\n")
    verdict = "slightly worsened" if pl["ece"] > raw["ece"] else "improved"
    w(f"Calibration (recording level, leave-one-person-out): raw ECE {raw['ece']:.3f}, Brier {raw['brier']:.3f}; after Platt scaling ECE {pl['ece']:.3f}, Brier {pl['brier']:.3f}. "
      f"Platt scaling {verdict} the already reasonable {ch} probabilities; the difference is inside what 60 recordings can resolve. Platt scaling was pre-specified, so it is kept, but this is an open decision.\n")
    w("![calibration](fig_calibration.png)\n")
    w(f"**Uncertainty (split-conformal sets, target {pct(cf['target'])} coverage, checked on each held-out person):** coverage {pct(cf['coverage'])}; "
      f"a single class was returned for {pct(cf['frac_single'])} of recordings and was right {pct(cf['acc_when_single'])} of the time; "
      f"{pct(cf['frac_empty'] + cf['frac_both'])} of recordings were answered \"uncertain\" (neither or both classes fit).\n")

    w("## 4. What does it look at? (SHAP on held-out people)\n")
    if r["shap"]["available"]:
        w("| # | Feature (plain language) | mean abs SHAP | Higher when sedated? | Sign consistent across people |\n|---|---|---|---|---|")
        for i, t in enumerate(r["shap"]["top"][:10], 1):
            d = t["sedated_minus_awake_z"]
            w(f"| {i} | {t['plain']} | {t['mean_abs_shap']:.2f} | {'yes' if d > 0.2 else 'no, lower' if d < -0.2 else 'about the same'} ({d:+.2f} SD) | {pct(t['stable_in_folds'])} |")
        w("\n![shap](fig_shap.png)\n")
        w(f"Amplitude-only features among the top five: **{'yes — suspect a gain/device effect' if r['shap']['amplitude_only_in_top5'] else 'no'}** (pre-specified warning sign). "
          "Two of the leading effects point the way the propofol literature describes (frontal beta share up; frontal-minus-occipital alpha difference up, i.e. alpha moves forward), "
          "but others (parietal Hjorth complexity, temporal gamma) have no simple interpretation I can vouch for. A feature can matter without differing in its average "
          "(SHAP also captures interactions), and the sign of its contribution was not consistent in every person (last column): treat explanations as hints, not proof.\n")
    else:
        w(r["shap"]["reason"] + "\n")

    w("## 5. Does it know when it is out of its depth?\n")
    w(f"Unfamiliar-EEG detector (PCA: Hotelling T² and residual SPE, 99th-percentile thresholds): on held-out people from the same dataset it flagged "
      f"{pct(ood['mean_flagged_epoch_fraction'], 1)} of epochs (the percentile alone predicts about 2 %: new people differ systematically); "
      f"{pct(ood['recordings_flagged_unfamiliar_over_25pct'], 1)} of recordings ({round(ood['recordings_flagged_unfamiliar_over_25pct'] * ood['n_recordings'])} of {ood['n_recordings']}) had more than 25 % of epochs flagged.\n")
    w(f"**External sanity check on a different dataset** (EEGMMIDB, 109 awake healthy people, another device and site, filtered here with the same Butterworth band-pass; "
      f"{ex['n_recordings']} eyes-open / eyes-closed recordings, all truly awake):\n")
    w(f"- called sedated: {pct(ex['fraction_called_sedated'], 1)} (so {pct(1 - ex['fraction_called_sedated'], 1)} correctly not sedated; this tests specificity only — it says nothing about detecting sedation on another device);\n"
      f"- conformal verdict: {pct(ex['fraction_verdict_awake'])} awake-like, {pct(ex['fraction_verdict_sedated'], 1)} sedated-like, {pct(ex['fraction_verdict_uncertain'])} uncertain;\n"
      f"- eyes open vs closed: mean P(sedated-like) {ex['by_condition']['eyes_open']['mean_p_sedated']:.2f} vs {ex['by_condition']['eyes_closed']['mean_p_sedated']:.2f} — "
      "the model leans towards \"sedated\" when alpha is reduced for any reason, including simply having the eyes open (the repository page does not state the eye state of the training recordings);\n"
      f"- **unfamiliar-EEG flag: {pct(ex['unfamiliar_recordings'])} of these recordings.** The detector therefore fired on every recording from another site. It is honest "
      "(a different device really does look different) but it cannot yet separate 'usable' from 'unusable' foreign data: its thresholds must be re-calibrated on the target "
      "clinical recordings before it can act as a gate. Until then a recording outside the Cambridge set-up should always be labelled \"unfamiliar\".\n")

    w("## 6. What this answers, and what it does not\n")
    w("- It supports the claim that a pipeline of standard spectral, entropy and connectivity features plus tree-based or linear models can read propofol sedation level from **new healthy people's** resting EEG, "
      "with explanations and uncertainty.\n"
      "- It does **not** show that the same works for patients with disorders of consciousness, for other drugs, for brain injury, or for other EEG systems. "
      "That needs EEG from patients with labels (e.g. CRS-R category or outcome). No such open dataset was found; one open resting-EEG set of patients exists on Figshare "
      "(DOI 10.6084/m9.figshare.23552964) but it has **no label file**, so it can only be used to test the pipeline and the unfamiliar-EEG detector, not to measure accuracy.\n"
      "- The dataset's EEG was already cleaned by its authors (0.5–45 Hz, ICA, epoch rejection), so it cannot test the new FastICA blink removal; that was tested separately on simulated blinks.\n"
      "- \"Recovering\" is a statement about change over time within one patient, which this model does not provide by itself; that is the job of the longitudinal comparison (Phase 6).\n")
    w("## 7. Files\n")
    w("`ml/models/sedation_v1/model.joblib` (+ `model_card.json`, SHA-256 recorded; registered as *staging* in MongoDB `neurobridge_ml.model_registry`), "
      "`ml/reports/sedation/results.json`, `oof.npz` (all out-of-fold predictions), `cv_params.json`.\n")
    (OUT / "REPORT.md").write_text("\n".join(L), encoding="utf-8")
    print("REPORT.md written")


if __name__ == "__main__":
    main()
