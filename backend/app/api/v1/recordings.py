"""EEG recordings: chunked upload, validation, de-identification, metadata and viewer data."""

import hashlib
import math
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument
from pymongo.database import Database

from app import storage
from app.api.deps import CLINICIAN, TECHNOLOGIST, VIEWER, audit_patient_access, require_roles
from app.api.v1.patients import load_patient
from app.core.config import get_settings
from app.db.base import utcnow
from app.db.mongo import get_db
from app.eeg import io as edf_io
from app.eeg import signal as sig
from app.models import User
from app.services import patients as patient_svc

router = APIRouter(tags=["recordings"])

WRITERS = (CLINICIAN, TECHNOLOGIST)
READERS = (CLINICIAN, TECHNOLOGIST, VIEWER)
write_rec = require_roles(*WRITERS)
read_rec = require_roles(*READERS)

SEDATION_ROLE_HELP = "Sedatives change the EEG; record every agent given around the recording."


# ---- Schemas --------------------------------------------------------------------------------------

class UploadInit(BaseModel):
    filename: str = Field(min_length=1, max_length=255)  # only the extension is checked; never stored
    size: int = Field(gt=0)


class UploadComplete(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    confirm_identity_mismatch: bool = False


class Sedation(BaseModel):
    agent: str = Field(min_length=1, max_length=80)
    dose: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=20)
    time: str | None = Field(default=None, max_length=40)  # free text, e.g. "08:30" or "infusion running"


class Condition(BaseModel):
    condition: Literal["resting_eyes_closed", "resting_eyes_open", "resting_unknown", "task_motor_imagery",
                       "task_command_following", "auditory_paradigm", "other"]
    patient_state: Literal["awake", "drowsy", "asleep", "sedated", "unknown"]
    sedation: list[Sedation] = Field(default_factory=list, max_length=20)
    anti_seizure_meds: list[str] = Field(default_factory=list, max_length=20)
    setting: Literal["icu", "ward", "lab", "home", "other"] | None = None
    time_of_day: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    device_model: str | None = Field(default=None, max_length=100)
    electrode_system: Literal["10-20", "10-10", "custom", "unknown"] = "unknown"
    reference_label: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("anti_seizure_meds")
    @classmethod
    def _meds(cls, v):
        return [m.strip()[:80] for m in v if m.strip()]


class ChannelEdit(BaseModel):
    index: int = Field(ge=0)
    ch_type: Literal["eeg", "eog", "ecg", "emg", "resp", "misc", "stim"] | None = None
    user_flag: Literal["bad", "ok"] | None = None
    clear_flag: bool = False


class ChannelPatch(BaseModel):
    channels: list[ChannelEdit] = Field(min_length=1, max_length=512)


class BaselineBody(BaseModel):
    is_baseline: bool
    reason: str | None = Field(default=None, max_length=500)


# ---- Helpers --------------------------------------------------------------------------------------

def _patient(db: Database, user: User, patient_id: uuid.UUID, *, write: bool) -> dict:
    return load_patient(db, user, patient_id, write=write, write_roles=WRITERS)


def _load(db: Database, user: User, recording_id: uuid.UUID, *, write: bool = False) -> tuple[dict, dict]:
    rec = db.recordings.find_one({"_id": recording_id})
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    return rec, _patient(db, user, rec["patient_id"], write=write)  # 404 if the user is not on the care team


def _summary(rec: dict) -> dict:
    return {
        "id": rec["_id"], "patient_id": rec["patient_id"], "recording_code": rec["recording_code"],
        "file_format": rec["file_format"], "start_time": rec.get("start_time"), "duration_s": rec["duration_s"],
        "n_channels": rec["n_channels"], "n_eeg_channels": rec["n_eeg_channels"], "sfreq_hz": rec["sfreq_hz"],
        "size_bytes": rec["size_bytes"], "identity_check": rec["identity_check"], "warnings": rec["warnings"],
        "condition": rec.get("condition"), "is_baseline": rec["is_baseline"], "qc_status": rec["qc_status"],
        "bad_channels": [c["name"] for c in rec["channels"] if sig.is_bad(c)], "created_at": rec["created_at"],
        "quality_grade": rec.get("quality_grade"), "quality_score": rec.get("quality_score"),
    }


def _detail(rec: dict) -> dict:
    return {**_summary(rec), "sha256": rec["sha256"], "channels": rec["channels"],
            "annotations": rec["annotation_counts"], "overview": rec.get("overview"),
            "baseline_reason": rec.get("baseline_reason")}


def _session(db: Database, user: User, upload_id: uuid.UUID) -> dict:
    s = db.upload_sessions.find_one({"_id": upload_id, "user_id": user.id, "status": "open"})
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    return s


def _fail(db: Database, upload_id: uuid.UUID, code: int, message, status_label: str = "failed"):
    db.upload_sessions.update_one({"_id": upload_id}, {"$set": {"status": status_label}})
    storage.remove_dir(storage.upload_dir(upload_id))
    raise HTTPException(code, message)


# ---- Upload ---------------------------------------------------------------------------------------

@router.post("/patients/{patient_id}/recordings/uploads", status_code=status.HTTP_201_CREATED)
def start_upload(patient_id: uuid.UUID, body: UploadInit, request: Request, db: Database = Depends(get_db),
                 user: User = Depends(write_rec)):
    s = get_settings()
    _patient(db, user, patient_id, write=True)
    if not body.filename.lower().endswith(".edf"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only .edf (EDF/EDF+) files are supported")
    if body.size > s.max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds the {s.max_upload_bytes // 1024**2} MB limit")
    if db.upload_sessions.count_documents({"user_id": user.id, "status": "open"}) >= s.max_open_uploads_per_user:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many uploads in progress; finish or wait")
    upload_id = uuid.uuid4()
    n_chunks = max(1, math.ceil(body.size / s.chunk_size))
    db.upload_sessions.insert_one({
        "_id": upload_id, "patient_id": patient_id, "user_id": user.id, "size": body.size,
        "chunk_size": s.chunk_size, "n_chunks": n_chunks, "received": [], "status": "open",
        "created_at": utcnow(),
    })
    storage.upload_dir(upload_id).mkdir(parents=True, exist_ok=True)
    audit_patient_access(request, db, user, "recording.upload_started", patient_id, {"size": body.size})
    return {"upload_id": upload_id, "chunk_size": s.chunk_size, "n_chunks": n_chunks}


@router.get("/uploads/{upload_id}")
def upload_status(upload_id: uuid.UUID, db: Database = Depends(get_db), user: User = Depends(write_rec)):
    s = _session(db, user, upload_id)
    return {"upload_id": s["_id"], "n_chunks": s["n_chunks"], "received": sorted(s["received"]),
            "size": s["size"], "chunk_size": s["chunk_size"]}


@router.put("/uploads/{upload_id}/chunks/{index}", status_code=status.HTTP_204_NO_CONTENT)
async def upload_chunk(upload_id: uuid.UUID, index: int, request: Request, db: Database = Depends(get_db),
                       user: User = Depends(write_rec)):
    s = _session(db, user, upload_id)
    if not 0 <= index < s["n_chunks"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Chunk index out of range")
    expected = s["chunk_size"] if index < s["n_chunks"] - 1 else s["size"] - s["chunk_size"] * (s["n_chunks"] - 1)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > s["chunk_size"]:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Chunk too large")
    body = await request.body()
    if len(body) != expected:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Chunk {index} must be exactly {expected} bytes")
    part = storage.upload_dir(upload_id) / f"{index:06d}.part"
    tmp = part.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(part)
    db.upload_sessions.update_one({"_id": upload_id}, {"$addToSet": {"received": index}})


def _assemble(upload_id: uuid.UUID, n_chunks: int):
    d = storage.upload_dir(upload_id)
    out = d / "assembled.edf"
    h = hashlib.sha256()
    with open(out, "wb") as f:
        for i in range(n_chunks):
            data = (d / f"{i:06d}.part").read_bytes()
            h.update(data)
            f.write(data)
    return out, h.hexdigest()


@router.post("/uploads/{upload_id}/complete", status_code=status.HTTP_201_CREATED)
def complete_upload(upload_id: uuid.UUID, body: UploadComplete, request: Request, db: Database = Depends(get_db),
                    user: User = Depends(write_rec)):
    s = _session(db, user, upload_id)
    patient_id = s["patient_id"]
    patient = _patient(db, user, patient_id, write=True)  # access is re-checked at completion
    missing = sorted(set(range(s["n_chunks"])) - set(s["received"]))
    if missing:
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Some chunks are missing", "missing": missing[:50]})

    assembled, digest = _assemble(upload_id, s["n_chunks"])
    if digest != body.sha256.lower():
        _fail(db, upload_id, status.HTTP_400_BAD_REQUEST,
              "Checksum mismatch: the file was corrupted in transfer. Please upload it again.")
    if db.recordings.find_one({"patient_id": patient_id, "sha256": digest}, {"_id": 1}):
        _fail(db, upload_id, status.HTTP_409_CONFLICT, {"message": "This exact file was already uploaded for this patient"},
              "duplicate")

    try:
        header = edf_io.read_raw_header(assembled)
        summary = edf_io.summarize(assembled, header)
    except edf_io.InvalidEdf as e:
        _fail(db, upload_id, status.HTTP_422_UNPROCESSABLE_CONTENT, str(e), "invalid")

    # Compare the header's patient field with the registered patient, in memory only.
    ident = patient_svc.reveal(db.patient_identifiers.find_one({"_id": patient_id}))
    reg_tokens = {t for t in patient_svc.normalize_name(ident["full_name"]).split() if len(t) >= 2}
    identity, reasons = edf_io.identity_check(header.patient_field, reg_tokens, ident["date_of_birth"])
    if identity == "mismatch" and not body.confirm_identity_mismatch:
        # The assembled file is kept so the user can confirm without uploading again.
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "message": "The patient details inside this file do not match the registered patient. "
                       "The file may belong to someone else.",
            "identity_check": "mismatch", "reasons": reasons})

    recording_id = uuid.uuid4()
    raw_dst = storage.raw_path(patient_id, recording_id)
    deid_dst = storage.deid_path(patient_id, recording_id)
    try:
        storage.encrypt_file(assembled, raw_dst)
        edf_io.deidentify_copy(assembled, deid_dst, header)
        raw = sig.open_raw(deid_dst)
        overview, flags = sig.overview_and_flags(raw, summary.channels)
    except Exception as e:
        sig.forget(deid_dst)
        raw_dst.unlink(missing_ok=True)
        deid_dst.unlink(missing_ok=True)
        _fail(db, upload_id, status.HTTP_422_UNPROCESSABLE_CONTENT, f"The EEG data could not be read: {str(e)[:150]}",
              "invalid")

    for c in summary.channels:
        if c["index"] in flags:
            c["auto_flag"] = flags[c["index"]]
    warnings = list(summary.warnings)
    for label in ("flat", "noisy", "clipped"):
        names = [c["name"] for c in summary.channels if c["auto_flag"] == label]
        if names:
            warnings.append(f"{len(names)} channel(s) look {label}: {', '.join(names[:8])}"
                            + ("..." if len(names) > 8 else "") + " (provisional automatic check)")

    seq = db.counters.find_one_and_update({"_id": f"recording:{patient_id}"}, {"$inc": {"last": 1}}, upsert=True,
                                          return_document=ReturnDocument.AFTER)["last"]
    eeg_n = sum(1 for c in summary.channels if c["ch_type"] == "eeg")
    rec = {
        "_id": recording_id, "patient_id": patient_id, "recording_code": f"{patient['display_id']}-R{seq:02d}",
        "sha256": digest, "size_bytes": s["size"], "file_format": summary.file_format,
        "start_time": summary.start_time, "duration_s": summary.duration_s, "n_channels": len(summary.channels),
        "n_eeg_channels": eeg_n, "sfreq_hz": summary.sfreq_hz, "channels": summary.channels,
        "annotation_counts": summary.annotations, "annotations": summary.annotation_list,
        "identity_check": identity if identity != "mismatch" else "mismatch_confirmed",
        "header_flags": {"edf_plus": header.edf_plus, "patient_field_blank": identity == "blank"},
        "warnings": warnings, "condition": None, "is_baseline": False, "qc_status": "pending",
        "overview": overview, "created_by": user.id, "created_at": utcnow(),
    }
    db.recordings.insert_one(rec)
    db.upload_sessions.update_one({"_id": upload_id}, {"$set": {"status": "done", "recording_id": recording_id}})
    storage.remove_dir(storage.upload_dir(upload_id))
    audit_patient_access(request, db, user, "recording.upload", patient_id, {
        "recording": str(recording_id), "format": summary.file_format, "n_channels": len(summary.channels),
        "identity_check": rec["identity_check"], "size": s["size"]})
    return _detail(rec)


# ---- Read / edit metadata -----------------------------------------------------------------------

@router.get("/patients/{patient_id}/recordings")
def list_recordings(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db),
                    user: User = Depends(read_rec)):
    _patient(db, user, patient_id, write=False)
    audit_patient_access(request, db, user, "recording.list", patient_id)
    rows = db.recordings.find({"patient_id": patient_id}).sort([("start_time", -1), ("created_at", -1)])
    return [_summary(r) for r in rows]


@router.get("/recordings/{recording_id}")
def get_recording(recording_id: uuid.UUID, request: Request, db: Database = Depends(get_db),
                  user: User = Depends(read_rec)):
    rec, _ = _load(db, user, recording_id)
    audit_patient_access(request, db, user, "recording.view", rec["patient_id"], {"recording": str(recording_id)})
    return _detail(rec)


@router.put("/recordings/{recording_id}/condition")
def set_condition(recording_id: uuid.UUID, body: Condition, request: Request, db: Database = Depends(get_db),
                  user: User = Depends(write_rec)):
    rec, _ = _load(db, user, recording_id, write=True)
    if body.patient_state == "sedated" and not body.sedation:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "List the sedation given when the patient state is 'sedated'. " + SEDATION_ROLE_HELP)
    doc = {**body.model_dump(), "set_by": user.id, "set_at": utcnow()}
    db.recordings.update_one({"_id": recording_id}, {"$set": {"condition": doc}})
    audit_patient_access(request, db, user, "recording.condition_set", rec["patient_id"],
                         {"recording": str(recording_id), "condition": body.condition, "state": body.patient_state})
    return _detail(db.recordings.find_one({"_id": recording_id}))


@router.patch("/recordings/{recording_id}/channels")
def edit_channels(recording_id: uuid.UUID, body: ChannelPatch, request: Request, db: Database = Depends(get_db),
                  user: User = Depends(write_rec)):
    rec, _ = _load(db, user, recording_id, write=True)
    by_index = {c["index"]: c for c in rec["channels"]}
    sets: dict = {}
    for e in body.channels:
        pos = next((i for i, c in enumerate(rec["channels"]) if c["index"] == e.index), None)
        if pos is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"No channel with index {e.index}")
        if e.ch_type:
            sets[f"channels.{pos}.ch_type"] = e.ch_type
            sets[f"channels.{pos}.has_position"] = by_index[e.index]["has_position"] and e.ch_type == "eeg"
        if e.clear_flag:
            sets[f"channels.{pos}.user_flag"] = None
        elif e.user_flag:
            sets[f"channels.{pos}.user_flag"] = e.user_flag
    db.recordings.update_one({"_id": recording_id}, {"$set": sets})
    audit_patient_access(request, db, user, "recording.channels_edit", rec["patient_id"],
                         {"recording": str(recording_id), "n_edits": len(body.channels)})
    return _detail(db.recordings.find_one({"_id": recording_id}))


@router.post("/recordings/{recording_id}/baseline")
def set_baseline(recording_id: uuid.UUID, body: BaselineBody, request: Request, db: Database = Depends(get_db),
                 user: User = Depends(require_roles(CLINICIAN))):
    rec, _ = _load(db, user, recording_id, write=True)
    if body.is_baseline and not (body.reason and body.reason.strip()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give a reason for designating a baseline")
    db.recordings.update_one({"_id": recording_id}, {"$set": {
        "is_baseline": body.is_baseline, "baseline_reason": body.reason if body.is_baseline else None}})
    audit_patient_access(request, db, user, "recording.baseline_set", rec["patient_id"],
                         {"recording": str(recording_id), "is_baseline": body.is_baseline})
    return _detail(db.recordings.find_one({"_id": recording_id}))


# ---- Viewer data --------------------------------------------------------------------------------

_last_signal_audit: dict[tuple, float] = {}
SIGNAL_AUDIT_INTERVAL_S = 600


@router.get("/recordings/{recording_id}/signal")
def get_signal(
    recording_id: uuid.UUID, request: Request,
    start: float = Query(0.0, ge=0),
    duration: float = Query(10.0, gt=0, le=sig.MAX_WINDOW_S),
    channels: str | None = Query(None, max_length=3000, description="comma-separated normalised names"),
    montage: str = Query("referential", pattern="^(referential|average|bipolar_longitudinal)$"),
    max_points: int = Query(2000, ge=200, le=10000),
    stage: Literal["raw", "processed"] = Query("raw"),
    run_id: uuid.UUID | None = Query(None, description="required for stage=processed"),
    db: Database = Depends(get_db), user: User = Depends(read_rec),
):
    rec, _ = _load(db, user, recording_id)
    run = None
    if stage == "processed":
        run = db.processing_runs.find_one({"_id": run_id, "recording_id": recording_id, "status": "succeeded"}) if run_id else None
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Processed data not found: give a finished run_id of this recording")
    key = (user.id, recording_id)
    now = time.monotonic()
    if now - _last_signal_audit.get(key, 0) > SIGNAL_AUDIT_INTERVAL_S:  # one audit row per viewing session
        _last_signal_audit[key] = now
        audit_patient_access(request, db, user, "recording.signal_view", rec["patient_id"],
                             {"recording": str(recording_id)})
    selected = [c.strip() for c in channels.split(",") if c.strip()] if channels else None
    meta = run["channels"] if run else rec["channels"]
    known = {c["name"] for c in meta}
    if selected and not set(selected) <= known:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown channel name(s)")
    path = storage.processed_path(rec["patient_id"], run_id) if run else storage.deid_path(rec["patient_id"], recording_id)
    try:
        raw = sig.open_raw(path)
        names, data, sf = sig.read_window(raw, meta, montage, selected, start, duration)
    except sig.SignalError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    n = data.shape[1]
    out, centres = sig.decimate_minmax(data, max_points)
    if centres is not None:
        times = (start + centres / sf).round(4).tolist()
    else:
        times = [round(start + i / sf, 4) for i in range(n)]
    end = start + n / sf
    anns = [a for a in rec.get("annotations", []) if start <= a["onset"] < end or start < a["onset"] + a["duration"] <= end]
    epochs = []
    if run:
        ep = run["epochs"]
        epochs = [{"onset": o, "duration": ep["length_s"], "status": st} for o, st in zip(ep["onset_s"], ep["status"], strict=True)
                  if st != 0 and o < end and o + ep["length_s"] > start]
    return {
        "stage": stage, "run_id": run_id, "unreviewed": bool(run and run["qc_status"] != "approved"), "epochs": epochs,
        "interpolated": run.get("interpolated_channels", []) if run else [],
        "sfreq": sf, "start": start, "duration": n / sf, "montage": montage, "unit": "uV",
        "channels": names, "times": times, "data": out.round(2).tolist(), "decimated": centres is not None,
        "bad_channels": [c["name"] for c in meta if sig.is_bad(c) and c["name"] in names],
        "annotations": anns, "total_duration": rec["duration_s"],
    }


@router.get("/recordings/{recording_id}/overview")
def get_overview(recording_id: uuid.UUID, db: Database = Depends(get_db), user: User = Depends(read_rec)):
    rec, _ = _load(db, user, recording_id)
    return {"duration_s": rec["duration_s"], **(rec.get("overview") or {})}


# ---- Download ----------------------------------------------------------------------------------

@router.get("/recordings/{recording_id}/download")
def download(recording_id: uuid.UUID, request: Request, variant: Literal["deidentified", "original"] = "deidentified",
             db: Database = Depends(get_db), user: User = Depends(write_rec)):
    rec, _ = _load(db, user, recording_id)
    if variant == "original" and CLINICIAN not in user.role_names:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only clinicians may download the original file")
    audit_patient_access(request, db, user, f"recording.download_{variant}", rec["patient_id"],
                         {"recording": str(recording_id)})
    name = f"{rec['recording_code']}{'' if variant == 'original' else '_deidentified'}.edf"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    if variant == "deidentified":
        return FileResponse(storage.deid_path(rec["patient_id"], recording_id), media_type="application/octet-stream",
                            headers=headers)
    try:
        return StreamingResponse(storage.decrypt_stream(storage.raw_path(rec["patient_id"], recording_id)),
                                 media_type="application/octet-stream", headers=headers)
    except storage.StorageError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)) from None

