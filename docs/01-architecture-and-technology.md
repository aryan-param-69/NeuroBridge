# 01 — System Architecture, Technology Choices & Folder Structure

> **Update 2026-11-19:** the database is now **MongoDB** (see [ADR 0001](adr/0001-mongodb-as-primary-database.md)). Wherever this document says PostgreSQL / SQL tables, read MongoDB collections.

Covers deliverables **1 (System architecture)** and **12 (Project folder structure)**, plus the technology justification requested in Section 4.

---

## 1. Design principles

| Principle | What it means in practice |
|---|---|
| **Scientific traceability** | Every number on screen can be traced to: the original EDF file (SHA-256 hash), the pipeline version, and the exact parameters used. |
| **Original data is immutable** | Uploaded EDF files are never modified. All processing creates new, versioned derivatives. |
| **Clinician stays in control** | The system measures and flags. It never diagnoses, never labels "recovery", and every AI output is marked *research use*. |
| **Asynchronous heavy work** | Filtering, ICA and feature extraction run in background workers so the UI never freezes. |
| **Simple first** | A modular monolith (one backend codebase, clearly separated modules) instead of microservices. Easier to build, test and deploy for a small team. |
| **Privacy by design** | Identifying information is stored separately from clinical/EEG data; EDF headers are de-identified on ingest. |

---

## 2. High-level architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         BROWSER (Clinician / Researcher)                  │
│  React + TypeScript SPA                                                   │
│  ├─ Patient & assessment UI        ├─ EEG viewer (Canvas/WebGL)           │
│  ├─ Comparison & trend charts      ├─ AI / XAI panels (research mode)     │
│  └─ Report builder                 └─ Admin / audit                       │
└───────────────▲──────────────────────────────────────────────────────────┘
                │ HTTPS (JSON + binary signal chunks), JWT access token
┌───────────────┴──────────────────────────────────────────────────────────┐
│                    API LAYER — FastAPI (Python 3.12)                      │
│  Auth & RBAC │ Patients │ Recordings │ Analysis │ Comparison │            │
│  Assessments │ ML inference │ Reports │ Audit                             │
│  (Pydantic validation, OpenAPI docs auto-generated)                       │
└──────┬───────────────┬────────────────────┬───────────────────┬──────────┘
       │ SQL           │ enqueue jobs       │ read/write files   │ load models
┌──────▼──────┐  ┌─────▼───────┐   ┌────────▼────────┐  ┌──────▼──────────┐
│ PostgreSQL  │  │ Redis       │   │ Object storage  │  │ MLflow model    │
│ (metadata,  │  │ (job queue, │   │ (MinIO / S3)    │  │ registry        │
│ features,   │  │ cache)      │   │ - original EDF  │  │ (versioned      │
│ audit log)  │  └─────▲───────┘   │ - derivatives   │  │ models + cards) │
└──────▲──────┘        │           │   (.fif,.parquet)│  └──────▲──────────┘
       │        ┌──────┴────────┐  │ - figures, PDFs │         │
       └────────┤ Celery workers├──►                 │         │
                │ MNE, NumPy,   │  └─────────────────┘         │
                │ SciPy, sklearn│──────────────────────────────┘
                │ PyWavelets,   │
                │ WeasyPrint    │
                └───────────────┘

      OFFLINE (separate from the clinical app)
      ┌───────────────────────────────────────────────────────┐
      │ ML research workspace (ml/): dataset download (MOABB), │
      │ training, nested group CV, evaluation, model cards →   │
      │ registers approved model versions in MLflow            │
      └───────────────────────────────────────────────────────┘
```

### Why training is separate from the app
Training is a research activity (needs public datasets, GPUs, experiments). The clinical app only performs **inference** with a frozen, registered, documented model. This prevents a clinician's patient data from silently being used to retrain a model, and makes every prediction reproducible ("model `mi-tangent-lr` v1.2.0 produced this").

---

## 3. Module map (backend modular monolith)

| Module | Responsibility | Depends on |
|---|---|---|
| `auth` | Login (password), JWT, roles, permissions | db |
| `audit` | Append-only event log for every read/write of patient data | db |
| `patients` | Registration, search, profile, medical history | auth, audit |
| `recordings` | Upload, hashing, de-identification, header parsing, storage | patients, storage |
| `eeg.quality` | Signal-quality scoring, bad channels, artifacts | recordings |
| `eeg.preprocess` | Filtering, re-referencing, ICA, epoching (versioned pipelines) | recordings |
| `eeg.features` | Time, frequency, wavelet, complexity features | preprocess |
| `eeg.viz` | Downsampled traces, PSD, spectrograms, topomaps | preprocess, features |
| `comparison` | Compatibility checks, harmonisation, statistical change | features |
| `assessments` | CRS-R and clinical observations | patients |
| `timeline` | Merges recordings, assessments, notes, events chronologically | all clinical modules |
| `ml` | Model registry access, inference, OOD, uncertainty | features |
| `xai` | SHAP, feature importance, explanation storage | ml |
| `reports` | PDF/CSV generation | all |

---

## 4. Technology selection and justification

### 4.1 Frontend — **React 18 + TypeScript + Vite**

| Option | Verdict | Reason |
|---|---|---|
| **React + TS** ✅ | Selected | Largest ecosystem for data-heavy apps, mature charting libraries, TypeScript catches errors in complex clinical forms. |
| Angular | Viable | Heavier, steeper learning curve; good for very large teams. |
| Vue / Svelte | Viable | Smaller ecosystem for medical-grade components and charts. |
| Streamlit / Dash | Rejected | Fast to prototype but cannot deliver premium hospital UX, fine-grained RBAC or a high-performance EEG viewer. |

Supporting libraries:

| Need | Choice | Why |
|---|---|---|
| UI components | **Mantine v7** (or shadcn/ui + Tailwind) | Accessible, clean, easy to theme to a white clinical look without looking "template-like". |
| Server state | **TanStack Query** | Caching, background refresh, job-status polling. |
| Routing | React Router | Standard. |
| Forms | React Hook Form + Zod | Strict validation for CRS-R and patient forms. |
| Analysis charts | **Apache ECharts** | Polished defaults, handles thousands of points, good for trend, box, heatmap, radar charts. |
| EEG trace viewer | **Custom Canvas 2D renderer** (WebGL upgrade later if needed) | Generic chart libraries are too slow and don't behave like clinical EEG viewers (gain in µV, timebase, montage switching, stacked channels). |
| Topomaps | Server-computed by MNE (SVG) + client display | MNE's interpolation is scientifically validated; reimplementing it in JS is error-prone. |

### 4.2 Backend — **FastAPI (Python 3.12)**

| Option | Verdict | Reason |
|---|---|---|
| **FastAPI** ✅ | Selected | Same language as MNE/NumPy/SciPy/scikit-learn — no cross-language bridge. Automatic OpenAPI docs, Pydantic validation, async I/O. |
| Django + DRF | Viable | Excellent admin and auth, but heavier; async story weaker. Good alternative if team knows Django. |
| Flask | Rejected | Too much assembled by hand (validation, docs). |
| Node/Express | Rejected | EEG processing would need a separate Python service anyway. |

Supporting: **SQLAlchemy 2.0** (ORM), **Alembic** (migrations), **Pydantic v2** (schemas), **Celery + Redis** (background jobs), **WeasyPrint + Jinja2** (PDF reports from HTML templates).

### 4.3 Database — **PostgreSQL 16** (not MongoDB)

| Criterion | PostgreSQL | MongoDB |
|---|---|---|
| Data shape | Patients → recordings → runs → features → comparisons is **strongly relational** | Better for loosely-structured documents |
| Integrity | Foreign keys, constraints, ACID transactions (critical for clinical records) | Weaker relational guarantees |
| Flexible fields | `JSONB` columns for pipeline parameters, header metadata | Native |
| Security | Row-level security, mature auditing extensions (pgAudit) | Available but less standard in hospital IT |
| Analytics | SQL window functions for trends | Aggregation pipeline |

**Decision:** PostgreSQL gives relational integrity *and* JSON flexibility. MongoDB adds no capability we need and loses integrity guarantees.

**Large numerical data is NOT stored in the database.** Epoch-level feature matrices go to **Parquet** files in object storage; the database stores summary values and file pointers. Raw signals stay in EDF / MNE `.fif` derivatives.

### 4.4 EEG and scientific stack

| Library | Purpose | Why |
|---|---|---|
| **MNE-Python** | EDF reading, filtering, ICA, epoching, montages, PSD, topomaps | The de-facto open standard for EEG/MEG research (Gramfort et al., 2013); peer-reviewed, actively maintained. |
| **NumPy / SciPy** | Arrays, Welch PSD, statistics, signal filters | Foundation of scientific Python. |
| **PyWavelets** | Discrete/continuous wavelet transforms | Standard, fast. |
| **mne-icalabel** | Suggests ICA component labels (eye, muscle, heart) | Python port of ICLabel (Pion-Tonachini et al., 2019). Suggestions only — human confirms. |
| **autoreject** | Data-driven epoch rejection thresholds | Jas et al., 2017; avoids arbitrary fixed µV thresholds. |
| **pyprep** (optional) | Robust bad-channel detection (PREP pipeline) | Bigdely-Shamlo et al., 2015. |
| **specparam (FOOOF)** | Separates periodic peaks from aperiodic 1/f slope | Donoghue et al., 2020; aperiodic exponent is relevant to consciousness research. |
| **antropy** | Entropy & complexity (permutation, spectral, Lempel-Ziv) | Lightweight, tested. |
| **pyRiemann** | Covariance / Riemannian features for motor imagery | Strong cross-subject MI baseline (Barachant et al., 2012). |
| **scikit-learn, XGBoost, LightGBM** | Classical ML | Standard, interpretable, CPU-friendly. |
| **PyTorch + braindecode** | EEGNet and other EEG deep nets | Reference implementations of EEGNet/ShallowConvNet. |
| **MOABB** | Standardised download + benchmarking of public MI datasets | Avoids hand-written dataset loaders and allows fair comparison. |
| **SHAP** | Explanations | Standard tree/linear explainers. |
| **MAPIE** | Conformal prediction (uncertainty) | Distribution-free uncertainty sets. |
| **MLflow** | Model versioning, metrics, artifacts | Open-source, self-hostable model registry. |

### 4.5 File storage — **MinIO (S3-compatible)**
- Local/on-premise deployments run MinIO; cloud deployments switch to S3/Azure Blob with no code change.
- Server-side encryption at rest; access only through the API (pre-signed URLs never exposed for originals).
- Layout: `raw/{patient_uuid}/{recording_uuid}/original.edf` (immutable, versioning on), `derived/{recording_uuid}/{run_uuid}/...`.

### 4.6 Authentication & access control
- **Phase 1:** Built-in OAuth2 password flow → short-lived JWT access token (15 min) + rotating refresh token in httpOnly secure cookie; passwords hashed with **Argon2id**; the originally planned TOTP second factor was removed (password-only login).
- **Production hospital deployment:** swap to **Keycloak** / hospital SSO (OIDC/SAML) without changing the RBAC model.
- **RBAC roles:**

| Role | Can do |
|---|---|
| `admin` | Manage users, roles, settings; view audit log. No default access to clinical content. |
| `clinician` | Full patient, recording, assessment, notes, reports; sign off QC. |
| `eeg_technologist` | Upload recordings, run preprocessing, annotate, QC; read patient summary. |
| `researcher` | Access **pseudonymised** data and AI research module only; no identifiers. |
| `viewer` | Read-only clinical summaries. |

- Patient-level access can be restricted to a care team (`patient_access` table + PostgreSQL row-level security).

### 4.7 Security & privacy controls
- TLS everywhere; HSTS; strict CSP headers.
- PHI separation: names, dates of birth, hospital numbers live in `patient_identifiers`, encrypted at the column level (pgcrypto / application-level envelope encryption).
- **EDF de-identification on ingest:** EDF headers contain patient name/ID/birthdate fields. These are read once, compared with the registered patient (mismatch warning), then blanked in the stored derivative; the original is kept encrypted and access-restricted.
- Audit log: append-only, hash-chained (each row stores hash of previous row) to make tampering detectable.
- Rate limiting, upload size limits, file-type validation (magic bytes + MNE parse), antivirus scan hook.
- Compliance targets: designed to *support* HIPAA / GDPR / local data-protection law; actual compliance depends on deployment and institutional governance.

### 4.8 Deployment
- **Docker Compose** for development and single-hospital deployment: `frontend`, `api`, `worker`, `postgres`, `redis`, `minio`, `mlflow`.
- CI: GitHub Actions — lint, type check, tests, build images, dependency vulnerability scan.
- No Kubernetes initially (unnecessary complexity).

---

## 5. Project folder structure (deliverable 12)

```
neurobridge-ai/
├── README.md
├── docker-compose.yml
├── .env.example
├── docs/                         # this design specification, ADRs, model cards
│   ├── adr/                      # Architecture Decision Records
│   └── model-cards/
├── backend/
│   ├── pyproject.toml
│   ├── alembic/                  # DB migrations
│   ├── app/
│   │   ├── main.py               # FastAPI app factory
│   │   ├── core/                 # config, security, logging, permissions
│   │   ├── db/                   # session, base, row-level security helpers
│   │   ├── models/               # SQLAlchemy ORM models
│   │   ├── schemas/              # Pydantic request/response models
│   │   ├── api/v1/
│   │   │   ├── auth.py  patients.py  recordings.py  analysis.py
│   │   │   ├── comparisons.py  assessments.py  timeline.py
│   │   │   ├── ml.py  reports.py  audit.py  admin.py
│   │   ├── services/             # business logic (no HTTP here)
│   │   ├── eeg/
│   │   │   ├── io.py             # EDF reading, header parsing, de-identification
│   │   │   ├── channels.py       # name normalisation, 10-20 mapping
│   │   │   ├── quality.py        # signal-quality metrics & score
│   │   │   ├── preprocess.py     # versioned pipeline steps
│   │   │   ├── epochs.py
│   │   │   ├── features/         # time.py, spectral.py, wavelet.py, complexity.py, aperiodic.py
│   │   │   ├── viz.py            # decimation, spectrogram, topomap
│   │   │   └── pipelines/        # YAML pipeline definitions (v1.0.0, ...)
│   │   ├── comparison/           # compatibility.py, harmonize.py, stats.py, summary.py
│   │   ├── ml/                   # registry.py, inference.py, ood.py, uncertainty.py
│   │   ├── xai/                  # shap_explain.py, importance.py
│   │   ├── reports/              # templates/ (Jinja2 HTML), pdf.py, csv.py
│   │   ├── audit/
│   │   └── workers/              # celery_app.py, tasks/*.py
│   └── tests/
│       ├── unit/  integration/  api/  pipeline_golden/  fixtures/
├── frontend/
│   ├── package.json  vite.config.ts  tsconfig.json
│   └── src/
│       ├── app/                  # router, providers, auth guard
│       ├── theme/                # design tokens, Mantine theme
│       ├── api/                  # typed client generated from OpenAPI
│       ├── components/           # shared UI: PatientCard, StatTile, QualityBadge...
│       ├── features/
│       │   ├── patients/  recordings/  eeg-viewer/  analysis/
│       │   ├── comparison/  progress/  assessments/  ai/  reports/  admin/
│       ├── charts/               # ECharts wrappers with house style
│       └── tests/
├── ml/                           # OFFLINE research workspace (not deployed with app)
│   ├── configs/                  # experiment YAMLs
│   ├── data/                     # gitignored; datasets downloaded via MOABB
│   ├── src/                      # datasets.py, features.py, models.py, cv.py, evaluate.py
│   ├── notebooks/                # exploratory only, never the source of reported results
│   └── reports/                  # generated evaluation reports + model cards
└── infra/
    ├── nginx/  minio/  postgres/
    └── ci/
```
