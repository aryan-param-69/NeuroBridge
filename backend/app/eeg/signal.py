"""Windowed signal access for the EEG viewer, plus the quick whole-file overview used at upload time.

All amplitudes are returned in microvolts. Only the requested window is read from disk
(`mne.io.read_raw_edf(preload=False)` + `get_data(start, stop)`), so multi-hour files stay responsive.
"""

import threading
from collections import OrderedDict
from pathlib import Path

import mne
import numpy as np

mne.set_log_level("ERROR")

MAX_WINDOW_S = 120.0
MAX_CHANNELS = 128
MONTAGES = ("referential", "average", "bipolar_longitudinal")

# "Double banana": anterior-to-posterior chains, left then right, then midline.
BIPOLAR_LONGITUDINAL = [
    ("Fp1", "F7"), ("F7", "T7"), ("T7", "P7"), ("P7", "O1"),
    ("Fp1", "F3"), ("F3", "C3"), ("C3", "P3"), ("P3", "O1"),
    ("Fz", "Cz"), ("Cz", "Pz"),
    ("Fp2", "F4"), ("F4", "C4"), ("C4", "P4"), ("P4", "O2"),
    ("Fp2", "F8"), ("F8", "T8"), ("T8", "P8"), ("P8", "O2"),
]

_CACHE: "OrderedDict[str, mne.io.BaseRaw]" = OrderedDict()
_LOCK = threading.Lock()
_CACHE_SIZE = 4


def open_raw(path: Path) -> mne.io.BaseRaw:
    """Open an EDF lazily; keep a few headers cached so paging through a file is fast."""
    key = str(path)
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    if Path(path).suffix.lower() == ".fif":
        raw = mne.io.read_raw_fif(path, preload=False, verbose="ERROR")
    else:
        raw = mne.io.read_raw_edf(path, preload=False, infer_types=False, verbose="ERROR")
    with _LOCK:
        _CACHE[key] = raw
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return raw


def forget(path: Path) -> None:
    with _LOCK:
        _CACHE.pop(str(path), None)


def is_bad(ch: dict) -> bool:
    flag = ch.get("user_flag") or ch.get("auto_flag")
    return flag in ("bad", "flat", "noisy", "clipped")


class SignalError(ValueError):
    pass


def _read(raw: mne.io.BaseRaw, idx: list[int], s0: int, s1: int) -> np.ndarray:
    return raw.get_data(picks=idx, start=s0, stop=s1) * 1e6  # volts -> microvolts


def read_window(raw: mne.io.BaseRaw, channels: list[dict], montage: str, selected: list[str] | None,
                start_s: float, duration_s: float) -> tuple[list[str], np.ndarray, float]:
    """Return (display_names, data_uV[n_display, n_samples], sfreq)."""
    if montage not in MONTAGES:
        raise SignalError(f"montage must be one of {MONTAGES}")
    if not 0 < duration_s <= MAX_WINDOW_S:
        raise SignalError(f"duration must be between 0 and {MAX_WINDOW_S:g} s")
    sf = float(raw.info["sfreq"])
    if start_s < 0 or start_s >= raw.n_times / sf:
        raise SignalError("start is outside the recording")
    s0 = int(round(start_s * sf))
    s1 = min(int(round((start_s + duration_s) * sf)), raw.n_times)
    by_name = {c["name"]: c for c in channels}
    eeg = [c for c in channels if c["ch_type"] == "eeg"]
    wanted = [by_name[n] for n in selected] if selected else eeg
    if len(wanted) > MAX_CHANNELS:
        raise SignalError(f"at most {MAX_CHANNELS} channels per request")

    if montage == "referential":
        data = _read(raw, [c["index"] for c in wanted], s0, s1)
        return [c["name"] for c in wanted], data, sf

    if montage == "average":
        # The average reference uses every good EEG channel, whichever channels are displayed.
        good = [c for c in eeg if not is_bad(c)] or eeg
        if not good:
            raise SignalError("no EEG channels available for an average reference")
        all_idx = sorted({c["index"] for c in good} | {c["index"] for c in wanted})
        block = _read(raw, all_idx, s0, s1)
        pos = {i: k for k, i in enumerate(all_idx)}
        mean = block[[pos[c["index"]] for c in good]].mean(axis=0)
        out = np.stack([block[pos[c["index"]]] - mean for c in wanted])
        return [c["name"] for c in wanted], out, sf

    # bipolar_longitudinal
    pairs = [(a, b) for a, b in BIPOLAR_LONGITUDINAL if a in by_name and b in by_name
             and by_name[a]["ch_type"] == "eeg" and by_name[b]["ch_type"] == "eeg"]
    if not pairs:
        raise SignalError("bipolar montage needs standard 10-20 channels (Fp1, F3, C3, ...) which are not present")
    sel = set(selected) if selected else None
    if sel:
        pairs = [(a, b) for a, b in pairs if a in sel or b in sel]
    need = sorted({by_name[n]["index"] for p in pairs for n in p})
    block = _read(raw, need, s0, s1)
    pos = {i: k for k, i in enumerate(need)}
    out = np.stack([block[pos[by_name[a]["index"]]] - block[pos[by_name[b]["index"]]] for a, b in pairs])
    return [f"{a}-{b}" for a, b in pairs], out, sf


def decimate_minmax(data: np.ndarray, max_points: int) -> tuple[np.ndarray, np.ndarray | None]:
    """Reduce each channel to <= max_points values while keeping every peak (min/max per bucket).

    Returns (data, bucket_index_per_output_sample or None if no decimation was needed).
    """
    n = data.shape[1]
    if n <= max_points:
        return data, None
    buckets = max(1, max_points // 2)
    edges = np.linspace(0, n, buckets + 1).astype(int)
    edges = np.unique(edges[:-1])  # strictly increasing starts for reduceat
    mins = np.minimum.reduceat(data, edges, axis=1)
    maxs = np.maximum.reduceat(data, edges, axis=1)
    out = np.empty((data.shape[0], 2 * len(edges)), dtype=data.dtype)
    out[:, 0::2], out[:, 1::2] = mins, maxs
    centres = (edges + np.append(edges[1:], n)) / 2.0
    return out, np.repeat(centres, 2)


def overview_and_flags(raw: mne.io.BaseRaw, channels: list[dict], max_bins: int = 600) -> tuple[dict, dict[int, str]]:
    """One pass over the file: per-bin amplitude (SD) for the overview strip, and provisional channel flags.

    Flags are deliberately simple and conservative (full quality scoring arrives in Phase 3):
      flat     median SD < 0.5 uV      noisy   median SD > 300 uV      clipped  > 0.1 % of samples at the ADC limit
    """
    sf = float(raw.info["sfreq"])
    n = raw.n_times
    bin_s = max(1.0, float(np.ceil(n / sf / max_bins)))
    bin_n = int(round(bin_s * sf))
    eeg = [c for c in channels if c["ch_type"] == "eeg"]
    if not eeg:
        return {"bin_s": bin_s, "global_sd_uv": []}, {}
    idx = [c["index"] for c in eeg]
    lim = np.array([max(abs(c["physical_min"]), abs(c["physical_max"])) for c in eeg])  # in file units (uV)
    n_bins = max(1, int(np.ceil(n / bin_n)))
    sd = np.zeros((n_bins, len(eeg)))
    clip = np.zeros(len(eeg))
    chunk_bins = max(1, int(60 / bin_s))
    for b0 in range(0, n_bins, chunk_bins):
        s0, s1 = b0 * bin_n, min((b0 + chunk_bins) * bin_n, n)
        block = _read(raw, idx, s0, s1)
        clip += (np.abs(block) >= 0.999 * lim[:, None]).sum(axis=1)
        for k in range(b0, min(b0 + chunk_bins, n_bins)):
            seg = block[:, (k - b0) * bin_n: (k - b0 + 1) * bin_n]
            if seg.shape[1] > 1:
                sd[k] = seg.std(axis=1)
    med = np.median(sd, axis=0)
    flags: dict[int, str] = {}
    for j, c in enumerate(eeg):
        if clip[j] / max(n, 1) > 0.001:
            flags[c["index"]] = "clipped"
        elif med[j] < 0.5:
            flags[c["index"]] = "flat"
        elif med[j] > 300:
            flags[c["index"]] = "noisy"
        else:
            flags[c["index"]] = "ok"
    return {"bin_s": bin_s, "global_sd_uv": np.round(np.median(sd, axis=1), 2).tolist(),
            "channel_median_sd_uv": {c["name"]: round(float(med[j]), 2) for j, c in enumerate(eeg)}}, flags
