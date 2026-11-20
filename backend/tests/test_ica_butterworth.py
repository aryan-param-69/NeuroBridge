"""Pipeline `resting` v1.1.0: Butterworth band-pass and FastICA blink removal.

The blink test adds a KNOWN simulated blink signal to a real recording (EEGMMIDB S001R02, eyes closed), so the truth is known:
the blink waveform must disappear from the frontal channels while the real alpha rhythm elsewhere is left alone.
"""

from pathlib import Path

import mne
import numpy as np
import pytest
from scipy.signal import butter as sp_butter
from scipy.signal import freqz_sos, welch

from app.eeg import channels as chan
from app.eeg import ica as ICA
from app.eeg import pipelines
from app.eeg import preprocess as pp

DATA = Path(__file__).resolve().parents[2] / "data" / "eegmmidb" / "files" / "S001" / "S001R02.edf"
P10 = pipelines.resolve("resting", "1.0.0", 50, None, ["clinician"])["params"]
P11 = pipelines.resolve("resting", "1.1.0", 50, None, ["clinician"])["params"]
real = pytest.mark.skipif(not DATA.exists(), reason="EEGMMIDB dataset not available")


def test_v110_is_opt_in_and_v100_stays_the_default():
    assert pipelines.default_version("resting") == "1.0.0"
    assert P11["filter"]["design"] == "butterworth" and P11["ica"]["enabled"] is True
    assert "ica" not in P10 and "filter" not in P10  # released version untouched
    assert pipelines.params_hash("resting", "1.0.0", P10) != pipelines.params_hash("resting", "1.1.0", P11)


def test_butterworth_response_matches_theory():
    """MNE's forward-backward Butterworth: flat pass-band, -6 dB at the edges (squared magnitude), steep skirts, no phase shift."""
    sf = 250.0
    n = int(60 * sf)
    t = np.arange(n) / sf
    sig = {f: np.sin(2 * np.pi * f * t) for f in (0.1, 0.5, 2.0, 10.0, 45.0, 80.0, 110.0)}
    out = {}
    for f, x in sig.items():
        raw = mne.io.RawArray(x[None, :] * 1e-6, mne.create_info(["Cz"], sf, "eeg"), verbose=False)
        raw.filter(0.5, 45.0, method="iir", iir_params={"order": 4, "ftype": "butter", "output": "sos"}, phase="zero", verbose=False)
        y = raw.get_data()[0] * 1e6
        seg = slice(int(10 * sf), int(50 * sf))  # ignore edge effects
        out[f] = (np.sqrt(np.mean(y[seg] ** 2)) / np.sqrt(np.mean(x[seg] ** 2)), np.corrcoef(x[seg], y[seg])[0, 1])
    gain_db = {f: 20 * np.log10(g) for f, (g, _) in out.items()}
    assert abs(gain_db[10.0]) < 0.1 and abs(gain_db[2.0]) < 0.5  # pass-band flat
    assert -7.5 < gain_db[0.5] < -4.5 and -7.5 < gain_db[45.0] < -4.5  # about -6 dB at the cut-offs (zero-phase squares |H|)
    assert gain_db[0.1] < -25 and gain_db[110.0] < -40  # stop-band
    assert out[10.0][1] > 0.999  # no phase shift: output is in phase with input in the pass-band
    sos = sp_butter(4, [0.5, 45.0], btype="band", fs=sf, output="sos")
    _, h = freqz_sos(sos, worN=[0.5, 45.0], fs=sf)
    assert np.allclose(20 * np.log10(np.abs(h) ** 2), [-6.02, -6.02], atol=0.1)  # theory: |H|^2 = 1/2 at the corner, twice -3 dB


def _load_real():
    raw = mne.io.read_raw_edf(DATA, preload=True, verbose=False)
    new = [d["name"] for d in chan.normalize_all(raw.ch_names)]
    raw.rename_channels(dict(zip(raw.ch_names, new, strict=True)))
    return raw


def _blinks(raw, seed=5, per_min=14, amp_uv=120.0):
    """Blink waveform (a 0.35 s smooth bump) at random times; spread over channels by distance from the frontal poles."""
    sf = raw.info["sfreq"]
    n = raw.n_times
    rng = np.random.default_rng(seed)
    truth = np.zeros(n)
    bump = np.hanning(int(0.35 * sf))
    for s in rng.uniform(1, n / sf - 1, size=int(per_min * n / sf / 60)):
        i = int(s * sf)
        truth[i:i + len(bump)] += bump[: n - i]
    pos = chan.positions(raw.ch_names)
    fp = np.mean([pos[c] for c in ("Fp1", "Fp2")], axis=0)
    w = np.array([np.exp(-np.linalg.norm(np.asarray(pos[c]) - fp) / 0.06) if c in pos else 0.0 for c in raw.ch_names])
    return truth, w * amp_uv


def _amplitude(res, ch_idx, ref, sf):
    """Least-squares amplitude of the KNOWN injected waveform `ref` in a channel of a processed run (the waveform is passed through the
    same 0.5-45 Hz band-pass first, so a perfect pipeline would return the injected weight times the filter's gain)."""
    r = mne.filter.filter_data(ref.astype(float), sf, 0.5, 45.0, method="iir", iir_params={"order": 4, "ftype": "butter", "output": "sos"},
                               phase="zero", verbose=False)
    x = res["processed"].get_data()[ch_idx] * 1e6
    x, r = x - x.mean(), r - r.mean()
    return float(x @ r / (r @ r))


def _run(raw, params):
    names = list(raw.ch_names)
    meta = [{"name": c, "physical_min": -3200.0, "physical_max": 3200.0} for c in names]
    return pp.run_pipeline(raw.copy(), meta, params, set(), set(), [], [])


def _step(res, name):
    return next(s for s in res["steps"] if s["name"] == name)


@real
def test_fastica_removes_simulated_blinks_and_keeps_the_real_alpha_rhythm():
    raw = _load_real()
    truth, w = _blinks(raw)
    contaminated = raw.copy()
    contaminated._data += (w[:, None] * truth[None, :]) * 1e-6
    fp1 = raw.ch_names.index("Fp1")
    occ = [raw.ch_names.index(c) for c in ("O1", "O2", "Oz") if c in raw.ch_names]
    sf = raw.info["sfreq"]

    no_ica = {**P11, "ica": {**P11["ica"], "enabled": False}}
    base = _run(contaminated, no_ica)
    cleaned = _run(contaminated, P11)
    clean_ref = _run(raw, P11)  # same pipeline on the recording WITHOUT simulated blinks

    info = _step(cleaned, "ica_blink_removal")["summary"]
    assert info["applied"] and 1 <= len(info["removed"]) <= 3, info
    assert any(c["blink_like"] for c in info["components"])

    w_fp1 = float(w[fp1])
    amp_base, amp_clean = _amplitude(base, fp1, truth, sf), _amplitude(cleaned, fp1, truth, sf)
    assert amp_base > 0.5 * w_fp1, (amp_base, w_fp1)  # without ICA the injected blinks are clearly present at Fp1 ...
    assert amp_clean < 0.35 * amp_base, (amp_base, amp_clean)  # ... and with ICA at least 65 % of their amplitude is gone

    def band(res, ch_idx, lo, hi):
        d = res["processed"].get_data()[ch_idx]
        f, p = welch(d, fs=res["sfreq_hz"], nperseg=int(4 * res["sfreq_hz"]), axis=-1)
        m = (f >= lo) & (f <= hi)
        return p[..., m].sum(axis=-1)

    for lo, hi in ((8.0, 13.0), (13.0, 30.0)):
        keep = band(cleaned, occ, lo, hi).mean() / band(clean_ref, occ, lo, hi).mean()
        assert 0.8 < keep < 1.25, (lo, hi, keep)  # neural rhythms at the back of the head are not distorted
    frontal_excess = band(cleaned, fp1, 0.5, 4.0) / band(base, fp1, 0.5, 4.0)
    assert frontal_excess < 0.4  # most of the slow blink power at Fp1 is gone


@real
def test_ica_is_deterministic_and_leaves_a_clean_recording_mostly_alone():
    raw = _load_real()
    a, b = _run(raw, P11), _run(raw, P11)
    assert a["output_sha256"] == b["output_sha256"]
    info = _step(a, "ica_blink_removal")["summary"]
    assert info["applied"] and len(info["removed"]) <= 3
    assert any("ICA" in w or "blink" in w for w in a["warnings"])  # the user is told what happened


def test_ica_is_skipped_with_a_reason_when_it_cannot_work():
    rng = np.random.default_rng(0)
    sf = 250.0
    names = ["C3", "Cz", "C4", "P3", "Pz", "P4", "O1", "O2", "T7", "T8"]  # no frontal-pole channels
    raw = mne.io.RawArray(rng.normal(0, 10, (len(names), int(60 * sf))) * 1e-6, mne.create_info(names, sf, "eeg"), verbose=False)
    out = ICA.remove_blinks(raw, P11["ica"])
    assert out["applied"] is False and "frontal" in out["reason"]
    short = mne.io.RawArray(rng.normal(0, 10, (10, int(10 * sf))) * 1e-6, mne.create_info(["Fp1", "Fp2"] + names[:8], sf, "eeg"), verbose=False)
    assert ICA.remove_blinks(short, P11["ica"])["applied"] is False
    few = mne.io.RawArray(rng.normal(0, 10, (4, int(60 * sf))) * 1e-6, mne.create_info(["Fp1", "Fp2", "Cz", "Pz"], sf, "eeg"), verbose=False)
    assert "channels" in ICA.remove_blinks(few, P11["ica"])["reason"]


@real
def test_v110_records_every_step_and_the_decisions():
    res = _run(_load_real(), P11)
    names = [s["name"] for s in res["steps"]]
    assert names.index("bandpass") < names.index("ica_blink_removal") < names.index("reference")
    assert "Butterworth order 4" in _step(res, "bandpass")["params"]["design"]
    s = _step(res, "ica_blink_removal")["summary"]
    assert s["method"] == "fastica" and s["frontal_channels"] and len(s["components"]) == s["n_components"]


@real
def test_sustained_frontal_delta_is_not_mistaken_for_blinks():
    """Frontal delta can be real brain activity (e.g. in patients). A smooth 1.5 Hz oscillation at the frontal poles is slow and frontal
    but NOT spiky; its amplitude must survive ICA. (The recording itself has real frontal artefacts, so the ICA may still remove those.)"""
    raw = _load_real()
    _, w = _blinks(raw)
    sf = raw.info["sfreq"]
    delta = np.sin(2 * np.pi * 1.5 * np.arange(raw.n_times) / sf)
    contaminated = raw.copy()
    contaminated._data += (w[:, None] * delta[None, :]) * 0.5e-6
    fp1 = raw.ch_names.index("Fp1")
    no_ica = {**P11, "ica": {**P11["ica"], "enabled": False}}
    kept = _amplitude(_run(contaminated, P11), fp1, delta, sf) / _amplitude(_run(contaminated, no_ica), fp1, delta, sf)
    assert kept > 0.8, kept
