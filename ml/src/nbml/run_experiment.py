"""Run the full, reproducible experiment:  python -m nbml.run_experiment --task left_vs_right

Stages (each saves to ml/reports/<task>/ and to MongoDB; reruns skip finished stages unless --force):
  dev          nested subject-wise CV of every model on the development subjects
  select       pre-specified selection rule (paired subject bootstrap)
  holdout      refit the chosen model on all development subjects, evaluate ONCE on locked hold-out subjects
  permutation  label-permutation test of the whole chosen pipeline (leakage / chance sanity check)
"""

import argparse
import datetime as dt
import json
import os
import platform
import sys

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):  # one thread per worker
    os.environ.setdefault(_v, "1")

import joblib
import numpy as np
import sklearn
from joblib import Parallel, delayed

from . import config as C
from .cv import (
    fit_predict,
    fold_metrics,
    nested_cv,
    outer_folds,
    pooled_oof,
    split_holdout,
)
from .data import build_dataset
from .features import bandpower, covariances, recenter_per_subject
from .metrics import calibration_curve, chance_upper_bound, evaluate, per_subject_accuracy
from .models import MODELS, PRIMARY
from .select import choose_model, paired_bootstrap, subject_counts
from .store import Store, clean

MODEL_VERSION = "0.1.0"


def write_json(path, obj) -> None:
    path.write_text(json.dumps(clean(obj), indent=2))


def load_inputs(task: str, max_subjects: int | None):
    d = build_dataset(task)
    X, y, subject = d["X"], d["y"].astype(int), d["subject"].astype(int)
    if max_subjects:  # smoke-test mode only
        keep = np.isin(subject, np.array(sorted(set(subject.tolist())))[:max_subjects])
        X, y, subject = X[keep], y[keep], subject[keep]
    feat_cache = C.CACHE_DIR / f"{task}.features{'_smoke' if max_subjects else ''}.npz"
    if feat_cache.exists():
        z = np.load(feat_cache)
        bp, cov = z["bp"], z["cov"]
    else:
        bp, _ = bandpower(X)
        cov = covariances(X)
        np.savez_compressed(feat_cache, bp=bp, cov=cov)
    return ({"bandpower": bp, "cov": cov, "cov_ra": recenter_per_subject(cov, subject), "raw": X},
            y, subject, d["excluded"])


def stage_dev(ctx, out, store, model_names, n_jobs):
    y, subject, inputs, dev = ctx["y"], ctx["subject"], ctx["inputs"], ctx["dev"]
    results = nested_cv(model_names, inputs, y, subject, dev, n_jobs=n_jobs)
    summary = {}
    oof = {}
    for m, rs in results.items():
        idx, proba = pooled_oof(rs)
        fm = fold_metrics(rs, y)
        pooled = evaluate(y[idx], proba)
        keys = ["balanced_accuracy", "accuracy", "precision_macro", "recall_macro", "f1_macro", "roc_auc", "brier", "ece"]
        fold_vals = {k: [f[k] for f in fm] for k in keys}
        summary[m] = {
            "model": m, "input": MODELS[m].input, "fold_metrics": fm, "pooled_metrics": pooled,
            "fold_mean": {k: float(np.mean(v)) for k, v in fold_vals.items()},
            "fold_sd": {k: float(np.std(v, ddof=1)) for k, v in fold_vals.items()},
            "per_subject_accuracy": per_subject_accuracy(y[idx], proba, subject[idx]),
            "calibration": calibration_curve(y[idx], proba),
            "train_seconds_total": float(sum(f["seconds"] for f in fm)),
        }
        oof[m] = (idx, proba)
        write_json(out / f"cv_{m}.json", summary[m])
        store.upsert("cv_results", f"{ctx['exp_id']}:{m}",
                     {"experiment_id": ctx["exp_id"], "stage": "dev_nested_cv", **summary[m],
                      "oof_index": idx, "oof_proba": np.round(proba, 5)})
    np.savez_compressed(out / "oof.npz", **{f"{m}__idx": v[0] for m, v in oof.items()},
                        **{f"{m}__proba": v[1] for m, v in oof.items()})
    return summary, oof


def stage_select(ctx, out, store, summary, oof):
    y, subject = ctx["y"], ctx["subject"]
    counts = {}
    for m, (idx, proba) in oof.items():
        order = np.argsort(idx)  # same epoch order for every model
        _, c = subject_counts(y[idx][order], proba[order], subject[idx][order])
        counts[m] = c
    boot = paired_bootstrap(counts)
    point = {m: summary[m]["pooled_metrics"]["balanced_accuracy"] for m in summary}
    brier = {m: summary[m]["pooled_metrics"]["brier"] for m in summary}
    sel = choose_model(point, brier, boot)
    sel["n_boot"] = len(next(iter(boot.values())))
    for row in sel["table"]:
        lo, hi = np.percentile(boot[row["model"]], [2.5, 97.5])
        row["balanced_accuracy_ci95"] = [float(lo), float(hi)]
    write_json(out / "selection.json", sel)
    store.set_fields("experiments", ctx["exp_id"], {"selection": sel})
    return sel


def stage_holdout(ctx, out, store, sel, summary):
    y, subject, inputs, dev, hold = ctx["y"], ctx["subject"], ctx["inputs"], ctx["dev"], ctx["hold"]
    m = sel["chosen"]
    spec = MODELS[m]
    X = inputs[spec.input]
    tr = np.where(np.isin(subject, dev))[0]
    te = np.where(np.isin(subject, hold))[0]
    r = fit_predict(m, X, y, subject, tr, te, fold=99)  # tunes by inner CV on DEVELOPMENT subjects only
    res = evaluate(y[te], r.proba)
    res.update({
        "model": m, "best_params": r.best_params, "n_holdout_subjects": len(hold),
        "chance_upper_bound_95": chance_upper_bound(len(te)),
        "per_subject_accuracy": per_subject_accuracy(y[te], r.proba, subject[te]),
        "calibration": calibration_curve(y[te], r.proba),
        "note": "evaluated once, after model selection, on subjects never used for training or tuning",
    })
    write_json(out / "holdout.json", res)
    store.set_fields("experiments", ctx["exp_id"], {"holdout": res})
    np.savez_compressed(out / "holdout_pred.npz", idx=te, proba=r.proba)

    # Persist the development-trained model so the app can load it later (status: staging).
    # Re-fit with the chosen parameters (identical to the evaluated model) to keep the artifact itself.
    from sklearn.base import clone
    est = clone(spec.build()).set_params(**r.best_params).fit(X[tr], y[tr])
    art = C.ML_DIR / "models" / m / MODEL_VERSION
    art.mkdir(parents=True, exist_ok=True)
    joblib.dump(est, art / "model.joblib")
    store.upsert("model_registry", f"{m}:{MODEL_VERSION}", {
        "name": m, "version": MODEL_VERSION, "task": ctx["task"], "algorithm": spec.name, "status": "staging",
        "classes": C.TASKS[ctx["task"]]["classes"], "feature_input": spec.input,
        "required_channels": 64, "sfreq_hz": C.SFREQ, "epoch_window_s": [C.EPOCH_START_S, C.EPOCH_END_S],
        "filter_hz": list(C.FILTER_BAND), "best_params": r.best_params,
        "cv_metrics": summary[m]["pooled_metrics"], "holdout_metrics": res,
        "trained_on": {"dataset": "PhysioNet EEGMMIDB 1.0.0", "n_subjects": len(dev),
                       "subjects": dev.tolist()},
        "artifact_path": str((art / "model.joblib").relative_to(C.ROOT)),
        "intended_use": "Research benchmark of motor-imagery decoding in healthy volunteers.",
        "out_of_scope": ["consciousness assessment", "cognitive motor dissociation detection",
                         "any diagnostic or prognostic use", "patient data (not validated)"],
        "limitations": ["trained on healthy volunteers only", "single recording session per subject",
                        "64-channel BioSemi-style montage required", "no out-of-distribution detector yet"],
    })
    return res


def _perm_once(spec_name, params, X, y, subject, folds, seed, permute: bool):
    rng = np.random.default_rng(seed)
    yy = y.copy()
    if permute:  # shuffle labels within each subject: keeps class balance, breaks signal-label link
        for s in np.unique(subject):
            ix = np.where(subject == s)[0]
            yy[ix] = rng.permutation(yy[ix])
    idxs, probas = [], []
    for i, (tr_s, te_s) in enumerate(folds):
        tr, te = np.where(np.isin(subject, tr_s))[0], np.where(np.isin(subject, te_s))[0]
        r = fit_predict(spec_name, X, yy, subject, tr, te, i, tune=False, params=params)
        idxs.append(te)
        probas.append(r.proba)
    idx, proba = np.concatenate(idxs), np.concatenate(probas)
    return evaluate(yy[idx], proba)["balanced_accuracy"]


def stage_permutation(ctx, out, store, sel, n_perm, n_jobs):
    m = sel["chosen"]
    params = json.loads((out / "holdout.json").read_text())["best_params"]
    X = ctx["inputs"][MODELS[m].input]
    folds = outer_folds(ctx["dev"])
    real = _perm_once(m, params, X, ctx["y"], ctx["subject"], folds, 0, permute=False)
    null = Parallel(n_jobs=n_jobs, verbose=5)(
        delayed(_perm_once)(m, params, X, ctx["y"], ctx["subject"], folds, 1000 + i, True) for i in range(n_perm))
    null = np.array(null)
    res = {"model": m, "params": params, "n_permutations": n_perm, "observed_balanced_accuracy": float(real),
           "null_mean": float(null.mean()), "null_sd": float(null.std(ddof=1)),
           "null_max": float(null.max()), "null_95th_percentile": float(np.percentile(null, 95)),
           "p_value": float((1 + (null >= real).sum()) / (1 + n_perm)), "null": null.tolist(),
           "note": "labels shuffled within subject; hyper-parameters fixed to the tuned values (no re-tuning)"}
    write_json(out / "permutation.json", res)
    store.upsert("permutation", f"{ctx['exp_id']}:{m}", {"experiment_id": ctx["exp_id"], **res})
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="left_vs_right", choices=list(C.TASKS))
    ap.add_argument("--models", default="all")
    ap.add_argument("--stage", default="all", choices=["dev", "select", "holdout", "permutation", "all"])
    ap.add_argument("--n-jobs", type=int, default=10)
    ap.add_argument("--n-perm", type=int, default=100)
    ap.add_argument("--max-subjects", type=int, default=None, help="smoke test only")
    ap.add_argument("--tag", default="", help="suffix for the output folder (exploratory runs)")
    ap.add_argument("--no-mongo", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    names = list(PRIMARY) if a.models == "all" else a.models.split(",")
    tag = f"{a.task}{'_smoke' if a.max_subjects else ''}{'_' + a.tag if a.tag else ''}"
    out = C.REPORT_DIR / tag
    out.mkdir(parents=True, exist_ok=True)
    store = Store(enabled=not a.no_mongo)
    print(f"MongoDB: {'connected' if store.ok else 'NOT used (' + str(store.error) + ')'}")

    inputs, y, subject, excluded = load_inputs(a.task, a.max_subjects)
    dev, hold = split_holdout(subject)
    ctx = {"task": a.task, "exp_id": f"{tag}-seed{C.SEED}", "inputs": inputs, "y": y, "subject": subject,
           "dev": dev, "hold": hold}
    manifest = json.loads((C.CACHE_DIR / f"{a.task}.manifest.json").read_text())
    store.upsert("experiments", ctx["exp_id"], {
        "task": a.task, "created_at": dt.datetime.now(dt.UTC), "seed": C.SEED, "models": names,
        "protocol": {"holdout_fraction": C.HOLDOUT_FRACTION, "outer_folds": C.OUTER_FOLDS,
                     "inner_folds": C.INNER_FOLDS, "primary_metric": "balanced_accuracy"},
        "dataset": manifest, "excluded_subjects": excluded,
        "subjects_dev": dev.tolist(), "subjects_holdout": hold.tolist(),
        "versions": {"python": sys.version.split()[0], "sklearn": sklearn.__version__,
                     "numpy": np.__version__, "platform": platform.platform()},
        "grids": {m: MODELS[m].grid for m in names},
    })
    print(f"{len(dev)} development subjects, {len(hold)} locked hold-out subjects, {len(y)} epochs")

    if a.stage in ("dev", "all"):
        summary, oof = stage_dev(ctx, out, store, names, a.n_jobs)
    else:
        summary = {m: json.loads((out / f"cv_{m}.json").read_text()) for m in names}
        z = np.load(out / "oof.npz")
        oof = {m: (z[f"{m}__idx"], z[f"{m}__proba"]) for m in names}
    for m in names:
        s = summary[m]["pooled_metrics"]
        print(f"  {m:18s} bal.acc {s['balanced_accuracy']:.3f}  auc {s['roc_auc']:.3f}  brier {s['brier']:.3f}")

    if a.stage in ("select", "holdout", "permutation", "all"):
        sel = stage_select(ctx, out, store, summary, oof)
        print("selected:", sel["chosen"], "| tied with best:", sel["tied"])
    if a.stage in ("holdout", "permutation", "all"):
        if not (out / "holdout.json").exists() or a.force or a.stage == "all":
            res = stage_holdout(ctx, out, store, sel, summary)
            print(f"HOLD-OUT ({sel['chosen']}): bal.acc {res['balanced_accuracy']:.3f}, auc {res['roc_auc']:.3f}")
    if a.stage in ("permutation", "all"):
        res = stage_permutation(ctx, out, store, sel, a.n_perm, a.n_jobs)
        print(f"permutation: observed {res['observed_balanced_accuracy']:.3f} vs null mean {res['null_mean']:.3f}, "
              f"p={res['p_value']:.4f}")


if __name__ == "__main__":
    main()
