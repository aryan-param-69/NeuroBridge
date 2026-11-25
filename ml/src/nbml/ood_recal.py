"""Recalibrate the unfamiliar-EEG detector of sedation_v2 so that it is useful instead of firing on every other-site recording.

Problem found earlier: the detector was fitted on the two propofol laboratories only, so ordinary resting EEG from any other device
(EEGMMIDB, routine clinical EEG, patient-type EEG) looked "unfamiliar" almost every time.  A detector that always fires carries no information.

Fix tested here (decided before looking at the numbers):
  1. REFERENCE POOL: fit the detector on epochs from four different sources (Cambridge + Oslo propofol, ds004504 routine clinical EEG,
     EEGMMIDB resting EEG) instead of two, so "normal resting EEG from a different machine" is familiar.
  2. Choose the percentile threshold (99 / 99.5 / 99.9) by LEAVE-ONE-DATASET-OUT: fit on three sources, test on the fourth (a site the
     detector has never seen).  Rule: maximise (detection rate of synthetic corruptions) minus (false-flag rate on real held-out sources).
  3. The Figshare patient-type set (no labels) is NEVER used for fitting or choosing: it is the external check.
Detection power is measured with corruptions of real EEG that a doctor should be warned about: muscle noise, white noise, dead channels,
shuffled electrode order (wrong montage), one very noisy electrode.

Run:  uv run python -m nbml.ood_recal            (about 10-20 minutes the first time: it extracts features for EEGMMIDB and Figshare)
"""

import ast
import json
import time

import joblib
import matplotlib
import mne
import numpy as np
from joblib import Parallel, delayed

from . import config as C
from . import dementia_data as DM
from . import oslo_data, prep, sed_data
from . import sed_ood as ood
from .sed_features import extract

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

mne.set_log_level("ERROR")
OUT = C.REPORT_DIR / "ood_recal"
POOL = C.CACHE_DIR / "ood_pool_v1.npz"
FIG_DIR = C.DATA_DIR / "doc_figshare"
SOURCES = ["cambridge", "oslo", "dementia", "eegmmidb"]  # reference candidates; figshare is external only
PCTS = (99.0, 99.5, 99.9)
REC_FRAC = 0.25  # a recording is "unfamiliar" if more than this share of its epochs is flagged (same rule as the app)


# ------------------------------------------------------------------ data pool
def _eegmmidb_one(s: int, run: int):
    f = C.DATASET_DIR / f"S{s:03d}" / f"S{s:03d}R{run:02d}.edf"
    try:
        raw = mne.io.read_raw_edf(f, preload=True, verbose=False)
        ep, _ = prep.prepare_epochs(raw, 60.0)
        if len(ep) == 0:
            return None
        a, names = extract(ep, prep.FS_OUT)
        return a.astype(np.float32), names
    except Exception:
        return None


def _figshare_one(path):
    try:
        raw = mne.io.read_raw_brainvision(path, preload=True)
        ep, _ = prep.prepare_epochs(raw, 50.0)
        if len(ep) == 0:
            return None
        a, names = extract(ep, prep.FS_OUT)
        return a.astype(np.float32), names
    except Exception:
        return None


def build_pool(force: bool = False) -> dict:
    if POOL.exists() and not force:
        z = np.load(POOL, allow_pickle=True)
        return {k: z[k] for k in z.files}
    t0 = time.time()
    c, o, d = sed_data.build(), oslo_data.build(), DM.build()
    names = [str(n) for n in c["features"]]
    assert [str(n) for n in o["features"]] == names and [str(n) for n in d["features"]] == names
    Xs, ds, rec = [], [], []
    rid = 0

    def add(X, label, rec_ids):
        Xs.append(np.asarray(X, dtype=np.float64))
        ds.extend([label] * len(X))
        rec.extend(rec_ids)

    add(c["X"], "cambridge", [rid + int(r) for r in c["recording"]])
    rid += int(c["recording"].max()) + 1
    add(o["X"], "oslo", [rid + int(r) for r in o["recording"]])
    rid += int(o["recording"].max()) + 1
    dr = []
    for line in d["meta"]:
        m = ast.literal_eval(str(line))
        if "n_rows" in m:
            dr += [rid] * m["n_rows"]
            rid += 1
    add(d["X"], "dementia", dr)
    jobs = [(s, run) for s in range(1, 110) for run in (1, 2)]
    res = Parallel(n_jobs=8)(delayed(_eegmmidb_one)(s, r) for s, r in jobs)
    n_mm = 0
    for r in res:
        if r is None:
            continue
        a, nm = r
        assert list(nm) == names
        add(a, "eegmmidb", [rid] * len(a))
        rid += 1
        n_mm += 1
    res = Parallel(n_jobs=8)(delayed(_figshare_one)(p) for p in sorted(FIG_DIR.glob("*.vhdr")))
    n_fig = 0
    for r in res:
        if r is None:
            continue
        a, nm = r
        assert list(nm) == names
        add(a, "figshare", [rid] * len(a))
        rid += 1
        n_fig += 1
    out = {"X": np.concatenate(Xs), "features": np.array(names), "dataset": np.array(ds), "recording": np.array(rec),
           "info": np.array([json.dumps({"eegmmidb_recordings": n_mm, "figshare_recordings": n_fig, "seconds": round(time.time() - t0)})])}
    np.savez_compressed(POOL, **out)
    return out


# ------------------------------------------------------------------ evaluation helpers
def rec_rates(m: dict, X: np.ndarray, rec: np.ndarray) -> dict:
    fl = ood.flags(m, X)
    ids = np.unique(rec)
    frac = np.array([fl[rec == r].mean() for r in ids])
    return {"n_recordings": int(len(ids)), "epoch_flag_rate": float(fl.mean()), "recording_unfamiliar_rate": float((frac > REC_FRAC).mean())}


def corruptions(ep: np.ndarray, rng: np.random.Generator) -> dict:
    n, c, t = ep.shape
    out = {}
    fs = prep.FS_OUT
    # muscle-like: noise band-limited to 25-45 Hz
    from scipy.signal import butter, sosfiltfilt

    sos = butter(4, [25, 44], btype="band", fs=fs, output="sos")
    mus = sosfiltfilt(sos, rng.normal(size=ep.shape), axis=-1)
    out["muscle_noise"] = ep + 20 * mus / mus.std()
    out["white_noise"] = ep + rng.normal(0, 30, size=ep.shape)
    flat = ep.copy()
    for i in range(n):
        flat[i, rng.choice(c, 3, replace=False)] = 0.0
    out["three_dead_channels"] = flat + rng.normal(0, 1e-3, size=flat.shape)
    sh = ep.copy()
    for i in range(n):
        sh[i] = ep[i, rng.permutation(c)]
    out["shuffled_electrodes"] = sh
    bad = ep.copy()
    for i in range(n):
        k = rng.integers(c)
        bad[i, k] += rng.normal(0, 150, size=t)
    out["one_noisy_electrode"] = bad
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pool = build_pool()
    X, ds, rec = pool["X"], pool["dataset"], pool["recording"]
    names = [str(n) for n in pool["features"]]
    bundle = joblib.load(C.ML_DIR / "models" / "sedation_v2" / "model.joblib")
    keep = [names.index(n) for n in bundle["features"]]
    X = X[:, keep]
    counts = {s: {"epochs": int((ds == s).sum()), "recordings": int(len(np.unique(rec[ds == s])))} for s in SOURCES + ["figshare"]}

    # ---- A. current detector (fit on the two propofol laboratories) -> other sources
    res = {"pool": counts, "current": {}, "lodo": {}, "corruption": {}, "external_figshare": {}}
    two = np.isin(ds, ["cambridge", "oslo"])
    cur = ood.fit(X[two])
    for s in ("dementia", "eegmmidb", "figshare"):
        res["current"][s] = rec_rates(cur, X[ds == s], rec[ds == s])

    # ---- B. leave-one-dataset-out, and detection of corruptions with the EEGMMIDB-free detector
    rng = np.random.default_rng(0)
    # clean EEGMMIDB epochs (rows) for the corruption test come from re-reading files: use run 2 (eyes closed) of 40 subjects
    ep_list = []
    for s in range(1, 110):
        if len(ep_list) >= 40:
            break
        try:
            raw = mne.io.read_raw_edf(C.DATASET_DIR / f"S{s:03d}" / f"S{s:03d}R02.edf", preload=True, verbose=False)
            ep, _ = prep.prepare_epochs(raw, 60.0)
        except Exception:
            continue
        if len(ep):
            ep_list.append(ep)
    ep_all = np.concatenate(ep_list)
    rec_ep = np.concatenate([[i] * len(e) for i, e in enumerate(ep_list)])
    cor_feats = {"clean": extract(ep_all, prep.FS_OUT)[0]}
    for k, v in corruptions(ep_all, rng).items():
        cor_feats[k] = extract(v, prep.FS_OUT)[0]
    cor_feats = {k: v[:, [names.index(n) for n in bundle["features"]]] for k, v in cor_feats.items()}

    table = []
    for pct in PCTS:
        row = {"pct": pct, "lodo_false_flag": {}}
        for held in SOURCES:
            ref = np.isin(ds, [s for s in SOURCES if s != held])
            m = ood.fit(X[ref], pct=pct)
            row["lodo_false_flag"][held] = rec_rates(m, X[ds == held], rec[ds == held])
        row["mean_lodo_false_rate"] = float(np.mean([v["recording_unfamiliar_rate"] for v in row["lodo_false_flag"].values()]))
        m_noMM = ood.fit(X[np.isin(ds, ["cambridge", "oslo", "dementia"])], pct=pct)
        det = {}
        for k, F in cor_feats.items():
            fin = np.isfinite(F).all(axis=1)
            Fz = np.where(fin[:, None], F, 0.0)
            fl = ood.flags(m_noMM, Fz) | ~fin
            frac = np.array([fl[rec_ep == r].mean() for r in np.unique(rec_ep)])
            det[k] = float((frac > REC_FRAC).mean())
        row["corruption_detection"] = det
        row["mean_detection"] = float(np.mean([v for k, v in det.items() if k != "clean"]))
        row["clean_false_flag_eegmmidb_closed"] = det["clean"]
        row["score"] = row["mean_detection"] - row["mean_lodo_false_rate"]
        table.append(row)
    best = max(table, key=lambda r: r["score"])
    res["lodo"] = table
    res["chosen_pct"] = best["pct"]

    # ---- C. final detector: reference = all four sources, chosen percentile; external check on Figshare (never used)
    final = ood.fit(X[np.isin(ds, SOURCES)], pct=best["pct"])
    res["external_figshare"] = rec_rates(final, X[ds == "figshare"], rec[ds == "figshare"])
    res["final_in_pool"] = {s: rec_rates(final, X[ds == s], rec[ds == s]) for s in SOURCES}
    res["final_components"] = int(final["k"])
    (OUT / "results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    joblib.dump({"ood": final, "pct": best["pct"], "sources": SOURCES, "features": bundle["features"]}, OUT / "ood_final.joblib")

    fig, ax = plt.subplots(1, 2, figsize=(9, 3.2))
    for r in table:
        rates = [v["recording_unfamiliar_rate"] for v in r["lodo_false_flag"].values()]
        ax[0].plot(list(r["lodo_false_flag"]), rates, marker="o", label=f"p{r['pct']}")
    ax[0].set_title("false flags on a never-seen source", fontsize=9)
    ax[0].legend(fontsize=7)
    ax[0].tick_params(axis="x", labelsize=7)
    ks = [k for k in table[0]["corruption_detection"] if k != "clean"]
    for r in table:
        ax[1].plot(ks, [r["corruption_detection"][k] for k in ks], marker="o", label=f"p{r['pct']}")
    ax[1].set_title("detection of corrupted EEG", fontsize=9)
    ax[1].tick_params(axis="x", labelsize=6, rotation=20)
    fig.tight_layout()
    fig.savefig(OUT / "fig_ood_recal.png", dpi=150)
    print(json.dumps({k: res[k] for k in ("pool", "current", "chosen_pct", "external_figshare", "final_in_pool")}, indent=1))
    for r in table:
        print(r["pct"], "false", round(r["mean_lodo_false_rate"], 3), "detect", round(r["mean_detection"], 3), r["corruption_detection"])


if __name__ == "__main__":
    main()
