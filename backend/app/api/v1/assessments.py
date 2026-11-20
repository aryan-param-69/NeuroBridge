"""CRS-R assessments: versioned drafts, signing, amendments, trend, repeated-assessment reminder, EEG pairing.

Rules:
* A SIGNED assessment is never modified. Corrections create a new version (amendment) with a reason.
* The total is always computed on the server from the six subscale scores.
* The clinician's category is entered by the clinician; the item-pattern check is only a prompt.
* Researchers see pseudonymised scores only (no dates, assessor or free text).
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pymongo.database import Database

from app.api.deps import CLINICIAN, RESEARCHER, TECHNOLOGIST, VIEWER, audit_patient_access, require_roles
from app.api.v1.patients import load_patient
from app.core.config import get_settings
from app.db.base import edf_local_to_utc, utcnow
from app.db.mongo import get_db
from app.models import User
from app.services import crsr
from app.services import users as users_repo

router = APIRouter(tags=["assessments"])
READERS = (CLINICIAN, TECHNOLOGIST, VIEWER)
read_clin = require_roles(*READERS)
read_any = require_roles(*READERS, RESEARCHER)
write_clin = require_roles(CLINICIAN)


class Scores(BaseModel):
    model_config = ConfigDict(extra="forbid")  # e.g. a client-sent 'total' is refused, never trusted

    auditory: Annotated[int, Field(strict=True, ge=0, le=4)]
    visual: Annotated[int, Field(strict=True, ge=0, le=5)]
    motor: Annotated[int, Field(strict=True, ge=0, le=6)]
    oromotor_verbal: Annotated[int, Field(strict=True, ge=0, le=3)]
    communication: Annotated[int, Field(strict=True, ge=0, le=2)]
    arousal: Annotated[int, Field(strict=True, ge=0, le=3)]


class Observations(BaseModel):
    model_config = ConfigDict(extra="forbid")  # e.g. a client-sent 'total' is refused, never trusted

    auditory: str | None = Field(default=None, max_length=2000)
    visual: str | None = Field(default=None, max_length=2000)
    motor: str | None = Field(default=None, max_length=2000)
    communication: str | None = Field(default=None, max_length=2000)


Category = Literal["UWS/VS", "MCS-", "MCS+", "eMCS", "other"]
Confounder = Literal["aphasia", "motor_impairment", "visual_impairment", "hearing_impairment", "fatigue", "pain", "seizure",
                     "infection", "medication_effects", "other"]


class AssessmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # e.g. a client-sent 'total' is refused, never trusted

    assessed_at: AwareDatetime
    setting: Literal["icu", "ward", "lab", "home", "other"] | None = None
    scores: Scores
    arousal_facilitation_used: bool = False
    sedation_status: Literal["none", "reduced", "ongoing", "unknown"] = "unknown"
    sedation_note: str | None = Field(default=None, max_length=300)
    confounders: list[Confounder] = Field(default_factory=list, max_length=10)
    observations: Observations = Field(default_factory=Observations)
    clinician_category: Category | None = None
    category_note: str | None = Field(default=None, max_length=500)


class AssessmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")  # e.g. a client-sent 'total' is refused, never trusted

    assessed_at: AwareDatetime | None = None
    setting: Literal["icu", "ward", "lab", "home", "other"] | None = None
    scores: Scores | None = None
    arousal_facilitation_used: bool | None = None
    sedation_status: Literal["none", "reduced", "ongoing", "unknown"] | None = None
    sedation_note: str | None = Field(default=None, max_length=300)
    confounders: list[Confounder] | None = Field(default=None, max_length=10)
    observations: Observations | None = None
    clinician_category: Category | None = None
    category_note: str | None = Field(default=None, max_length=500)


class SignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")  # e.g. a client-sent 'total' is refused, never trusted

    amendment_reason: str | None = Field(default=None, max_length=500)


# ---- helpers --------------------------------------------------------------------------------------------------------

def _patient(db: Database, user: User, pid: uuid.UUID, *, write: bool = False) -> dict:
    return load_patient(db, user, pid, write=write)


def _check_time(patient: dict, when: datetime) -> None:
    if when > utcnow() + timedelta(minutes=5):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The assessment time cannot be in the future")
    inj = patient.get("date_of_injury")
    if inj and when.date() < date.fromisoformat(inj):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The assessment cannot be dated before the date of injury")


def _latest(docs: list[dict], *, signed_only: bool) -> list[dict]:
    """Highest version per assessment (among signed versions only when signed_only)."""
    best: dict = {}
    for d in docs:
        if signed_only and d["status"] != "signed":
            continue
        cur = best.get(d["assessment_id"])
        if cur is None or d["version"] > cur["version"]:
            best[d["assessment_id"]] = d
    return sorted(best.values(), key=lambda d: d["assessed_at"], reverse=True)


def _nearest_recording(recs: list[dict], when: datetime, hours: float) -> dict | None:
    tz = get_settings().site_timezone
    best = None
    for r in recs:
        if not r.get("start_time"):
            continue
        delta = (edf_local_to_utc(r["start_time"], tz) - when).total_seconds() / 3600
        if abs(delta) <= hours and (best is None or abs(delta) < abs(best[1])):
            best = (r, delta)
    return None if best is None else {"id": best[0]["_id"], "recording_code": best[0]["recording_code"], "hours_from_assessment": round(best[1], 1)}


def _out(d: dict, names: dict, recs: list[dict] | None = None, *, with_text: bool = True) -> dict:
    s = d["scores"]
    out = {
        "id": d["_id"], "assessment_id": d["assessment_id"], "version": d["version"], "status": d["status"],
        "assessed_at": d["assessed_at"], "scores": s, "total": d["total"], "max_total": crsr.MAX_TOTAL,
        "setting": d.get("setting"), "arousal_facilitation_used": d.get("arousal_facilitation_used", False),
        "sedation_status": d.get("sedation_status"), "sedation_note": d.get("sedation_note"),
        "confounders": d.get("confounders", []), "clinician_category": d.get("clinician_category"),
        "category_note": d.get("category_note"), "pattern": crsr.pattern_check(s, d.get("clinician_category")),
        "assessor": {"id": d["assessor_id"], "name": names.get(d["assessor_id"], "Unknown")},
        "signed_by": names.get(d.get("signed_by")) if d.get("signed_by") else None, "signed_at": d.get("signed_at"),
        "amends": d.get("amends"), "amendment_reason": d.get("amendment_reason"), "created_at": d["created_at"],
        "nearest_recording": _nearest_recording(recs, d["assessed_at"], 48) if recs is not None else None,
    }
    if with_text:
        out["observations"] = d.get("observations", {})
    return out


def _research_out(d: dict, patient: dict) -> dict:
    inj = patient.get("date_of_injury")
    day = (d["assessed_at"].date() - date.fromisoformat(inj)).days if inj else None
    return {"id": d["_id"], "scores": d["scores"], "total": d["total"], "clinician_category": d.get("clinician_category"),
            "days_since_injury": day}


def _names(db: Database, docs: list[dict]) -> dict:
    ids = list({i for d in docs for i in (d.get("assessor_id"), d.get("signed_by")) if i})
    return {u.id: u.full_name for u in users_repo.get_many(db, ids, active_only=False).values()}


def _load(db: Database, user: User, assessment_id: uuid.UUID, *, write: bool = False) -> tuple[dict, dict]:
    d = db.assessments.find_one({"_id": assessment_id})
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found")
    patient = _patient(db, user, d["patient_id"], write=write)
    if d["status"] == "draft" and CLINICIAN not in user.role_names:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found")  # drafts are visible to clinicians only
    return d, patient


def _must_be_draft(d: dict) -> None:
    if d["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "A signed assessment cannot be changed. Amend it to create a new version.")


def _must_be_author(d: dict, user: User) -> None:
    if d["assessor_id"] != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the clinician who started this draft can change or sign it")


# ---- definition -------------------------------------------------------------------------------------------------------

@router.get("/assessments/crsr-definition")
def crsr_definition(_: User = Depends(read_any)):
    return crsr.definition()


# ---- list / create ------------------------------------------------------------------------------------------------------

@router.get("/patients/{patient_id}/assessments")
def list_assessments(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(read_any)):
    patient = _patient(db, user, patient_id)
    docs = list(db.assessments.find({"patient_id": patient_id}))
    audit_patient_access(request, db, user, "assessment.list", patient_id)
    if set(user.role_names) <= {RESEARCHER}:
        return [_research_out(d, patient) for d in _latest(docs, signed_only=True)]
    names = _names(db, docs)
    recs = list(db.recordings.find({"patient_id": patient_id}, {"start_time": 1, "recording_code": 1}))
    rows = _latest(docs, signed_only=CLINICIAN not in user.role_names)
    return [_out(d, names, recs, with_text=False) for d in rows]


@router.post("/patients/{patient_id}/assessments", status_code=status.HTTP_201_CREATED)
def create_assessment(patient_id: uuid.UUID, body: AssessmentIn, request: Request, db: Database = Depends(get_db),
                      user: User = Depends(write_clin)):
    patient = _patient(db, user, patient_id, write=True)
    _check_time(patient, body.assessed_at)
    scores = body.scores.model_dump()
    aid = uuid.uuid4()
    doc = {"_id": uuid.uuid4(), "assessment_id": aid, "version": 1, "status": "draft", "patient_id": patient_id,
           "instrument": "CRS-R", "instrument_version": crsr.CRSR_VERSION, "assessor_id": user.id, "created_at": utcnow(),
           **body.model_dump(exclude={"scores"}), "scores": scores, "total": crsr.total(scores),
           "signed_by": None, "signed_at": None, "amends": None, "amendment_reason": None}
    db.assessments.insert_one(doc)
    audit_patient_access(request, db, user, "assessment.create", patient_id, {"assessment": str(aid), "version": 1})
    return _out(doc, {user.id: user.full_name})


# ---- one assessment -----------------------------------------------------------------------------------------------------

@router.get("/assessments/{assessment_id}")
def get_assessment(assessment_id: uuid.UUID, request: Request, db: Database = Depends(get_db),
                   user: User = Depends(read_clin)):
    d, _ = _load(db, user, assessment_id)
    versions = list(db.assessments.find({"assessment_id": d["assessment_id"]}).sort("version", 1))
    if CLINICIAN not in user.role_names:
        versions = [v for v in versions if v["status"] == "signed"]
    names = _names(db, versions)
    recs = list(db.recordings.find({"patient_id": d["patient_id"]}, {"start_time": 1, "recording_code": 1}))
    audit_patient_access(request, db, user, "assessment.view", d["patient_id"], {"assessment": str(d["assessment_id"])})
    return {**_out(d, names, recs), "versions": [
        {"id": v["_id"], "version": v["version"], "status": v["status"], "signed_at": v.get("signed_at"),
         "total": v["total"], "amendment_reason": v.get("amendment_reason")} for v in versions]}


@router.patch("/assessments/{assessment_id}")
def edit_draft(assessment_id: uuid.UUID, body: AssessmentPatch, request: Request, db: Database = Depends(get_db),
               user: User = Depends(write_clin)):
    d, patient = _load(db, user, assessment_id, write=True)
    _must_be_draft(d)
    _must_be_author(d, user)
    changes = body.model_dump(exclude_unset=True)
    if "assessed_at" in changes and changes["assessed_at"]:
        _check_time(patient, changes["assessed_at"])
    if "scores" in changes and changes["scores"]:
        changes["total"] = crsr.total(changes["scores"])
    for k in ("assessed_at", "scores"):
        if k in changes and changes[k] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{k} cannot be cleared")
    if changes:
        db.assessments.update_one({"_id": assessment_id, "status": "draft"}, {"$set": changes})
    audit_patient_access(request, db, user, "assessment.edit_draft", d["patient_id"],
                         {"assessment": str(d["assessment_id"]), "fields": sorted(changes)})
    return _out(db.assessments.find_one({"_id": assessment_id}), {user.id: user.full_name})


@router.delete("/assessments/{assessment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(assessment_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(write_clin)):
    d, _ = _load(db, user, assessment_id, write=True)
    _must_be_draft(d)
    _must_be_author(d, user)
    db.assessments.delete_one({"_id": assessment_id, "status": "draft"})
    audit_patient_access(request, db, user, "assessment.delete_draft", d["patient_id"], {"assessment": str(d["assessment_id"])})


@router.post("/assessments/{assessment_id}/sign")
def sign(assessment_id: uuid.UUID, body: SignBody, request: Request, db: Database = Depends(get_db), user: User = Depends(write_clin)):
    d, _ = _load(db, user, assessment_id, write=True)
    _must_be_draft(d)
    _must_be_author(d, user)
    if not d.get("clinician_category"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter the clinical category before signing (it is your judgement; it is never filled in automatically)")
    if d["clinician_category"] == "other" and not (d.get("category_note") or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Describe the category in the note when 'other' is chosen")
    if d["version"] > 1 and len((body.amendment_reason or "").strip()) < 5:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give the reason for the amendment")
    sets = {"status": "signed", "signed_by": user.id, "signed_at": utcnow()}
    if d["version"] > 1:
        sets["amendment_reason"] = body.amendment_reason.strip()
    done = db.assessments.update_one({"_id": assessment_id, "status": "draft"}, {"$set": sets})  # only a draft can become signed
    if done.modified_count != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already signed")
    audit_patient_access(request, db, user, "assessment.sign", d["patient_id"],
                         {"assessment": str(d["assessment_id"]), "version": d["version"], "total": d["total"]})
    return _out(db.assessments.find_one({"_id": assessment_id}), {user.id: user.full_name})


@router.post("/assessments/{assessment_id}/amend", status_code=status.HTTP_201_CREATED)
def amend(assessment_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(write_clin)):
    d, _ = _load(db, user, assessment_id, write=True)
    if d["status"] != "signed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a signed assessment can be amended; edit the draft instead")
    if db.assessments.find_one({"assessment_id": d["assessment_id"], "version": {"$gt": d["version"]}}, {"_id": 1}):
        raise HTTPException(status.HTTP_409_CONFLICT, "A newer version already exists: amend that one")
    keep = ("assessed_at", "setting", "scores", "total", "arousal_facilitation_used", "sedation_status", "sedation_note",
            "confounders", "observations", "clinician_category", "category_note", "instrument", "instrument_version")
    new = {"_id": uuid.uuid4(), "assessment_id": d["assessment_id"], "version": d["version"] + 1, "status": "draft",
           "patient_id": d["patient_id"], "assessor_id": user.id, "created_at": utcnow(), "signed_by": None, "signed_at": None,
           "amends": d["_id"], "amendment_reason": None, **{k: d[k] for k in keep if k in d}}
    db.assessments.insert_one(new)
    audit_patient_access(request, db, user, "assessment.amend_started", d["patient_id"],
                         {"assessment": str(d["assessment_id"]), "from_version": d["version"]})
    return _out(new, {user.id: user.full_name})


# ---- trend, reminder, EEG pairing ------------------------------------------------------------------------------------------

@router.get("/patients/{patient_id}/assessments/trend")
def trend(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(read_any)):
    patient = _patient(db, user, patient_id)
    docs = _latest(list(db.assessments.find({"patient_id": patient_id})), signed_only=True)
    docs.sort(key=lambda d: d["assessed_at"])
    audit_patient_access(request, db, user, "assessment.trend", patient_id)
    if set(user.role_names) <= {RESEARCHER}:
        return {"points": [_research_out(d, patient) for d in docs], "max": crsr.MAX}
    return {"max": crsr.MAX, "max_total": crsr.MAX_TOTAL, "points": [
        {"id": d["_id"], "assessed_at": d["assessed_at"], "total": d["total"], "scores": d["scores"],
         "clinician_category": d.get("clinician_category"), "sedation_status": d.get("sedation_status"),
         "version": d["version"], "confounders": d.get("confounders", [])} for d in docs],
        "note": "Scores are descriptive. A change in score is not a diagnosis; consider sedation, arousal and confounders."}


@router.get("/patients/{patient_id}/assessments/reminder")
def reminder(patient_id: uuid.UUID, db: Database = Depends(get_db), user: User = Depends(read_clin)):
    patient = _patient(db, user, patient_id)
    s = get_settings()
    docs = _latest(list(db.assessments.find({"patient_id": patient_id})), signed_only=True)
    since = utcnow() - timedelta(days=s.reminder_window_days)
    recent = [d for d in docs if d["assessed_at"] >= since]
    active = patient["status"] == "active"
    due = active and len(recent) < s.reminder_min_assessments
    last = docs[0]["assessed_at"] if docs else None
    if not docs:
        msg = "No CRS-R assessment has been signed for this patient yet."
    elif due:
        msg = (f"{len(recent)} signed CRS-R assessment(s) in the last {s.reminder_window_days} days; the literature suggests "
               f"repeating the assessment (about {s.reminder_min_assessments} within a short period) because a single "
               "assessment can miss signs of consciousness.")
    else:
        msg = f"{len(recent)} signed CRS-R assessments in the last {s.reminder_window_days} days."
    return {"due": bool(due), "count_in_window": len(recent), "window_days": s.reminder_window_days,
            "required": s.reminder_min_assessments, "last_assessed_at": last, "message": msg,
            "basis": "Repeated behavioural assessment lowers the risk of misdiagnosis (e.g. Wannez et al., 2017). Check the "
                     "recommended number and window against current guidelines; both are settings of this site."}


@router.get("/patients/{patient_id}/eeg-assessment-pairs")
def eeg_pairs(patient_id: uuid.UUID, request: Request, hours: float = Query(48, gt=0, le=720),
              db: Database = Depends(get_db), user: User = Depends(read_clin)):
    _patient(db, user, patient_id)
    tz = get_settings().site_timezone
    recs = [r for r in db.recordings.find({"patient_id": patient_id}).sort("start_time", 1) if r.get("start_time")]
    docs = _latest(list(db.assessments.find({"patient_id": patient_id})), signed_only=True)
    audit_patient_access(request, db, user, "assessment.eeg_pairs", patient_id)
    pairs, paired_a = [], set()
    for r in recs:
        t = edf_local_to_utc(r["start_time"], tz)
        cand = min(docs, key=lambda d: abs((d["assessed_at"] - t).total_seconds()), default=None)
        if cand is not None and abs((cand["assessed_at"] - t).total_seconds()) / 3600 <= hours:
            paired_a.add(cand["assessment_id"])
            pairs.append({"recording": {"id": r["_id"], "recording_code": r["recording_code"], "start_utc": t,
                                        "qc_status": r["qc_status"], "quality_grade": r.get("quality_grade"),
                                        "condition": (r.get("condition") or {}).get("condition"),
                                        "patient_state": (r.get("condition") or {}).get("patient_state"),
                                        "sedated": bool((r.get("condition") or {}).get("sedation"))},
                          "assessment": {"id": cand["_id"], "assessed_at": cand["assessed_at"], "total": cand["total"],
                                         "scores": cand["scores"], "clinician_category": cand.get("clinician_category"),
                                         "sedation_status": cand.get("sedation_status")},
                          "hours_between": round((cand["assessed_at"] - t).total_seconds() / 3600, 1)})
    return {"window_hours": hours, "pairs": pairs,
            "unpaired_recordings": [r["recording_code"] for r in recs if r["recording_code"] not in {p["recording"]["recording_code"] for p in pairs}],
            "unpaired_assessments": [{"id": d["_id"], "assessed_at": d["assessed_at"], "total": d["total"]} for d in docs
                                     if d["assessment_id"] not in paired_a],
            "note": "Exploratory view of time-matched EEG and behavioural assessments. With a single patient and few points, no "
                    "statistics are shown and no association should be inferred. EDF start times are read in the site time zone "
                    f"({tz})."}
