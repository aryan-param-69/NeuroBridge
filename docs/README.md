# NeuroTrace (design name: NeuroBridge AI) — Design Specification (v0.1, pre-implementation)

**An explainable EEG-based patient monitoring and longitudinal neurophysiological analysis platform.**

> **Status note (Nov 2026).** Documents 01-07 are the ORIGINAL design (v0.1, written before any code). The platform has since been built and some decisions changed:
> MongoDB instead of PostgreSQL ([ADR 0001](adr/0001-mongodb-as-primary-database.md)), no Celery/Redis/MinIO (in-process jobs, encrypted files on disk),
> password-only login (the planned TOTP step was removed). For what actually exists, read the root [README](../README.md), the
> [operations guide](OPERATIONS.md) (rate limiting, backups, key rotation, HTTPS) and the generated reports under `ml/reports/`.
> Every performance number comes from real, reproducible experiments (see `ml/reports/`).

## Purpose in one paragraph
NeuroBridge AI puts in one place everything a clinician needs to follow a patient's EEG over time: patient records, EDF upload and quality control, standard qEEG analysis (waveforms, spectra, topographic maps, features), **fair comparison of recordings over time** (with compatibility checks and measurement-error thresholds), CRS-R assessment tracking on the same timeline, and signed PDF/CSV reports. A separate, clearly labelled research module adds explainable ML (motor-imagery benchmark, artifact detection, within-patient command-following analysis). The platform measures and documents; **it never diagnoses or labels recovery**.

## Documents
| # | Document | Deliverables covered |
|---|---|---|
| 01 | [Architecture & technology](01-architecture-and-technology.md) | 1 System architecture · 12 Folder structure · technology justification |
| 02 | [Features & database](02-features-and-database.md) | 2 Feature specifications · 3 Database schema · additional features |
| 03 | [EEG pipeline & longitudinal method](03-eeg-pipeline-and-longitudinal.md) | 4 EDF-to-analysis workflow · 5 Longitudinal comparison methodology |
| 04 | [Research, datasets, ML, XAI](04-research-datasets-ml-xai.md) | Literature & research gaps · 6 Datasets · 7 ML selection · 8 Validation · 9 XAI |
| 05 | [UI/UX blueprint](05-ui-ux-blueprint.md) | 10 Page-by-page blueprint, design system |
| 06 | [API specification](06-api-specification.md) | 11 API endpoints |
| 07 | [Testing, roadmap, limitations](07-testing-roadmap-limitations.md) | 13 Testing · 14 Roadmap · 15 Limitations |

## Key decisions at a glance
| Area | Decision | Main reason |
|---|---|---|
| Frontend | React + TypeScript, Mantine, ECharts, custom Canvas EEG viewer | Mature ecosystem; clinical-grade viewer needs custom rendering |
| Backend | FastAPI (Python) + Celery workers | Same language as MNE/SciPy/scikit-learn |
| Database | PostgreSQL (+ Parquet files for epoch-level features) | Relational clinical data, integrity, JSONB flexibility |
| Storage | MinIO / S3, immutable encrypted originals | Data safety, portability |
| EEG | MNE-Python, autoreject, mne-icalabel, specparam, PyWavelets, antropy | Peer-reviewed open standards |
| Longitudinal | Compatibility check → harmonisation → block-bootstrap CIs → FDR → smallest detectable change | Separate real change from measurement noise |
| ML | Evidence-based selection via nested subject-wise CV; expected winner Riemannian tangent space + logistic regression, EEGNet as deep benchmark (hypothesis, to be tested) | Small data, interpretability, calibration |
| XAI | Permutation importance, SHAP, Haufe patterns, calibration, conformal prediction, OOD gating, version tracking | Trustworthy, scoped outputs |
| Summaries | Deterministic templates, banned-word list | No generative text, no implied diagnosis |

## Development sequence
```
P0 Foundations ─┬─► P1 Patients ─────────────► P5 Assessments/timeline ─┐
                └─► P2 EDF + viewer ─► P3 Preprocess/QC ─► P4 Features ─┴─► P6 Comparison/trends ─► P7 Reports ─┐
                                                              └─► P8 Offline ML ─► P9 Inference + XAI ──────────┴─► P10 Hardening & validation
```
MVP = end of P7 (fully useful to clinicians without any ML). Measurable completion criteria for every phase are in [document 07](07-testing-roadmap-limitations.md#22-phases-with-measurable-completion-criteria).

## Next step
Review and approve this specification (especially pipeline defaults, compatibility thresholds and roles), then start **P0 Foundations**.
