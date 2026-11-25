# ruff: noqa: E501
"""Pilot on real patient EEG: vegetative state (VS) vs minimally conscious state (MCS) in the Mendeley polysomnography dataset.
Plan: reports/psg_doc/PRESPEC.md (written before any feature was computed).   Run:  uv run python -m nbml.psg_doc
"""

import json
import re
import time

import matplotlib
import mne
import numpy as np
from joblib import Parallel, delayed
from scipy.signal import welch
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import config as C
from .sed_features import BANDS, _perm_entropy, _wpli

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

mne.set_log_level("ERROR")
DIR = C.DATA_DIR / "mendeley_doc_psg"
OUT = C.REPORT_DIR / "psg_doc"
CACHE = C.CACHE_DIR / "psg_doc_features_v1.npz"
CH = ["F3", "F4", "C3", "C4", "O1", "O2"]
EPOCH_S = 30.0
REJECT_UV = 400.0
FLAT_UV = 0.5
MIN_EPOCHS = 60
FEATURES = ["rel_delta", "rel_theta", "rel_alpha", "rel_beta", "spectral_entropy", "perm_entropy", "slope", "hjorth_mobility", "hjorth_complexity",
            "alpha_over_delta", "frontal_minus_occipital_alpha", "wpli_delta", "wpli_theta", "wpli_alpha"]
N_PERM = 1000
N_BOOT = 2000


def label_of(name: str) -> tuple[int, str]:
    m = re.search(r"_(VS|MCS[+-])\.edf$", name)
    if not m:
        raise ValueError(name)
    return (0 if m.group(1) == "VS" else 1), m.group(1)


def epoch_features(X: np.ndarray, fs: float) -> np.ndarray:
    """X: (n, 6, T) microvolts -> (n, len(FEATURES))."""
    f, psd = welch(X, fs=fs, nperseg=int(4 * fs), noverlap=int(2 * fs), axis=-1)
    band = (f >= 1) & (f < 45)
    tot = psd[..., band].sum(-1) + 1e-30
    rel = {b: psd[..., (f >= lo) & (f < hi)].sum(-1) / tot for b, (lo, hi) in BANDS.items() if b != "gamma"}
    p = psd[..., band] / tot[..., None]
    sent = -(p * np.log(p + 1e-30)).sum(-1) / np.log(p.shape[-1])
    d1, d2 = np.diff(X, axis=-1), np.diff(np.diff(X, axis=-1), axis=-1)
    v0, v1, v2 = X.var(-1) + 1e-30, d1.var(-1) + 1e-30, d2.var(-1) + 1e-30
    mob = np.sqrt(v1 / v0)
    comp = np.sqrt(v2 / v1) / mob
    pe = _perm_entropy(X, 3, max(1, int(round(fs / 125))))
    fm = (f >= 2) & (f <= 40) & ~((f >= 7) & (f <= 14))
    lf = np.log10(f[fm])
    lp = np.log10(psd[..., fm] + 1e-30)
    slope = ((lf - lf.mean()) * (lp - lp.mean(-1, keepdims=True))).sum(-1) / ((lf - lf.mean()) ** 2).sum()
    fr, oc = [CH.index("F3"), CH.index("F4")], [CH.index("O1"), CH.index("O2")]
    out = {
        "rel_delta": rel["delta"].mean(1), "rel_theta": rel["theta"].mean(1), "rel_alpha": rel["alpha"].mean(1), "rel_beta": rel["beta"].mean(1),
        "spectral_entropy": sent.mean(1), "perm_entropy": pe.mean(1), "slope": slope.mean(1), "hjorth_mobility": mob.mean(1), "hjorth_complexity": comp.mean(1),
        "alpha_over_delta": np.log10((rel["alpha"] + 1e-9) / (rel["delta"] + 1e-9)).mean(1),
        "frontal_minus_occipital_alpha": rel["alpha"][:, fr].mean(1) - rel["alpha"][:, oc].mean(1),
    }
    iu = np.triu_indices(len(CH), 1)
    for b in ("delta", "theta", "alpha"):
        W = np.stack([_wpli(X[i], fs, BANDS[b]) for i in range(len(X))])
        out[f"wpli_{b}"] = W[:, iu[0], iu[1]].mean(1)
    return np.stack([out[k] for k in FEATURES], axis=1)


def one_patient(path) -> dict:
    y, lab = label_of(path.name)
    raw = mne.io.read_raw_edf(path, include=CH, preload=True, verbose=False)
    raw.reorder_channels(CH)
    fs = float(raw.info["sfreq"])
    raw.notch_filter([50.0], method="iir", verbose=False)
    raw.filter(0.5, 45.0, method="iir", iir_params={"order": 4, "ftype": "butter", "output": "sos"}, phase="zero", verbose=False)
    x = raw.get_data() * 1e6
    x -= x.mean(axis=0, keepdims=True)
    L = int(EPOCH_S * fs)
    n = x.shape[1] // L
    ep = np.stack([x[:, i * L:(i + 1) * L] for i in range(n)])
    ptp = ep.max(2) - ep.min(2)
    bad = ((ptp > REJECT_UV).sum(1) >= 2) | ((ep.std(2) < FLAT_UV).sum(1) >= 2)
    ep = ep[~bad]
    info = {"file": path.name, "label": y, "diagnosis": lab, "hours": round(x.shape[1] / fs / 3600, 2), "epochs_total": int(n), "epochs_kept": int(len(ep))}
    if len(ep) < MIN_EPOCHS:
        return {**info, "excluded": True}
    F = epoch_features(ep, fs)
    F = F[np.isfinite(F).all(1)]
    q = F[:, FEATURES.index("rel_delta")] <= np.quantile(F[:, FEATURES.index("rel_delta")], 0.25)
    return {**info, "excluded": False, "median": np.median(F, 0), "median_low_delta": np.median(F[q], 0), "n_features_epochs": int(len(F))}


def build(force: bool = False) -> dict:
    if CACHE.exists() and not force:
        z = np.load(CACHE, allow_pickle=True)
        return {k: z[k] for k in z.files}
    t0 = time.time()
    rows = Parallel(n_jobs=4)(delayed(one_patient)(p) for p in sorted(DIR.glob("*.edf")))
    ok = [r for r in rows if not r["excluded"]]
    out = {"X": np.stack([r["median"] for r in ok]), "X_low": np.stack([r["median_low_delta"] for r in ok]), "y": np.array([r["label"] for r in ok]),
           "diag": np.array([r["diagnosis"] for r in ok]), "files": np.array([r["file"] for r in ok]),
           "meta": np.array([json.dumps({k: v for k, v in r.items() if k not in ("median", "median_low_delta")}) for r in rows]),
           "seconds": np.array([time.time() - t0])}
    np.savez_compressed(CACHE, **out)
    return out


def model():
    return make_pipeline(StandardScaler(), LogisticRegression(C=0.1, class_weight="balanced", max_iter=1000))


def lopo(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.zeros(len(y))
    for i in range(len(y)):
        tr = np.arange(len(y)) != i
        p[i] = model().fit(X[tr], y[tr]).predict_proba(X[i:i + 1])[0, 1]
    return p


def ba(y, p, t=0.5):
    pr = p >= t
    sens, spec = pr[y == 1].mean(), (~pr)[y == 0].mean()
    return float((sens + spec) / 2), float(sens), float(spec)


def boot_ci(y, p, rng):
    aucs, bas = [], []
    for _ in range(N_BOOT):
        i = rng.integers(0, len(y), len(y))
        if len(np.unique(y[i])) < 2:
            continue
        aucs.append(roc_auc_score(y[i], p[i]))
        bas.append(ba(y[i], p[i])[0])
    return [float(np.percentile(aucs, 2.5)), float(np.percentile(aucs, 97.5))], [float(np.percentile(bas, 2.5)), float(np.percentile(bas, 97.5))]


def analyse(X, y, rng):
    p = lopo(X, y)
    auc = float(roc_auc_score(y, p))
    b, se, sp = ba(y, p)
    auc_ci, ba_ci = boot_ci(y, p, rng)
    null = []
    for _ in range(N_PERM):
        yp = rng.permutation(y)
        null.append(roc_auc_score(yp, lopo(X, yp)))
    null = np.array(null)
    return {"auc": auc, "auc_ci95": auc_ci, "balanced_accuracy": b, "sensitivity_mcs": se, "specificity_vs": sp, "ba_ci95": ba_ci,
            "perm_p": float((1 + (null >= auc).sum()) / (N_PERM + 1)), "perm_null_mean_auc": float(null.mean()), "oof": p.tolist()}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    d = build()
    X, Xl, y = d["X"], d["X_low"], d["y"]
    meta = [json.loads(m) for m in d["meta"]]
    rng = np.random.default_rng(0)
    res = {"n_patients": int(len(y)), "n_vs": int((y == 0).sum()), "n_mcs": int((y == 1).sum()), "diagnoses": {k: int((d["diag"] == k).sum()) for k in np.unique(d["diag"])},
           "excluded": [m["file"] for m in meta if m.get("excluded")], "median_hours": float(np.median([m["hours"] for m in meta])),
           "median_epochs_kept": float(np.median([m["epochs_kept"] for m in meta])), "features": FEATURES}
    res["primary"] = analyse(X, y, rng)
    res["sensitivity_low_delta"] = analyse(Xl, y, rng)
    res["univariate_auc_mcs_higher"] = {f: float(roc_auc_score(y, X[:, i])) for i, f in enumerate(FEATURES)}
    top = max(res["univariate_auc_mcs_higher"].items(), key=lambda kv: abs(kv[1] - 0.5))
    drop = [i for i, f in enumerate(FEATURES) if f != top[0]]
    res["primary_without_top_feature"] = {"dropped": top[0], **{k: v for k, v in analyse(X[:, drop], y, np.random.default_rng(1)).items() if k != "oof"}}
    for k in ("primary", "sensitivity_low_delta"):
        res[k].pop("oof")
    (OUT / "results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    fig, ax = plt.subplots(1, 2, figsize=(8, 3.2))
    ax[0].boxplot([X[y == 0, FEATURES.index("alpha_over_delta")], X[y == 1, FEATURES.index("alpha_over_delta")]], tick_labels=["VS", "MCS"])
    ax[0].set_title("log alpha/delta per patient", fontsize=9)
    ax[1].boxplot([X[y == 0, FEATURES.index("rel_delta")], X[y == 1, FEATURES.index("rel_delta")]], tick_labels=["VS", "MCS"])
    ax[1].set_title("delta share per patient", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "fig_psg_doc.png", dpi=150)
    print(json.dumps({k: res[k] for k in res if k not in ("features", "univariate_auc_mcs_higher")}, indent=1))
    print(json.dumps(res["univariate_auc_mcs_higher"], indent=1))


if __name__ == "__main__":
    main()
