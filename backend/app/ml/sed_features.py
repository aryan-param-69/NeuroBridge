# VENDORED from ml/src/nbml (parity is tested in tests/test_ai_sedation.py). Change both copies together.
"""Compact, interpretable, device-independent EEG features for the sedation / level-of-responsiveness task.

Why this feature set (and not "everything"): with ~20 people, hundreds of correlated features overfit. Each feature is
  - a standard, published marker (relative band power, Hjorth, spectral / permutation entropy, aperiodic slope, wPLI),
  - averaged into 5 regions + the whole head so it can be computed from any 19-channel 10-20 recording,
  - computed per epoch from that epoch alone and without labels (nothing is fitted here, so nothing can leak).

Epochs are 10 s long (the dataset's own epoching). The names are plain-language so SHAP output can be read by a doctor.
"""

import numpy as np
from scipy.signal import stft, welch

BANDS = {"delta": (1.0, 4.0), "theta": (4.0, 8.0), "alpha": (8.0, 13.0), "beta": (13.0, 30.0), "gamma": (30.0, 45.0)}
STANDARD_19 = ["Fp1", "Fp2", "F7", "F3", "Fz", "F4", "F8", "T7", "C3", "Cz", "C4", "T8", "P7", "P3", "Pz", "P4", "P8", "O1", "O2"]
REGIONS = {
    "frontal": ["Fp1", "Fp2", "F7", "F3", "Fz", "F4", "F8"],
    "central": ["C3", "Cz", "C4"],
    "temporal": ["T7", "T8", "P7", "P8"],
    "parietal": ["P3", "Pz", "P4"],
    "occipital": ["O1", "O2"],
}
PLAIN = {
    "rel_delta": "share of power in the delta band (1-4 Hz, slow waves)", "rel_theta": "share of power in the theta band (4-8 Hz)",
    "rel_alpha": "share of power in the alpha band (8-13 Hz)", "rel_beta": "share of power in the beta band (13-30 Hz)",
    "rel_gamma": "share of power in the gamma band (30-45 Hz)", "log_power": "overall signal size (log power 1-45 Hz)",
    "hjorth_mobility": "average frequency of the signal (Hjorth mobility)", "hjorth_complexity": "how much the waveform departs from a sine (Hjorth complexity)",
    "spectral_entropy": "how evenly power is spread over frequencies (spectral entropy)",
    "perm_entropy": "how unpredictable the signal is (permutation entropy)", "slope": "steepness of the 1/f background (aperiodic slope)",
    "wpli_delta": "phase synchrony between regions in delta (wPLI)", "wpli_theta": "phase synchrony between regions in theta (wPLI)",
    "wpli_alpha": "phase synchrony between regions in alpha (wPLI)",
}


def _perm_entropy(x: np.ndarray, order: int = 3, delay: int = 1) -> np.ndarray:
    """Normalised permutation entropy of every row of x (Bandt & Pompe 2002)."""
    n = x.shape[-1] - (order - 1) * delay
    idx = np.arange(n)[:, None] + delay * np.arange(order)[None, :]
    win = x[..., idx]  # (..., n, order)
    ranks = np.argsort(np.argsort(win, axis=-1), axis=-1)
    code = (ranks * (order ** np.arange(order))).sum(axis=-1)
    out = np.empty(x.shape[:-1])
    flat_c = code.reshape(-1, n)
    k = order ** order
    for i, c in enumerate(flat_c):
        p = np.bincount(c, minlength=k) / n
        p = p[p > 0]
        out.reshape(-1)[i] = -(p * np.log(p)).sum() / np.log(float(np.prod(np.arange(1, order + 1))))
    return out


def _wpli(X: np.ndarray, fs: float, band: tuple[float, float]) -> np.ndarray:
    """Debiased-free weighted phase-lag index between every channel pair for one epoch: (channels, channels)."""
    f, _, Z = stft(X, fs=fs, nperseg=int(fs), noverlap=int(fs) // 2, boundary=None, padded=False, axis=-1)
    m = (f >= band[0]) & (f < band[1])
    Z = Z[:, m, :]  # (ch, freq, seg)
    ch = Z.shape[0]
    Zr = Z.reshape(ch, -1)  # all (freq, seg) cells of the band
    S = Zr[:, None, :] * np.conj(Zr[None, :, :])  # cross-spectrum per cell
    im = S.imag
    num = np.abs(im.mean(axis=-1))
    den = np.abs(im).mean(axis=-1)
    w = np.divide(num, den, out=np.zeros_like(num), where=den > 0)
    np.fill_diagonal(w, 0.0)
    return w


def extract(X: np.ndarray, fs: float, names: list[str] = STANDARD_19) -> tuple[np.ndarray, list[str]]:
    """X: (n_epochs, 19 channels in `names` order, samples) in microvolts. Returns (n_epochs, n_features), feature names."""
    X = X.astype(np.float64)
    n, c, t = X.shape
    idx = {r: [names.index(ch) for ch in chans] for r, chans in REGIONS.items()}
    idx["global"] = list(range(c))
    f, psd = welch(X, fs=fs, nperseg=int(2 * fs), noverlap=int(fs), axis=-1)  # (n, c, freq)
    tot = psd[..., (f >= 1) & (f < 45)].sum(axis=-1) + 1e-30
    rel = {b: psd[..., (f >= lo) & (f < hi)].sum(axis=-1) / tot for b, (lo, hi) in BANDS.items()}
    d1 = np.diff(X, axis=-1)
    d2 = np.diff(d1, axis=-1)
    v0, v1, v2 = X.var(-1) + 1e-30, d1.var(-1) + 1e-30, d2.var(-1) + 1e-30
    mobility = np.sqrt(v1 / v0)
    complexity = np.sqrt(v2 / v1) / mobility
    band = (f >= 1) & (f < 45)
    p = psd[..., band] / tot[..., None]
    sent = -(p * np.log(p + 1e-30)).sum(-1) / np.log(p.shape[-1])
    pe = _perm_entropy(X, 3, max(1, int(round(fs / 125))))  # delay ~ 8 ms
    # aperiodic slope: robust straight-line fit of log10 power vs log10 frequency over 2-40 Hz, skipping the 7-14 Hz alpha peak
    fm = (f >= 2) & (f <= 40) & ~((f >= 7) & (f <= 14))
    lf = np.log10(f[fm])
    lp = np.log10(psd[..., fm] + 1e-30)
    slope = ((lf - lf.mean()) * (lp - lp.mean(-1, keepdims=True))).sum(-1) / ((lf - lf.mean()) ** 2).sum()

    feats: dict[str, np.ndarray] = {}
    per_ch = {f"rel_{b}": v for b, v in rel.items()}
    per_ch.update(log_power=np.log10(tot), hjorth_mobility=mobility, hjorth_complexity=complexity, spectral_entropy=sent,
                  perm_entropy=pe, slope=slope)
    for r, ii in idx.items():
        for k, v in per_ch.items():
            feats[f"{r}:{k}"] = v[:, ii].mean(axis=1)
    feats["global:alpha_over_delta"] = np.log10((rel["alpha"] + 1e-9) / (rel["delta"] + 1e-9)).mean(axis=1)
    feats["frontal_minus_occipital:rel_alpha"] = rel["alpha"][:, idx["frontal"]].mean(1) - rel["alpha"][:, idx["occipital"]].mean(1)
    fr, po = idx["frontal"], idx["parietal"] + idx["occipital"]
    for b in ("delta", "theta", "alpha"):
        W = np.stack([_wpli(X[i], fs, BANDS[b]) for i in range(n)])  # (n, c, c)
        iu = np.triu_indices(c, 1)
        feats[f"global:wpli_{b}"] = W[:, iu[0], iu[1]].mean(axis=1)
        feats[f"frontal_to_posterior:wpli_{b}"] = W[:, fr][:, :, po].mean(axis=(1, 2))
    names_out = list(feats)
    return np.stack([feats[k] for k in names_out], axis=1), names_out


def plain_name(feature: str) -> str:
    """'occipital:rel_alpha' -> 'occipital region: share of power in the alpha band (8-13 Hz)'."""
    area, _, key = feature.partition(":")
    if key in PLAIN:
        return f"{area.replace('_', ' ')}: {PLAIN[key]}"
    extra = {"alpha_over_delta": "log ratio of alpha to delta power", "rel_alpha": PLAIN["rel_alpha"]}
    if area == "frontal_minus_occipital":
        return "frontal minus occipital share of alpha power (alpha 'anteriorisation')"
    return f"{area.replace('_', ' ')}: {extra.get(key, key)}"
