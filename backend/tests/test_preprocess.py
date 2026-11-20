"""The pre-processing pipeline does what its definition says (synthetic signals = test fixtures only)."""

import mne
import numpy as np
import pytest
from scipy.signal import butter, sosfiltfilt, welch

from app.eeg import pipelines
from app.eeg import preprocess as pp

NAMES16 = ["Fp1", "Fp2", "F7", "F3", "Fz", "F4", "F8", "T7", "C3", "Cz", "C4", "T8", "P3", "Pz", "P4", "O1"]
NAMES32 = NAMES16 + ["F1", "F2", "FC3", "FC4", "C1", "C2", "C5", "C6", "CP3", "CP4", "P1", "P2", "P7", "P8", "O2", "Oz"]
PARAMS = pipelines.resolve("resting", None, 50, None, ["clinician"])["params"]


def make(names=NAMES16, sf=250.0, seconds=120, seed=0, ):
    rng = np.random.default_rng(seed)
    n = int(seconds * sf)
    t = np.arange(n) / sf
    lp = butter(4, 25, btype="low", fs=sf, output="sos")
    common = sosfiltfilt(lp, rng.normal(size=n))
    common = 15 * common / common.std() + 12 * np.sin(2 * np.pi * 10 * t)
    X = np.stack([common * rng.uniform(0.7, 1.1) + rng.normal(0, 3, n) for _ in names])
    return X, t, rng


def to_raw(X, names, sf):
    info = mne.create_info(list(names), sf, "eeg")
    return mne.io.RawArray(X * 1e-6, info, verbose=False)  # volts


def meta(names, limit=3200.0):
    return [{"name": n, "physical_min": -limit, "physical_max": limit} for n in names]


def go(X, names=NAMES16, sf=250.0, params=PARAMS, user_bad=(), user_ok=(), excluded=(), selected=()):
    return pp.run_pipeline(to_raw(X, names, sf), meta(names), params, set(user_bad), set(user_ok), list(excluded), list(selected))


def step(res, name):
    return next(s for s in res["steps"] if s["name"] == name)


def power_at(x, sf, f0, half=1.0):
    f, p = welch(x, fs=sf, nperseg=int(10 * sf))
    m = (f >= f0 - half) & (f <= f0 + half)
    return p[m].sum()


def test_pipeline_is_deterministic_bit_for_bit():
    X, *_ = make()
    a, b = go(X), go(X)
    assert a["output_sha256"] == b["output_sha256"]
    assert [s["summary"] for s in a["steps"]] == [s["summary"] for s in b["steps"]]


def test_every_step_is_recorded_with_parameters():
    res = go(make()[0])
    names = [s["name"] for s in res["steps"]]
    assert names == ["load", "quality_assessment", "bad_channels", "notch", "bandpass", "bad_channel_handling",
                     "reference", "epoching", "final_quality"]
    assert all({"name", "params", "seconds", "summary"} <= set(s) for s in res["steps"])
    assert step(res, "bandpass")["params"]["low_hz"] == 0.5 and step(res, "notch")["params"]["freqs_hz"][0] == 50


def test_filtering_removes_drift_and_mains_and_keeps_alpha():
    # Alpha with a different random phase on every channel: a signal common to ALL channels would (correctly)
    # be removed by the average reference, so it cannot be used to test that the band-pass keeps 10 Hz.
    sf = 250.0
    rng = np.random.default_rng(5)
    t = np.arange(int(120 * sf)) / sf
    X = np.stack([20 * np.sin(2 * np.pi * 10 * t + rng.uniform(0, 6.28)) + rng.normal(0, 3, t.size) for _ in NAMES16])
    X = X + 120 * np.sin(2 * np.pi * 0.1 * t) * rng.uniform(0.5, 1.5, (16, 1)) + 30 * np.sin(2 * np.pi * 50 * t + rng.uniform(0, 6.28, (16, 1)))
    full = go(X, user_ok=NAMES16)["processed"].get_data() * 1e6  # independent channels are (rightly) auto-flagged; user says OK
    out = full[0]
    assert power_at(out, sf, 0.1, 0.1) < 0.01 * power_at(X[0], sf, 0.1, 0.1)         # drift attenuated > 20 dB
    assert power_at(out, sf, 50) < 0.01 * power_at(X[0], sf, 50)                      # mains attenuated > 20 dB
    # Alpha is kept. Per channel the average reference can raise or lower power, but the TOTAL over channels can
    # only fall slightly (by the removed mean component): sum |x_c - m|^2 = sum |x_c|^2 - N |m|^2.
    total_in = sum(power_at(X[c], sf, 10) for c in range(16))
    total_out = sum(power_at(full[c], sf, 10) for c in range(16))
    assert 0.85 < total_out / total_in <= 1.01


def test_average_reference_is_applied():
    out = go(make()[0])["processed"].get_data()
    assert np.abs(out.mean(axis=0)).max() < 1e-12


def test_one_bad_channel_with_a_position_is_interpolated():
    X, *_ = make()
    X[3] = 0.0  # F3 flat
    res = go(X)
    assert res["bad_channels"] == ["F3"] and res["interpolated"] == ["F3"] and res["dropped"] == []
    assert len(res["processed"].ch_names) == 16
    assert res["processed"].get_data()[3].std() * 1e6 > 0.5  # reconstructed from neighbours, not flat (a flat channel has SD 0)
    assert step(res, "bad_channel_handling")["summary"]["action"].startswith("interpolated")


def test_too_many_bad_channels_are_dropped_not_interpolated():
    X, *_ = make()
    X[[1, 5, 9, 13]] = 0.0  # 25 % bad > 20 %
    res = go(X)
    assert len(res["dropped"]) == 4 and res["interpolated"] == [] and len(res["processed"].ch_names) == 12
    assert "more than 20%" in step(res, "bad_channel_handling")["summary"]["reason"]
    assert any("NOT interpolated" in w for w in res["warnings"])


def test_clustered_bad_channels_are_dropped_not_interpolated():
    X, *_ = make(NAMES32)
    for n in ("C3", "C1", "C5"):  # three adjacent electrodes
        X[NAMES32.index(n)] = 0.0
    res = go(X, NAMES32)
    assert sorted(res["dropped"]) == ["C1", "C3", "C5"] and res["interpolated"] == []
    assert "next to each other" in step(res, "bad_channel_handling")["summary"]["reason"]


def test_channels_without_electrode_positions_prevent_interpolation():
    names = NAMES16[:-1] + ["Zz9"]  # not a standard electrode
    X, *_ = make(names)
    X[3] = 0.0
    res = go(X, names)
    assert res["dropped"] == ["F3"] and "electrode positions" in step(res, "bad_channel_handling")["summary"]["reason"]


def test_user_decisions_override_the_automatic_check():
    X, *_ = make()
    X[4] = 0.0
    res = go(X, user_ok=["Fz"], user_bad=["C4"])
    assert "Fz" not in res["bad_channels"] and "C4" in res["bad_channels"]


def test_run_fails_clearly_when_almost_every_channel_is_bad():
    X, *_ = make()
    X[:14] = 0.0
    with pytest.raises(pp.PipelineFailure, match="usable channels"):
        go(X)


def burst(X, t, channels, start=30, amp=400.0):
    out = X.copy()
    out[channels, int(start * 250): int((start + 1) * 250)] += amp * np.sin(2 * np.pi * 3 * t[:250])
    return out


def test_epoching_counts_and_a_single_bad_channel_is_masked_not_the_whole_epoch():
    X, t, _ = make()
    res = go(X)
    assert res["epochs"]["summary"]["total"] == 119 and res["epochs"]["summary"]["kept"] == 119
    r2 = go(burst(X, t, [5]))  # 1 s burst on ONE channel (F4)
    ep = r2["epochs"]
    assert ep["summary"]["kept"] == 119  # the epochs survive ...
    masked = {(ep["onset_s"][e], c) for e, c in zip(ep["masked"]["epoch"], ep["masked"]["channel"], strict=True)}
    assert {(29.0, 5), (30.0, 5)} <= masked and {c for _, c in masked} == {5}  # ... but F4 is masked in them
    assert ep["summary"]["most_masked_channels"][0] == "F4"


def test_an_epoch_is_rejected_when_many_channels_are_affected():
    X, t, _ = make()
    r = go(burst(X, t, [0, 1, 2, 3, 4, 5]))  # 6 of 16 channels (> 20 %)
    ep = r["epochs"]
    rejected = {o for o, s in zip(ep["onset_s"], ep["status"], strict=True) if s == pp.STATUS["ptp"]}
    assert {29.0, 30.0} <= rejected and len(rejected) <= 5
    assert ep["summary"]["kept"] == 119 - len(rejected) and ep["summary"]["rejected_ptp"] == len(rejected)


def test_quiet_recordings_use_a_lower_relative_limit():
    X, t, _ = make()
    X = X * 0.15  # a uniformly quiet recording (typical amplitudes of a few microvolts)
    r = go(burst(X, t, [2], amp=65.0))  # a 130 uV peak-to-peak transient: gross here, although far below the 300 uV ceiling
    ep = r["epochs"]
    assert (29.0, 2) in {(ep["onset_s"][e], c) for e, c in zip(ep["masked"]["epoch"], ep["masked"]["channel"], strict=True)}


def test_annotations_exclude_and_selection_restricts_epochs():
    X, *_ = make()
    r = go(X, excluded=[(10.0, 14.0)])
    ann = {o for o, s in zip(r["epochs"]["onset_s"], r["epochs"]["status"], strict=True) if s == pp.STATUS["annotation"]}
    assert ann == {9.0, 10.0, 11.0, 12.0, 13.0}
    r = go(X, selected=[(20.0, 40.0)])
    kept = [o for o, s in zip(r["epochs"]["onset_s"], r["epochs"]["status"], strict=True) if s == 0]
    assert kept == [float(v) for v in range(20, 39)]
    assert r["epochs"]["summary"]["outside_selection"] == 119 - 19


def test_short_recordings_are_flagged_low_confidence():
    r = go(make(seconds=60)[0])
    assert r["epochs"]["summary"]["kept"] == 59 and r["low_confidence"] is True
    assert any("low-confidence" in w for w in r["warnings"])
    assert go(make()[0])["low_confidence"] is False


def test_high_sampling_rates_are_filtered_then_resampled():
    X, *_ = make(sf=1000.0, seconds=70)
    r = go(X, sf=1000.0)
    assert r["sfreq_hz"] == 250.0 and step(r, "resample")["params"] == {"from_hz": 1000.0, "to_hz": 250.0}
    r2 = go(make()[0])
    assert r2["sfreq_hz"] == 250.0 and not any(s["name"] == "resample" for s in r2["steps"])


def test_final_quality_uses_epoch_statistics():
    r = go(make()[0])
    q = r["quality"]
    assert q["grade"] == "good" and q["usable_duration_s"] == pytest.approx(120, abs=1) and "rejected" in q["sub_scores"]


def test_pipeline_definition_is_versioned_hashed_and_overrides_are_restricted():
    h1 = pipelines.params_hash("resting", "1.0.0", PARAMS)
    assert h1 == pipelines.params_hash("resting", "1.0.0", dict(PARAMS))
    p60 = pipelines.resolve("resting", "1.0.0", 60, None, ["eeg_technologist"])
    assert pipelines.params_hash("resting", "1.0.0", p60["params"]) != h1  # different line frequency -> different hash
    with pytest.raises(pipelines.PipelineError, match="may not change"):
        pipelines.resolve("resting", None, 50, {"epoching.reject_ptp_uv": 100}, ["eeg_technologist"])
    with pytest.raises(pipelines.PipelineError, match="cannot be overridden"):
        pipelines.resolve("resting", None, 50, {"reference": "none"}, ["clinician"])
    with pytest.raises(pipelines.PipelineError, match="Invalid"):
        pipelines.resolve("resting", None, 50, {"line_freq_hz": 55}, ["clinician"])
    with pytest.raises(pipelines.PipelineError, match="Unknown pipeline"):
        pipelines.resolve("nope", None, 50, None, ["clinician"])
    ok = pipelines.resolve("resting", None, 50, {"epoching.reject_ptp_uv": 100}, ["clinician"])
    assert ok["params"]["epoching"]["reject_ptp_uv"] == 100 and PARAMS["epoching"]["reject_ptp_uv"] == 300.0
