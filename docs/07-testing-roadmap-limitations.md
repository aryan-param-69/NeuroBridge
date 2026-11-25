# 07 — Testing Strategy, Phased Roadmap & Limitations

Covers deliverables **13 (Testing strategy)**, **14 (Phased development roadmap)** and **15 (Scientific & technical limitations)**.

---

## Part 1 — Testing strategy

### 1.1 Test pyramid
| Level | Tools | What is tested |
|---|---|---|
| **Unit (signal correctness)** | pytest, hypothesis | Feature maths with **synthetic test signals** (labelled as such, never used as evidence): a 10 Hz sine → peak alpha = 10 Hz, relative alpha ≈ 1; white noise → flat PSD, aperiodic exponent ≈ 0; pink noise → exponent ≈ 1; known-amplitude sine → expected absolute power (Parseval check); flat channel → flagged; clipped signal → clipping detected; 50 Hz added → line-noise metric fires. |
| **Unit (logic)** | pytest | Channel-name normalisation (T3→T7, `EEG FP1-REF`→`Fp1`), compatibility rules (table-driven tests per rule), SDC computation, FDR correction vs statsmodels reference, CRS-R range validation and totals, template summaries contain **no banned words**. |
| **Pipeline golden tests** | pytest + stored fixtures | Real public EDFs (EEGMMIDB subject, Sleep-EDF file, MNE testing data) processed with `resting_v1.0.0` → features compared to stored golden outputs (tolerance 1e-9 rel). Any change forces a new pipeline version. |
| **EDF robustness** | pytest | Corrupt header, EDF+D, mixed sampling rates, non-standard names, 1-channel file, 24 h file (streaming, memory < 2 GB), identity mismatch, duplicate hash. |
| **Leakage tests (ML)** | pytest | Assert no subject ID shared between train/test in every fold; scalers/CSP fitted only inside pipelines (inspect fitted pipeline inputs); label-permutation test yields ~chance performance. |
| **Integration** | pytest + testcontainers (Postgres, Redis, MinIO) | Upload → process → features → comparison → report end to end. |
| **API** | pytest + httpx | Schemas, status codes, pagination, **RBAC matrix** (each role × endpoint → expected allow/deny), researchers never receive identifiers, audit event written for every patient-data access. |
| **Frontend unit** | Vitest + React Testing Library | Forms (CRS-R validation), components (QualityBadge, CompatibilityChecklist), number/unit formatting. |
| **E2E** | Playwright | Key journeys: register → upload → QC → analysis → compare → report; login; role restrictions in UI. |
| **Visual regression** | Playwright screenshots | Theme consistency, charts, PDF report pages. |
| **Accessibility** | axe-core in Playwright | WCAG 2.1 AA, keyboard navigation. |
| **Performance** | Locust (API), browser perf traces | `/signal` p95 < 300 ms; viewer scrolling 32 ch stays smooth (≥ 50 fps target); 20 concurrent users. |
| **Security** | bandit, pip-audit, npm audit, OWASP ZAP baseline, Trivy (images) | No high/critical findings; auth bypass attempts; file upload attacks; JWT expiry. |
| **Scientific validation** | Notebooks → scripted reports in `ml/` | Feature reliability (ICC) on multi-session data; cross-check band power against an independent implementation (e.g., SciPy Welch vs MNE multitaper agreement within tolerance). |
| **Usability** | Moderated sessions with 5–8 clinicians/technologists | Task completion, SUS score, misinterpretation check (do users read "change" as "recovery"?). |

### 1.2 CI gates
Lint (ruff, eslint), type check (mypy strict for `eeg/`, `comparison/`, `ml/`; tsc), unit + API tests, coverage ≥ 85 % for `eeg/`, `comparison/`, `ml/` and ≥ 70 % overall, golden tests, security scans. Nightly: E2E, performance, integration with full containers.

---

## Part 2 — Phased development roadmap

### 2.1 Module dependency graph
```
P0 Foundations (repo, CI, Docker, DB, auth, RBAC, audit)
 │
 ├──► P1 Patient management ──────────────┐
 │                                        │
 └──► P2 EDF ingestion + viewer ──► P3 Preprocessing + QC ──► P4 Features + visuals
                                                                │
                      P5 Assessments, notes, timeline ◄─(P1)    │
                                    │                           │
                                    └────────► P6 Longitudinal comparison + trends ◄─┘
                                                    │
                                                    ▼
                                              P7 Reports
 ML track (parallel after P4 feature code exists):
   P8 Offline ML research (datasets, CV, selection, model cards)
        │
        ▼
   P9 Inference + XAI integration (needs P4 + P8)
        │
        ▼
 P10 Hardening, validation, usability study, release
```
Parallelisation: P1 ∥ P2; P5 can start after P1; P8 can start once the feature-extraction library (P4) is stable, in parallel with P5–P7.

### 2.2 Phases with measurable completion criteria
Durations assume 1–2 developers; adjust to team size.

| Phase | Scope | Completion criteria (all must be met) | Est. |
|---|---|---|---|
| **P0 Foundations** | Monorepo, Docker Compose (api, worker, postgres, redis, minio, frontend), Alembic, auth (Argon2 + JWT), RBAC, audit log with hash chain, app shell & theme tokens, CI | `docker compose up` brings all services healthy; password login works; RBAC matrix tests pass for stub endpoints; every request to a patient-scoped stub writes an audit row; `/audit/verify` detects a manually tampered row; CI green | 2–3 wks |
| **P1 Patient management** | Register/edit/search, display IDs, duplicate check, PHI encryption, medical history, events, consent, care team | Create 1 000 test patients (fake identities from generator, marked test) and search returns in < 200 ms; duplicate warning triggers on same name+DOB; researchers' API responses contain zero identifier fields (automated test); field change history recorded | 2 wks |
| **P2 EDF ingestion + viewer** | Chunked upload, hashing, de-identification, header parse, validation, channel normalisation, recording context form, Canvas viewer (raw), overview strip | 10 diverse public EDFs (EEGMMIDB, Sleep-EDF, CHB-MIT, TUH sample if licensed) ingest correctly or fail with clear messages; identity-mismatch warning works; 24 h file viewable with memory < 2 GB; `/signal` p95 < 300 ms; viewer pages with ←/→ | 3–4 wks |
| **P3 Preprocessing + QC** | Versioned pipeline YAML, filtering, bad channels, interpolation rules, re-reference, epoching, autoreject, quality score, artifact segment proposals, ICA + ICLabel (S), QC sign-off, processed view in viewer | Pipeline golden tests pass on 3 public files; rerun gives identical output; quality metrics fire correctly on synthetic test cases (flat, clipped, 50 Hz, EMG); provenance shows all params & versions; unapproved runs excluded from downstream endpoints | 3–4 wks |
| **P4 Features + visuals** | Time, spectral, wavelet, complexity, aperiodic features; regional aggregation; bootstrap CIs; PSD, spectrogram, topomap endpoints; Analysis page | All synthetic-signal unit tests pass; band power agrees between Welch and multitaper within predefined tolerance on real data; feature catalog has plain-language definition for 100 % of features; all Analysis tabs render for EEGMMIDB baseline runs; CSV export matches API values | 3 wks |
| **P5 Assessments, notes, timeline** | CRS-R entry (items), validation, sign/amend, observations, notes, clinical events, timeline UI, assessment trend | CRS-R totals correct for all valid item combinations (table test); out-of-range rejected; signed records immutable (amend creates version); timeline shows all 5 lanes with filters; repeated-assessment reminder triggers when < 5 assessments in window | 2–3 wks |
| **P6 Longitudinal comparison + trends** | Compatibility engine, harmonisation, block bootstrap, FDR, cluster permutation (S), SDC (patient + reference), template summaries, comparison UI, trends UI, baseline profiles | Every compatibility rule has passing table tests; comparing differently-montaged files restricts to shared channels and lists exclusions; comparing a recording with **itself** (split halves) produces no flags beyond expected FDR rate; summary never contains banned words (fuzz test across 500 generated results); reference SDC table computed from a public multi-session dataset with documented ICCs | 4 wks |
| **P7 Reports** | PDF (WeasyPrint) & CSV, section builder, identifier levels, draft/sign/final, hash | PDF generated < 30 s for patient with 10 recordings; values in PDF match API (automated text extraction check); report hash verifiable; pseudonymised report contains no identifiers (automated scan) | 2 wks |
| **P8 Offline ML research** | MOABB loaders, feature + raw pipelines, all candidate models, nested subject-wise CV, hold-out, external validation, calibration, permutation tests, evaluation report, model cards, MLflow | Leakage tests pass; full experiment reproducible from one command + config + seed; evaluation report includes balanced accuracy with CI, ROC-AUC, F1, confusion matrix, calibration, per-subject distribution, chance level, permutation p; model selection decision documented against pre-registered rule; model card written | 4–6 wks |
| **P9 Inference + XAI** | Model registry integration, scope/OOD checks, inference jobs, calibrated outputs, conformal sets, SHAP, plain-language explanations, research banner, command-following prototype (C) | OOD check rejects inputs with missing required channels / wrong sfreq (tests); in-distribution held-out data → empirical conformal coverage within ±3 % of target; each prediction stores model + pipeline version + input hash and is reproducible; SHAP additivity check (sum of values + base = model output) passes | 3–4 wks |
| **P10 Hardening & validation** | Security review, pen-test fixes, performance, accessibility, backups/restore, documentation, clinician usability study, feature-reliability study write-up | Zero high/critical security findings; WCAG AA axe pass; restore from backup tested; SUS ≥ 70 in usability study; < 10 % of participants misinterpret comparison output as clinical recovery (pre-defined comprehension questions); user & admin guides complete | 3–4 wks |

**Total estimate:** roughly 8–10 months for 1–2 developers to reach a research-grade release (not a certified medical device).

### 2.3 Recommended development sequence (summary)
1. P0 → 2. P1 and P2 in parallel → 3. P3 → 4. P4 → 5. P5 (can overlap P3/P4) → 6. P6 → 7. P7 → in parallel from step 4: P8 → P9 → 8. P10.

**MVP definition (end of P7):** a clinician can register a patient, upload EDFs, run QC'd preprocessing, view waveforms/spectra/topomaps, record CRS-R assessments, compare recordings with compatibility checks and uncertainty, view trends, and export a signed PDF — **without any ML component**. ML is valuable but not required for the platform to help doctors.

---

## Part 3 — Scientific and technical limitations

### 3.1 Scientific
1. **Scalp EEG is indirect.** Limited spatial resolution; volume conduction; deep-structure activity poorly captured.
2. **No ground truth for consciousness.** CRS-R is the behavioural reference standard but can miss covert awareness; EEG markers are group-level associations, not individual diagnostic tests.
3. **Confounders dominate longitudinal change.** Sedation, sleep/arousal fluctuation, medications, infection, electrolyte disturbance, skull defects and electrode placement can produce EEG changes larger than any neurological change. Compatibility checks reduce but cannot eliminate this.
4. **Measurement error is uncertain in patients.** Test–retest reliability estimates mainly come from healthy adults; SDC thresholds derived from healthy data may underestimate patient variability.
5. **Command-following analysis risks false positives and false negatives.** Movement/EMG artifacts and statistical dependence can create false positives (cf. Goldfine et al., 2013); negative results do not prove absence of awareness.
6. **MI models trained on healthy data do not transfer reliably to patients.** They are research benchmarks only; OOD gating will often (correctly) refuse patient data.
7. **Small samples.** Any patient cohort analysis will have few participants → wide CIs; exploratory associations need independent replication.
8. **Feature multiplicity.** Hundreds of features × channels create many comparisons; FDR control and pre-specified primary features are required for any research claim.
9. **No normative database.** Comparisons are within-patient only; population-norm z-scores are deliberately excluded.
10. **Explanations are model-internal.** SHAP shows what the model used, not brain mechanisms.

### 3.2 Technical
1. **EDF heterogeneity.** Vendor-specific channel labels, EDF+D discontinuities, wrong header dates, non-standard units; normalisation rules will need continuous extension.
2. **Missing electrode positions** prevent topomaps and interpolation for some files.
3. **Compute load.** ICA and connectivity (wSMI) on long, high-density recordings are slow on CPU; requires background workers and possibly limits.
4. **Browser rendering** of very high-density/long EEG depends on server-side decimation; fine detail at high zoom requires refetching.
5. **Pipeline evolution.** New pipeline versions require reprocessing histories for consistent trends.
6. **Integration.** No HL7/FHIR or hospital EEG system (PACS-like) integration in the initial scope; manual EDF export/upload is required.
7. **Security depends on deployment.** Encryption, backups, network isolation and access governance are shared responsibilities with hospital IT.

### 3.3 Regulatory & ethical
- NeuroBridge AI as specified is a **research and clinical-decision-support prototype, not a certified medical device**. Using it to inform diagnosis or treatment would likely classify it as Software as a Medical Device (e.g., EU MDR, US FDA), requiring a quality management system (ISO 13485), risk management (ISO 14971), software lifecycle (IEC 62304) and clinical validation.
- Patient data use requires ethics approval, consent (or a lawful basis), data-protection impact assessment and compliance with local law (e.g., HIPAA, GDPR or the applicable national law).
- The UI and reports must state these limitations wherever outputs are shown.

### 3.4 Explicitly excluded features (per project brief)
No PDF research-paper chatbot, no RAG, no vector database, no document embeddings, no research-document question answering, no general-purpose AI chatbot. All summaries are deterministic templates.
