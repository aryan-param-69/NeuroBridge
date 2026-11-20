"""Feature primitives against signals whose correct answers are known (synthetic = test fixtures only)."""

import math

import numpy as np
import pytest

from app.eeg.features import compute as F
from app.eeg.features import regions
from app.eeg.features.catalog import band_edges

FS, L = 250.0, 500  # 2 s epochs


def sine(freq=10.0, amp=20.0, n=6, ch=2, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(L) / FS
    return np.stack([np.stack([amp * np.sin(2 * np.pi * freq * t + rng.uniform(0, 6.28)) for _ in range(ch)]) for _ in range(n)])


def noise(n=200, ch=2, seed=1, sd=10.0):
    return np.random.default_rng(seed).normal(0, sd, (n, ch, L))


def pink(n=120, ch=2, seed=2, exponent=1.0, peak=0.0):
    """1/f^exponent power spectrum, built by shaping white noise in the frequency domain (optionally + 10 Hz rhythm)."""
    rng = np.random.default_rng(seed)
    N = L * n
    spec = np.fft.rfft(rng.normal(size=(ch, N)), axis=-1)
    f = np.fft.rfftfreq(N, 1 / FS)
    f[0] = f[1]
    x = np.fft.irfft(spec / f ** (exponent / 2), n=N, axis=-1) * 1000
    t = np.arange(N) / FS
    x = x + peak * np.sin(2 * np.pi * 10 * t)
    return x.reshape(ch, n, L).transpose(1, 0, 2)


# ---- time domain --------------------------------------------------------------------------------------------

def test_time_features_of_a_sine_match_theory():
    A, f = 20.0, 10.0
    r = F.time_features(sine(f, A))
    m = lambda k: float(np.mean(r[k]))  # noqa: E731
    assert m("variance") == pytest.approx(A**2 / 2, rel=0.01) and m("hjorth_activity") == pytest.approx(A**2 / 2, rel=0.01)
    assert m("rms") == pytest.approx(A / math.sqrt(2), rel=0.01) and m("peak_to_peak") == pytest.approx(2 * A, rel=0.01)
    assert m("kurtosis") == pytest.approx(-1.5, abs=0.05) and abs(m("skewness")) < 0.05
    assert m("zero_crossing_rate") == pytest.approx(2 * f / FS, abs=0.01)
    assert m("hjorth_mobility") == pytest.approx(2 * math.sin(math.pi * f / FS), abs=0.01)  # discrete-time value
    assert m("hjorth_complexity") == pytest.approx(1.0, abs=0.05)  # a pure sine has complexity 1


def test_line_length_equals_the_direct_definition():
    X = noise(5, 3)
    ll = F.time_features(X)["line_length"]
    assert np.allclose(ll, np.abs(np.diff(X - X.mean(-1, keepdims=True), axis=-1)).sum(-1))


# ---- spectral ------------------------------------------------------------------------------------------------

def test_psd_integrates_to_the_variance_parseval():
    X = noise(50, 2)
    f, p = F.epoch_psd(X, FS)
    total = p.sum(-1) * (f[1] - f[0])
    assert np.mean(total) == pytest.approx(np.mean(X.var(-1)), rel=0.03)


def test_a_10_hz_sine_has_its_power_and_peak_in_alpha():
    A = 20.0
    f, p = F.epoch_psd(sine(10.0, A), FS)
    s = F.spectral_features(f, p, FS)
    assert float(np.mean(s[("abs_power", "alpha")])) == pytest.approx(A**2 / 2, rel=0.05)
    assert float(np.min(s[("rel_power", "alpha")])) > 0.97
    assert np.all(s[("peak_alpha_freq", None)] == 10.0)
    assert float(np.mean(s[("spectral_centroid", None)])) == pytest.approx(10.0, abs=0.3)
    assert 9.5 <= float(np.mean(s[("spectral_edge_90", None)])) <= 11.0
    assert float(np.mean(s[("spectral_entropy", None)])) < 0.2


def test_white_noise_has_a_flat_spectrum_and_relative_powers_partition_to_one():
    f, p = F.epoch_psd(noise(300), FS)
    s = F.spectral_features(f, p, FS)
    bands = band_edges(FS)
    rel = {b: float(np.mean(s[("rel_power", b)])) for b in bands}
    assert sum(rel.values()) == pytest.approx(1.0, abs=1e-9)  # the bands exactly partition 1-45 Hz
    for b, (lo, hi) in bands.items():
        assert rel[b] == pytest.approx((hi - lo) / 44.0, abs=0.03), b  # flat spectrum: share proportional to bandwidth
    assert float(np.mean(s[("spectral_entropy", None)])) > 0.9


def test_a_slowing_shift_changes_the_ratios_in_the_right_direction():
    X = sine(10.0, 20.0) + sine(5.0, 10.0, seed=3)  # alpha + weaker theta
    X2 = sine(10.0, 10.0) + sine(5.0, 20.0, seed=3)  # theta now stronger
    r1 = F.spectral_features(*F.epoch_psd(X, FS), FS)
    r2 = F.spectral_features(*F.epoch_psd(X2, FS), FS)
    assert np.mean(r2[("ratio_theta_alpha", None)]) > 4 * np.mean(r1[("ratio_theta_alpha", None)])
    assert np.mean(r2[("ratio_slow_fast", None)]) > np.mean(r1[("ratio_slow_fast", None)])


@pytest.mark.parametrize("fs,expected", [
    (250.0, {"delta": (1, 4), "theta": (4, 8), "alpha": (8, 13), "beta": (13, 30), "gamma_low": (30, 45)}),
    (160.0, {"delta": (1, 4), "theta": (4, 8), "alpha": (8, 13), "beta": (13, 30), "gamma_low": (30, 45)}),
    (80.0, {"delta": (1, 4), "theta": (4, 8), "alpha": (8, 13), "beta": (13, 30), "gamma_low": (30, 36)}),
    (60.0, {"delta": (1, 4), "theta": (4, 8), "alpha": (8, 13), "beta": (13, 27)}),
])
def test_bands_are_capped_at_the_usable_frequency_range(fs, expected):
    assert band_edges(fs) == {k: (float(a), float(b)) for k, (a, b) in expected.items()}


# ---- aperiodic -----------------------------------------------------------------------------------------------

def _mean_psd(X):
    f, p = F.epoch_psd(X, FS)
    return f, p.mean(axis=0)[0]


def test_aperiodic_exponent_of_white_and_pink_noise():
    f, p = _mean_psd(noise(200))
    white = F.aperiodic_fit(f, p)
    assert abs(white["aperiodic_exponent"]) < 0.3  # (R^2 is ~0 for a flat spectrum: nothing to explain, so it is not checked)
    f, p = _mean_psd(pink(exponent=1.0))
    fit = F.aperiodic_fit(f, p)
    assert fit["aperiodic_exponent"] == pytest.approx(1.0, abs=0.25) and fit["aperiodic_r2"] > 0.9
    f, p = _mean_psd(pink(exponent=2.0))
    assert F.aperiodic_fit(f, p)["aperiodic_exponent"] == pytest.approx(2.0, abs=0.3)


def test_aperiodic_fit_separates_a_rhythm_peak_from_the_background():
    f, p = _mean_psd(pink(exponent=1.0, peak=40.0))
    fit = F.aperiodic_fit(f, p)
    assert fit["aperiodic_exponent"] == pytest.approx(1.0, abs=0.3)  # the 10 Hz peak does not bend the slope


def test_aperiodic_fit_failure_returns_nan_not_an_exception():
    out = F.aperiodic_fit(np.array([0.5, 1.0, 1.5]), np.array([1.0, 1.0, 1.0]))
    assert set(out) == {"aperiodic_exponent", "aperiodic_offset", "aperiodic_r2", "aperiodic_error"}


# ---- wavelet --------------------------------------------------------------------------------------------------

def test_wavelet_energy_of_a_10_hz_sine_sits_in_the_matching_scale():
    w = F.wavelet_features(sine(10.0, 20.0), FS)
    shares = {b: float(np.mean(v)) for (name, b), v in w.items() if name == "wavelet_rel_energy"}
    assert sum(shares.values()) == pytest.approx(1.0, abs=1e-9)
    alpha_scale = next(k for k in shares if k.startswith("D4_"))  # D4 covers 7.8-15.6 Hz at 250 Hz
    # db4 scales overlap, so a pure rhythm spreads over neighbours, but the matching scale must clearly dominate.
    assert max(shares, key=shares.get) == alpha_scale and shares[alpha_scale] > 0.45
    assert float(np.mean(w[("wavelet_entropy", None)])) < 0.7


def test_wavelet_energy_of_white_noise_is_spread_by_bandwidth():
    w = F.wavelet_features(noise(150), FS)
    d1 = next(float(np.mean(v)) for (n, b), v in w.items() if n == "wavelet_rel_energy" and b.startswith("D1_"))
    assert d1 == pytest.approx(0.5, abs=0.05)  # D1 is the top half of the spectrum
    assert float(np.mean(w[("wavelet_entropy", None)])) > 0.5


# ---- complexity -----------------------------------------------------------------------------------------------

def test_permutation_entropy_extremes():
    ramp = np.tile(np.arange(L, dtype=float), (3, 2, 1))
    assert np.all(F.permutation_entropy(ramp) == 0.0)  # monotone: only one pattern ever occurs
    assert float(np.mean(F.permutation_entropy(noise(50)))) > 0.97  # all 6 patterns equally likely
    assert float(np.mean(F.permutation_entropy(sine(10.0)))) < 0.6


def test_lempel_ziv_and_sample_entropy_separate_regular_from_random():
    reg, rnd = sine(10.0, n=1, ch=1)[0, 0], noise(1, 1)[0, 0]
    assert F.lempel_ziv(reg) < 0.4 < 0.9 < F.lempel_ziv(rnd) < 1.2
    assert F.sample_entropy(reg) < 0.5 and 1.9 < F.sample_entropy(rnd) < 2.6
    assert math.isnan(F.sample_entropy(np.zeros(L)))  # undefined for a flat signal, reported as NaN


def test_complexity_subset_leaves_the_other_epochs_nan():
    X = noise(10, 2)
    r = F.complexity_features(X, np.array([0, 5]))
    assert np.isfinite(r[("permutation_entropy", None)]).all()
    assert np.isfinite(r[("lempel_ziv", None)][[0, 5]]).all() and np.isnan(r[("lempel_ziv", None)][[1, 2]]).all()


# ---- regions ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,region", [("F3", "frontal_L"), ("Fp2", "frontal_R"), ("Fz", "frontal_M"), ("AF7", "frontal_L"),
                                         ("FC4", "central_R"), ("Cz", "central_M"), ("CP5", "central_L"), ("T8", "temporal_R"),
                                         ("FT7", "temporal_L"), ("T10", "temporal_R"), ("P3", "parietal_L"), ("PO8", "parietal_R"),
                                         ("Oz", "occipital_M"), ("O1", "occipital_L"), ("Iz", "occipital_M"), ("Zz9", None), ("EOG", None)])
def test_regions(name, region):
    assert regions.region_of(name) == region


def test_homologous_pairs():
    names = ["Fp1", "Fp2", "F3", "F4", "Cz", "T7", "T8", "C3", "P7", "FC1", "FC2", "O1"]
    pairs = {p[2] for p in regions.homologous_pairs(names)}
    assert pairs == {"Fp1-Fp2", "F3-F4", "T7-T8", "P7-P8"} - {"P7-P8"} | {"FC1-FC2"}  # O1 and C3 and Cz have no partner here
    i, j, label = next(p for p in regions.homologous_pairs(names) if p[2] == "F3-F4")
    assert names[i] == "F3" and names[j] == "F4"


def _sampen_bruteforce(x, m=2, r_frac=0.2):
    """Reference implementation: full distance matrix (slow but obviously correct)."""
    x = np.asarray(x, float)
    r, N = r_frac * x.std(), len(x)

    def matches(mm):
        t = np.lib.stride_tricks.sliding_window_view(x, mm)[: N - m]
        d = np.abs(t[:, None, :] - t[None, :, :]).max(axis=-1)
        return int((d <= r).sum() - len(t))

    b, a = matches(m), matches(m + 1)
    return -math.log(a / b)


def test_fast_sample_entropy_equals_the_brute_force_definition():
    rng = np.random.default_rng(4)
    for x in (rng.normal(size=300), np.cumsum(rng.normal(size=300)), sine(7.0, n=1, ch=1)[0, 0] + rng.normal(0, 3, L)):
        assert F.sample_entropy(x) == pytest.approx(_sampen_bruteforce(x), abs=1e-9)
