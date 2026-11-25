# 06 — API Endpoint Specification

Covers deliverable **11**. REST, JSON, base path `/api/v1`. OpenAPI generated automatically by FastAPI; the frontend client is generated from it.

**Conventions**
- Auth: `Authorization: Bearer <access JWT>`; refresh via httpOnly cookie.
- IDs: UUIDs in paths. Pagination: `?page=1&page_size=25` → `{items, total, page, page_size}`.
- Errors: RFC 7807 problem JSON `{type, title, status, detail, errors[]}`.
- Long operations return `202 Accepted` + `{job_id}`; poll `GET /jobs/{id}` (or SSE `GET /jobs/{id}/events`).
- Every endpoint touching patient data writes an audit event.
- Roles: **A** admin, **C** clinician, **T** technologist, **R** researcher (pseudonymised only), **V** viewer.

---

## Auth & users
| Method | Path | Purpose | Roles |
|---|---|---|---|
| POST | `/auth/login` | Email + password → access token + refresh cookie (no second factor) | public |
| POST | `/auth/refresh` | Rotate tokens | authenticated |
| POST | `/auth/logout` | Revoke refresh token | authenticated |
| GET | `/auth/me` | Current user, roles, permissions | all |
| GET/POST | `/admin/users` | List / create users | A |
| PATCH | `/admin/users/{id}` | Update roles, deactivate | A |

## Patients
| Method | Path | Purpose | Roles |
|---|---|---|---|
| GET | `/patients` | Search: `q, status, etiology, clinician_id, injury_from, injury_to` | C T V (R: pseudonymised) |
| POST | `/patients` | Register (returns `display_id`); `409` with `duplicates[]` unless `confirm_duplicate=true` | C |
| GET | `/patients/{id}` | Profile (identifiers per role) | C T V R* |
| PATCH | `/patients/{id}` | Edit (field history recorded) | C |
| GET | `/patients/{id}/history` | Field change history | C |
| GET/POST | `/patients/{id}/medical-history` | Items | C |
| PATCH/DELETE | `/patients/{id}/medical-history/{item_id}` | Update / soft-delete | C |
| GET/POST | `/patients/{id}/events` | Clinical events | C |
| GET/PUT | `/patients/{id}/access` | Care team | C A |
| GET | `/patients/{id}/timeline` | Merged chronological items: `types=recording,assessment,note,event`, `from,to` | C T V |

**POST /patients — request**
```json
{
  "full_name": "string", "date_of_birth": "1972-04-10", "sex": "male",
  "mrn": "string|null", "etiology": "traumatic", "date_of_injury": "2026-02-02",
  "admission_date": "2026-02-02", "craniectomy": false,
  "consent_clinical": true, "consent_research": false
}
```
**Response 201** `{ "id": "uuid", "display_id": "NB-2026-000123", ... }`

## Recordings
| Method | Path | Purpose | Roles |
|---|---|---|---|
| POST | `/patients/{id}/recordings/uploads` | Start chunked upload → `{upload_id, chunk_size}` | C T |
| PUT | `/uploads/{upload_id}/chunks/{n}` | Upload chunk | C T |
| POST | `/uploads/{upload_id}/complete` | `{sha256}` → verifies, parses header → `{recording_id, header_summary, identity_check, channel_warnings}` | C T |
| GET | `/patients/{id}/recordings` | List | C T V R* |
| GET | `/recordings/{id}` | Metadata, header summary, condition, QC status | C T V R* |
| PUT | `/recordings/{id}/condition` | Recording context (condition, state, sedation...) | C T |
| GET | `/recordings/{id}/channels` | Channel table | C T V |
| PATCH | `/recordings/{id}/channels` | Correct types/names, mark bad | C T |
| POST | `/recordings/{id}/baseline` | Set/unset baseline `{is_baseline, reason}` | C |
| GET | `/recordings/{id}/quality` | Latest quality report | C T V |
| GET/POST | `/recordings/{id}/annotations` | Segment annotations | C T |
| DELETE | `/recordings/{id}/annotations/{ann_id}` | Remove user annotation | C T |
| GET | `/recordings/{id}/download` | Original (C only, audited) or de-identified | C (R: de-identified, consented) |

## Processing
| Method | Path | Purpose | Roles |
|---|---|---|---|
| GET | `/pipelines` | Available pipeline definitions & defaults | all |
| POST | `/recordings/{id}/runs` | `{pipeline_name, pipeline_version, overrides?}` → `202 {job_id, run_id}` | C T |
| GET | `/recordings/{id}/runs` | Runs list | C T V |
| GET | `/runs/{run_id}` | Run detail incl. steps, params, software versions | C T V R* |
| GET | `/runs/{run_id}/ica` | Components: topomap URLs, ICLabel probabilities | C T |
| PUT | `/runs/{run_id}/ica/exclusions` | `{components: [0,3]}` → re-runs downstream steps (`202`) | C T |
| POST | `/runs/{run_id}/qc` | `{decision: approve|reject, note}` | C T |
| POST | `/runs/{run_id}/current` | Mark run as current for trends | C |
| GET | `/jobs/{job_id}` | `{status, progress, message, result_ref}` | owner, C |

## Signals & visualisation
| Method | Path | Purpose |
|---|---|---|
| GET | `/recordings/{id}/signal` | `stage=raw|processed&run_id&start=120.0&duration=10&channels=Fp1,Fp2&montage=average|bipolar_longitudinal|reference&max_points=2000` → binary (Float32 min/max pairs) or JSON `{sfreq_eff, channels[], data[][], annotations[]}` |
| GET | `/recordings/{id}/overview` | Whole-recording decimated envelope + quality heat bar |
| GET | `/runs/{run_id}/psd` | `channels, fmin, fmax, aggregate=none|region|global` → freqs[], psd[][] (+ CI) |
| GET | `/runs/{run_id}/spectrogram` | `channel, fmax` → times[], freqs[], power[][] |
| GET | `/runs/{run_id}/topomap` | `band, kind=absolute|relative, format=svg|json` |
| GET | `/runs/{run_id}/features` | `names, bands, channels, level=channel|region|global` → rows with mean, median, sd, ci |
| GET | `/runs/{run_id}/features/export.csv` | Long-format CSV |
| GET | `/runs/{run_id}/aperiodic` | Fit params & quality per channel |
| GET | `/features/catalog` | Feature definitions with plain-language descriptions |

## Comparisons & trends
| Method | Path | Purpose |
|---|---|---|
| POST | `/patients/{id}/comparisons/check` | `{run_a_id, run_b_id}` → compatibility report only (fast, no job) |
| POST | `/patients/{id}/comparisons` | `{mode, run_a_id?, run_b_id?, features?, override_reason?}` → `202 {job_id, comparison_id}` (auto-resolves runs for `baseline_latest`/`previous_latest`; auto-reprocesses if pipeline versions differ) |
| GET | `/patients/{id}/comparisons` | List |
| GET | `/comparisons/{id}` | Compatibility, harmonisation, summary text, status |
| GET | `/comparisons/{id}/results` | Filterable rows (channel, feature, band, Δ, CI, q, exceeds_sdc) |
| GET | `/comparisons/{id}/topomap` | `band, which=a|b|diff` |
| PATCH | `/comparisons/{id}` | Clinician comment |
| POST | `/comparisons/{id}/sign` | Sign off |
| GET | `/patients/{id}/trends` | `features=rel_power:alpha,aperiodic_exponent&level=global&only_comparable=true` → series with CI, condition, quality, sedation per point + SDC band |
| GET/POST | `/patients/{id}/baseline-profiles` | View / create baseline profile from recordings |

**Compatibility report example**
```json
{
  "grade": "caveats",
  "checks": [
    {"name": "shared_channels", "result": "caveat", "detail": "17/19 shared", "excluded": ["T7","O2"]},
    {"name": "condition", "result": "pass", "detail": "resting_eyes_closed both"},
    {"name": "sedation", "result": "pass"},
    {"name": "time_of_day", "result": "caveat", "detail": "4.1 h apart"},
    {"name": "pipeline_version", "result": "pass", "detail": "resting_v1.0.0"}
  ]
}
```

## Assessments & notes
| Method | Path | Purpose | Roles |
|---|---|---|---|
| GET | `/patients/{id}/assessments` | List (CRS-R) | C T V R* |
| POST | `/patients/{id}/assessments` | Create (validated ranges; items) | C |
| GET/PATCH | `/assessments/{id}` | Detail / edit draft | C |
| POST | `/assessments/{id}/sign` | Sign (immutable afterwards; amendments create versions) | C |
| GET | `/patients/{id}/assessments/trend` | Totals & subscales over time | C V |
| GET | `/patients/{id}/eeg-assessment-pairs` | Time-aligned pairs within window `±hours` | C |
| GET/POST | `/patients/{id}/notes` | Clinician notes | C |
| POST | `/notes/{id}/versions` | Amend note | C |

## ML & XAI (research)
| Method | Path | Purpose | Roles |
|---|---|---|---|
| GET | `/models` | Registered models (`status=approved`) | C R A |
| GET | `/models/{id}` | Model card, metrics, required inputs | C R A |
| POST | `/models/{id}/status` | Approve / retire | A (+ designated reviewer) |
| POST | `/runs/{run_id}/predictions` | `{model_id}` → scope check; `200 {status: out_of_scope, reasons[]}` or `202 {job_id, prediction_id}` | C R |
| GET | `/predictions/{id}` | Output, calibrated probabilities, conformal set, OOD score, versions | C R |
| GET | `/predictions/{id}/explanation` | SHAP values, plain-language contributions, pattern topomap | C R |
| POST | `/runs/{run_id}/command-following` | Within-patient permutation analysis (task recordings) → `202` | C R |
| GET | `/command-following/{id}` | AUC, permutation p, null distribution, EMG check | C R |
| GET | `/research/cohort/associations` | Exploratory mixed-model results (consented, pseudonymised) | R |

## Reports
| Method | Path | Purpose |
|---|---|---|
| POST | `/patients/{id}/reports` | `{type: pdf|csv, sections[], date_from, date_to, identifier_level}` → `202 {job_id, report_id}` |
| GET | `/patients/{id}/reports` | List |
| GET | `/reports/{id}` | Metadata (status, hash) |
| GET | `/reports/{id}/download` | File (audited) |
| POST | `/reports/{id}/sign` | Finalise |

## Audit & system
| Method | Path | Purpose | Roles |
|---|---|---|---|
| GET | `/audit` | Filter `user_id, patient_id, action, from, to` | A |
| GET | `/audit/verify` | Hash-chain integrity check | A |
| GET | `/admin/settings` / PUT | Site settings (line freq, bands, protocols) | A |
| GET | `/health` | Liveness | public |
| GET | `/version` | App, pipeline, model versions | authenticated |

## Non-functional targets
| Endpoint class | Target (p95) |
|---|---|
| CRUD / lists | < 200 ms |
| `/signal` 10 s × 64 ch window | < 300 ms |
| Compatibility check | < 1 s |
| Preprocessing 30 min × 32 ch @ 256 Hz (no ICA) | < 2 min background |
| PDF report | < 30 s background |
