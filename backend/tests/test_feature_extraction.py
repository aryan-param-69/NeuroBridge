"""Run-level extraction: aggregation, masking, asymmetry, uncertainty, determinism, catalogue coverage."""

import time

import numpy as np
import pytest

from app.eeg.features import extract as E
from app.eeg.features.catalog import CATALOG, catalog_rows
from app.eeg.features.regions import region_of

FS = 250.0
NAMES = ["Fp1", "Fp2", "F3", "F4", "C3", "C4", "P3", "P4", "O1", "O2", "Cz", "Fz"]


def make(seconds=60, seed=0, left=20.0, right=10.0):
    """Left electrodes carry a 20 uV 10 Hz rhythm, right ones 10 uV, plus noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * FS)) / FS
    X = np.empty((len(NAMES), t.size))
    for i, n in enumerate(NAMES):
        amp = left if region_of(n).endswith("_L") else right if region_of(n).endswith("_R") else 15.0
        X[i] = amp * np.sin(2 * np.pi * 10 * t + rng.uniform(0, 6.28)) + rng.normal(0, 4, t.size)
    return X


def run(X, masked=(), onsets=None, **kw):
    on = np.arange(0, X.shape[1] / FS - 2 + 1e-9, 1.0) if onsets is None else onsets
    return E.compute_run_features(X, FS, NAMES, on, 2.0, set(masked), **kw)


def row(res, level, name, feature, band=None):
    return next(r for r in res["rows"] if (r["level"], r["name"], r["feature"], r["band"]) == (level, name, feature, band))


@pytest.fixture(scope="module")
def res():
    return run(make())


def test_every_emitted_feature_has_a_plain_language_catalogue_entry(res):
    emitted = {r["feature"] for r in res["rows"]}
    assert emitted and emitted <= set(CATALOG)
    for f in emitted:
        d = CATALOG[f]
        assert d.description.strip() and d.how.strip() and d.domain, f
        assert len(d.description) > 25  # a sentence, not a label
    assert {r["name"] for r in catalog_rows()} == set(CATALOG)
    domains = {CATALOG[f].domain for f in emitted}
    assert {"time", "frequency", "aperiodic", "wavelet", "complexity", "asymmetry"} <= domains


def test_rows_exist_at_channel_region_and_global_levels_and_regions_average_their_channels(res):
    levels = {r["level"] for r in res["rows"]}
    assert {"channel", "region", "global", "pair"} <= levels
    a = row(res, "channel", "Fp1", "abs_power", "alpha")["mean"]
    b = row(res, "channel", "F3", "abs_power", "alpha")["mean"]
    assert row(res, "region", "frontal_L", "abs_power", "alpha")["mean"] == pytest.approx((a + b) / 2, rel=0.01)
    allm = np.mean([row(res, "channel", n, "abs_power", "alpha")["mean"] for n in NAMES])
    assert row(res, "global", "global", "abs_power", "alpha")["mean"] == pytest.approx(allm, rel=0.01)


def test_relative_powers_of_all_bands_add_up_to_one(res):
    for n in NAMES:
        total = sum(row(res, "channel", n, "rel_power", b)["mean"] for b in ("delta", "theta", "alpha", "beta", "gamma_low"))
        assert total == pytest.approx(1.0, abs=1e-4)


def test_known_left_right_alpha_asymmetry(res):
    # power ~ amplitude^2: left 20 uV vs right 10 uV -> (400-100)/(400+100) = 0.6
    r = row(res, "pair", "F3-F4", "asymmetry_abs_power", "alpha")
    assert r["mean"] == pytest.approx(0.6, abs=0.04) and r["ci_low"] < r["mean"] < r["ci_high"]
    assert row(res, "global", "mean of pairs", "asymmetry_abs_power", "alpha")["mean"] > 0.5


def test_psd_summary_peaks_at_10_hz_and_has_ordered_intervals(res):
    psd = res["psd"]
    f = np.array(psd["freqs_hz"])
    ch = np.array(psd["channels"]["O1"], dtype=float)
    assert f[np.argmax(ch)] == pytest.approx(10.0, abs=0.5)
    g = psd["groups"]["global"]
    lo, mean, hi = (np.array(g[k], dtype=float) for k in ("ci_low", "mean", "ci_high"))
    assert np.all(lo <= mean + 1e-9) and np.all(mean <= hi + 1e-9)


def test_masked_channel_values_are_excluded_not_averaged_in():
    X = make()
    clean = run(X)
    X2 = X.copy()
    X2[0, int(10 * FS): int(12 * FS)] += 800 * np.sin(2 * np.pi * 3 * np.arange(int(2 * FS)) / FS)  # gross artefact in epoch 10 of Fp1
    dirty = run(X2)
    masked = run(X2, masked={(9, 0), (10, 0), (11, 0)})  # a 2 s artefact touches three overlapping epochs
    v = lambda r: row(r, "channel", "Fp1", "variance")  # noqa: E731
    assert v(dirty)["mean"] > 1.5 * v(clean)["mean"]  # unmasked artefact inflates the mean ...
    assert v(masked)["mean"] == pytest.approx(v(clean)["mean"], rel=0.05)  # ... masking removes it
    assert v(masked)["n_epochs"] == v(clean)["n_epochs"] - 3
    assert row(masked, "channel", "Fp2", "variance")["n_epochs"] == v(clean)["n_epochs"]  # other channels unaffected


def test_more_epochs_give_narrower_intervals_and_few_epochs_give_none():
    X = make(120)
    w = lambda r: (lambda x: x["ci_high"] - x["ci_low"])(row(r, "channel", "C3", "variance"))  # noqa: E731
    short = run(X, onsets=np.arange(0, 30.0, 1.0))
    long = run(X)
    assert w(long) < 0.75 * w(short)
    tiny = run(X, onsets=np.array([0.0, 1.0]))
    r = row(tiny, "channel", "C3", "variance")
    assert r["mean"] is not None and r["ci_low"] is None and r["ci_high"] is None


def test_block_bootstrap_interval_has_close_to_nominal_coverage():
    rng = np.random.default_rng(0)
    hits, n_sim = 0, 300
    for s in range(n_sim):
        x = rng.normal(0, 1, (100, 1))  # true mean 0
        m, _, _, lo, hi, _ = E.block_bootstrap(x, block=5, n_boot=200, seed=s)
        hits += lo[0] <= 0 <= hi[0]
    assert 0.88 <= hits / n_sim <= 0.99, hits / n_sim


def test_block_bootstrap_widens_intervals_for_autocorrelated_epochs():
    rng = np.random.default_rng(1)
    z = rng.normal(size=(400, 1))
    ar = np.zeros_like(z)
    for i in range(1, len(z)):  # strongly autocorrelated series, like overlapping epochs
        ar[i] = 0.8 * ar[i - 1] + z[i]
    ar = ar / ar.std()
    w = lambda x, b: (lambda r: r[4][0] - r[3][0])(E.block_bootstrap(x, b, 400, 0))  # noqa: E731
    assert w(ar, 10) > 1.3 * w(ar, 1)  # blocks respect the dependence; independent resampling would be too narrow


def test_extraction_is_deterministic_and_sensitive_to_the_data():
    X = make()
    a, b = run(X), run(X)
    assert a["features_hash"] == b["features_hash"]
    assert [r for r in a["rows"]] == [r for r in b["rows"]]
    assert run(make(seed=5))["features_hash"] != a["features_hash"]


def test_errors_are_clear():
    X = make()
    with pytest.raises(E.FeatureError, match="no clean epochs"):
        run(X, onsets=np.array([]))
    with pytest.raises(E.FeatureError, match="does not match"):
        run(X, onsets=np.array([0.0, 999.0]))


def test_extraction_speed_is_reasonable():
    X = make(120)
    t0 = time.perf_counter()
    run(X)
    assert time.perf_counter() - t0 < 60  # 12 channels x 2 min incl. 500 bootstrap resamples and aperiodic fits
