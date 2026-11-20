"""Eye-blink removal with FastICA (pipeline `resting` v1.1.0 and later).

Method (deterministic: fixed seed, fixed rules, every decision is returned for the provenance record):
  1. Fit FastICA on a copy that is high-passed at 1 Hz (ICA fits slow drifts badly) and use at most `fit_max_s`
     seconds of data. Apply the unmixing to the full-length 0.5 Hz data.
  2. A component is called a BLINK component only if ALL of these hold:
       - its scalp map is concentrated at the frontal-pole channels (mean |weight| there / mean |weight| elsewhere
         >= `frontal_ratio_min`),
       - its time course is sparse and spiky (excess kurtosis >= `kurtosis_min`),
       - most of its power is slow (share of 0.5-4 Hz power >= `low_freq_share_min`).
  3. At most `max_remove` components are removed (the most blink-like first).

No EOG channel is needed, but the method then depends on frontal-pole channels (Fp1, Fp2, Fpz, AF7, AF8, AFz...). Without them
the step is skipped and a warning is raised. This rule set is a transparent heuristic, NOT a validated artefact classifier:
it has been tested on simulated blinks added to real recordings and must be checked on real clinical recordings. Saccades,
muscle and heartbeat artefacts are not removed.
"""

import mne
import numpy as np
from scipy.signal import welch
from scipy.stats import kurtosis

mne.set_log_level("ERROR")
FRONTAL_POLE = ("Fp1", "Fp2", "Fpz", "AF7", "AF8", "AFz", "AF3", "AF4")


def remove_blinks(work: mne.io.BaseRaw, p: dict, say=None) -> dict:
    """Remove blink components from `work` in place. Returns a JSON-safe summary of every decision."""
    names = list(work.ch_names)
    frontal = [n for n in names if n in FRONTAL_POLE]
    sf = float(work.info["sfreq"])
    base = {"method": "fastica", "frontal_channels": frontal, "removed": [], "components": []}
    if len(frontal) < 1:
        return {**base, "applied": False, "reason": "no frontal-pole channels (Fp1, Fp2, Fpz, AF7, AF8, AFz, AF3, AF4) are present"}
    if len(names) < p["min_channels"]:
        return {**base, "applied": False, "reason": f"only {len(names)} channels (need at least {p['min_channels']})"}
    if work.n_times / sf < p["min_duration_s"]:
        return {**base, "applied": False, "reason": f"recording shorter than {p['min_duration_s']} s"}

    n_comp = min(int(p["n_components"]), len(names) - 1)
    fit = work.copy()
    if p["fit_max_s"] and fit.n_times / sf > p["fit_max_s"]:
        fit.crop(tmax=p["fit_max_s"] - 1 / sf)
    fit.filter(1.0, None, method="fir", fir_design="firwin", phase="zero", verbose=False)
    ica = mne.preprocessing.ICA(n_components=n_comp, method="fastica", rng=p["seed"], max_iter=p["max_iter"],
                                fit_params={"tol": 1e-4})
    try:
        ica.fit(fit, verbose=False)
    except Exception as e:  # convergence or rank problems must not break the whole run
        return {**base, "applied": False, "reason": f"FastICA failed: {type(e).__name__}"}

    src = ica.get_sources(work).get_data()  # (components, samples), from the full 0.5 Hz data
    A = np.abs(ica.get_components())  # (channels, components): scalp map of every component
    fr_idx = [names.index(n) for n in frontal]
    other = [i for i in range(len(names)) if i not in fr_idx]
    comps = []
    for k in range(src.shape[0]):
        ratio = float(A[fr_idx, k].mean() / max(A[other, k].mean(), 1e-12)) if other else float("inf")
        kurt = float(kurtosis(src[k]))
        f, pxx = welch(src[k], fs=sf, nperseg=int(min(src.shape[1], 4 * sf)))
        total = pxx[(f >= 0.5) & (f <= 45)].sum()
        low = float(pxx[(f >= 0.5) & (f <= 4)].sum() / total) if total > 0 else 0.0
        is_blink = ratio >= p["frontal_ratio_min"] and kurt >= p["kurtosis_min"] and low >= p["low_freq_share_min"]
        comps.append({"index": k, "frontal_ratio": round(ratio, 2), "kurtosis": round(kurt, 2), "low_freq_share": round(low, 3),
                      "blink_like": bool(is_blink)})
    cand = sorted((c for c in comps if c["blink_like"]), key=lambda c: -(c["frontal_ratio"] * c["kurtosis"]))
    drop = [c["index"] for c in cand[: p["max_remove"]]]
    if drop:
        ica.exclude = drop
        ica.apply(work, verbose=False)
    return {**base, "applied": True, "n_components": int(src.shape[0]), "removed": drop, "components": comps,
            "n_blink_like_found": len(cand), "note": "heuristic blink detection; verify on your own recordings"}
