# NeuroTrace

*(code name in the packages, database names and UI: NeuroBridge AI)*

Explainable EEG-based patient monitoring and longitudinal neurophysiological analysis platform.
Design specification: [docs/README.md](docs/README.md) · roadmap: [docs/07-testing-roadmap-limitations.md](docs/07-testing-roadmap-limitations.md) ·
database decision: [ADR 0001 (MongoDB)](docs/adr/0001-mongodb-as-primary-database.md).

> Research software. It measures and documents; it never diagnoses, and ML outputs are research-only.
> Motor-imagery decoding is **not** consciousness or CMD detection.

## Status

| Phase | State |
|---|---|
| P0 Foundations (password login, RBAC, hash-chained audit log) | Done |
| P1 Patient management | Done |
| P2 EDF upload + EEG viewer | Done |
| P3 Preprocessing + QC | Done (ICA and autoreject deferred) |
| P4 Features + visuals | Done (criteria below; connectivity deferred) |
| **P5 Assessments + timeline** | **Done** (criteria below; CRS-R tables need verification against the official guidelines) |
| P8 (early) Offline ML on PhysioNet EEGMMIDB | Run once; see [ml/reports/left_vs_right/REPORT.md](ml/reports/left_vs_right/REPORT.md) |
| P3+ Pipeline v1.1.0 (Butterworth + FastICA blink removal, opt-in) | Done, tested on simulated blinks only |
| P8 Sedation-level model v1 (propofol, 20 healthy people) | Done; selectable in the website (see [REPORT](ml/reports/sedation/REPORT.md)) |
| P8 Sedation-level model v2 (40 people, two laboratories) | Done; **default in the website**, status *staging* (see [REPORT](ml/reports/sedation_multisite/REPORT.md)) |
| **P6 Longitudinal comparison** | **Done** (`/patients/{id}/compare`, tests in `test_comparisons.py`) |
| **P7 Reports** | **Done** (versioned, signed = immutable, SHA-256, HTML export; `test_reports.py`) |
| **P9 AI analysis** (sedation model in the website, SHAP, uncertainty) | **Done for the sedation model only**, flagged *staging*; never validated on patients (`test_ai_sedation.py`) |
| Cross-patient pages (Recordings, Assessments, Comparisons, Reports, AI analysis) | Done |
| P10 hardening | Not started |

Storage is **MongoDB** (database `neurobridge`; ML results in `neurobridge_ml`). Originals are stored encrypted on disk.

### Phase 2 criteria

| Criterion (docs/07) | Evidence |
|---|---|
| Diverse EDFs ingest correctly or fail with a clear message | 26 tests in `backend/tests/test_recordings.py`: generated EDFs, text/garbage/truncated files, and a real PhysioNet EDF+ (`Fc5.` → `FC5`, 64 ch, annotations) |
| Identity-mismatch warning works | name or birth-date mismatch blocks until a clinician confirms; the override is stored and shown |
| Original is protected | AES-256-GCM chunked encryption; tamper / truncation / reordering detected (tests); filenames and header text are never stored |
| De-identified copy | header patient/recording fields blanked; still opens in MNE |
| Viewer paging is fast | windowed reads (`get_data(start, stop)`) + min/max decimation that keeps peaks; checked in the browser on real data |
| `/signal` p95 < 300 ms on a 10 s × 64 ch window | **Not yet measured** (functional only; correctness tests pass) |
| 24 h file viewable with memory < 2 GB | **Not yet tested** (windowed reads are designed for it; upload assembles in memory-light streaming but no long file was tried) |

### Phase 3 criteria

| Criterion (docs/07) | Evidence |
|---|---|
| Golden tests pass on 3 public files; re-run gives identical output | `tests/test_processing.py::test_real_recordings_match_the_stored_regression_summaries` (EEGMMIDB S001R01, S001R02, S002R01; output hashes identical across two runs). **Caveat:** the golden values were produced by this code, so they catch *unintended change*; they are not independent ground truth |
| Quality metrics fire on flat / clipped / 50 Hz / EMG | `tests/test_quality.py` (synthetic signals with known defects, plus blink-robustness and neighbour-correlation tests) |
| Provenance shows all parameters and versions | each run stores resolved parameters, `params_hash`, input/output SHA-256, library versions and every step with its parameters, timing and result (shown in the UI) |
| Unapproved runs excluded from downstream endpoints | `/patients/{id}/analysis-runs` returns approved **and** current runs only (`test_unapproved_runs_never_reach_downstream_analysis`) |
| Pipeline versioned, hashed, overrides restricted | `backend/app/eeg/pipelines/resting_v1.0.0.yaml`; role-restricted overrides tested |
| ICA with ICLabel; autoreject | **Not done** (deferred: heavy dependencies). A fixed, documented amplitude rule is used instead, so eye blinks stay in frontal channels (they are masked per channel, and later features skip masked values) |

**Defaults were tuned on real data — read this.** The first version rejected 98–100 % of epochs in 3 of 8 public recordings and flagged
peripheral electrodes as bad in 6 of 8. They were redesigned (per-channel masks, blink-robust SD, neighbour-based correlation) after inspecting
those eight healthy-volunteer recordings. The same recordings back the regression tests, so they are **not** an independent validation, and
nothing is validated on clinical/ICU EEG. Quality-score weights and grade cut-offs are expert-chosen and uncalibrated.

### Phase 4 criteria

| Criterion (docs/07) | Evidence |
|---|---|
| Synthetic-signal unit tests pass | `tests/test_feature_primitives.py` (37) and `test_feature_extraction.py` (12): sine RMS / Hjorth / kurtosis, Parseval, white-noise flat spectrum, pink-noise exponent, ramp permutation entropy = 0, fast sample entropy = brute-force definition, known 0.6 left-right asymmetry, bootstrap coverage, masking |
| Band power agrees with an independent estimate on real data | Hann periodogram vs MNE multitaper on EEGMMIDB S001R02: mean absolute difference < 0.03 per band, rank agreement ≥ 0.9. **Tolerance fixed in advance; it failed once because my first test compared unlike quantities (ratio of means vs mean of ratios) — corrected to like-for-like, tolerance unchanged.** One file only |
| Feature catalogue: plain-language definition for 100 % of features | `test_every_emitted_feature_has_a_plain_language_catalogue_entry` and the API-level equivalent |
| All Analysis tabs render for EEGMMIDB baseline runs | Checked **by eye in the browser on one recording** (S001R02, eyes closed): Spectrum, Band power, Spectrogram, Topographic maps, All features, Aperiodic. Not automated |
| CSV export matches API values | `test_csv_export_matches_the_api_values_exactly` (every row, every statistic) |
| Physiological sanity (not a validation) | Eyes-closed vs eyes-open occipital relative alpha, 5 subjects, expectation fixed beforehand (≥ 4 of 5 higher with eyes closed): higher in 5 of 5 (+0.47, +0.38, +0.51, +0.70, +0.07) |

What is computed: 10 time-domain, band power (absolute/relative) + ratios + 5 spectral-shape, aperiodic (specparam), 7-scale wavelet energies + entropy,
permutation / sample entropy and Lempel-Ziv, left-right asymmetry — for every channel, 16 regions and the whole head, with 95 % **block-bootstrap**
intervals (epochs overlap by 50 %, so ordinary resampling would be too narrow; tested). Masked (epoch, channel) values from the processing run are excluded.

Deliberate deviations from the design: epoch-level values are saved as `.npz`, not Parquet; spectrograms use the STFT, not Morlet wavelets; connectivity (wSMI,
coherence) is not implemented; sample entropy and Lempel-Ziv use at most 40 evenly spaced epochs per recording (so their intervals are wider and `n_epochs` shows it).
`specparam` is a release candidate (2.0.0rc7). Aperiodic values have no confidence interval (one fit per recording-average spectrum; fit quality R² is reported instead).

### Phase 5 criteria

| Criterion (docs/07) | Evidence |
|---|---|
| CRS-R totals correct for all valid item combinations | `tests/test_crsr.py`: all 10 080 combinations (5·6·7·4·3·4) summed independently, every total 0–23 reachable; the pattern prompt is re-stated independently and checked on all 10 080 |
| Out-of-range / wrong-type scores rejected | Unit table (every subscale at -1, max+1, 99, 1.5, "2", None, true) and the same through the API (422). Client-sent `total` is refused, never trusted: the server computes it |
| Signed records immutable; amendment creates a version | `test_signed_assessments_are_immutable...`: version 1 is compared byte-for-byte after an amendment is signed; PATCH/DELETE on a signed row is 409; the chain shows all versions, lists show the latest signed one; unsigned amendments never replace the signed value for other roles or in trends |
| Timeline shows all five lanes with filters | `test_timeline.py`: recordings, CRS-R, events (+ injury/admission milestones), medication spans, notes; lane and date filters; soft-deleted items hidden; edited notes appear once. Looked at in the browser on one seeded patient |
| Repeated-assessment reminder when fewer than 5 signed assessments in the window | `test_reminder_counts_recent_signed_assessments` (0, 4, 5 and a changed threshold; discharged patients are not reminded). **The window (10 days) and the count (5) are site settings taken from the literature as I remember it; check them against current guidance before relying on them** |
| Care-team and role rules | Drafts visible to clinicians only; only the author edits or signs; technologists/viewers read signed records; researchers get a pseudonymised view (scores, total, category, days since injury — no dates, assessor or free text) and only with research consent; outsiders get 404 |

Also built: versioned clinician notes (observation / interpretation / plan; an edit adds a version, nothing is overwritten), EEG–assessment pairing within a window
(EDF start times are read in the configurable site time zone), trend small-multiples (hollow markers for reduced/ongoing sedation), a pattern check that is a prompt
and never fills in the category, and audit entries that contain no free text.

Limitations specific to Phase 5: the CRS-R item labels, the 23-point structure and the pattern rules were transcribed from published descriptions and
**must be checked against the official CRS-R Administration and Scoring Guidelines before clinical use**. Only the CRS-R is implemented (no other scales). EDF headers
carry no time zone and sometimes a wrong date (the PhysioNet files are dated 2009): such recordings are flagged "dated before injury" on the timeline, not corrected.

## Sedation-level model and pipeline v1.1 (added after Phase 5)

**Pipeline `resting` v1.1.0** (opt-in, `default: false` in its YAML; v1.0.0 stays the default): 4th-order Butterworth band-pass applied forward and backward (zero-phase, -6 dB at the edges) and
FastICA eye-blink removal with a transparent rule set (frontal-pole scalp map + spiky time course + mostly slow power; at most 3 components; every decision is stored in the run record).
Evidence (`tests/test_ica_butterworth.py`): the filter's gain is checked against theory; on a real recording (EEGMMIDB S001R02) with a KNOWN simulated blink added, 94 % of the injected
amplitude at Fp1 is removed while occipital alpha and beta power stay within 20-25 % of the same recording processed without blinks; a smooth 1.5 Hz frontal oscillation (a stand-in for real
frontal delta) keeps more than 80 % of its amplitude; ICA is skipped with a stated reason when there are no frontal-pole channels, too few channels or too little data.
**Honest limits:** the first threshold guess (kurtosis 5) removed nothing and was lowered to 1.0 after looking at one simulation; real clinical blinks, saccades, muscle and heartbeat artefacts are not validated.

**Sedation-level model** (`ml/`, results in [ml/reports/sedation/REPORT.md](ml/reports/sedation/REPORT.md), protocol fixed beforehand in [PRESPEC.md](ml/reports/sedation/PRESPEC.md)):
trained on the open Cambridge propofol dataset (Chennu et al. 2016, 20 healthy people, CC BY 2.0 UK) to tell awake (baseline/recovery) from moderate sedation using 74 interpretable features
(relative band power, Hjorth, spectral/permutation entropy, 1/f slope, wPLI; 5 regions + whole head) from the 19 standard channels. Six models (logistic regression, SVM, random forest, gradient boosting, XGBoost,
LightGBM) with leave-one-person-out nested CV: recording-level balanced accuracy 0.89-0.94, **all six statistically tied** (the pre-specified rule picked XGBoost, 0.938, 95 % CI 0.86-1.00, permutation p = 0.010).
The output rises with dose on a level it was never trained on (mild sedation) and correlates with plasma propofol (rho = 0.72). Conformal sets give 83 % coverage (target 80 %), 12 % "uncertain".
SHAP is computed on held-out people. **Not shown or claimed:** anything about patients. The unfamiliar-EEG detector flagged 100 % of recordings from another site (EEGMMIDB), where the model still called 96 % of awake people not sedated;
Platt calibration slightly worsened an already reasonable XGBoost. The model is saved as `ml/models/sedation_v1/` (model card, SHA-256) and registered as *staging* in `neurobridge_ml.model_registry`; the website can use it (setting `sedation_model`).

**Multi-site sedation model v2** (`ml/reports/sedation_multisite/`, protocol in [PRESPEC.md](ml/reports/sedation_multisite/PRESPEC.md), written before training):
Cambridge (20 people, 91 ch) plus the Oslo propofol set (OpenNeuro ds005620, 20 people, 65 ch) = 40 people, 131 recordings, 68 features (overall signal size removed on purpose).
Pooled leave-one-person-out: six models 0.96-0.97 balanced accuracy, statistically tied; LightGBM chosen by the pre-set rule (0.971, 95 % CI 0.94-0.99, permutation p = 0.0099).
Cross-laboratory: Cambridge to Oslo 0.980, Oslo to Cambridge 0.875. All pre-set success criteria met. Dementia EEG check (ds004504, 88 people, nobody sedated): 2 of 88 called sedated.
Caveats: Oslo awake/sedated come from different recording blocks, the Oslo sedation was probably deeper, only healthy volunteers, never validated on patients. Sensitivity run with all 74 features gives the same picture.
Two-factor login was removed (password-only, Argon2id + lockout + audit). Datasets are fetched with `scripts/download_datasets.py` into `data/` (git-ignored); run `python scripts/download_datasets.py --list` to see what is available.

Datasets used: Cambridge sedation (https://www.repository.cam.ac.uk/handle/1810/252736, ~3.7 GB, not in git), PhysioNet EEGMMIDB. Not used: the open Figshare resting-EEG set of patients with disorders of consciousness
(DOI 10.6084/m9.figshare.23552964) because it has no label file.

## Phases 6, 7 and AI analysis (added after the sedation study)

**Comparisons (Phase 6).** Two recordings of one patient, only if both processing runs are QC-approved, current and have features; runs processed with different pipeline versions, parameters or feature versions are refused (409).
Every difference in condition, patient state, sedation, quality, channels, time of day or low epoch count is shown next to the numbers. "Interval-separated" means the two 95 % block-bootstrap intervals do not overlap: a screening flag, not a significance test.
A feature-over-time view shows signed CRS-R totals alongside, and marks recordings made under different conditions. The page says plainly that it describes change between recordings and does not say the patient is recovering.

**Reports (Phase 7).** A draft report is a snapshot (signed CRS-R assessments, approved recordings with global relative power, an optional comparison, optional model outputs and notes) plus the clinician's own written summary (required to sign; the platform does not interpret).
Signing makes it immutable; an amendment is a new version with a reason; every version has a SHA-256 over title + summary + snapshot, checked on every read and before export (a tampered database row blocks export). The stored snapshot holds the platform patient ID only (no name, DOB, MRN) and no free-text observations; the HTML export escapes everything and is served with a restrictive CSP.

**AI analysis.** The sedation model (above) runs inside the backend on a processed run: the model file is loaded only from the configured model directory and only after its SHA-256 matches the model card (a pickle can run code). It needs the 19 standard channels and at least one minute of clean 10-s windows, otherwise it refuses with a reason.
Each run stores an immutable prediction record (model version + SHA, probability, conformal answer set, unfamiliar-EEG fraction, SHAP reasons, reliability warnings). Only clinicians start runs; researchers see a pseudonymised list (consented patients, no dates). The inference code is vendored from `ml/` (`backend/app/ml`) and a golden-file test fails if the two copies drift.
**On every non-Cambridge recording the "unfamiliar EEG" flag fires, as it did for EEGMMIDB and for the open Figshare patient set; treat every output as unreliable until thresholds are recalibrated on target recordings.**

**Open Figshare patient set** (DOI 10.6084/m9.figshare.23552964; 98 recordings, 2 without a data file): no labels exist, so it is used only as a pipeline/behaviour check ([report](ml/reports/doc_figshare/REPORT.md)), not for accuracy.

## Repository layout

```
.
├── backend/            FastAPI service (Python 3.12): auth, patients, EEG upload, processing, features, assessments,
│   ├── app/              comparisons, AI analysis, reports; app/eeg = signal processing, app/ml = model inference
│   ├── tests/            319 tests (pytest); needs a local MongoDB
│   └── scripts/          create_user.py (first admin / clinician accounts)
├── frontend/           React 19 + TypeScript + Mantine + ECharts single-page app (vitest tests)
├── ml/                 offline research workspace (training, validation, reports); never deployed with the app
│   ├── src/nbml/         data loaders, features, nested CV, models, SHAP, calibration, reports
│   ├── tests/            leakage guards, metric and reliability tests
│   ├── reports/          generated results, figures and PRESPEC files (pre-specified protocols)
│   └── models/           trained model + model card (SHA-256 checked before loading)
├── data/               datasets and caches (git-ignored; fetched by scripts/download_datasets.py)
├── scripts/            download_datasets.py
├── docs/               design documents, ADRs, project overview, viva notes, presentation (docs/presentation)
├── docker-compose.yml  MongoDB + API + web (written, not yet run)
└── .github/workflows/  CI: backend, frontend, ml
```

## Run locally

Requirements: [uv](https://docs.astral.sh/uv/), Node 22+, a running MongoDB (`mongodb://localhost:27017`).

```powershell
cd backend
$env:NB_USER_PASSWORD = "choose-a-strong-Passw0rd"
uv run python -m scripts.create_user you@example.com "Your Name" admin      # or clinician, eeg_technologist, researcher, viewer
uv run uvicorn app.main:app --port 8000

# second terminal (proxies /api to port 8000)
cd frontend; npm install; npm run dev        # http://localhost:5173
```
Sign in with the e-mail and password you set. **There is no second factor** (it was removed on request): add one again before any real clinical use.

## Tests

```powershell
cd backend;  uv run pytest -q        # needs MongoDB; uses (and drops) the database `neurobridge_test` only
                                     # tests that need real EEG skip themselves when data/eegmmidb is absent
cd frontend; npm test
cd ml;       uv run pytest -q        # leakage guards + metric checks
```

## Machine learning (offline, `ml/`)
Reproduce: `cd ml; uv run python -m nbml.run_experiment --task left_vs_right` then `uv run python -m nbml.report`.
Protocol: label-agnostic subject exclusion rule, locked hold-out subjects, subject-wise nested CV, pre-specified
selection rule, label-permutation test. Results, model registry and out-of-fold predictions go to MongoDB `neurobridge_ml`.

## Known limitations
- MongoDB standalone has no multi-document transactions; writes are ordered to avoid visible half-written patients (ADR 0001).
- The audit log is tamper-**evident** (hash chain). Tamper-**resistance** needs an insert-only MongoDB role (deployment).
- De-identified EDF copies are not encrypted by the app (needs volume encryption); only originals are.
- Background jobs run in an in-process thread pool with job records in MongoDB (no Redis/Celery here); jobs interrupted by a restart are marked failed.
- Processing loads a whole recording into memory (guarded by `max_process_bytes`); upload completion still parses the file within the request.
- Quality assessment samples long recordings (30 s chunks, up to 60 min).
- Login is password-only (no second factor) with per-account lockout after 5 failures; there is no per-IP rate limiting.
- Docker/Compose files are written for MongoDB but **never run** (Docker is not installed here); CI workflow not yet run on GitHub.
- Model files are pickles: keep `model_dir` writable only by the deployment; the SHA-256 check stops accidental swaps, not a person with write access to both the file and its card.
- EDF headers often carry a wrong date (the PhysioNet files say 2009): such recordings are flagged on the timeline; the comparison and trend views order by the header date.
- Set `ENCRYPTION_KEY`, `HMAC_KEY`, `SECRET_KEY` before real use. Losing `ENCRYPTION_KEY` makes identifiers and original recordings unrecoverable; key rotation is not implemented.
