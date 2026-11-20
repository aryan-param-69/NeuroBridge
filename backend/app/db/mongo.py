"""MongoDB connection and indexes.

Collections
  users, refresh_tokens, audit_log, counters
  patients               non-identifying data + care_team + hashed search keys
  patient_identifiers    ENCRYPTED name / date of birth / MRN / guardian contact (one doc per patient)
  patient_field_history, medical_history_items, clinical_events
  recordings             EDF metadata, channel table, provisional quality flags, overview (files live in storage/)
  processing_runs        versioned pre-processing runs: params, hash, steps, quality report, epoch table, QC state
  feature_runs, feature_summaries   per-run feature extraction status and long-format summaries (mean/median/SD/95 % CI)
  assessments, clinician_notes   versioned rows: a signed assessment is never edited, an amendment is a new version
  reports                versioned clinical summaries (snapshot + clinician text; signed = immutable; SHA-256 per version)
  ai_predictions         immutable model outputs (model version + SHA-256, reliability fields, explanation)
  jobs, segment_annotations   background job status; user/auto artefact and selection segments
  upload_sessions        chunked uploads in progress (filenames are never stored: they often contain names)

Notes
- Dates are stored as ISO strings ("2026-02-02") and timestamps as UTC datetimes.
- A standalone mongod has no multi-document transactions; services order their writes so a failure
  can never leave a patient visible without its identifiers (see services/patients.py).
"""

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from app.core.config import get_settings

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(
            get_settings().mongo_url, uuidRepresentation="standard", tz_aware=True,
            serverSelectionTimeoutMS=5000,
        )
    return _client


def get_db() -> Database:
    """FastAPI dependency and general accessor."""
    return get_client()[get_settings().mongo_db]


def ensure_indexes(db: Database) -> None:
    db.users.create_index("email", unique=True)
    db.refresh_tokens.create_index("token_hash", unique=True)
    db.refresh_tokens.create_index("user_id")
    db.audit_log.create_index("seq", unique=True)  # also serialises concurrent chain appends
    db.audit_log.create_index("user_id")
    db.audit_log.create_index("patient_id")
    db.audit_log.create_index("action")
    p = db.patients
    p.create_index("display_id", unique=True)
    p.create_index([("created_at", DESCENDING), ("_id", ASCENDING)])
    p.create_index("care_team.user_id")
    p.create_index("status")
    p.create_index("etiology")
    p.create_index("date_of_injury")
    p.create_index("consent_research")
    p.create_index("search.identity_hash")
    p.create_index("search.mrn_hash")
    p.create_index("search.name_tokens")  # multikey: every hashed name prefix
    db.patient_field_history.create_index([("patient_id", ASCENDING), ("changed_at", DESCENDING)])
    db.medical_history_items.create_index("patient_id")
    db.recordings.create_index([("patient_id", ASCENDING), ("sha256", ASCENDING)], unique=True)
    db.recordings.create_index([("patient_id", ASCENDING), ("start_time", DESCENDING)])
    db.upload_sessions.create_index([("user_id", ASCENDING), ("status", ASCENDING)])
    db.processing_runs.create_index([("recording_id", ASCENDING), ("created_at", DESCENDING)])
    db.processing_runs.create_index([("patient_id", ASCENDING), ("qc_status", ASCENDING), ("is_current", ASCENDING)])
    db.feature_runs.create_index("recording_id")
    db.feature_summaries.create_index([("run_id", ASCENDING), ("level", ASCENDING), ("feature", ASCENDING), ("band", ASCENDING),
                                       ("name", ASCENDING)])
    db.assessments.create_index([("assessment_id", ASCENDING), ("version", ASCENDING)], unique=True)
    db.assessments.create_index([("patient_id", ASCENDING), ("assessed_at", DESCENDING)])
    db.clinician_notes.create_index([("note_id", ASCENDING), ("version", ASCENDING)], unique=True)
    db.clinician_notes.create_index([("patient_id", ASCENDING), ("created_at", DESCENDING)])
    db.reports.create_index([("report_id", ASCENDING), ("version", ASCENDING)], unique=True)
    db.reports.create_index([("patient_id", ASCENDING), ("created_at", DESCENDING)])
    db.ai_predictions.create_index([("run_id", ASCENDING), ("created_at", DESCENDING)])
    db.ai_predictions.create_index([("patient_id", ASCENDING), ("created_at", DESCENDING)])
    db.jobs.create_index("created_by")
    db.segment_annotations.create_index("recording_id")
    db.clinical_events.create_index([("patient_id", ASCENDING), ("occurred_at", DESCENDING)])
