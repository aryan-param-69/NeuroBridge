"""Run the frozen sedation model on the open Figshare resting-EEG set of patients with prolonged disorders of consciousness and healthy people.

Source: DOI 10.6084/m9.figshare.23552964 (CC BY 4.0). It has NO label file (which recording is a patient, which is healthy, and no CRS-R), so this
is NOT an accuracy test. It answers three narrower questions: does the pipeline run on real patient-type EEG from another site, what do the
model's outputs and the unfamiliar-EEG detector look like there, and is anything alarming (e.g. identical answers for everyone).
Caveat: the recordings are 60 already-cut 2 s epochs concatenated; 10 s windows therefore contain 4 epoch joins.
Run:  uv run python -m nbml.doc_check
"""

import json
from pathlib import Path

import joblib
import matplotlib
import mne
import numpy as np

from . import config as C
from . import sed_predict as P
from .sed_features import STANDARD_19

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

mne.set_log_level("ERROR")
DIR = C.DATA_DIR / "doc_figshare"
OUT = C.REPORT_DIR / "doc_figshare"


def windows(path: Path):
    raw = mne.io.read_raw_brainvision(path, preload=True)
    x = raw.get_data(picks=STANDARD_19) * 1e6
    fs = float(raw.info["sfreq"])
    L = int(10 * fs)
    n = x.shape[1] // L
    ep = np.stack([x[:, i * L:(i + 1) * L] for i in range(n)])
    return ep - ep.mean(axis=1, keepdims=True), fs, float(x.std())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    bundle = joblib.load(C.ML_DIR / "models" / "sedation_v1" / "model.joblib")
    rows = []
    for v in sorted(DIR.glob("*.vhdr")):
        try:
            ep, fs, sd = windows(v)
            o = P.predict_epochs(bundle, ep, fs)
        except Exception as e:  # report, do not hide
            rows.append({"id": v.stem, "error": f"{type(e).__name__}: {str(e)[:80]}"})
            continue
        rows.append({"id": v.stem, "n_windows": len(ep), "p_sedated": o["p_sedated"], "verdict": o["verdict"], "unfamiliar_fraction": o["unfamiliar_fraction"],
                     "unfamiliar": o["unfamiliar"], "signal_sd_uv": sd})
    ok = [r for r in rows if "error" not in r]
    p = np.array([r["p_sedated"] for r in ok])
    summ = {"n_files": len(rows), "n_ok": len(ok), "n_failed": len(rows) - len(ok), "errors": [r for r in rows if "error" in r][:5],
            "verdict_counts": {k: sum(1 for r in ok if r["verdict"] == k) for k in ("awake-like", "sedated-like", "uncertain")},
            "p_sedated_quantiles": {q: float(np.percentile(p, q)) for q in (5, 25, 50, 75, 95)},
            "frac_unfamiliar": float(np.mean([r["unfamiliar"] for r in ok])),
            "mean_unfamiliar_epoch_fraction": float(np.mean([r["unfamiliar_fraction"] for r in ok])),
            "signal_sd_uv_median": float(np.median([r["signal_sd_uv"] for r in ok])),
            "bimodality_note": "A histogram of P(sedated-like) is saved; with no labels no group split is inferred."}
    (OUT / "results.json").write_text(json.dumps({"summary": summ, "rows": rows}, indent=1), encoding="utf-8")
    fig, ax = plt.subplots(1, 2, figsize=(8, 3))
    ax[0].hist(p, bins=20, range=(0, 1), color="#1F5FAE")
    ax[0].set_xlabel("P(sedated-like)")
    ax[0].set_title("98 recordings, no labels", fontsize=9)
    ax[1].hist([r["unfamiliar_fraction"] for r in ok], bins=10, range=(0, 1), color="#B7791F")
    ax[1].set_xlabel("fraction of windows flagged unfamiliar")
    fig.tight_layout()
    fig.savefig(OUT / "fig_doc_distribution.png", dpi=150)
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
