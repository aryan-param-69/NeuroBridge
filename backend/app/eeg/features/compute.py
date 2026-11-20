"""Per-epoch feature primitives. Every function takes epochs X of shape (n_epochs, n_channels, n_samples) in
microvolts and returns arrays of shape (n_epochs, n_channels). Pure numpy/scipy, deterministic, no hidden state.
"""

import math

import numpy as np
import pywt
from scipy import stats
from scipy.signal import get_window

from app.eeg.features.catalog import band_edges

# ---- Time domain ------------------------------------------------------------------------------------------------

def time_features(X: np.ndarray) -> dict[str, np.ndarray]:
    Xc = X - X.mean(axis=-1, keepdims=True)
    var = Xc.var(axis=-1)
    d = np.diff(Xc, axis=-1)
    var_d = d.var(axis=-1)
    dd = np.diff(d, axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        mobility = np.sqrt(var_d / var)
        mobility_d = np.sqrt(dd.var(axis=-1) / var_d)
        complexity = mobility_d / mobility
    return {
        "variance": var, "rms": np.sqrt((X**2).mean(axis=-1)), "peak_to_peak": X.max(axis=-1) - X.min(axis=-1),
        "skewness": stats.skew(Xc, axis=-1), "kurtosis": stats.kurtosis(Xc, axis=-1),
        "line_length": np.abs(d).sum(axis=-1), "zero_crossing_rate": (Xc[..., :-1] * Xc[..., 1:] < 0).mean(axis=-1),
        "hjorth_activity": var, "hjorth_mobility": mobility, "hjorth_complexity": complexity,
    }


# ---- Spectral ---------------------------------------------------------------------------------------------------

def epoch_psd(X: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """One-sided power spectral density (uV^2/Hz) of every epoch: Hann window, mean removed.
    Frequency resolution is 1 / epoch length (0.5 Hz for 2 s epochs). Returns (freqs, psd[n, ch, F])."""
    L = X.shape[-1]
    w = get_window("hann", L)
    Xc = X - X.mean(axis=-1, keepdims=True)
    spec = np.abs(np.fft.rfft(Xc * w, axis=-1)) ** 2 / (fs * (w**2).sum())
    spec[..., 1:] *= 2.0
    if L % 2 == 0:
        spec[..., -1] /= 2.0  # the Nyquist bin is not doubled
    return np.fft.rfftfreq(L, 1.0 / fs), spec


def spectral_features(freqs: np.ndarray, psd: np.ndarray, fs: float) -> dict[tuple[str, str | None], np.ndarray]:
    """Band powers, ratios and spectral shape measures from per-epoch PSDs. Bands partition [1, cap) Hz exactly,
    so the relative powers of all bands add up to 1."""
    df = float(freqs[1] - freqs[0])
    bands = band_edges(fs)
    cap = max(hi for _, hi in bands.values())
    tot_mask = (freqs >= 1.0) & (freqs < cap)
    total = psd[..., tot_mask].sum(axis=-1) * df
    out: dict[tuple[str, str | None], np.ndarray] = {}
    ab = {}
    for name, (lo, hi) in bands.items():
        m = (freqs >= lo) & (freqs < hi)
        ab[name] = psd[..., m].sum(axis=-1) * df
        out[("abs_power", name)] = ab[name]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[("rel_power", name)] = ab[name] / total
    with np.errstate(divide="ignore", invalid="ignore"):
        if "theta" in ab and "alpha" in ab:
            out[("ratio_theta_alpha", None)] = ab["theta"] / ab["alpha"]
        if "delta" in ab and "alpha" in ab:
            out[("ratio_delta_alpha", None)] = ab["delta"] / ab["alpha"]
        if all(k in ab for k in ("delta", "theta", "alpha", "beta")):
            out[("ratio_slow_fast", None)] = (ab["delta"] + ab["theta"]) / (ab["alpha"] + ab["beta"])
        P = psd[..., tot_mask]
        f = freqs[tot_mask]
        p = P / P.sum(axis=-1, keepdims=True)
        out[("spectral_centroid", None)] = (p * f).sum(axis=-1)
        cum = np.cumsum(p, axis=-1)
        out[("spectral_edge_90", None)] = f[np.minimum((cum < 0.9).sum(axis=-1), len(f) - 1)]
        out[("spectral_entropy", None)] = -(p * np.log(np.where(p > 0, p, 1.0))).sum(axis=-1) / math.log(len(f))
        am = (freqs >= 7.0) & (freqs <= 13.0)
        out[("peak_alpha_freq", None)] = freqs[am][np.argmax(psd[..., am], axis=-1)]
    return out


# ---- Wavelet ----------------------------------------------------------------------------------------------------

def wavelet_levels(n_samples: int, fs: float) -> int:
    return max(1, min(pywt.dwt_max_level(n_samples, pywt.Wavelet("db4").dec_len), int(math.floor(math.log2(fs / 2.0)))))


def wavelet_features(X: np.ndarray, fs: float) -> dict[tuple[str, str | None], np.ndarray]:
    """Daubechies-4 DWT. Detail level j covers [fs/2^(j+1), fs/2^j] Hz; the final approximation covers what remains."""
    L = wavelet_levels(X.shape[-1], fs)
    # mode="periodization": exactly energy-preserving (no extra edge coefficients on short epochs)
    coeffs = pywt.wavedec(X - X.mean(axis=-1, keepdims=True), "db4", level=L, axis=-1, mode="periodization")  # [A_L, D_L, ..., D_1]
    energies = [(c**2).sum(axis=-1) for c in coeffs]
    total = np.sum(energies, axis=0)
    out: dict[tuple[str, str | None], np.ndarray] = {}
    shares = []
    with np.errstate(divide="ignore", invalid="ignore"):
        for j, e in zip(range(L, 0, -1), energies[1:], strict=True):
            label = f"D{j}_{fs / 2 ** (j + 1):.1f}-{fs / 2 ** j:.1f}Hz"
            out[("wavelet_rel_energy", label)] = e / total
            shares.append(e / total)
        a = energies[0] / total
        out[("wavelet_rel_energy", f"A{L}_0-{fs / 2 ** (L + 1):.1f}Hz")] = a
        shares.append(a)
        s = np.stack(shares, axis=0)
        out[("wavelet_entropy", None)] = -(s * np.log(np.where(s > 0, s, 1.0))).sum(axis=0) / math.log(len(shares))
    return out


# ---- Complexity -------------------------------------------------------------------------------------------------

def permutation_entropy(X: np.ndarray, order: int = 3, delay: int = 1) -> np.ndarray:
    """Normalised permutation entropy (Bandt & Pompe, 2002) along the last axis."""
    T = X.shape[-1] - (order - 1) * delay
    idx = np.arange(T)[:, None] + delay * np.arange(order)[None, :]
    ranks = np.argsort(X[..., idx], axis=-1, kind="stable")  # (..., T, order)
    code = np.zeros(ranks.shape[:-1], dtype=np.int64)
    for k in range(order):
        code = code * order + ranks[..., k]
    flat = code.reshape(-1, T)
    nb = order**order
    counts = np.bincount((flat + (np.arange(flat.shape[0]) * nb)[:, None]).ravel(), minlength=flat.shape[0] * nb)
    p = counts.reshape(flat.shape[0], nb) / T
    h = -(p * np.log(np.where(p > 0, p, 1.0))).sum(axis=-1)
    return (h / math.log(math.factorial(order))).reshape(X.shape[:-1])


def _lz_count(s: list[int]) -> int:
    """Lempel-Ziv (1976) complexity by the Kaspar-Schuster algorithm."""
    n = len(s)
    i, k, l_, c, k_max = 0, 1, 1, 1, 1
    while True:
        if s[i + k - 1] == s[l_ + k - 1]:
            k += 1
            if l_ + k > n:
                c += 1
                break
        else:
            k_max = max(k, k_max)
            i += 1
            if i == l_:
                c += 1
                l_ += k_max
                if l_ + 1 > n:
                    break
                i, k, k_max = 0, 1, 1
            else:
                k = 1
    return c


def lempel_ziv(x: np.ndarray) -> float:
    """Normalised LZ complexity of one series binarised at its median (about 1 for random, near 0 for regular)."""
    s = (x > np.median(x)).astype(np.int8).tolist()
    n = len(s)
    return _lz_count(s) * math.log2(n) / n


def sample_entropy(x: np.ndarray, m: int = 2, r_frac: float = 0.2) -> float:
    """Sample entropy (Richman & Moorman, 2000) of one series; NaN if it cannot be defined.
    Template matches are counted with a KD-tree (Chebyshev distance), which is far faster than a full distance matrix."""
    from scipy.spatial import cKDTree

    x = np.asarray(x, dtype=np.float64)
    sd = x.std()
    if sd == 0:
        return float("nan")
    r, N = r_frac * sd, len(x)

    def matches(mm: int) -> int:
        t = np.lib.stride_tricks.sliding_window_view(x, mm)[: N - m]
        tree = cKDTree(t)
        return int(tree.count_neighbors(tree, r, p=np.inf) - len(t))  # ordered pairs within r, minus self-matches

    b, a = matches(m), matches(m + 1)
    return float(-math.log(a / b)) if a > 0 and b > 0 else float("nan")


def complexity_features(X: np.ndarray, subset: np.ndarray) -> dict[tuple[str, str | None], np.ndarray]:
    """Permutation entropy on every epoch; LZ and sample entropy on the epochs listed in `subset` (others NaN)."""
    n, c, _ = X.shape
    pe = permutation_entropy(X)
    lz = np.full((n, c), np.nan)
    se = np.full((n, c), np.nan)
    for e in subset:
        for ch in range(c):
            lz[e, ch] = lempel_ziv(X[e, ch])
            se[e, ch] = sample_entropy(X[e, ch])
    return {("permutation_entropy", None): pe, ("lempel_ziv", None): lz, ("sample_entropy", None): se}


# ---- Aperiodic --------------------------------------------------------------------------------------------------

def aperiodic_fit(freqs: np.ndarray, psd: np.ndarray, fit_range=(1.0, 40.0)) -> dict[str, float]:
    """Separate the 1/f background from rhythm peaks with specparam (fixed mode, no knee)."""
    from specparam import SpectralModel

    hi = min(fit_range[1], float(freqs[-1]))
    m = (freqs > 0) & np.isfinite(psd) & (psd > 0)
    try:
        fm = SpectralModel(peak_width_limits=(1.0, 8.0), max_n_peaks=6, min_peak_height=0.1, aperiodic_mode="fixed", verbose=False)
        fm.fit(freqs[m], psd[m], [fit_range[0], hi])
        offset, exponent = (float(v) for v in fm.get_params("aperiodic"))
        return {"aperiodic_exponent": exponent, "aperiodic_offset": offset,
                "aperiodic_r2": float(fm.get_metrics("gof", "rsquared")), "aperiodic_error": float(fm.get_metrics("error", "mae"))}
    except Exception:  # a failed fit must never abort the whole extraction
        return {k: float("nan") for k in ("aperiodic_exponent", "aperiodic_offset", "aperiodic_r2", "aperiodic_error")}
