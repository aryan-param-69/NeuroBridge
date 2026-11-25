# 03 — EDF-to-Analysis Workflow & Longitudinal Comparison Methodology

Covers deliverables **4 (EDF-to-analysis workflow)** and **5 (Longitudinal comparison methodology)**.

> **Beginner note.** EEG records tiny voltage changes (microvolts) from the scalp. Before measuring anything, we must (1) check the recording is usable, (2) remove noise that isn't brain activity, (3) cut the signal into short windows ("epochs"), and (4) compute numbers ("features") from each window. Comparing two recordings is only fair if both went through *identical* steps under *similar* conditions.

---

## Part 1 — EDF-to-analysis workflow

### 1.1 Pipeline overview

```
 UPLOAD ─► INGEST ─► VALIDATE ─► STANDARDISE ─► QUALITY ─► PREPROCESS ─► EPOCH ─► FEATURES ─► VISUALS ─► QC SIGN-OFF
   │         │          │            │             │            │           │          │           │           │
 chunked   SHA-256    MNE parse   channel names  score &     filter,     fixed or   time/freq/  PSD, spectro  enters trends
 upload    de-ident   checks      10-20 mapping  bad chans   reref, ICA  events     wavelet/    topomaps     & comparisons
                                                                                     complexity
```
Each box = one recorded `run_step` with its parameters. Steps 4–8 run as a background Celery job; the UI shows progress.

### 1.2 Step details

**Step 1 — Upload.** Chunked, resumable upload (tus protocol or multipart S3). Client sends expected SHA-256; server verifies.

**Step 2 — Ingest.**
- Compute SHA-256; reject exact duplicates for the same patient.
- Store original (encrypted, immutable).
- Read EDF header fields *patient identification* and *recording identification*; compare with registered patient (name/DOB) → `match / mismatch / blank`. Mismatch = blocking warning ("this file may belong to another patient").
- Write a de-identified copy (header identity fields blanked; start date optionally shifted for research exports).

**Step 3 — Validate** (`mne.io.read_raw_edf(path, preload=False)`):
| Check | Failure handling |
|---|---|
| File parses | Reject with message |
| EDF+D (discontinuous) | Warn; segments treated separately |
| Mixed sampling rates across channels | Warn; non-EEG channels resampled or excluded |
| Duration ≥ minimum (e.g., 60 s) | Warn below 2 min for resting analysis |
| At least N EEG channels (configurable, default 8) | Warn |
| Physical range plausible (e.g., ±3200 µV typical) | Warn |
| Annotations parse | Store as `edf_annotation` segments |
| Header start time plausible (not 1985 default) | Ask user to confirm date |

**Step 4 — Standardise channels.**
- Normalise names: strip prefixes/suffixes (`EEG Fp1-REF` → `Fp1`, `EEG T3-LE` → `T7` with legacy-name mapping T3→T7, T4→T8, T5→P7, T6→P8), case-fix (`FP1`→`Fp1`).
- Assign types (EEG/EOG/ECG/EMG/stim) by name rules; user can correct.
- Attach `standard_1020` (or `standard_1005`) montage; channels without positions are flagged (still usable for spectral features but excluded from topomaps and interpolation).
- Record the original reference (from header/prefilter text or user input).

**Step 5 — Quality assessment** (on lightly filtered copy, 1–45 Hz): metrics in doc 02 §2.1 → `quality_reports`. Automatically proposes bad channels and artifact segments; user confirms.

**Step 6 — Preprocessing (pipeline `resting_v1.0.0` defaults).**
| # | Operation | Default | Rationale |
|---|---|---|---|
| 6.1 | Pick EEG (+EOG/ECG kept for ICA reference) | | |
| 6.2 | Resample (if sfreq > 500 Hz) | 250 Hz, with anti-alias filter | Analysis bands ≤ 45 Hz; reduces compute. Done *after* filtering per MNE guidance. |
| 6.3 | Notch | 50 or 60 Hz + harmonics (site setting) | Mains noise. |
| 6.4 | Band-pass | 0.5–45 Hz, zero-phase FIR (MNE default design) | Removes drift and high-frequency noise; zero-phase avoids timing distortion. |
| 6.5 | Bad channels | Confirmed list from Step 5 | |
| 6.6 | Interpolate bad channels | Spherical spline, only if positions known **and** bad ≤ 20 % and not clustered | Too many interpolated channels creates fake spatial smoothness. Interpolated channels are marked in all outputs. |
| 6.7 | Re-reference | Average reference (default) | Required for topomaps & comparability; original reference preserved in metadata. |
| 6.8 | ICA (optional, S) | Picard/extended-Infomax on 1 Hz high-passed copy; n_components = rank-aware; ICLabel suggestions | Removes eye blinks, ECG. Human confirms; requires ≥ ~20 channels and enough clean data to be reliable. |
| 6.9 | Annotated artifact segments | Marked `BAD_` → excluded from epoching | |

**Step 7 — Epoching.**
- Resting: fixed-length 2 s epochs, 50 % overlap (gives 0.5 Hz frequency resolution with Welch on 2 s windows).
- Task (motor imagery / command following): event-locked from EDF annotations, e.g., −1 to +4 s; baseline as per paradigm.
- Epoch rejection: `autoreject` (local thresholds) or fallback peak-to-peak threshold (default 150 µV after filtering) — method recorded.
- Minimum clean epochs for reliable spectral estimates: target ≥ 60 epochs (~1 min clean); fewer → "low-confidence" flag on every feature.

**Step 8 — Feature extraction** (per epoch per channel, then summarised).

| Domain | Feature | Method / notes |
|---|---|---|
| **Time** | Mean, variance, RMS, peak-to-peak, skewness, kurtosis, line length, zero-crossing rate | NumPy/SciPy |
| | Hjorth activity, mobility, complexity | Hjorth 1970 |
| **Frequency** | PSD | Welch (2 s Hann, 50 % overlap) or multitaper (MNE) |
| | Absolute band power (µV²) | Integrated PSD per band |
| | Relative band power | Band / total power 1–45 Hz |
| | Bands (default, configurable) | delta 1–4, theta 4–8, alpha 8–13, beta 13–30, gamma-low 30–45 Hz |
| | Ratios | theta/alpha, delta/alpha, (delta+theta)/(alpha+beta) |
| | Peak alpha frequency; spectral edge frequency (SEF90); spectral centroid | |
| | Spectral entropy | antropy |
| | Aperiodic exponent & offset; periodic peak parameters | specparam (FOOOF), fit 1–40 Hz; fit quality (R²) stored and low-quality fits flagged |
| | Inter-hemispheric asymmetry (e.g., (L−R)/(L+R) per homologous pair) | Useful in focal injury |
| **Wavelet** | DWT (db4) sub-band energies and relative energies; wavelet entropy | PyWavelets; levels chosen from sfreq so sub-bands approximate clinical bands |
| | CWT (Morlet) time-frequency for spectrograms | MNE `tfr_morlet` |
| **Complexity** | Permutation entropy, sample entropy, Lempel-Ziv complexity | antropy; parameters fixed per pipeline version |
| **Connectivity** (phase 2, C) | Coherence, debiased wPLI, wSMI | wSMI (King et al., 2013) is a published DoC marker; computationally heavy, add later. |

Summaries stored: mean, median, SD, bootstrap 95 % CI across epochs. Regional aggregates: frontal, central, temporal, parietal, occipital × left/right/midline, and global.

**Step 9 — Visualisation artefacts.** Downsampled trace tiles (min-max decimation per pixel column), PSD arrays, spectrogram matrices, topomap SVGs per band (MNE `plot_topomap`), all cached in object storage keyed by run.

**Step 10 — QC sign-off.** Clinician/technologist reviews quality, ICA choices, rejected epochs → approves. Only approved runs feed trends and comparisons (unapproved runs shown with "Unreviewed" watermark).

### 1.3 Reproducibility rules
- Pipeline definitions are versioned YAML files; changing any default = new version.
- `params_hash` + `sha256` + `software_versions` uniquely identify a result.
- Random seeds fixed (ICA, bootstrap).
- When a new pipeline version is released, old runs remain; a "reprocess all for patient" action creates new runs so a longitudinal series is always computed with **one** pipeline version.

---

## Part 2 — Longitudinal comparison methodology

### 2.1 The core scientific problem
EEG features change between sessions for many reasons that are **not** brain recovery: different electrode placement, impedance, montage, reference, sampling rate, alertness, sleep, sedation, time of day, medication, ICU noise, skull defects, and pure measurement noise. Resting-state spectral features show moderate-to-good test–retest reliability in healthy adults (e.g., Salinsky et al., 1991; Gudmundsson et al., 2007), but reliability varies by feature, band and recording length, and is far less studied in critically ill patients. Therefore the comparison engine must (1) refuse or caveat unfair comparisons, (2) harmonise what can be harmonised, and (3) report change *relative to measurement uncertainty*.

### 2.2 Step A — Compatibility check

| Check | Compatible | Caveat | Not comparable |
|---|---|---|---|
| Shared EEG channels (after normalisation) | ≥ 90 % of union | 50–90 % | < 50 % or < 8 channels |
| Electrode system | same | 10-20 vs 10-10 subset | unknown positions + topomap requested |
| Sampling rate | both ≥ 2× (upper band edge) after resampling | one < 200 Hz (gamma excluded) | < 100 Hz |
| Original reference | same | different (re-referenced to average) | — |
| Condition | same | resting_unknown vs known | eyes-open vs eyes-closed; rest vs task |
| Patient state / sedation | same | dose differs | sedated vs not sedated (unless user explicitly overrides with reason) |
| Clean data duration | both ≥ 2 min | 1–2 min | < 1 min |
| Quality grade | both good/fair | one poor | both poor |
| Pipeline version | same (enforced — auto-reprocess if not) | — | — |
| Time of day | within 3 h | > 3 h apart | — |

Result shown as a checklist; any "Not comparable" blocks automatic summary (user can still view raw numbers with a red banner and must enter an override reason, which is audited).

### 2.3 Step B — Harmonisation
1. Re-process both recordings with the same pipeline version.
2. Restrict to the **intersection** of good channels (channels bad in either recording removed — not interpolated for comparison, to avoid fabricating data). Excluded channels are listed.
3. Common sampling rate (resample to the lower of the two, minimum 128 Hz) before feature extraction; restrict bands to below 0.4 × common sfreq.
4. Common reference: average reference computed **over the common channel set only** (average reference depends on which channels are included).
5. Equalise epoch counts for the bootstrap (random subsample of the larger set, repeated) so the recording with more data does not appear more "certain" by construction — or report CIs separately (both are recorded).

### 2.4 Step C — Change estimation

For each feature *f*, channel/region *c*:

- **Difference:** Δ = median(B) − median(A); also % change for power features; for power, use log10 power so changes are symmetric.
- **Uncertainty:** 95 % CI of Δ via **block bootstrap over epochs** (blocks of consecutive epochs, because neighbouring epochs are correlated); 2 000 resamples.
- **Within-recording permutation test** (epoch labels A/B shuffled) → p-value. *Interpretation caution:* epochs are not independent participants; p-values describe whether the two recording segments differ, not whether the patient changed clinically.
- **Multiple comparisons:** Benjamini–Hochberg FDR across all channel×feature×band tests; optional **cluster-based permutation** across neighbouring channels (MNE `permutation_cluster_test`) for spatial maps.
- **Measurement-error threshold — Smallest Detectable Change (SDC):**
  `SDC = 1.96 × √2 × SEM`, where `SEM = SD_within`.
  `SD_within` comes from, in order of preference:
  1. The patient's own repeated baseline recordings (≥ 2 same-condition recordings within a clinically stable window);
  2. A test–retest reference dataset processed with the same pipeline (see doc 04 datasets);
  3. Not available → SDC not shown; summary says "measurement variability unknown".
- **Flag** = change exceeds SDC **and** CI excludes 0 **and** FDR q < 0.05. The UI label is *"Change beyond estimated measurement variability"* — never "improvement".

### 2.5 Step D — Multi-session trends (≥ 3 recordings)
- Plot each session's feature value with its CI; marker shape = condition, colour saturation = quality grade, icon = sedation.
- Robust trend estimate: Theil–Sen slope with bootstrap CI over sessions; displayed only when ≥ 4 comparable sessions; labelled "descriptive trend".
- Optional change-point view (e.g., PELT on session series) — C priority, exploratory only.
- Clinical events and CRS-R assessments drawn on the same time axis in a linked panel.

### 2.6 Step E — Automatic comparison summary (template-based)
No generative AI. Deterministic templates fill in numbers, ensuring neutral, verifiable language:

> *"Comparison of R01 (12 Mar 2026, resting eyes-closed, no sedation, quality Good) with R04 (02 May 2026, resting eyes-closed, no sedation, quality Fair). 17 of 19 channels shared (excluded: T7, O2 — poor quality in R04). Global relative alpha power: 0.18 → 0.24 (Δ +0.06, 95 % CI +0.03 to +0.09); this exceeds the estimated measurement variability from 2 baseline recordings. Global aperiodic exponent: no change beyond measurement variability. Caveats: recording times differ by 4 h. These are quantitative signal changes and do not by themselves indicate a change in clinical or consciousness state. Clinical interpretation requires correlation with bedside assessment."*

Banned-word list enforced in tests: *recovery, improved, improvement, worsened, deterioration, conscious, aware, diagnosis*.

### 2.7 Step F — EEG–assessment association (exploratory)
- **Within a patient:** pair each recording with the nearest CRS-R assessment within ±48 h (configurable); show side-by-side series; no p-values for a single patient with few points (too few observations).
- **Across a research cohort (researcher role, consented data):** linear mixed-effects model `feature ~ CRS-R_total + days_since_injury + sedation + (1 | patient)` or Spearman correlation with patient-clustered bootstrap. Clearly labelled exploratory; report effect sizes with CIs and the number of patients, not just recordings.

### 2.8 What the comparison module will **not** do
- Will not classify a patient as recovering or deteriorating.
- Will not compare against "normal" population norms (no validated normative database for critically ill patients is included; using healthy norms would mislead).
- Will not interpolate missing channels for comparison.
