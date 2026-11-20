"""Sedation features behave as their definitions say (synthetic signals = test fixtures only)."""

import numpy as np
import pytest

from nbml import sed_features as F

FS = 250.0
N = int(10 * FS)
T = np.arange(N) / FS


def epochs(sig_fn, n_ep=3, seed=0):
    rng = np.random.default_rng(seed)
    return np.stack([np.stack([sig_fn(rng, i) for i in range(19)]) for _ in range(n_ep)])


def feat(X):
    A, names = F.extract(X, FS)
    return {k: A[:, i] for i, k in enumerate(names)}


def test_shape_names_and_determinism():
    X = epochs(lambda rng, i: rng.normal(0, 10, N))
    A, names = F.extract(X, FS)
    assert A.shape == (3, len(names)) and len(set(names)) == len(names) and np.isfinite(A).all()
    assert np.array_equal(A, F.extract(X, FS)[0])
    assert {"global:rel_alpha", "occipital:rel_alpha", "global:wpli_alpha", "frontal_to_posterior:wpli_alpha"} <= set(names)
    assert all(F.plain_name(n) for n in names)


def test_relative_band_powers_sum_to_one():
    f = feat(epochs(lambda rng, i: rng.normal(0, 10, N)))
    total = sum(f[f"global:rel_{b}"] for b in F.BANDS)
    assert np.allclose(total, 1.0, atol=1e-9)


def test_pure_alpha_sine_is_all_alpha_and_predictable():
    f = feat(epochs(lambda rng, i: 20 * np.sin(2 * np.pi * 10 * T + rng.uniform(0, 6)) + rng.normal(0, 0.2, N)))
    assert (f["global:rel_alpha"] > 0.95).all() and (f["global:rel_delta"] < 0.02).all()
    assert (f["global:spectral_entropy"] < 0.3).all()  # power concentrated at one frequency
    assert (f["global:perm_entropy"] < 0.75).all()  # a sine is far more predictable than noise


def test_white_noise_has_high_entropy_and_flat_slope_pink_noise_has_minus_one():
    white = feat(epochs(lambda rng, i: rng.normal(0, 10, N)))
    assert (white["global:perm_entropy"] > 0.97).all() and (white["global:spectral_entropy"] > 0.95).all()
    assert (np.abs(white["global:slope"]) < 0.4).all()

    def pink(rng, i):
        spec = np.fft.rfft(rng.normal(size=N))
        fr = np.fft.rfftfreq(N, 1 / FS)
        spec[1:] /= np.sqrt(fr[1:])  # power ~ 1/f
        return 10 * np.fft.irfft(spec, N)

    p = feat(epochs(pink))
    assert (np.abs(p["global:slope"] + 1.0) < 0.35).all(), p["global:slope"]


def test_hjorth_mobility_of_a_sine_matches_theory():
    f = feat(epochs(lambda rng, i: np.sin(2 * np.pi * 10 * T)))
    expected = 2 * np.sin(np.pi * 10 / FS)  # discrete first difference of a sine
    assert np.allclose(f["global:hjorth_mobility"], expected, rtol=0.02)
    assert np.allclose(f["global:hjorth_complexity"], 1.0, atol=0.05)  # a pure sine has complexity 1


def test_log_power_scales_with_amplitude():
    a = feat(epochs(lambda rng, i: rng.normal(0, 5, N)))["global:log_power"].mean()
    b = feat(epochs(lambda rng, i: rng.normal(0, 50, N)))["global:log_power"].mean()
    assert b - a == pytest.approx(2.0, abs=0.1)  # 10x amplitude = 100x power = +2 in log10


def test_wpli_ignores_zero_lag_coupling_and_detects_consistent_lag():
    """wPLI is built to ignore instantaneous coupling (volume conduction) and respond to a consistent phase lag."""
    def make(lag_samples):
        def fn(rng, i):
            return None
        out = []
        for e in range(3):
            rng = np.random.default_rng(e)
            base = np.zeros(N)
            for fq in rng.uniform(8.5, 12.5, 6):  # broadband alpha, random phases: coupling is only through the lag
                base += np.sin(2 * np.pi * fq * T + rng.uniform(0, 6.28))
            chans = [np.roll(base, lag_samples * (1 if i < 5 else 0)) + rng.normal(0, 0.3, N) for i in range(19)]
            out.append(np.stack(chans))
        return np.stack(out)

    zero = feat(make(0))
    lagged = feat(make(6))  # 24 ms lag on some channels (frontal 0-4 are lagged relative to the rest)
    ind = feat(epochs(lambda rng, i: rng.normal(0, 10, N)))
    assert (lagged["frontal_to_posterior:wpli_alpha"] > 0.6).all(), lagged["frontal_to_posterior:wpli_alpha"]
    assert (zero["frontal_to_posterior:wpli_alpha"] < 0.25).all(), zero["frontal_to_posterior:wpli_alpha"]
    assert (ind["global:wpli_alpha"] < 0.3).all()


def test_frontal_alpha_anteriorisation_feature_has_the_right_sign():
    def fn(rng, i):  # alpha only over the front (channel index 0-6 are the frontal channels)
        amp = 20 if i < 7 else 0.2
        return amp * np.sin(2 * np.pi * 10 * T + rng.uniform(0, 6)) + rng.normal(0, 1, N)

    f = feat(epochs(fn))
    assert (f["frontal_minus_occipital:rel_alpha"] > 0.3).all()
    assert (f["frontal:rel_alpha"] > f["occipital:rel_alpha"]).all()
