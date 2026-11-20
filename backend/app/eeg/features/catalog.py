"""Feature catalogue: every feature the platform computes, with a plain-language definition.

A test asserts that every feature name emitted by the extractor appears here with a non-empty description.
"""

from dataclasses import asdict, dataclass

FEATURES_VERSION = "1.0.0"

# Default bands (Hz). The top edge is capped at 0.45 x sampling rate; bands that start above the cap are skipped.
BANDS = {"delta": (1.0, 4.0), "theta": (4.0, 8.0), "alpha": (8.0, 13.0), "beta": (13.0, 30.0), "gamma_low": (30.0, 45.0)}
BAND_PLAIN = {"delta": "very slow waves (1–4 Hz)", "theta": "slow waves (4–8 Hz)", "alpha": "alpha rhythm (8–13 Hz)",
              "beta": "beta activity (13–30 Hz)", "gamma_low": "fast activity (30–45 Hz; can include muscle)"}


def band_edges(sfreq: float, bands: dict | None = None) -> dict[str, tuple[float, float]]:
    cap = min(45.0, 0.45 * sfreq)
    out = {}
    for name, (lo, hi) in (bands or BANDS).items():
        if lo < cap:
            out[name] = (lo, min(hi, cap))
    return out


@dataclass(frozen=True)
class FeatureDef:
    name: str
    domain: str  # time | frequency | aperiodic | wavelet | complexity | asymmetry
    unit: str
    description: str
    how: str
    caution: str = ""


_ = FeatureDef
CATALOG: dict[str, FeatureDef] = {f.name: f for f in [
    # ---- time domain ----
    _("variance", "time", "µV²", "How much the signal varies around its average. Larger means stronger activity overall.",
      "Variance of the voltage in each 2 s epoch.", "Depends on electrode contact and skull thickness; compare the same patient only."),
    _("rms", "time", "µV", "The typical size of the signal, in microvolts (root-mean-square).", "Square root of the mean squared voltage."),
    _("peak_to_peak", "time", "µV", "The largest swing of the signal inside an epoch (highest minus lowest value).",
      "max − min within the epoch.", "Dominated by artefacts such as blinks; useful for spotting them."),
    _("skewness", "time", "", "Whether the signal has more large upward or large downward swings. 0 means balanced.", "Third standardised moment."),
    _("kurtosis", "time", "", "How 'spiky' the signal is. 0 is bell-shaped; high values mean rare large spikes (excess kurtosis).",
      "Fourth standardised moment minus 3.", "Very sensitive to artefacts."),
    _("line_length", "time", "µV", "How much the trace moves up and down in total: a simple measure of activity and wiggliness.",
      "Sum of absolute differences between consecutive samples."),
    _("zero_crossing_rate", "time", "", "How often the signal crosses its average, as a fraction of samples. Faster rhythms cross more often.",
      "Fraction of sample pairs that change sign after removing the mean."),
    _("hjorth_activity", "time", "µV²", "Hjorth 'activity': the signal's power (same as variance).", "Variance of the signal (Hjorth, 1970)."),
    _("hjorth_mobility", "time", "", "Hjorth 'mobility': roughly the average frequency of the signal, relative to the sampling rate.",
      "√(variance of the derivative / variance of the signal).", "Depends on the sampling rate."),
    _("hjorth_complexity", "time", "", "Hjorth 'complexity': how much the shape differs from a pure sine wave (1 = pure sine).",
      "Mobility of the derivative divided by mobility of the signal.", "Depends on the sampling rate."),
    # ---- frequency domain ----
    _("abs_power", "frequency", "µV²", "How much signal strength lies in a frequency band, in microvolts squared.",
      "Spectrum (Hann-windowed 2 s epoch) summed over the band.", "Depends on electrode contact and skull thickness; compare the same patient only."),
    _("rel_power", "frequency", "", "The share (0–1) of the total 1–45 Hz power that falls in the band. Less affected by overall signal strength.",
      "Band power divided by total 1–45 Hz power."),
    _("ratio_theta_alpha", "frequency", "", "Slow (theta) activity compared with alpha activity. Higher means relatively more slow activity.",
      "Theta power ÷ alpha power."),
    _("ratio_delta_alpha", "frequency", "", "Very slow (delta) activity compared with alpha activity.", "Delta power ÷ alpha power."),
    _("ratio_slow_fast", "frequency", "", "All slow activity (delta+theta) compared with faster activity (alpha+beta).",
      "(delta + theta) ÷ (alpha + beta)."),
    _("peak_alpha_freq", "frequency", "Hz", "The frequency of the strongest rhythm between 7 and 13 Hz.",
      "Frequency of the highest spectral value in 7–13 Hz.", "Resolution is 0.5 Hz; only meaningful when there is a clear alpha peak."),
    _("spectral_edge_90", "frequency", "Hz", "The frequency below which 90 % of the 1–45 Hz power lies. Lower means the signal is slower.",
      "90th percentile of the cumulative spectrum."),
    _("spectral_centroid", "frequency", "Hz", "The 'centre of mass' of the spectrum: the power-weighted average frequency.",
      "Sum of frequency × power ÷ sum of power (1–45 Hz)."),
    _("spectral_entropy", "frequency", "", "How spread out the power is across frequencies (0–1). Near 0: one dominant rhythm; near 1: flat, noise-like.",
      "Normalised Shannon entropy of the spectrum (1–45 Hz)."),
    # ---- aperiodic ----
    _("aperiodic_exponent", "aperiodic", "", "The steepness of the background 1/f slope of the spectrum, after separating out rhythm peaks. Steeper = more slow-dominated background.",
      "Fixed-mode aperiodic fit of the recording-average spectrum over 1–40 Hz (specparam).",
      "Research feature. Fit quality (R²) is stored; poor fits are flagged. specparam version is a release candidate."),
    _("aperiodic_offset", "aperiodic", "log10 µV²/Hz", "The overall height of the background spectrum.", "Intercept of the aperiodic fit."),
    _("aperiodic_r2", "aperiodic", "", "How well the aperiodic model fits the spectrum (1 = perfect). Low values mean the exponent is unreliable.",
      "R² of the full spectral model."),
    _("aperiodic_error", "aperiodic", "log10 µV²/Hz", "The average size of the misfit between model and spectrum.", "Mean absolute error of the model fit."),
    # ---- wavelet ----
    _("wavelet_rel_energy", "wavelet", "", "The share (0–1) of signal energy in each wavelet scale (each covers a band of frequencies; see the band label).",
      "Discrete wavelet transform (Daubechies-4); energy of each detail level ÷ total energy.",
      "Neighbouring scales overlap (a pure rhythm spreads over 2–3 scales); band edges depend on the sampling rate."),
    _("wavelet_entropy", "wavelet", "", "How evenly energy is spread across wavelet scales (0–1). Low: energy concentrated in one band.",
      "Normalised Shannon entropy of the wavelet energy shares."),
    # ---- complexity ----
    _("permutation_entropy", "complexity", "", "How unpredictable the ordering of successive values is (0–1). Near 0: very regular; near 1: random-like.",
      "Normalised permutation entropy, order 3, delay 1 (Bandt & Pompe, 2002)."),
    _("sample_entropy", "complexity", "", "How rarely a short pattern in the signal repeats later on. Higher means less regular.",
      "Sample entropy, m = 2, tolerance 0.2 × SD (Richman & Moorman, 2000).",
      "Computed on a subset of epochs to limit computing time; depends on the sampling rate."),
    _("lempel_ziv", "complexity", "", "How many distinct patterns are needed to describe the signal: a measure of its complexity.",
      "Normalised Lempel-Ziv complexity of the signal binarised at its median.", "Computed on a subset of epochs."),
    # ---- asymmetry ----
    _("asymmetry_abs_power", "asymmetry", "", "Left–right difference in band power for matching electrodes, from −1 to 1. Positive: left is stronger.",
      "(left − right) ÷ (left + right) of the band power.", "Skull defects or craniectomy can create asymmetry that is not neurological."),
]}


def catalog_rows() -> list[dict]:
    return [asdict(f) for f in CATALOG.values()]
