"""Compare an exploratory model with the pre-specified winner on the SAME development subjects and folds.

Usage: python -m nbml.compare_extra --task left_vs_right --tag ra --extra ts_ra_logreg
Paired subject-level bootstrap of the balanced-accuracy difference (subjects are resampled, not epochs).
"""

import argparse
import json

import numpy as np

from . import config as C
from .select import paired_bootstrap, subject_counts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="left_vs_right")
    ap.add_argument("--tag", default="ra")
    ap.add_argument("--extra", default="ts_ra_logreg")
    ap.add_argument("--base", default=None, help="defaults to the model chosen by the main experiment")
    a = ap.parse_args(argv)

    main_dir = C.REPORT_DIR / a.task
    extra_dir = C.REPORT_DIR / f"{a.task}_{a.tag}"
    base = a.base or json.loads((main_dir / "selection.json").read_text())["chosen"]
    d = np.load(C.CACHE_DIR / f"{a.task}.npz")
    y, subject = d["y"].astype(int), d["subject"].astype(int)

    counts, point, brier = {}, {}, {}
    for name, folder in ((base, main_dir), (a.extra, extra_dir)):
        z = np.load(folder / "oof.npz")
        idx, proba = z[f"{name}__idx"], z[f"{name}__proba"]
        order = np.argsort(idx)
        idx, proba = idx[order], proba[order]
        subs, c = subject_counts(y[idx], proba, subject[idx])
        counts[name] = c
        pred = (proba >= 0.5).astype(int)
        pos, neg = y[idx] == 1, y[idx] == 0
        tpr, tnr = ((pred == 1) & pos).sum() / pos.sum(), ((pred == 0) & neg).sum() / neg.sum()
        point[name] = float((tpr + tnr) / 2)
        brier[name] = float(np.mean((proba - y[idx]) ** 2))
        counts[name + "__subs"] = subs
    assert (counts[base + "__subs"] == counts[a.extra + "__subs"]).all(), "different subjects: not comparable"
    boot = paired_bootstrap({base: counts[base], a.extra: counts[a.extra]})
    diff = boot[a.extra] - boot[base]
    lo, hi = np.percentile(diff, [2.5, 97.5])
    # per-subject view: in how many people did the extra model do better?
    per = {}
    for name in (base, a.extra):
        z = np.load((extra_dir if name == a.extra else main_dir) / "oof.npz")
        idx, proba = z[f"{name}__idx"], z[f"{name}__proba"]
        per[name] = {int(s): float(((proba[subject[idx] == s] >= 0.5).astype(int) == y[idx][subject[idx] == s]).mean())
                     for s in np.unique(subject[idx])}
    better = sum(per[a.extra][s] > per[base][s] for s in per[base])
    worse = sum(per[a.extra][s] < per[base][s] for s in per[base])
    out = {"base": base, "extra": a.extra, "balanced_accuracy": point, "brier": brier,
           "difference_extra_minus_base": point[a.extra] - point[base], "difference_ci95": [float(lo), float(hi)],
           "ci_excludes_zero": bool(lo > 0 or hi < 0), "n_subjects": len(per[base]),
           "subjects_better": int(better), "subjects_worse": int(worse),
           "status": "EXPLORATORY: judged on development subjects only. The locked hold-out was already used once; "
                     "confirm on a NEW dataset before relying on this."}
    (extra_dir / "comparison.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
