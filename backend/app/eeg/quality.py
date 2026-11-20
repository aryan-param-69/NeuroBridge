"""Signal-quality assessment: transparent rules, every sub-metric visible. No black box.

Per channel (median over sampled 30 s chunks):
  flat        fraction of 5 s windows with SD < 0.5 uV            -> bad if > 50 %
  clipped     fraction of samples at the ADC limit                -> bad if > 0.1 %
  noisy       robust z-score of the channel's typical SD (median of 1 s SDs, so blinks do not count) or SD > 300 uV -> bad
  uncorrelated  fraction of 1 s windows whose correlation with the median of its 6 nearest electrodes is < 0.4
              (global median when positions are unknown) -> bad if > 50 %
  line noise  power at mains frequency vs neighbouring frequencies (dB)  -> warning if > 10 dB
  EMG         power 30-45 Hz / 1-30 Hz                            -> warning if > 0.5
  drift       power 0.1-0.5 Hz / 0.5-45 Hz                        -> warning if > 0.5
Overall score 0-100 = weighted mean of sub-scores; more than 15 % / 30 % bad channels caps the grade at fair / poor;
the weights and the grade cut-offs are EXPERT-CHOSEN and
NOT yet calibrated against expert-labelled data (planned: TUH Artifact Corpus). Treat the grade as a guide.
"""

import numpy as np
from scipy.signal import butter, sosfiltfilt, welch

QUALITY_VERSION = "1.0.0"
WEIGHTS = {"bad_channels": 0.30, "line_noise": 0.15, "emg": 0.15, "drift": 0.10, "usable": 0.15, "rejected": 0.15}
MIN_SECONDS = 5.0


class QualityError(ValueError):
    pass


def _band_sum(f: np.ndarray, p: np.ndarray, lo: float, hi: float) -> np.ndarray:
    m = (f >= lo) & (f < hi)
    return p[..., m].sum(axis=-1) if m.any() else np.zeros(p.shape[:-1])


def _band_mean(f: np.ndarray, p: np.ndarray, lo: float, hi: float) -> np.ndarray:
    m = (f >= lo) & (f < hi)
    return p[..., m].mean(axis=-1) if m.any() else np.full(p.shape[:-1], np.nan)


def _merge(windows: list[tuple[float, str]]) -> list[dict]:
    """Merge consecutive 1 s windows with the same label into segments."""
    out: list[dict] = []
    for label in sorted({lab for _, lab in windows}):
        times = sorted(t for t, lab in windows if lab == label)
        start = prev = times[0]
        for t in times[1:] + [None]:
            if t is not None and t - prev <= 1.0 + 1e-9:
                prev = t
                continue
            out.append({"onset_s": float(start), "duration_s": float(prev - start + 1.0), "label": label,
                        "source": "auto"})
            if t is not None:
                start = prev = t
    return sorted(out, key=lambda s: s["onset_s"])


def _neighbours(names: list[str], positions: dict | None, k: int = 6) -> dict[int, list[int]] | None:
    """k nearest electrodes (by 3-D distance) for every channel that has a position; None if too few positions."""
    if not positions:
        return None
    have = [i for i, n in enumerate(names) if n in positions]
    if len(have) < max(8, 0.7 * len(names)):
        return None
    P = np.array([positions[names[i]] for i in have])
    D = np.linalg.norm(P[:, None] - P[None], axis=-1)
    return {i: [have[b] for b in np.argsort(D[a])[1:k + 1]] for a, i in enumerate(have)}


def _window_corr(x: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Correlation per 1 s window between x and ref, both (n_windows, window_samples)."""
    xc, rc = x - x.mean(-1, keepdims=True), ref - ref.mean(-1, keepdims=True)
    return (xc * rc).sum(-1) / (np.sqrt((xc**2).sum(-1) * (rc**2).sum(-1)) + 1e-12)


def assess(read, n_times: int, sfreq: float, names: list[str], limits_uv: list[float], line_freq_hz: float,
           cfg: dict, progress=None, positions: dict | None = None) -> dict:
    """Assess quality of `n_ch` channels. `read(start, stop)` must return an (n_ch, n) array in microvolts."""
    th = cfg["thresholds"]
    n_ch = len(names)
    sf = float(sfreq)
    if n_times < MIN_SECONDS * sf:
        raise QualityError(f"The recording is too short to assess ({n_times / sf:.1f} s; at least {MIN_SECONDS:g} s needed)")
    chunk_n = int(cfg["chunk_s"] * sf)
    n_chunks = max(1, n_times // chunk_n)
    max_chunks = max(1, int(cfg["max_minutes"] * 60 / cfg["chunk_s"]))
    idx = np.arange(n_chunks) if n_chunks <= max_chunks else np.unique(np.linspace(0, n_chunks - 1, max_chunks).astype(int))
    spans = [(0, n_times)] if n_times < chunk_n else [(i * chunk_n, (i + 1) * chunk_n) for i in idx]
    lims = np.array([x if x and np.isfinite(x) and x > 0 else np.inf for x in limits_uv], dtype=float)
    hi45 = min(45.0, 0.45 * sf)
    line_ok = line_freq_hz + 6.0 < sf / 2 * 0.98
    sos = butter(4, [1.0, hi45], btype="band", fs=sf, output="sos")
    nbrs = _neighbours(names, positions)

    flat, clip, sdv, emg, drift, line, corr_low = ([] for _ in range(7))
    windows: list[tuple[float, str]] = []
    sampled_s = 0.0
    for ci, (s0, s1) in enumerate(spans):
        X = np.asarray(read(s0, s1), dtype=np.float64)
        n = X.shape[1]
        sampled_s += n / sf
        w5 = max(1, int(th["flat_window_s"] * sf))
        nw5 = n // w5
        sd5 = X[:, : nw5 * w5].reshape(n_ch, nw5, w5).std(axis=2) if nw5 else X.std(axis=1)[:, None]
        flat.append((sd5 < th["flat_std_uv"]).mean(axis=1))
        clip.append((np.abs(X) >= 0.999 * lims[:, None]).mean(axis=1))

        nper = min(int(10 * sf), n)
        f, p = welch(X, fs=sf, nperseg=nper, noverlap=nper // 2, axis=-1, detrend="constant")
        tot = _band_sum(f, p, 0.5, hi45) + 1e-20
        lf = _band_sum(f, p, 1.0, min(30.0, hi45)) + 1e-20
        hf = _band_sum(f, p, min(30.0, hi45), hi45)
        emg.append(hf / lf if hi45 > 30 else np.full(n_ch, np.nan))
        drift.append(_band_sum(f, p, 0.1, 0.5) / tot if nper / sf >= 5 else np.full(n_ch, np.nan))
        if line_ok:
            flank = (_band_mean(f, p, line_freq_hz - 6, line_freq_hz - 2) + _band_mean(f, p, line_freq_hz + 2, line_freq_hz + 6)) / 2
            line.append(10 * np.log10((_band_mean(f, p, line_freq_hz - 1, line_freq_hz + 1) + 1e-20) / (flank + 1e-20)))
        else:
            line.append(np.full(n_ch, np.nan))

        Xf = sosfiltfilt(sos, X, axis=-1)
        w1 = max(1, int(sf))
        nw1 = n // w1
        if nw1 < 1:
            corr_low.append(np.full(n_ch, np.nan))
            sdv.append(X.std(axis=1))
            continue
        Xw = Xf[:, : nw1 * w1].reshape(n_ch, nw1, w1)
        if n_ch >= 8:
            glob = np.median(Xw, axis=0)
            low = np.empty(n_ch)
            for i in range(n_ch):
                ref = np.median(Xw[nbrs[i]], axis=0) if nbrs and i in nbrs else glob
                low[i] = (_window_corr(Xw[i], ref) < th["corr_min"]).mean()
            corr_low.append(low)
        else:
            corr_low.append(np.full(n_ch, np.nan))

        # 1 s windows for artefact proposals
        t0 = s0 / sf
        ptp = Xw.max(-1) - Xw.min(-1)
        sd1 = X[:, : nw1 * w1].reshape(n_ch, nw1, w1).std(-1)  # raw (unfiltered): filter ringing would hide flat stretches
        sdv.append(np.median(sd1, axis=1))  # typical SD: robust to blinks and other brief transients
        high = (ptp > th["high_amp_ptp_uv"]).mean(axis=0) >= 0.5
        flat_all = (sd1 < th["flat_std_uv"]).all(axis=0)
        power = np.abs(np.fft.rfft(Xw, axis=-1)) ** 2
        fr = np.fft.rfftfreq(w1, 1 / sf)
        ratio = np.median(power[..., (fr >= min(30.0, hi45)) & (fr < hi45)].sum(-1)
                          / (power[..., (fr >= 1) & (fr < min(30.0, hi45))].sum(-1) + 1e-12), axis=0) if hi45 > 30 else np.zeros(nw1)
        for k in range(nw1):
            if flat_all[k]:
                windows.append((t0 + k, "flat"))
            elif high[k]:
                windows.append((t0 + k, "high_amplitude"))
            elif ratio[k] > th["muscle_ratio"]:
                windows.append((t0 + k, "muscle"))
        if progress:
            progress(int(100 * (ci + 1) / len(spans)), "Assessing signal quality")

    med = lambda a: np.nanmedian(np.stack(a), axis=0) if np.isfinite(np.stack(a)).any() else np.full(n_ch, np.nan)  # noqa: E731
    flat_f, clip_f, sd_m = np.mean(flat, axis=0), np.mean(clip, axis=0), np.median(sdv, axis=0)
    emg_m, drift_m, line_m, corr_m = med(emg), med(drift), med(line), med(corr_low)

    logsd = np.log10(sd_m + 1e-6)
    center = np.median(logsd)
    mad = max(1.4826 * np.median(np.abs(logsd - center)), 0.05)
    z = (logsd - center) / mad
    noisy = ((z > th["noisy_z"]) & (sd_m > 2 * np.median(sd_m))) | (sd_m > th["noisy_std_uv"])

    channels, bad = [], []
    for i, name in enumerate(names):
        flags = []
        if flat_f[i] > th["flat_fraction"]:
            flags.append("flat")
        if clip_f[i] > th["clip_fraction"]:
            flags.append("clipped")
        if noisy[i] and "flat" not in flags:
            flags.append("noisy")
        if np.isfinite(corr_m[i]) and corr_m[i] > th["corr_bad_fraction"] and not flags:
            flags.append("uncorrelated")
        is_bad = bool(flags)
        if np.isfinite(line_m[i]) and line_m[i] > th["line_db"]:
            flags.append("line_noise")
        if np.isfinite(emg_m[i]) and emg_m[i] > th["emg_ratio"]:
            flags.append("emg")
        if np.isfinite(drift_m[i]) and drift_m[i] > th["drift_ratio"]:
            flags.append("drift")
        if is_bad:
            bad.append(name)
        r = lambda v: None if not np.isfinite(v) else round(float(v), 4)  # noqa: E731
        channels.append({"name": name, "std_uv": r(sd_m[i]), "flat_fraction": r(flat_f[i]), "clipped_fraction": r(clip_f[i]),
                         "line_noise_db": r(line_m[i]), "emg_ratio": r(emg_m[i]), "drift_ratio": r(drift_m[i]),
                         "corr_low_fraction": r(corr_m[i]), "flags": flags, "bad": is_bad})

    good = [c for c in channels if not c["bad"]]

    def frac(flag: str) -> float:
        """Share of GOOD channels with this warning (bad channels are already penalised by their own score)."""
        return float(np.mean([flag in c["flags"] for c in good])) if good else 1.0

    segments = _merge(windows)
    flagged_s = float(sum(s["duration_s"] for s in segments))
    return {
        "algorithm_version": QUALITY_VERSION, "line_freq_hz": line_freq_hz, "line_noise_assessed": bool(line_ok),
        "sampled_seconds": round(sampled_s, 1), "recording_seconds": round(n_times / sf, 1),
        "sampled_all": bool(len(spans) >= n_chunks or n_times < chunk_n), "n_channels": n_ch,
        "correlation_reference": "6 nearest electrodes" if nbrs else "global median (positions unknown)",
        "bad_channels": bad, "channels": channels, "segments": segments,
        "sub_scores": {
            "bad_channels": float(np.clip(1 - (len(bad) / n_ch) / 0.3, 0, 1)),
            "line_noise": (1 - frac("line_noise")) if line_ok else None,
            "emg": 1 - frac("emg") if hi45 > 30 else None,
            "drift": 1 - frac("drift"),
        },
        "flagged_window_fraction": round(min(1.0, flagged_s / max(sampled_s, 1e-9)), 4),
        "thresholds": th,
    }


def finalize(q: dict, cfg: dict, epoch_stats: dict | None) -> dict:
    """Add the epoch-based sub-scores, the overall score (0-100) and the grade."""
    sub = dict(q["sub_scores"])
    if epoch_stats:
        kept, step, length = epoch_stats["kept"], epoch_stats["step_s"], epoch_stats["length_s"]
        usable = kept * step + (length - step if kept else 0)
        rej = epoch_stats["rejected_fraction"]
        sub["usable"] = float(min(1.0, usable / 120.0))
        sub["rejected"] = float(np.clip(1 - rej / 0.5, 0, 1))
        q["usable_duration_s"] = round(float(usable), 1)
        q["rejected_epoch_fraction"] = round(float(rej), 4)
    else:
        usable = q["sampled_seconds"] * (1 - q["flagged_window_fraction"])
        sub["usable"] = float(min(1.0, usable / 120.0))
        q["usable_duration_s"] = round(float(usable), 1)
    used = {k: v for k, v in sub.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in used)
    score = 100 * sum(WEIGHTS[k] * v for k, v in used.items()) / total_w
    grade = "good" if score >= cfg["grade_good_min"] else "fair" if score >= cfg["grade_fair_min"] else "poor"
    bad_frac = len(q["bad_channels"]) / max(q["n_channels"], 1)
    cap = "poor" if bad_frac > 0.30 else "fair" if bad_frac > 0.15 else None
    order = ["poor", "fair", "good"]
    if cap and order.index(grade) > order.index(cap):
        grade = cap  # a high share of bad channels cannot be averaged away by clean ones
    q["grade_cap"] = cap
    q["sub_scores"] = {k: (None if v is None else round(v, 3)) for k, v in sub.items()}
    q["overall_score"] = round(float(score), 1)
    q["grade"] = grade
    q["weights"] = WEIGHTS
    q["calibration_note"] = "Weights and grade cut-offs are expert-chosen and not yet calibrated against labelled data."
    return q
