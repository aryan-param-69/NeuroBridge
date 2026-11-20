"""Quality metrics fire on the defects they are meant to catch (synthetic signals = test fixtures only)."""

import numpy as np
import pytest
import yaml
from scipy.signal import butter, sosfiltfilt

from app.eeg import quality as Q
from app.eeg.pipelines import DIR

CFG = yaml.safe_load((DIR / "resting_v1.0.0.yaml").read_text())["params"]["quality"]
SF = 250.0
LIMIT = 300.0


def make(n_ch=16, seconds=120, seed=0):
    """Channels share a common brain-like signal (so they correlate) plus small independent noise."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SF)
    t = np.arange(n) / SF
    lp = butter(4, 25, btype="low", fs=SF, output="sos")
    common = sosfiltfilt(lp, rng.normal(size=n))
    common = 18 * common / common.std() + 15 * np.sin(2 * np.pi * 10 * t)
    X = np.stack([common * rng.uniform(0.7, 1.1) + rng.normal(0, 3, n) for _ in range(n_ch)])
    return X, t, rng


def run(X, line=50.0, cfg=CFG, limits=None):
    names = [f"Ch{i}" for i in range(X.shape[0])]
    return Q.assess(lambda a, b: X[:, a:b], X.shape[1], SF, names, limits or [LIMIT] * len(names), line, cfg)


def flags(q, i):
    return set(q["channels"][i]["flags"])


def test_clean_recording_has_no_bad_channels_and_grades_good():
    X, *_ = make()
    q = Q.finalize(run(X), CFG, None)
    assert q["bad_channels"] == [] and q["grade"] == "good" and q["overall_score"] > 85
    assert all(not c["flags"] for c in q["channels"]), [c["flags"] for c in q["channels"] if c["flags"]]


def test_each_defect_is_detected_only_on_the_affected_channel():
    X, t, rng = make()
    X[3] = 0.0                                                           # flat
    X[4] = np.clip(X[4] * 30, -LIMIT, LIMIT)                            # clipped at the ADC limit
    X[5] = X[5] + 30 * np.sin(2 * np.pi * 50 * t)                       # mains interference
    hf = sosfiltfilt(butter(4, [30, 45], btype="band", fs=SF, output="sos"), rng.normal(size=X.shape[1]))
    X[6] = X[6] + 25 * hf / hf.std()                                    # muscle-like high-frequency activity
    X[7] = rng.normal(0, 1000, X.shape[1])                              # very noisy
    X[8] = sosfiltfilt(butter(4, 25, btype="low", fs=SF, output="sos"), rng.normal(size=X.shape[1])) * 8  # unrelated to the others
    q = run(X, limits=[LIMIT] * 16)
    assert "flat" in flags(q, 3) and "clipped" in flags(q, 4) and "line_noise" in flags(q, 5)
    assert "emg" in flags(q, 6) and "noisy" in flags(q, 7) and "uncorrelated" in flags(q, 8)
    assert set(q["bad_channels"]) == {"Ch3", "Ch4", "Ch7", "Ch8"}  # line noise / EMG are warnings, not "bad"
    for i in (0, 1, 2, 9, 10, 11):
        assert not flags(q, i), (i, flags(q, i))


def test_line_noise_uses_the_configured_mains_frequency():
    X, t, _ = make()
    X[2] = X[2] + 30 * np.sin(2 * np.pi * 60 * t)
    assert "line_noise" in flags(run(X, line=60.0), 2)
    assert "line_noise" not in flags(run(X, line=50.0), 2)


def test_line_noise_is_not_assessed_when_the_sampling_rate_is_too_low():
    X, *_ = make(seconds=60)
    q = Q.assess(lambda a, b: X[:, a:b], X.shape[1], 100.0, [f"c{i}" for i in range(16)], [LIMIT] * 16, 50.0, CFG)
    assert q["line_noise_assessed"] is False and q["sub_scores"]["line_noise"] is None


def test_artifact_segments_are_proposed_with_labels_and_positions():
    X, t, rng = make()
    X[:, int(40 * SF): int(41 * SF)] += 400 * np.sin(2 * np.pi * 2 * t[: int(SF)])  # 1 s slow high-amplitude burst
    hf = sosfiltfilt(butter(4, [30, 45], btype="band", fs=SF, output="sos"), rng.normal(size=X.shape[1]))
    X[:, int(80 * SF): int(83 * SF)] += 25 * hf[int(80 * SF): int(83 * SF)] / hf.std()
    X[:, int(100 * SF): int(102 * SF)] = 0.0
    segs = run(X)["segments"]
    by = {s["label"]: s for s in segs}
    assert abs(by["high_amplitude"]["onset_s"] - 40) <= 1 and by["high_amplitude"]["duration_s"] <= 3
    assert 79 <= by["muscle"]["onset_s"] <= 81 and by["muscle"]["duration_s"] >= 2
    assert 99 <= by["flat"]["onset_s"] <= 101 and by["flat"]["duration_s"] >= 1.5


def test_many_bad_channels_give_a_poor_grade_and_epochs_change_the_score():
    X, *_ = make()
    X[:8] = 0.0
    q = Q.finalize(run(X), CFG, None)
    assert q["grade"] == "poor" and len(q["bad_channels"]) == 8
    X2, *_ = make(seed=1)
    base = Q.finalize(run(X2), CFG, {"kept": 110, "step_s": 1.0, "length_s": 2.0, "rejected_fraction": 0.05})
    worse = Q.finalize(run(X2), CFG, {"kept": 10, "step_s": 1.0, "length_s": 2.0, "rejected_fraction": 0.9})
    assert base["overall_score"] > worse["overall_score"] and worse["usable_duration_s"] < 20


def test_long_recordings_are_sampled_and_say_so():
    X, *_ = make(seconds=300)
    q = run(X, cfg={**CFG, "max_minutes": 1})
    assert q["sampled_all"] is False and q["sampled_seconds"] == pytest.approx(60, abs=1)


def test_too_short_recording_is_rejected():
    X, *_ = make(seconds=3)
    with pytest.raises(Q.QualityError):
        run(X)


def test_scoring_is_deterministic():
    X, *_ = make()
    a, b = Q.finalize(run(X), CFG, None), Q.finalize(run(X), CFG, None)
    assert a["overall_score"] == b["overall_score"] and a["channels"] == b["channels"]


def test_neighbour_correlation_avoids_false_alarms_for_a_spatially_distinct_group():
    """A left cluster of seven electrodes shares one source, a right cluster of nine another. Against a GLOBAL median the smaller
    group looks 'uncorrelated'; against their NEAREST neighbours they are fine."""
    from app.eeg.channels import positions

    names = ["C3", "C1", "C5", "CP3", "FC3", "CP1", "FC1", "C4", "C2", "C6", "CP4", "FC4", "CP2", "FC2", "T8", "FT8"]
    rng = np.random.default_rng(3)
    n = int(120 * SF)
    lp = butter(4, 25, btype="low", fs=SF, output="sos")

    def src():
        x = sosfiltfilt(lp, rng.normal(size=n))
        return 20 * x / x.std()

    a, b = src(), src()
    X = np.stack([(a if nm in names[:7] else b) * rng.uniform(0.8, 1.1) + rng.normal(0, 3, n) for nm in names])
    def go(pos):
        return Q.assess(lambda s, e: X[:, s:e], n, SF, names, [LIMIT] * 16, 50.0, CFG, positions=pos)
    g, nb = go(None), go(positions(names))
    assert nb["correlation_reference"] == "6 nearest electrodes" and "global" in g["correlation_reference"]
    assert len([c for c in g["channels"] if "uncorrelated" in c["flags"]]) >= 3  # false alarms with a global reference
    assert not [c["name"] for c in nb["channels"] if "uncorrelated" in c["flags"]]


def test_blinks_do_not_make_a_frontal_channel_look_noisy():
    X, t, rng = make()
    for k in range(10, 110, 6):  # a 250 uV, 0.3 s blink on one frontal channel every 6 s
        X[0, int(k * SF): int((k + 0.3) * SF)] += 250 * np.sin(np.pi * np.arange(int(0.3 * SF)) / int(0.3 * SF))
    q = run(X)
    assert "noisy" not in flags(q, 0) and q["bad_channels"] == []
