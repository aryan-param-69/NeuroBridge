# 02 — Feature Specifications & Database Schema

> **Update 2026-11-19:** the database is now **MongoDB** (see [ADR 0001](adr/0001-mongodb-as-primary-database.md)). Wherever this document says PostgreSQL / SQL tables, read MongoDB collections.

Covers deliverables **2 (Feature specifications)** and **3 (Database schema)**, plus Section 2 (additional features).

Each feature has: **purpose**, **functional requirements (FR)**, and **acceptance criteria (AC)**. Priority: **M** = must (MVP), **S** = should, **C** = could (later).

---

## A. Patient Management

**Purpose:** a single, reliable record per patient that every recording and assessment hangs off.

| ID | Requirement | Priority |
|---|---|---|
| PM-1 | Register patient: name, DOB, sex, hospital MRN (optional), contact/guardian, consent status | M |
| PM-2 | Generate unique ID: `NB-YYYY-NNNNNN` (human-readable, sequential per year) + internal UUID v4 (never shown in URLs to non-admins as the only identifier) | M |
| PM-3 | Duplicate check on registration (same name + DOB, or same MRN) → warning before save | M |
| PM-4 | Edit profile with field-level change history (who/when/old→new) | M |
| PM-5 | Search by ID, name, MRN, diagnosis tag, date of injury; filters by status (active/discharged), clinician | M |
| PM-6 | Clinical information: etiology (traumatic / anoxic / vascular / other), date of injury, admission date, current clinical status (clinician-entered label), relevant comorbidities, seizure history, current medications (esp. sedatives/anti-seizure meds), skull defects/craniectomy (affects EEG amplitude) | M |
| PM-7 | Patient status lifecycle: active → discharged → archived (never hard-deleted while retention rules apply) | M |
| PM-8 | Care-team assignment (which clinicians can access) | S |
| PM-9 | Consent tracking: clinical care vs research use (separate flags, date, scope) | M |

**AC:** registering a duplicate shows a warning; every edit appears in history and audit log; researchers never see name/DOB/MRN.

## B. EEG Analysis

| ID | Requirement | Priority |
|---|---|---|
| EA-1 | Upload EDF/EDF+ (BDF optional later); max size configurable (default 2 GB); resumable chunked upload | M |
| EA-2 | Parse header via MNE: channels, types, sampling rate(s), duration, start time, annotations, physical min/max, prefiltering string | M |
| EA-3 | Recording metadata form: recording condition (eyes open/closed/unknown, task paradigm, resting), patient state (awake/drowsy/asleep/sedated), sedation agents & doses, time of day, setting (ICU/ward/lab), device/amplifier, electrode system, reference used, technologist | M |
| EA-4 | Channel inspection table: name (original + normalised), type, 10-20 mapping found?, sampling rate, flat/noisy flags, amplitude range | M |
| EA-5 | Quality report (see §2.1) | M |
| EA-6 | Preprocessing with a selected **versioned pipeline** (defaults below; parameters editable only by allowed roles and always recorded) | M |
| EA-7 | ICA artifact handling with ICLabel suggestions; human confirms components to remove | S |
| EA-8 | Epoching: fixed-length (resting) or event-locked (task annotations) | M |
| EA-9 | Waveform viewer: raw vs processed toggle, montage switch (referential / average / longitudinal bipolar), gain, timebase, channel selection, annotation display | M |
| EA-10 | PSD (per channel and averaged), spectrogram (per channel), topographic maps per band | M |
| EA-11 | Features: time-domain, frequency-domain, wavelet, complexity, aperiodic (full list in doc 03) | M |
| EA-12 | Absolute and relative band power per channel and region | M |

**AC:** a valid EDF is viewable within 10 s of upload completion; every derived figure shows the pipeline version; re-running the same pipeline on the same file yields identical features (bit-exact or within 1e-9 relative tolerance).

## C. Longitudinal EEG Comparison
| ID | Requirement | Priority |
|---|---|---|
| LC-1 | Modes: baseline vs latest, previous vs latest, any two selected | M |
| LC-2 | Automatic **compatibility check** (channels, sampling rate, reference, condition, sedation, duration, quality, pipeline version) → Compatible / Compatible with caveats / Not comparable | M |
| LC-3 | Harmonisation to common channel set, common rate, common reference, same pipeline version | M |
| LC-4 | Change metrics with uncertainty (bootstrap CIs), multiple-comparison control, smallest-detectable-change flags | M |
| LC-5 | Visuals: difference topomaps, paired bar charts, channel×band change heatmap, PSD overlay | M |
| LC-6 | Auto-generated **neutral** comparison summary (template-based, no free-form generative text) | M |
| LC-7 | Multi-session trend view (≥3 recordings) | M |
| LC-8 | Clinician can save comparison with comment and sign-off | S |

**AC:** comparing two recordings with different montages yields only the shared channels and lists the excluded ones; a comparison never displays the words "improvement", "recovery" or "deterioration".

## D. Patient Progress Monitoring
| ID | Requirement | Priority |
|---|---|---|
| PP-1 | Interactive timeline: recordings, CRS-R assessments, clinician notes, clinical events (surgery, medication change, infection, transfer) | M |
| PP-2 | Feature trend charts with recording-quality and condition markers on each point | M |
| PP-3 | CRS-R total and subscale trends on the same time axis (dual panel, not dual y-axis) | M |
| PP-4 | Patient-specific baseline profile (see §2.2) | S |
| PP-5 | Filter timeline by type and date range | M |

## E. AI/ML Analysis (research mode)
| ID | Requirement | Priority |
|---|---|---|
| ML-1 | Run registered model on a recording's features (only if compatibility & OOD checks pass, otherwise shown as "outside model scope") | S |
| ML-2 | Show model name, version, training data, intended use, known limitations (model card) next to every output | M (if ML-1) |
| ML-3 | Calibrated probability + uncertainty interval / conformal set | S |
| ML-4 | Persistent banner: "Research output. Not a diagnosis. Motor-imagery classification ≠ consciousness detection." | M |
| ML-5 | Within-patient command-following analysis (Claassen-style permutation test) for task recordings — research prototype | C |

## F. Explainable AI
| ID | Requirement | Priority |
|---|---|---|
| XA-1 | Global feature importance (permutation importance on held-out data; SHAP summary) | S |
| XA-2 | Local explanation per prediction (SHAP waterfall) mapped to channels/bands in plain language | S |
| XA-3 | Uncertainty and OOD score with explanation of *why* (e.g., "channel set differs from training", "feature X outside training range") | S |
| XA-4 | Model version tracking: every prediction stores model version, feature-pipeline version, input hash | M (if ML-1) |

## G. Clinical Assessment Tracking
| ID | Requirement | Priority |
|---|---|---|
| CA-1 | CRS-R entry by subscale item (Auditory 0–4, Visual 0–5, Motor 0–6, Oromotor/Verbal 0–3, Communication 0–2, Arousal 0–3; total 0–23) with item-level checkboxes per the official scoring guidelines | M |
| CA-2 | Assessor, date/time, setting, arousal facilitation used, sedation status, confounders (e.g., aphasia, motor impairment, visual impairment) | M |
| CA-3 | Clinician-entered diagnostic category (UWS/VS, MCS−, MCS+, eMCS, other) — **entered, never auto-derived**; system shows whether the item pattern is consistent with the entered category as a gentle check | M |
| CA-4 | Free-text structured observations: auditory, visual, motor, communication responses | M |
| CA-5 | Assessment history table + trend | M |
| CA-6 | Reminder when fewer than recommended repeated assessments exist in a diagnostic window (literature suggests ≥5 repeated CRS-R assessments reduce misdiagnosis — Wannez et al., 2017) | S |
| CA-7 | EEG–assessment association view (time-aligned pairs; exploratory correlation at cohort level only) | S |

## H. Reports
| ID | Requirement | Priority |
|---|---|---|
| RP-1 | PDF report: patient summary (configurable identifiers), recordings list with quality, selected analyses, comparisons, CRS-R history, clinician notes, AI outputs with model cards & disclaimers | M |
| RP-2 | CSV exports: features (long format), assessments, comparison results | M |
| RP-3 | Every report stamped with generation time, user, software version, pipeline versions, and a report hash | M |
| RP-4 | Report draft → clinician sign-off → final (immutable) | S |
| RP-5 | BIDS-EEG export for research (pseudonymised) | C |

---

## 2. Additional features (researched recommendations)

### 2.1 Automatic signal-quality scoring (M)
Transparent, rule-based composite — **not** a black box. Each sub-score is shown.

| Metric | How computed | Typical flag |
|---|---|---|
| Flat channels | Std < 0.5 µV over 5 s windows | any |
| Clipping | Samples at physical min/max | > 0.1 % of samples |
| Line noise | Power ratio 49–51 (or 59–61) Hz vs 40–45 Hz | > 10 dB |
| Muscle (EMG) | Power 30–45 Hz / 1–30 Hz | high ratio |
| Drift | Power < 0.5 Hz relative | high |
| Bad channels | Flat / noisy / low correlation with neighbours (PREP-style) | > 15 % of channels |
| Rejected epochs | After autoreject | > 30 % |
| Usable duration | Clean data length | < 2 min for resting analysis |

Score: Good / Fair / Poor with a 0–100 number = weighted mean of normalised sub-scores. Weights are initially expert-chosen (documented); later calibrated against expert-annotated artifact data (e.g., TUH Artifact Corpus).

### 2.2 Patient-specific baseline profile (S)
- Clinician designates one or more recordings as baseline (with reason).
- If ≥2 baseline-condition recordings exist, the system estimates **within-patient variability** per feature → used for smallest-detectable-change thresholds.
- Baseline can be re-designated; history kept.

### 2.3 Others recommended
| Feature | Why it is genuinely useful | Priority |
|---|---|---|
| Artifact & abnormal-segment identification | Highlights high-amplitude, flat, EMG-burst and eye-blink segments on the viewer; clinician confirms. (*Abnormal* = signal-quality abnormal, not epileptiform diagnosis.) | M |
| Channel-wise comparison | Per-channel change maps reveal focal vs diffuse changes (e.g., near craniectomy). | M |
| Interactive segment selection | Clinician selects clean segments for analysis; selection is saved and versioned. | M |
| Recording compatibility checks | Core of scientific validity for comparison. | M |
| Missing-channel warnings | Shown at upload, comparison and ML time. | M |
| **Standardised acquisition protocol templates** | The best way to make longitudinal comparison valid is to record consistently (same montage, condition, duration, time of day). A protocol checklist at upload improves future comparability. | S |
| **Sedation & medication logging per recording** | Sedatives (propofol, benzodiazepines) strongly alter EEG spectra; must be visible on every trend point. | M |
| **Clinical event markers** | Medication changes, surgery, infections explain EEG changes better than any algorithm. | M |
| **Montage switching in viewer** | Clinicians read bipolar "double banana"; researchers use average reference. | M |
| **Provenance panel** | "How was this computed?" on every figure: file hash, pipeline, parameters, versions. | M |
| **QC sign-off workflow** | Technologist preprocesses → clinician approves before data enter trends/comparisons. | S |
| **Pseudonymised research workspace** | Separates research use from clinical care with consent check. | S |
| Secure records & audit logs | Required for clinical trust. | M |
| Inter-rater annotation | Two reviewers annotate artifacts; agreement tracked — useful research data. | C |

---

## 3. Database schema (PostgreSQL)

Conventions: `id UUID PK`, `created_at/updated_at timestamptz`, `created_by FK users`. Soft delete via `deleted_at` where retention applies.

### 3.1 Entity-relationship overview
```
users ─< user_roles >─ roles
users ─< patient_access >─ patients
patients ─1:1─ patient_identifiers (encrypted PHI)
patients ─< medical_history_items
patients ─< clinical_events
patients ─< recordings ─< recording_channels
                     ├─< recording_conditions (1:1)
                     ├─< quality_reports
                     ├─< segment_annotations
                     └─< processing_runs ─< run_steps
                                      ├─< feature_summaries
                                      └─< predictions ─< explanations
patients ─< assessments (CRS-R) ─< assessment_items
patients ─< clinician_notes
patients ─< baseline_profiles
comparisons (run_a, run_b) ─< comparison_results
ml_models (registry mirror)
reports
jobs
audit_log
```

### 3.2 Tables

**users**
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| email | citext unique | |
| full_name | text | |
| password_hash | text | Argon2id (null if SSO) |
| is_active | bool | |
| last_login_at | timestamptz | |

**roles** (`id`, `name` unique: admin/clinician/eeg_technologist/researcher/viewer), **user_roles** (`user_id`, `role_id`), **patient_access** (`patient_id`, `user_id`, `access_level`, `granted_by`, `granted_at`).

**patients**
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| display_id | text unique | `NB-2026-000123` |
| sex | text | enum |
| birth_year | int | coarse value for researchers |
| etiology | text | enum + other |
| date_of_injury | date | |
| admission_date | date | |
| clinical_status_label | text | clinician-entered |
| status | text | active/discharged/archived |
| consent_clinical | bool | |
| consent_research | bool | |
| consent_research_date | date | |
| craniectomy | bool | affects EEG amplitude |
| tags | text[] | |

**patient_identifiers** (restricted, column-encrypted)
| column | type |
|---|---|
| patient_id | uuid PK FK |
| full_name_enc | bytea |
| date_of_birth_enc | bytea |
| mrn_enc | bytea |
| mrn_hash | bytea (HMAC for exact-match search) |
| name_search_hash | bytea (HMAC of normalised name for duplicate check) |
| guardian_contact_enc | bytea |

**medical_history_items** (`id`, `patient_id`, `category` (diagnosis/comorbidity/medication/surgery/seizure), `description`, `code_system` (ICD-10/SNOMED optional), `code`, `start_date`, `end_date`, `is_active`).

**clinical_events** (`id`, `patient_id`, `event_type` (medication_change, surgery, infection, transfer, seizure, other), `occurred_at`, `description`, `entered_by`).

**recordings**
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| patient_id | uuid FK | |
| recording_code | text | `NB-2026-000123-R03` |
| original_object_key | text | storage key (immutable) |
| deid_object_key | text | de-identified copy |
| sha256 | char(64) unique per patient | duplicate upload detection |
| file_format | text | EDF / EDF+C / EDF+D |
| start_time | timestamptz | from header (validated) |
| duration_s | numeric | |
| n_channels | int | |
| sfreq_hz | numeric | (if mixed, the EEG rate; others in channels table) |
| header_json | jsonb | full parsed header (identifying fields removed) |
| header_identity_check | text | match / mismatch / blank |
| is_baseline | bool | |
| qc_status | text | pending / approved / rejected |
| qc_signed_by / qc_signed_at | | |
| upload_status | text | uploading / parsed / failed |

**recording_conditions** (1:1 with recordings)
`condition` (resting_eyes_closed / resting_eyes_open / resting_unknown / task_motor_imagery / task_command_following / auditory_paradigm / other), `patient_state` (awake / drowsy / asleep / sedated / unknown), `sedation_agents` jsonb [{agent, dose, unit, time}], `anti_seizure_meds` jsonb, `setting` (ICU/ward/lab), `time_of_day` time, `device_model`, `electrode_system` (10-20 / 10-10 / custom), `reference_label`, `technologist_id`, `protocol_template_id`, `notes`.

**recording_channels**
`id`, `recording_id`, `index`, `original_name`, `normalized_name` (e.g., `Fp1`), `ch_type` (eeg/eog/ecg/emg/misc/stim), `sfreq_hz`, `unit`, `physical_min/max`, `prefilter`, `has_position` bool, `auto_flag` (ok/flat/noisy/clipped), `user_flag`.

**quality_reports**
`id`, `recording_id`, `run_id` nullable, `overall_score` numeric, `grade` (good/fair/poor), `metrics` jsonb (each sub-metric value + threshold + flag), `bad_channels` text[], `usable_duration_s`, `algorithm_version`.

**segment_annotations**
`id`, `recording_id`, `onset_s`, `duration_s`, `channels` text[] (null = all), `label` (artifact_eye, artifact_muscle, artifact_movement, electrode_pop, flat, excluded, selected_for_analysis, event_*), `source` (auto / user / edf_annotation), `confidence`, `author_id`.

**processing_runs**
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| recording_id | uuid FK | |
| pipeline_name | text | `resting_v1` |
| pipeline_version | text | semver `1.0.0` |
| params_json | jsonb | full resolved parameters |
| params_hash | char(64) | identical params → same hash |
| software_versions | jsonb | mne, numpy, scipy, app version |
| status | text | queued / running / succeeded / failed |
| derived_object_prefix | text | storage path |
| n_epochs_total / n_epochs_kept | int | |
| ica_excluded_components | int[] | with labels in run_steps |
| is_current | bool | which run feeds trends |
| error_message | text | |

**run_steps** (`id`, `run_id`, `step_order`, `step_name`, `params` jsonb, `started_at`, `finished_at`, `summary` jsonb) — full provenance.

**feature_summaries** (long/narrow format — scales to any number of features)
| column | type | notes |
|---|---|---|
| id | bigserial PK | |
| run_id | uuid FK | |
| channel | text | `Fp1` or region (`frontal_L`) or `global` |
| feature_name | text | e.g., `rel_power` |
| band | text nullable | `alpha` |
| value_mean | double | across epochs |
| value_median | double | |
| value_sd | double | |
| ci_low / ci_high | double | bootstrap 95 % CI across epochs |
| n_epochs | int | |
| unit | text | |
Index on (`run_id`, `feature_name`, `band`, `channel`). Epoch-level matrices stored as Parquet at `derived/{run}/features_epochs.parquet`.

**feature_definitions** (catalogue): `name`, `domain` (time/frequency/wavelet/complexity/aperiodic/connectivity), `description_plain`, `formula_ref`, `unit`, `version`.

**baseline_profiles**
`id`, `patient_id`, `recording_ids` uuid[], `condition`, `pipeline_version`, `feature_stats` jsonb (per feature: mean, within-patient SD, n), `designated_by`, `reason`, `is_active`.

**comparisons**
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| patient_id | uuid | |
| mode | text | baseline_latest / previous_latest / custom |
| run_a_id / run_b_id | uuid | processing runs (not recordings) |
| compatibility_grade | text | compatible / caveats / not_comparable |
| compatibility_report | jsonb | each check + result |
| harmonization | jsonb | common channels, excluded, resample rate, reference |
| stats_method | jsonb | bootstrap n, correction method, alpha |
| summary_text | text | template-generated, neutral |
| results_object_key | text | full results parquet |
| clinician_comment | text | |
| signed_by / signed_at | | |

**comparison_results** (`comparison_id`, `channel`, `feature_name`, `band`, `value_a`, `value_b`, `diff`, `pct_change`, `ci_low`, `ci_high`, `p_value`, `q_value`, `exceeds_sdc` bool).

**assessments**
`id`, `patient_id`, `instrument` ('CRS-R'), `assessed_at`, `assessor_id`, `setting`, `auditory`, `visual`, `motor`, `oromotor_verbal`, `communication`, `arousal` (smallint each, CHECK ranges), `total` (generated column), `clinician_category` (entered), `arousal_facilitation_used` bool, `sedation_status`, `confounders` jsonb, `observations` jsonb {auditory, visual, motor, communication} text, `nearest_recording_id` (computed link).

**assessment_items** (`assessment_id`, `subscale`, `item_code`, `item_label`, `present` bool) — item-level CRS-R detail.

**clinician_notes** (`id`, `patient_id`, `recording_id` nullable, `comparison_id` nullable, `note_type` (observation / interpretation / plan), `body`, `author_id`, `signed_at`; edits create new version rows).

**ml_models** (mirror of MLflow registry)
`id`, `name`, `version`, `task` (e.g., `mi_left_right_research`), `algorithm`, `feature_pipeline_version`, `training_datasets` jsonb, `metrics` jsonb (cross-validated, with CIs), `intended_use`, `limitations`, `required_channels` text[], `sfreq_hz`, `ood_detector_key`, `calibration_method`, `model_card_md`, `status` (staging / approved / retired), `approved_by`.

**predictions**
`id`, `run_id`, `model_id`, `input_hash`, `output` jsonb (class probabilities, conformal set), `uncertainty` jsonb, `ood_score`, `ood_flag`, `status` (ok / out_of_scope / refused), `created_by`.

**explanations** (`id`, `prediction_id`, `method` (shap_tree/shap_linear/permutation), `values` jsonb (feature → contribution), `plain_language` text (template-generated), `object_key` for plots).

**reports** (`id`, `patient_id`, `type` (pdf/csv/bids), `scope` jsonb (what was included), `object_key`, `sha256`, `status` (draft/final), `generated_by`, `signed_by`, `signed_at`).

**jobs** (`id`, `type`, `target_id`, `status`, `progress` 0–100, `message`, `celery_task_id`, `created_by`, timestamps).

**audit_log** (append-only; INSERT-only grant)
| column | type |
|---|---|
| id | bigserial |
| occurred_at | timestamptz |
| user_id | uuid |
| action | text (`patient.view`, `recording.upload`, `report.download`...) |
| entity_type / entity_id | text / uuid |
| patient_id | uuid nullable |
| ip_address | inet |
| user_agent | text |
| details | jsonb (never PHI values) |
| prev_hash / row_hash | char(64) (hash chain) |

**protocol_templates** (`id`, `name`, `condition`, `min_duration_s`, `required_channels`, `instructions`).
