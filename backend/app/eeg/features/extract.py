"""Run-level feature extraction: epochs -> features -> per-channel / region / global summaries with uncertainty.

Rules that matter scientifically:
* Only KEPT epochs are used, and (epoch, channel) values that the pre-processing run MASKED are excluded
  (NaN), never silently included.
* Uncertainty is a circular BLOCK bootstrap over epochs (blocks of consecutive epochs), because epochs overlap by
  50 % and neighbouring epochs are not independent. Seeded, so results are reproducible.
* A region value is the mean over its unmasked channels, computed per epoch before summarising.
"""

import hashlib
import json
import math
import warnings

import numpy as np

from app.eeg.features import compute as F
from app.eeg.features.catalog import CATALOG, FEATURES_VERSION, band_edges
from app.eeg.features.regions import homologous_pairs, regions_for

DEFAULT_CFG = {"complexity_epochs": 40, "bootstrap_block": 5, "bootstrap_n": 500, "psd_bootstrap_n": 200, "seed": 20260101,
               "aperiodic_range_hz": [1.0, 40.0], "chunk_epochs": 100}


class FeatureError(ValueError):
    pass


def block_bootstrap(vals: np.ndarray, block: int, n_boot: int, seed: int):
    """vals: (n_epochs, n_series) with NaN for missing. Returns mean, median, sd, ci_lo, ci_hi, n_valid per series."""
    n, s = vals.shape
    ok = np.isfinite(vals)
    v0 = np.where(ok, vals, 0.0)
    cnt = ok.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = v0.sum(axis=0) / cnt
        sd = np.sqrt(np.where(cnt > 1, ((np.where(ok, vals - mean, 0.0)) ** 2).sum(axis=0) / np.maximum(cnt - 1, 1), np.nan))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns are expected (fully masked channels)
        median = np.nanmedian(np.where(ok, vals, np.nan), axis=0) if n else np.full(s, np.nan)
    lo = np.full(s, np.nan)
    hi = np.full(s, np.nan)
    if n >= 3 and n_boot > 0:
        b = max(1, min(block, n))
        rng = np.random.default_rng(seed)
        nb = math.ceil(n / b)
        starts = rng.integers(0, n, size=(n_boot, nb))
        idx = ((starts[:, :, None] + np.arange(b)[None, None, :]).reshape(n_boot, -1)[:, :n]) % n
        boots = np.empty((n_boot, s))
        okf = ok.astype(np.float64)
        for i in range(n_boot):
            c = okf[idx[i]].sum(axis=0)
            with np.errstate(invalid="ignore", divide="ignore"):
                boots[i] = v0[idx[i]].sum(axis=0) / c
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            lo, hi = np.nanpercentile(boots, [2.5, 97.5], axis=0)
        lo = np.where(cnt >= 3, lo, np.nan)
        hi = np.where(cnt >= 3, hi, np.nan)
    return mean, median, sd, lo, hi, cnt


def _num(x) -> float | None:
    return None if x is None or not np.isfinite(x) else float(f"{x:.6g}")


def compute_run_features(data_uv: np.ndarray, sfreq: float, names: list[str], onsets_s: np.ndarray, length_s: float,
                         masked: set[tuple[int, int]], cfg: dict | None = None, progress=None) -> dict:
    """data_uv (n_ch, n_samples) in microvolts; `onsets_s` the onsets of the KEPT epochs; `masked` holds
    (kept-epoch index, channel index) pairs to exclude."""
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    n, n_ch = len(onsets_s), len(names)
    if n == 0:
        raise FeatureError("This run has no clean epochs, so no features can be computed.")
    L = int(round(length_s * sfreq))
    starts = np.round(np.asarray(onsets_s) * sfreq).astype(int)
    if starts.max() + L > data_uv.shape[1]:
        raise FeatureError("Epoch table does not match the processed data.")
    sub = np.unique(np.linspace(0, n - 1, min(n, cfg["complexity_epochs"])).round().astype(int))

    series: dict[tuple[str, str | None], np.ndarray] = {}
    psd_all = None
    freqs = None
    for c0 in range(0, n, cfg["chunk_epochs"]):
        idx = np.arange(c0, min(n, c0 + cfg["chunk_epochs"]))
        X = np.stack([data_uv[:, s:s + L] for s in starts[idx]])
        freqs, psd = F.epoch_psd(X, sfreq)
        if psd_all is None:
            psd_all = np.full((n, n_ch, len(freqs)), np.nan, dtype=np.float32)
        psd_all[idx] = psd
        parts = {(k, None): v for k, v in F.time_features(X).items()}
        parts.update(F.spectral_features(freqs, psd, sfreq))
        parts.update(F.wavelet_features(X, sfreq))
        local = np.array([i - c0 for i in sub if c0 <= i < c0 + len(idx)], dtype=int)
        parts.update(F.complexity_features(X, local))
        for key, v in parts.items():
            if key not in series:
                series[key] = np.full((n, n_ch), np.nan)
            series[key][idx] = v
        if progress:
            progress(int(60 * (c0 + len(idx)) / n), "Computing features")

    # exclude masked (epoch, channel) values everywhere, and non-finite results
    if masked:
        e, c = (np.array(v) for v in zip(*sorted(masked), strict=True))
        for v in series.values():
            v[e, c] = np.nan
        psd_all[e, c] = np.nan
    for v in series.values():
        v[~np.isfinite(v)] = np.nan

    # ---- aggregation columns: channels, regions, global
    reg = regions_for(names)
    columns = [("channel", nm, [i]) for i, nm in enumerate(names)] + [("region", r, ix) for r, ix in sorted(reg.items())]
    columns.append(("global", "global", list(range(n_ch))))
    def aggregate(arr: np.ndarray) -> np.ndarray:
        out = np.empty((arr.shape[0], len(columns)))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            for j, (_, _, ix) in enumerate(columns):
                out[:, j] = np.nanmean(arr[:, ix], axis=1) if len(ix) > 1 else arr[:, ix[0]]
        return out

    rows: list[dict] = []
    seed = cfg["seed"]
    for fi, ((feat, band), arr) in enumerate(sorted(series.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))):
        mean, med, sd, lo, hi, cnt = block_bootstrap(aggregate(arr), cfg["bootstrap_block"], cfg["bootstrap_n"], seed + fi)
        for j, (level, name, _) in enumerate(columns):
            if cnt[j] == 0:
                continue
            rows.append({"level": level, "name": name, "feature": feat, "band": band, "mean": _num(mean[j]), "median": _num(med[j]),
                         "sd": _num(sd[j]), "ci_low": _num(lo[j]), "ci_high": _num(hi[j]), "n_epochs": int(cnt[j]),
                         "unit": CATALOG[feat].unit})
        if progress:
            progress(60 + int(25 * (fi + 1) / len(series)), "Summarising with confidence intervals")

    # ---- left-right asymmetry of band power (channel pairs + their mean)
    pairs = homologous_pairs(names)
    if pairs:
        for band in band_edges(sfreq):
            ab = series[("abs_power", band)]
            with np.errstate(invalid="ignore", divide="ignore"):
                asym = np.stack([(ab[:, i] - ab[:, j]) / (ab[:, i] + ab[:, j]) for i, j, _ in pairs], axis=1)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                allv = np.concatenate([asym, np.nanmean(asym, axis=1, keepdims=True)], axis=1) if asym.size else asym
            mean, med, sd, lo, hi, cnt = block_bootstrap(allv, cfg["bootstrap_block"], cfg["bootstrap_n"], seed + 10_000 + len(rows))
            for j, label in enumerate([p[2] for p in pairs] + ["mean of pairs"]):
                if cnt[j]:
                    rows.append({"level": "pair" if j < len(pairs) else "global", "name": label, "feature": "asymmetry_abs_power",
                                 "band": band, "mean": _num(mean[j]), "median": _num(med[j]), "sd": _num(sd[j]),
                                 "ci_low": _num(lo[j]), "ci_high": _num(hi[j]), "n_epochs": int(cnt[j]), "unit": ""})

    # ---- PSD summaries and aperiodic fits (one fit per recording-average spectrum)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        ch_mean = np.nanmean(psd_all, axis=0)  # (n_ch, F)
    fmask = (freqs >= 0.5) & (freqs <= min(48.0, 0.48 * sfreq))
    psd_doc = {"freqs_hz": [round(float(f), 3) for f in freqs[fmask]], "unit": "uV^2/Hz", "n_epochs": int(n),
               "channels": {nm: [_num(v) for v in ch_mean[i][fmask]] for i, nm in enumerate(names)}, "groups": {}}
    mean_by_col = {}
    for j, (level, name, ix) in enumerate(columns):
        if level == "channel":
            mean_by_col[j] = ch_mean[ix[0]]
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            per_epoch = np.nanmean(psd_all[:, ix, :], axis=1)  # (n, F): region mean per epoch
        m, _, _, lo, hi, _ = block_bootstrap(per_epoch[:, fmask], cfg["bootstrap_block"], cfg["psd_bootstrap_n"], seed + 20_000 + j)
        psd_doc["groups"][name] = {"mean": [_num(v) for v in m], "ci_low": [_num(v) for v in lo], "ci_high": [_num(v) for v in hi]}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mean_by_col[j] = np.nanmean(per_epoch, axis=0)
    for j, (level, name, _) in enumerate(columns):
        fit = F.aperiodic_fit(freqs, mean_by_col[j], tuple(cfg["aperiodic_range_hz"]))
        for feat, v in fit.items():
            if np.isfinite(v):
                rows.append({"level": level, "name": name, "feature": feat, "band": None, "mean": _num(v), "median": _num(v),
                             "sd": None, "ci_low": None, "ci_high": None, "n_epochs": int(n), "unit": CATALOG[feat].unit})
    if progress:
        progress(95, "Finishing")

    digest = hashlib.sha256(json.dumps(sorted([(r["level"], r["name"], r["feature"], r["band"] or "", r["mean"]) for r in rows]),
                                       separators=(",", ":")).encode()).hexdigest()
    return {"rows": rows, "psd": psd_doc, "n_epochs": int(n), "n_channels": n_ch, "features_hash": digest,
            "bands": {k: list(v) for k, v in band_edges(sfreq).items()}, "version": FEATURES_VERSION, "config": cfg,
            "epoch_values": {f"{f}|{b or ''}": v for (f, b), v in series.items()}}
