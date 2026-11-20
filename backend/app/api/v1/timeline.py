"""Clinician notes (versioned) and the merged patient timeline."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from pymongo.database import Database

from app.api.deps import CLINICIAN, TECHNOLOGIST, VIEWER, audit_patient_access, require_roles
from app.api.v1.assessments import _latest
from app.api.v1.patients import load_patient
from app.core.config import get_settings
from app.db.base import edf_local_to_utc, utcnow
from app.db.mongo import get_db
from app.models import User
from app.services import users as users_repo

router = APIRouter(tags=["timeline"])
READERS = (CLINICIAN, TECHNOLOGIST, VIEWER)
read_clin = require_roles(*READERS)
write_clin = require_roles(CLINICIAN)

NoteType = Literal["observation", "interpretation", "plan"]
LANES = ("recording", "assessment", "event", "medication", "note")


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_type: NoteType
    body: str = Field(min_length=1, max_length=5000)
    recording_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


class NoteVersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=5000)


def _names(db: Database, docs: list[dict]) -> dict:
    ids = list({d["author_id"] for d in docs})
    return {u.id: u.full_name for u in users_repo.get_many(db, ids, active_only=False).values()}


def _note_out(d: dict, names: dict, n_versions: int | None = None) -> dict:
    return {"id": d["_id"], "note_id": d["note_id"], "version": d["version"], "note_type": d["note_type"], "body": d["body"],
            "recording_id": d.get("recording_id"), "run_id": d.get("run_id"), "created_at": d["created_at"],
            "author": {"id": d["author_id"], "name": names.get(d["author_id"], "Unknown")}, "n_versions": n_versions,
            "first_written_at": d.get("first_written_at")}


# ---- notes ----------------------------------------------------------------------------------------------------------

@router.post("/patients/{patient_id}/notes", status_code=status.HTTP_201_CREATED)
def add_note(patient_id: uuid.UUID, body: NoteIn, request: Request, db: Database = Depends(get_db), user: User = Depends(write_clin)):
    load_patient(db, user, patient_id, write=True)
    if body.recording_id and not db.recordings.find_one({"_id": body.recording_id, "patient_id": patient_id}, {"_id": 1}):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That recording does not belong to this patient")
    if body.run_id and not db.processing_runs.find_one({"_id": body.run_id, "patient_id": patient_id, **(
            {"recording_id": body.recording_id} if body.recording_id else {})}, {"_id": 1}):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That processing run does not belong to this patient/recording")
    now = utcnow()
    doc = {"_id": uuid.uuid4(), "note_id": uuid.uuid4(), "version": 1, "patient_id": patient_id, "author_id": user.id,
           "created_at": now, "first_written_at": now, **body.model_dump()}
    db.clinician_notes.insert_one(doc)
    audit_patient_access(request, db, user, "note.create", patient_id, {"note": str(doc["note_id"]), "type": body.note_type})
    return _note_out(doc, {user.id: user.full_name}, 1)


@router.get("/patients/{patient_id}/notes")
def list_notes(patient_id: uuid.UUID, request: Request, note_type: NoteType | None = None, recording_id: uuid.UUID | None = None,
               db: Database = Depends(get_db), user: User = Depends(read_clin)):
    load_patient(db, user, patient_id)
    q: dict = {"patient_id": patient_id}
    if note_type:
        q["note_type"] = note_type
    if recording_id:
        q["recording_id"] = recording_id
    docs = list(db.clinician_notes.find(q))
    counts: dict = {}
    best: dict = {}
    for d in docs:
        counts[d["note_id"]] = counts.get(d["note_id"], 0) + 1
        if d["note_id"] not in best or d["version"] > best[d["note_id"]]["version"]:
            best[d["note_id"]] = d
    latest = sorted(best.values(), key=lambda d: d["first_written_at"], reverse=True)
    names = _names(db, latest)
    audit_patient_access(request, db, user, "note.list", patient_id)
    return [_note_out(d, names, counts[d["note_id"]]) for d in latest]


@router.get("/notes/{note_id}/versions")
def note_versions(note_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(read_clin)):
    docs = list(db.clinician_notes.find({"note_id": note_id}).sort("version", 1))
    if not docs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    load_patient(db, user, docs[0]["patient_id"])
    audit_patient_access(request, db, user, "note.versions", docs[0]["patient_id"], {"note": str(note_id)})
    names = _names(db, docs)
    return [_note_out(d, names, len(docs)) for d in docs]


@router.post("/notes/{note_id}/versions", status_code=status.HTTP_201_CREATED)
def edit_note(note_id: uuid.UUID, body: NoteVersionIn, request: Request, db: Database = Depends(get_db), user: User = Depends(write_clin)):
    """An edit never overwrites: it adds a new version. Only the author may edit their own note."""
    last = db.clinician_notes.find_one({"note_id": note_id}, sort=[("version", -1)])
    if last is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    load_patient(db, user, last["patient_id"], write=True)
    if last["author_id"] != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author can edit a note; add a new note instead")
    if body.body == last["body"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Nothing changed")
    new = {**{k: v for k, v in last.items() if k != "_id"}, "_id": uuid.uuid4(), "version": last["version"] + 1, "body": body.body,
           "created_at": utcnow()}
    db.clinician_notes.insert_one(new)  # unique (note_id, version) stops two concurrent edits claiming the same number
    audit_patient_access(request, db, user, "note.edit", last["patient_id"], {"note": str(note_id), "version": new["version"]})
    return _note_out(new, {user.id: user.full_name}, new["version"])


# ---- timeline -----------------------------------------------------------------------------------------------------------

def _day(s: str) -> datetime:
    return datetime.combine(date.fromisoformat(s), datetime.min.time(), tzinfo=UTC)


@router.get("/patients/{patient_id}/timeline")
def timeline(patient_id: uuid.UUID, request: Request, lanes: str | None = Query(None, max_length=100),
             start: datetime | None = Query(None, alias="from"), end: datetime | None = Query(None, alias="to"),
             db: Database = Depends(get_db), user: User = Depends(read_clin)):
    patient = load_patient(db, user, patient_id)
    want = [x.strip() for x in lanes.split(",") if x.strip()] if lanes else list(LANES)
    bad = [x for x in want if x not in LANES]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown lane(s): {bad}; choose from {list(LANES)}")
    tz = get_settings().site_timezone
    items: list[dict] = []

    injury = patient.get("date_of_injury")
    if "recording" in want:
        for r in db.recordings.find({"patient_id": patient_id}):
            if not r.get("start_time"):
                continue
            t0 = edf_local_to_utc(r["start_time"], tz)
            cond = r.get("condition") or {}
            items.append({"lane": "recording", "id": r["_id"], "start": t0, "end": t0 + timedelta(seconds=r["duration_s"]),
                          "title": r["recording_code"], "subtitle": (cond.get("condition") or "context missing").replace("_", " "),
                          "badges": [b for b in (r.get("quality_grade") and f"quality {r['quality_grade']}", f"QC {r['qc_status']}",
                                                 "baseline" if r.get("is_baseline") else None, "sedated" if cond.get("sedation") else None,
                                                 "dated before injury" if injury and t0 < _day(injury) else None) if b],
                          "link": f"/recordings/{r['_id']}"})
    if "assessment" in want:
        docs = _latest(list(db.assessments.find({"patient_id": patient_id})), signed_only=CLINICIAN not in user.role_names)
        for d in docs:
            items.append({"lane": "assessment", "id": d["_id"], "start": d["assessed_at"], "end": None,
                          "title": f"CRS-R {d['total']}/23", "subtitle": d.get("clinician_category") or "category not entered",
                          "badges": [b for b in ("draft" if d["status"] == "draft" else None, f"v{d['version']}" if d["version"] > 1 else None,
                                                 f"sedation {d['sedation_status']}" if d.get("sedation_status") in ("reduced", "ongoing") else None) if b],
                          "link": f"/assessments/{d['_id']}"})
    if "event" in want:
        for e in db.clinical_events.find({"patient_id": patient_id, "deleted_at": None}):
            items.append({"lane": "event", "id": e["_id"], "start": e["occurred_at"], "end": None,
                          "title": e["event_type"].replace("_", " "), "subtitle": e["description"][:120], "badges": [], "link": None})
        for key, label in (("date_of_injury", "injury"), ("admission_date", "admission")):
            if patient.get(key):
                items.append({"lane": "event", "id": f"{patient_id}-{key}", "start": _day(patient[key]), "end": None, "title": label,
                              "subtitle": "milestone", "badges": ["milestone"], "link": None})
    if "medication" in want:
        for m in db.medical_history_items.find({"patient_id": patient_id, "category": "medication", "deleted_at": None}):
            if not m.get("start_date"):
                continue
            items.append({"lane": "medication", "id": m["_id"], "start": _day(m["start_date"]),
                          "end": _day(m["end_date"]) if m.get("end_date") else None, "title": m["description"][:60],
                          "subtitle": "ongoing" if not m.get("end_date") and m.get("is_active") else "", "badges": [], "link": None})
    if "note" in want:
        docs = list(db.clinician_notes.find({"patient_id": patient_id}))
        best: dict = {}
        for d in docs:
            if d["note_id"] not in best or d["version"] > best[d["note_id"]]["version"]:
                best[d["note_id"]] = d
        for d in best.values():
            items.append({"lane": "note", "id": d["note_id"], "start": d["first_written_at"], "end": None,
                          "title": d["note_type"], "subtitle": d["body"][:120], "badges": [f"v{d['version']}"] if d["version"] > 1 else [],
                          "link": None})

    def overlaps(i: dict) -> bool:
        a, b = i["start"], i["end"] or i["start"]
        return (end is None or a <= end) and (start is None or b >= start)

    items = sorted((i for i in items if overlaps(i)), key=lambda i: (i["start"], i["lane"]))
    audit_patient_access(request, db, user, "timeline.view", patient_id)
    return {"lanes": want, "items": items, "counts": {ln: sum(1 for i in items if i["lane"] == ln) for ln in LANES},
            "note": "Recording start times are read in the site time zone; all times are shown in UTC."}
