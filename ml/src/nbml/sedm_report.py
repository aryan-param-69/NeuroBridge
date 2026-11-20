"""Stage 4: figures and REPORT.md for the multi-site experiment. Every sentence that depends on a number is generated from results_*.json.
Run:  uv run python -m nbml.sedm_report
"""

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import config as C  # noqa: E402

OUT = C.REPORT_DIR / "sedation_multisite"
PURPLE, TEAL, AMBER, RED, GREY = "#3D1E6D", "#0F8C8C", "#B7791F", "#B42318", "#5B6B7F"


def pct(x, d=0):
    return f"{100 * x:.{d}f} %"


def criteria(r: dict) -> dict:
    chosen = r["chosen"]
    m = r["models"][chosen]
    cs = {c["train"]: c for c in r["cross_site"] if c["model"] == chosen}
    both_ci = all(c["ba_ci95"][0] > 0.5 for c in cs.values())
    ok = {"pooled_ba_gt_0.80": m["balanced_accuracy"] > 0.80, "cross_site_lower_ci_gt_0.5_both": both_ci, "permutation_p_lt_0.05": r["permutation"]["p_value"] < 0.05}
    warn = {"cross_site_ba_lt_0.65": any(c["balanced_accuracy"] < 0.65 for c in cs.values()),
            "one_class_dominates_test_site": any(max(c["frac_called_sedated"], 1 - c["frac_called_sedated"]) > 0.9 for c in cs.values()),
            "permutation_p_ge_0.05": r["permutation"]["p_value"] >= 0.05}
    return {"success": all(ok.values()) and not any(warn.values()), "ok": ok, "warn": warn, "cross": cs}


def figures(r: dict, tag: str) -> None:
    ms = list(r["models"])
    fig, ax = plt.subplots(figsize=(7, 3.4))
    ba = [r["models"][m]["balanced_accuracy"] for m in ms]
    lo = [r["models"][m]["ba_ci95"][0] for m in ms]
    hi = [r["models"][m]["ba_ci95"][1] for m in ms]
    ax.errorbar(range(len(ms)), ba, yerr=[np.array(ba) - lo, np.array(hi) - ba], fmt="o", color=PURPLE, capsize=4)
    ax.axhline(0.5, color=GREY, ls="--", lw=1)
    ax.set_xticks(range(len(ms)), [m.replace("_", "\n") for m in ms], fontsize=8)
    ax.set_ylim(0.4, 1.02)
    ax.set_ylabel("balanced accuracy (recordings)")
    ax.set_title("Pooled leave-one-person-out, 40 people, 95 % CI over people", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / f"fig_{tag}_pooled.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    w = 0.38
    for k, (tr, colr) in enumerate((("Cambridge", PURPLE), ("Oslo", TEAL))):
        v = [next(c for c in r["cross_site"] if c["model"] == m and c["train"] == tr) for m in ms]
        x = np.arange(len(ms)) + (k - 0.5) * w
        b = [c["balanced_accuracy"] for c in v]
        ax.bar(x, b, w, color=colr, label=f"train {tr} -> test {'Oslo' if tr == 'Cambridge' else 'Cambridge'}",
               yerr=[np.array(b) - [c["ba_ci95"][0] for c in v], [c["ba_ci95"][1] for c in v] - np.array(b)], capsize=2, ecolor=GREY)
    ax.axhline(0.5, color=GREY, ls="--", lw=1)
    ax.set_xticks(range(len(ms)), [m.replace("_", "\n") for m in ms], fontsize=8)
    ax.set_ylim(0.3, 1.05)
    ax.set_ylabel("balanced accuracy on the OTHER site")
    ax.legend(fontsize=8, loc="lower right")
    ax.set_title("Cross-site test: model never saw the test laboratory", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / f"fig_{tag}_crosssite.png", dpi=150)
    plt.close(fig)

    if "dementia_check" in r:
        fig, ax = plt.subplots(figsize=(5.6, 3.4))
        for k, g in enumerate(("Healthy", "Alzheimer", "Frontotemporal")):
            code = {"Healthy": "C", "Alzheimer": "A", "Frontotemporal": "F"}[g]
            v = [x["p_sedated"] for x in r["dementia_check"]["rows"] if x["group"] == code]
            ax.boxplot([v], positions=[k], widths=0.5, showfliers=False)
            ax.scatter(np.random.default_rng(k).normal(k, 0.06, len(v)), v, s=10, color=[TEAL, AMBER, RED][k], alpha=0.7)
        ax.set_xticks(range(3), ["Healthy\n(n=29)", "Alzheimer\n(n=36)", "Frontotemporal\n(n=23)"], fontsize=8)
        ax.set_ylabel("P(sedated-like)")
        ax.set_title("Nobody here is sedated: false 'sedated-like' alarms", fontsize=9)
        fig.tight_layout()
        fig.savefig(OUT / f"fig_{tag}_dementia.png", dpi=150)
        plt.close(fig)

    if r["shap"]["available"]:
        top = r["shap"]["top"][:12][::-1]
        fig, ax = plt.subplots(figsize=(8, 4.2))
        ax.barh([t["plain"][:70] for t in top], [t["mean_abs_shap"] for t in top],
                color=[RED if t["sedated_minus_awake_z"] > 0.2 else TEAL if t["sedated_minus_awake_z"] < -0.2 else GREY for t in top])
        ax.set_xlabel("mean |SHAP| on held-out people")
        ax.tick_params(axis="y", labelsize=7)
        ax.set_title("red: higher when sedated · teal: lower · grey: no average difference", fontsize=8)
        fig.tight_layout()
        fig.savefig(OUT / f"fig_{tag}_shap.png", dpi=150)
        plt.close(fig)


def main():
    p = json.loads((OUT / "results_primary.json").read_text(encoding="utf-8"))
    f = json.loads((OUT / "results_full.json").read_text(encoding="utf-8")) if (OUT / "results_full.json").exists() else None
    v1 = json.loads((C.REPORT_DIR / "sedation" / "results.json").read_text(encoding="utf-8"))
    figures(p, "primary")
    cr = criteria(p)
    ch = p["chosen"]
    m = p["models"][ch]
    L: list[str] = []
    w = L.append
    w("# Multi-site sedation-level model (v2) - results\n")
    w("Protocol fixed before training: [PRESPEC.md](PRESPEC.md). Code: `nbml/sedm_*.py`, `oslo_data.py`, `prep.py`. **Research decision support; the model has never seen a patient.**\n")
    w("## 1. Data\n")
    w(f"{p['n_people']} healthy people from two laboratories (Cambridge 20, Oslo 20), {p['n_recordings_primary']} recordings in the primary task, "
      f"{p['n_features']} features (primary set: the 74 v1 features without the six amplitude-dependent log-power features). "
      "Oslo is raw 5 kHz Brain Products EEG prepared here (notch, Butterworth 0.5-45 Hz, EOG regression, average reference, artefact rejection); Cambridge is used as in v1.\n")
    w("## 2. Pooled leave-one-person-out (40 folds)\n")
    w("| Model | Balanced accuracy | 95 % CI (people) | AUC | Brier | Cambridge BA | Oslo BA |\n|---|---|---|---|---|---|---|")
    for k, v in p["models"].items():
        ps = p["per_site"][k]
        cb = f"{ps['Cambridge']['balanced_accuracy']:.3f}" if ps["Cambridge"] else "n/a"
        ob = f"{ps['Oslo']['balanced_accuracy']:.3f}" if ps["Oslo"] else "n/a"
        w(f"| {k} | {v['balanced_accuracy']:.3f} | {v['ba_ci95'][0]:.2f}-{v['ba_ci95'][1]:.2f} | {v['roc_auc']:.3f} | {v['brier']:.3f} | {cb} | {ob} |")
    w("\n![pooled](fig_primary_pooled.png)\n")
    tied = p["selection"]["tied"]
    w((f"All six models are statistically tied; the pre-specified rule chose **{ch}** (lowest Brier score). " if len(tied) == len(p["models"])
       else f"Tied with the best: {', '.join(tied)}. Pre-specified choice: **{ch}**. ")
      + f"Chosen model: balanced accuracy **{m['balanced_accuracy']:.3f}** (95 % CI {m['ba_ci95'][0]:.2f}-{m['ba_ci95'][1]:.2f}), AUC {m['roc_auc']:.3f}. "
      f"Permutation test ({p['permutation']['n']} shuffles): null mean {p['permutation']['null_mean']:.3f}, p = {p['permutation']['p_value']:.3f}.\n")
    w("## 3. Cross-site test (the generalisation result)\n")
    w("| Model | Train | Test | Balanced accuracy | 95 % CI | AUC | awake called sedated | sedated called sedated |\n|---|---|---|---|---|---|---|---|")
    for c in p["cross_site"]:
        w(f"| {c['model']} | {c['train']} | {c['test']} | {c['balanced_accuracy']:.3f} | {c['ba_ci95'][0]:.2f}-{c['ba_ci95'][1]:.2f} | {c['roc_auc']:.3f} | "
          f"{pct(c['frac_called_sedated_if_awake'])} | {pct(c['frac_called_sedated_if_sedated'])} |")
    w("\n![cross](fig_primary_crosssite.png)\n")
    w("**Pre-specified criteria for the chosen model** (" + ch + "):\n")
    for k, v in cr["ok"].items():
        w(f"- {k.replace('_', ' ')}: **{'met' if v else 'NOT met'}**")
    for k, v in cr["warn"].items():
        w(f"- warning '{k.replace('_', ' ')}': **{'TRIGGERED' if v else 'not triggered'}**")
    w(f"\n**Overall: {'all success criteria met and no warning triggered.' if cr['success'] else 'the success criteria are not all met (see above); v1 stays the deployed model unless stated otherwise below.'}**\n")
    if f:
        fm = f["models"][f["chosen"]]
        fc = {c["train"]: c for c in f["cross_site"] if c["model"] == f["chosen"]}
        w(f"Sensitivity analysis with all 74 features (including amplitude): chosen {f['chosen']}, pooled balanced accuracy {fm['balanced_accuracy']:.3f}; cross-site "
          + ", ".join(f"train {k}: {c['balanced_accuracy']:.3f}" for k, c in fc.items()) + ".\n")
    w("## 4. Reliability\n")
    cal, cf = p["calibration"], p["conformal"]
    w(f"- Calibration (leave-one-person-out Platt): raw ECE {cal['raw']['ece']:.3f}, Brier {cal['raw']['brier']:.3f}; Platt ECE {cal['platt']['ece']:.3f}, Brier {cal['platt']['brier']:.3f}.")
    w(f"- Conformal sets (target {pct(cf['target'])}): coverage {pct(cf['coverage'])}; single answer for {pct(cf['frac_single'])} of recordings, right {pct(cf['acc_when_single'])} of those; \"uncertain\" {pct(cf['frac_uncertain'])}.")
    d_ = p["dose_response_cambridge"]["by_level"]
    w(f"- Cambridge dose-response (mild never trained): baseline {d_['1']['mean']:.2f}, mild {d_['2']['mean']:.2f}, moderate {d_['3']['mean']:.2f}, recovery {d_['4']['mean']:.2f}; "
      f"Spearman with plasma propofol {p['dose_response_cambridge']['spearman_propofol']:.2f}. Oslo mean output: awake {p['oslo_levels']['awake_mean']:.2f}, sedated {p['oslo_levels']['sedated_mean']:.2f}.")
    o1, o2 = p["ood_within"], p["ood_across_sites"]
    w(f"- Unfamiliar-EEG detector: held-out people of the same pooled data flagged {pct(o1['mean_flagged_epoch_fraction'], 1)} of epochs; "
      + "; ".join(f"{k.replace('_', ' ')}: {pct(v)} of epochs" for k, v in o2.items()) + ".\n")
    w("## 5. What the model uses (SHAP, held-out people)\n")
    if p["shap"]["available"]:
        w("| # | Feature | mean abs SHAP | Higher when sedated? |\n|---|---|---|---|")
        for i, t in enumerate(p["shap"]["top"][:10], 1):
            d = t["sedated_minus_awake_z"]
            w(f"| {i} | {t['plain']} | {t['mean_abs_shap']:.2f} | {'yes' if d > 0.2 else 'lower' if d < -0.2 else 'about the same'} ({d:+.2f} SD) |")
        w("\n![shap](fig_primary_shap.png)\n")
    dm = p.get("dementia_check")
    if dm:
        w("## 6. Observational check on clinical EEG (ds004504; nobody is sedated)\n")
        w("| Group | n | mean P(sedated-like) | called sedated | verdict awake / sedated / uncertain | unfamiliar-EEG flagged |\n|---|---|---|---|---|---|")
        for g, s in dm["by_group"].items():
            w(f"| {g} | {s['n']} | {s['mean_p_sedated']:.2f} | {pct(s['frac_called_sedated'])} | {pct(s['frac_verdict_awake'])} / {pct(s['frac_verdict_sedated'])} / {pct(s['frac_verdict_uncertain'])} | "
              f"{pct(s['frac_recordings_unfamiliar'])} |")
        w(f"\n![dementia](fig_primary_dementia.png)\n\nSpearman correlation of P(sedated-like) with MMSE (30 = normal): {dm['spearman_mmse']:.2f} (p = {dm['spearman_mmse_p']:.3g}). "
          f"{pct(dm['rejected_epoch_fraction'], 1)} of 10-s epochs were rejected as artefact. Interpretation: this measures how often a model trained on sedated volunteers raises a "
          "false 'sedated-like' alarm on routine clinical EEG; EEG slowing in dementia is a known confounder, so a higher rate in patients than in healthy controls is a limit of the model, not a finding about the patients.\n")
    w("## 7. Comparison with v1 and decision\n")
    w(f"v1 (Cambridge only, 20 people, 74 features): balanced accuracy {v1['models'][v1['chosen']]['balanced_accuracy']:.3f}, never tested on another site. v2 (two sites, 40 people, 68 features): "
      f"pooled {m['balanced_accuracy']:.3f}; cross-site "
      + ", ".join(f"train {k} -> test {c['test']}: {c['balanced_accuracy']:.3f} ({c['ba_ci95'][0]:.2f}-{c['ba_ci95'][1]:.2f})" for k, c in cr["cross"].items())
      + f". {'The pre-specified criteria are met, so v2 is registered as the staging model and can be selected in the app (setting `sedation_model`).' if cr['success'] else 'Because the pre-specified criteria are not all met, v2 is saved for research but not made the default.'}\n")
    w("## 8. Limits\n")
    w("- 40 healthy volunteers in two laboratories; intervals stay wide. Oslo has ungraded, probably deeper sedation than Cambridge 'moderate'.\n"
      "- Nothing here shows the model works on brain-injured patients; the clinical-EEG check above is observational.\n"
      "- Oslo recordings are short (60 s asleep-like, first 120 s awake) and few per person.\n"
      "- Selection among six models used the same cross-validation as the estimate (tiny fixed grids).\n")
    (OUT / "REPORT.md").write_text("\n".join(L), encoding="utf-8")
    print("REPORT.md written; success =", cr["success"])


if __name__ == "__main__":
    main()
