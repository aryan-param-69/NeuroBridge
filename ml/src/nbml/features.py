"""Per-epoch feature extraction.

Everything here is a deterministic per-epoch transform that uses no labels and no other epochs,
so it cannot leak information between training and test data. Anything that must be *fitted*
(scalers, CSP filters, the Riemannian reference mean) lives inside the CV pipeline instead.
"""

import numpy as np
from pyriemann.estimation import Covariances
from scipy.signal import welch

from . import config as C


def bandpower(X: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """log10 band power per channel and band (Welch, 1 s Hann windows, 50 % overlap)."""
    freqs, psd = welch(X.astype(np.float64), fs=C.SFREQ, nperseg=int(C.SFREQ), noverlap=int(C.SFREQ) // 2, axis=-1)
    feats, names = [], []
    for band, (lo, hi) in C.BANDS.items():
        m = (freqs >= lo) & (freqs < hi)
        feats.append(np.log10(psd[:, :, m].mean(axis=-1) + 1e-20))  # (n, channels)
        names += [f"{band}:{i}" for i in range(X.shape[1])]
    return np.concatenate(feats, axis=1), names


def covariances(X: np.ndarray) -> np.ndarray:
    """Oracle-approximating-shrinkage covariance of each epoch: (n, 64, 64)."""
    return Covariances(estimator="oas").fit_transform(X.astype(np.float64))


def recenter_per_subject(cov: np.ndarray, subject: np.ndarray) -> np.ndarray:
    """Riemannian recentring: whiten each subject's covariances by that subject's own mean covariance.

    Uses NO labels and only the subject's own (unlabelled) epochs, so it cannot leak labels between
    people; it removes much of the person-to-person offset (skull, electrode contact, overall power).
    At deployment it needs a few minutes of the new recording's own data (transductive, label-free).
    """
    from pyriemann.utils.base import invsqrtm
    from pyriemann.utils.mean import mean_riemann

    out = np.empty_like(cov)
    for sub in np.unique(subject):
        m = subject == sub
        w = invsqrtm(mean_riemann(cov[m]))
        out[m] = w @ cov[m] @ w.T
    return out
