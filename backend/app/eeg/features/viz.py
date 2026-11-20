"""Server-side rendering: topographic maps (MNE) and spectrograms (SciPy)."""

import io
import threading

import numpy as np
from scipy.signal import spectrogram

_MPL_LOCK = threading.Lock()  # matplotlib is not thread-safe; renders are short
MAX_TIME_BINS = 1200


def topomap_svg(values: dict[str, float], title: str = "", cmap: str = "viridis", vlim: tuple[float, float] | None = None,
                label: str = "") -> str:
    """Render channel values on a head. Channels without standard electrode positions are skipped.
    The SVG contains no text from user data (channel names are not drawn)."""
    import matplotlib

    matplotlib.use("Agg", force=False)
    import mne
    from matplotlib.figure import Figure

    mont = mne.channels.make_standard_montage("standard_1005")
    names = [n for n in values if n in set(mont.ch_names) and np.isfinite(values[n])]
    if len(names) < 4:
        raise ValueError("Fewer than 4 channels with electrode positions: a map cannot be drawn")
    info = mne.create_info(names, 100.0, "eeg")
    info.set_montage(mont, on_missing="ignore", verbose=False)
    data = np.array([values[n] for n in names], dtype=float)
    lo, hi = vlim if vlim else (float(np.percentile(data, 2)), float(np.percentile(data, 98)))
    if hi <= lo:
        hi = lo + 1e-9
    with _MPL_LOCK:
        fig = Figure(figsize=(3.2, 3.5), dpi=100)
        ax = fig.add_axes([0.04, 0.12, 0.92, 0.80])
        im, _ = mne.viz.plot_topomap(data, info, axes=ax, show=False, cmap=cmap, vlim=(lo, hi), contours=4, sensors=True,
                                     outlines="head", names=None)
        cax = fig.add_axes([0.2, 0.06, 0.6, 0.03])
        fig.colorbar(im, cax=cax, orientation="horizontal")
        cax.tick_params(labelsize=7)
        if label:
            cax.set_xlabel(label, fontsize=7, labelpad=1)
        if title:
            ax.set_title(title, fontsize=9)
        buf = io.StringIO()
        fig.savefig(buf, format="svg", metadata={"Date": None})
    return buf.getvalue()


def make_spectrogram(x_uv: np.ndarray, fs: float, fmax: float = 45.0, win_s: float = 2.0, step_s: float = 0.5) -> dict:
    """Short-time power spectrum of one channel. Returns log10(uV^2/Hz) on a (freq x time) grid, time-averaged
    down to at most MAX_TIME_BINS columns."""
    nper = int(win_s * fs)
    f, t, S = spectrogram(x_uv - x_uv.mean(), fs=fs, window="hann", nperseg=nper, noverlap=nper - int(step_s * fs),
                          detrend="constant", scaling="density", mode="psd")
    keep = (f >= 0.5) & (f <= fmax)
    f, S = f[keep], S[keep]
    n_t = S.shape[1]
    if n_t > MAX_TIME_BINS:
        g = int(np.ceil(n_t / MAX_TIME_BINS))
        trim = (n_t // g) * g
        S = S[:, :trim].reshape(S.shape[0], -1, g).mean(axis=2)
        t = t[:trim].reshape(-1, g).mean(axis=1)
    return {"freqs_hz": np.round(f, 2).tolist(), "times_s": np.round(t, 2).tolist(),
            "log10_power": np.round(np.log10(S + 1e-12), 2).tolist(), "unit": "log10(uV^2/Hz)"}
