"""Pre-processing pipeline `resting` v1.x. Every step is recorded (name, parameters, seconds, summary).

Order:  quality check -> bad channels -> notch -> band-pass -> (resample) -> bad-channel handling -> average
reference -> 2 s epochs -> channel masks / epoch rejection -> final quality score.
Everything here is deterministic (no random numbers), so the same input and parameters give the same output.
v1.0.0 has no ICA; v1.1.0 adds an optional Butterworth band-pass and FastICA blink removal (app/eeg/ica.py).
Data-driven epoch rejection (autoreject) is not implemented; a fixed peak-to-peak threshold is used.
"""

import hashlib
import time

import mne
import numpy as np

from app.eeg import channels as chan
from app.eeg import ica as ICA
from app.eeg import quality as Q

mne.set_log_level("ERROR")

STATUS = {"ok": 0, "ptp": 1, "annotation": 2, "outside_selection": 3}
EXCLUDE_LABELS = ("artifact_eye", "artifact_muscle", "artifact_movement", "electrode_pop", "flat", "excluded")
SELECT_LABEL = "selected_for_analysis"


class PipelineFailure(ValueError):
    """A problem the user can understand and act on (shown in the UI)."""


def _timed(steps: list, name: str, params: dict, fn):
    t0 = time.perf_counter()
    summary = fn()
    steps.append({"name": name, "params": params, "seconds": round(time.perf_counter() - t0, 2), "summary": summary})
    return summary


def _positions(raw: mne.io.BaseRaw) -> dict[str, np.ndarray]:
    return {ch["ch_name"]: ch["loc"][:3] for ch in raw.info["chs"] if np.isfinite(ch["loc"][:3]).all() and np.any(ch["loc"][:3])}


def _interpolation_decision(raw, bad: list[str], p: dict) -> tuple[bool, str]:
    n = len(raw.ch_names)
    pos = _positions(raw)
    if not p["enabled"]:
        return False, "interpolation disabled in this pipeline"
    if len(pos) != n:
        return False, f"only {len(pos)} of {n} channels have electrode positions"
    if n < p["min_channels_with_positions"]:
        return False, f"fewer than {p['min_channels_with_positions']} channels with positions"
    if len(bad) / n > p["max_bad_fraction"]:
        return False, (f"{len(bad)} of {n} channels are bad (more than {p['max_bad_fraction']:.0%}); "
                       "interpolating would invent too much data")
    for b in bad:
        near = [o for o in bad if np.linalg.norm(pos[b] - pos[o]) <= p["cluster_distance_m"]]
        if len(near) > p["max_clustered"]:
            return False, f"{len(near)} bad channels are next to each other ({', '.join(near)}); neighbours cannot reconstruct them"
    return True, "ok"


def run_pipeline(raw: mne.io.BaseRaw, meta: list[dict], params: dict, user_bad: set[str], user_ok: set[str],
                 excluded: list[tuple[float, float]], selected: list[tuple[float, float]], progress=None) -> dict:
    """`raw`: preloaded, EEG channels only, in volts, with normalised unique names. `meta`: per-channel dicts
    (name, physical_min, physical_max). Returns the processed Raw plus a full provenance record."""
    steps: list[dict] = []
    warnings: list[str] = []
    cfg_q = params["quality"]
    names = list(raw.ch_names)
    sf0 = float(raw.info["sfreq"])

    def say(pct, msg):
        if progress:
            progress(pct, msg)

    _timed(steps, "load", {}, lambda: {"n_channels": len(names), "sfreq_hz": sf0, "duration_s": round(raw.n_times / sf0, 1)})

    say(5, "Assessing signal quality")
    limits = [max(abs(m.get("physical_min") or 0), abs(m.get("physical_max") or 0)) for m in meta]
    qual: dict = {}

    def _quality():
        qual.update(Q.assess(lambda a, b: raw.get_data(start=a, stop=b) * 1e6, raw.n_times, sf0, names, limits,
                             params["line_freq_hz"], cfg_q, lambda pct, m: say(5 + pct // 4, m),
                             positions=chan.positions(names)))
        return {"bad": qual["bad_channels"], "segments_proposed": len(qual["segments"]),
                "sampled_seconds": qual["sampled_seconds"]}

    _timed(steps, "quality_assessment", {"line_freq_hz": params["line_freq_hz"], "algorithm": Q.QUALITY_VERSION}, _quality)

    bad = sorted((set(qual["bad_channels"]) | user_bad) - user_ok, key=names.index)
    steps.append({"name": "bad_channels", "params": {"user_marked_bad": sorted(user_bad), "user_marked_ok": sorted(user_ok)},
                  "seconds": 0.0, "summary": {"bad": bad, "automatic": qual["bad_channels"]}})
    if len(names) - len(bad) < 4:
        raise PipelineFailure(f"Only {len(names) - len(bad)} usable channels remain after removing {len(bad)} bad ones; "
                              "this recording cannot be processed with this pipeline.")

    say(30, "Filtering")
    work = raw.copy()
    montage = mne.channels.make_standard_montage("standard_1005")
    work.set_montage(montage, on_missing="ignore", match_case=False)

    nyq = sf0 / 2
    if params["notch"]:
        freqs = [f for f in np.arange(params["line_freq_hz"], nyq * 0.98, params["line_freq_hz"])]
        if freqs:
            _timed(steps, "notch", {"freqs_hz": [float(f) for f in freqs]},
                   lambda: (work.notch_filter(freqs, method="fir", fir_design="firwin", verbose=False), {"applied": True})[1])
    lo, hi = params["bandpass_hz"]
    hi = min(float(hi), 0.45 * sf0)
    if lo >= hi:
        raise PipelineFailure(f"Band-pass {lo}-{hi} Hz is impossible at a sampling rate of {sf0:g} Hz")
    flt = params.get("filter") or {"design": "fir"}
    if flt["design"] == "butterworth":
        order = int(flt["order"])
        # Forward-backward (zero-phase) filtering squares the magnitude response: -6 dB at the cut-offs, effective order 2 x `order`.
        _timed(steps, "bandpass", {"low_hz": lo, "high_hz": hi, "design": f"Butterworth order {order}, forward-backward (zero-phase)"},
               lambda: (work.filter(lo, hi, method="iir", iir_params={"order": order, "ftype": "butter", "output": "sos"},
                                    phase="zero", verbose=False), {"applied": True})[1])
    else:
        _timed(steps, "bandpass", {"low_hz": lo, "high_hz": hi, "design": "zero-phase FIR (firwin)"},
               lambda: (work.filter(lo, hi, method="fir", fir_design="firwin", phase="zero", verbose=False), {"applied": True})[1])

    if sf0 > params["resample_if_above_hz"]:
        _timed(steps, "resample", {"from_hz": sf0, "to_hz": params["resample_to_hz"]},
               lambda: (work.resample(params["resample_to_hz"], verbose=False), {"applied": True})[1])
    sf = float(work.info["sfreq"])

    say(55, "Handling bad channels")
    interpolated: list[str] = []
    dropped: list[str] = []
    if bad:
        ok, why = _interpolation_decision(work, bad, params["interpolation"])

        def _handle():
            nonlocal interpolated, dropped
            if ok:
                work.info["bads"] = list(bad)
                work.interpolate_bads(reset_bads=True, verbose=False)
                interpolated = list(bad)
                return {"action": "interpolated (spherical spline)", "channels": bad}
            work.drop_channels(bad)
            dropped = list(bad)
            return {"action": "dropped (not interpolated)", "reason": why, "channels": bad}

        _timed(steps, "bad_channel_handling", params["interpolation"], _handle)
        if dropped:
            warnings.append(f"{len(dropped)} bad channel(s) were removed and NOT interpolated: {why}.")
        else:
            warnings.append(f"{len(interpolated)} bad channel(s) were interpolated; they are marked and excluded from comparisons.")
    else:
        steps.append({"name": "bad_channel_handling", "params": params["interpolation"], "seconds": 0.0,
                      "summary": {"action": "none needed"}})

    ica_cfg = params.get("ica") or {"enabled": False}
    if ica_cfg["enabled"]:
        say(62, "Removing eye-blink artefacts (FastICA)")
        res = _timed(steps, "ica_blink_removal", {k: v for k, v in ica_cfg.items() if k != "enabled"},
                     lambda: ICA.remove_blinks(work, ica_cfg))
        if not res["applied"]:
            warnings.append(f"Eye-blink removal was skipped: {res['reason']}.")
        elif res["removed"]:
            warnings.append(f"{len(res['removed'])} blink-like ICA component(s) were removed (heuristic, not clinically validated).")
        else:
            warnings.append("No blink-like ICA component was found; nothing was removed.")

    _timed(steps, "reference", {"type": params["reference"]},
           lambda: (work.set_eeg_reference("average", projection=False, verbose=False),
                    {"applied": True, "channels": len(work.ch_names)})[1])

    say(70, "Cutting epochs")
    ep = params["epoching"]
    data = work.get_data()
    L, S = int(round(ep["length_s"] * sf)), int(round(ep["step_s"] * sf))
    starts = np.arange(0, data.shape[1] - L + 1, S)
    onsets = starts / sf
    status = np.zeros(len(starts), dtype=np.int8)

    masked_pairs: tuple = ((), ())

    def _epoch():
        nonlocal masked_pairs
        # peak-to-peak of every channel in every epoch (microvolts): shape (n_epochs, n_channels)
        ptp = np.array([data[:, s:s + L].max(axis=1) - data[:, s:s + L].min(axis=1) for s in starts]) * 1e6
        med = np.median(ptp, axis=0)
        limit = np.minimum(ep["reject_ptp_uv"], np.maximum(ep["reject_relative_floor_uv"], ep["reject_relative_k"] * med))
        mask = ptp > limit[None, :]
        status[mask.mean(axis=1) > ep["reject_channel_fraction"]] = STATUS["ptp"]
        if selected:
            inside = np.array([any(a <= o and o + ep["length_s"] <= b for a, b in selected) for o in onsets])
            status[~inside] = STATUS["outside_selection"]
        for i, o in enumerate(onsets):
            if any(o < b and o + ep["length_s"] > a for a, b in excluded):
                status[i] = STATUS["annotation"]
        keep = status == 0
        pair_epoch, pair_chan = np.nonzero(mask & keep[:, None])  # channel-level masks inside KEPT epochs
        masked_pairs = (pair_epoch, pair_chan)
        per_channel = (mask & keep[:, None]).sum(axis=0)
        return {"total": int(len(starts)), "kept": int(keep.sum()),
                "rejected_ptp": int((status == 1).sum()), "rejected_annotation": int((status == 2).sum()),
                "outside_selection": int((status == 3).sum()), "reject_ptp_uv": ep["reject_ptp_uv"],
                "masked_channel_epochs": int(len(pair_epoch)),
                "masked_fraction_of_kept": round(float(len(pair_epoch) / max(keep.sum() * ptp.shape[1], 1)), 4),
                "most_masked_channels": [work.ch_names[i] for i in np.argsort(per_channel)[::-1][:5] if per_channel[i] > 0]}

    es = _timed(steps, "epoching", {k: ep[k] for k in ("length_s", "step_s", "reject_ptp_uv", "reject_relative_k",
                                                          "reject_relative_floor_uv", "reject_channel_fraction")}, _epoch)
    kept = es["kept"]
    low_conf = kept < ep["min_clean_epochs"]
    if low_conf:
        warnings.append(f"Only {kept} clean epochs (fewer than {ep['min_clean_epochs']}): every feature from this run will be "
                        "flagged low-confidence.")
    rej = 1 - kept / max(len(starts), 1)
    qual = Q.finalize(qual, cfg_q, {"kept": kept, "step_s": ep["step_s"], "length_s": ep["length_s"],
                                    "rejected_fraction": rej})
    steps.append({"name": "final_quality", "params": {}, "seconds": 0.0,
                  "summary": {"score": qual["overall_score"], "grade": qual["grade"], "usable_s": qual["usable_duration_s"]}})

    out = np.ascontiguousarray(data.astype(np.float32))
    digest = hashlib.sha256(out.tobytes() + "|".join(work.ch_names).encode()).hexdigest()
    return {
        "processed": work, "steps": steps, "quality": qual, "bad_channels": bad, "interpolated": interpolated,
        "dropped": dropped, "sfreq_hz": sf, "output_sha256": digest, "low_confidence": low_conf, "warnings": warnings,
        "epochs": {"length_s": ep["length_s"], "step_s": ep["step_s"], "onset_s": np.round(onsets, 3).tolist(),
                   "status": status.tolist(), "legend": STATUS, "summary": es,
                   # (epoch index, channel index) pairs inside KEPT epochs whose channel exceeded its limit:
                   # downstream features must skip these values. Capped to keep the document small.
                   "masked": {"epoch": masked_pairs[0][:100000].tolist(), "channel": masked_pairs[1][:100000].tolist(),
                              "truncated": bool(len(masked_pairs[0]) > 100000)}},
        "channels": [{"index": i, "name": n, "ch_type": "eeg", "interpolated": n in interpolated}
                     for i, n in enumerate(work.ch_names)],
    }
