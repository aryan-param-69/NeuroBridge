"""Pre-processing runs, QC sign-off, artefact/selection annotations and job status."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from pymongo.database import Database

from app import jobs
from app.api.deps import CLINICIAN, TECHNOLOGIST, audit_patient_access, require_roles
from app.api.v1.recordings import READERS, WRITERS, _load, _patient
from app.core.config import get_settings
from app.db.base import utcnow
from app.db.mongo import get_db
from app.eeg import pipelines
from app.eeg import preprocess as pp
from app.models import User
from app.services import processing as svc

router = APIRouter(tags=["processing"])
read_proc = require_roles(*READERS)
write_proc = require_roles(*WRITERS)

LABELS = (*pp.EXCLUDE_LABELS, pp.SELECT_LABEL)
MAX_ANNOTATIONS = 5000


# ---- Schemas ---------------------------------------------------------------------------------------------

class RunRequest(BaseModel):
    pipeline_name: str = "resting"
    pipeline_version: str | None = None
    overrides: dict = Field(default_factory=dict)


class QcBody(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=1000)


class AnnotationIn(BaseModel):
    onset_s: float = Field(ge=0)
    duration_s: float = Field(gt=0, le=86400)
    label: Literal["artifact_eye", "artifact_muscle", "artifact_movement", "electrode_pop", "flat", "excluded",
                   "selected_for_analysis"]
    note: str | None = Field(default=None, max_length=300)
    source: Literal["user", "auto_accepted"] = "user"


# ---- Helpers ---------------------------------------------------------------------------------------------

def _run_summary(r: dict) -> dict:
    q = r.get("quality") or {}
    return {
        "id": r["_id"], "recording_id": r["recording_id"], "pipeline_name": r["pipeline_name"],
        "pipeline_version": r["pipeline_version"], "params_hash": r["params_hash"], "status": r["status"],
        "qc_status": r["qc_status"], "qc_note": r.get("qc_note"), "is_current": r["is_current"],
        "grade": q.get("grade"), "score": q.get("overall_score"), "n_epochs_total": r.get("n_epochs_total"),
        "n_epochs_kept": r.get("n_epochs_kept"), "bad_channels": r.get("bad_channels", []),
        "interpolated_channels": r.get("interpolated_channels", []), "dropped_channels": r.get("dropped_channels", []),
        "low_confidence": r.get("low_confidence"), "error": r.get("error"), "created_at": r["created_at"],
        "finished_at": r.get("finished_at"),
    }


def _run_detail(r: dict) -> dict:
    return {**_run_summary(r), "params": r["params"], "software_versions": r["software_versions"],
            "input_sha256": r["input_sha256"], "output_sha256": r.get("output_sha256"), "steps": r["steps"],
            "quality": r.get("quality"), "warnings": r.get("warnings", []), "channels": r.get("channels", []),
            "epochs": r.get("epochs"), "segments_proposed": r.get("segments_proposed", []),
            "sfreq_hz": r.get("sfreq_hz")}


def _load_run(db: Database, user: User, run_id: uuid.UUID, *, write: bool = False) -> tuple[dict, dict]:
    run = db.processing_runs.find_one({"_id": run_id})
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    rec, _ = _load(db, user, run["recording_id"], write=write)
    return run, rec


# ---- Pipelines and runs ----------------------------------------------------------------------------------

@router.get("/pipelines")
def get_pipelines(_: User = Depends(read_proc)):
    return {"line_freq_hz": get_settings().line_freq_hz, "pipelines": pipelines.list_pipelines(),
            "overridable": {k: list(v[1]) for k, v in pipelines.OVERRIDABLE.items()}}


@router.post("/recordings/{recording_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def start_run(recording_id: uuid.UUID, body: RunRequest, request: Request, db: Database = Depends(get_db),
              user: User = Depends(write_proc)):
    rec, _ = _load(db, user, recording_id, write=True)
    if db.processing_runs.find_one({"recording_id": recording_id, "status": {"$in": ["queued", "running"]}}, {"_id": 1}):
        raise HTTPException(status.HTTP_409_CONFLICT, "This recording is already being processed")
    try:
        resolved = pipelines.resolve(body.pipeline_name, body.pipeline_version, get_settings().line_freq_hz,
                                     body.overrides, user.role_names)
    except pipelines.PipelineError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    phash = pipelines.params_hash(resolved["name"], resolved["version"], resolved["params"])
    run_id = svc.create_run_doc(db, recording=rec, user_id=user.id, name=resolved["name"], version=resolved["version"],
                                params=resolved["params"], params_hash=phash)
    job_id = jobs.create_job(db, job_type="preprocess", user_id=user.id,
                             target={"run_id": run_id, "recording_id": recording_id, "patient_id": rec["patient_id"]})
    db.processing_runs.update_one({"_id": run_id}, {"$set": {"job_id": job_id}})
    audit_patient_access(request, db, user, "processing.run_started", rec["patient_id"], {
        "recording": str(recording_id), "run": str(run_id), "pipeline": f"{resolved['name']} v{resolved['version']}",
        "overrides": sorted(body.overrides)})
    jobs.submit(job_id, lambda d, progress: svc.execute_run(d, run_id, progress))
    return {"job_id": job_id, "run_id": run_id, "params_hash": phash, "pipeline": f"{resolved['name']} v{resolved['version']}"}


@router.get("/recordings/{recording_id}/runs")
def list_runs(recording_id: uuid.UUID, db: Database = Depends(get_db), user: User = Depends(read_proc)):
    _load(db, user, recording_id)
    return [_run_summary(r) for r in db.processing_runs.find({"recording_id": recording_id}).sort("created_at", -1)]


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(read_proc)):
    run, rec = _load_run(db, user, run_id)
    audit_patient_access(request, db, user, "processing.run_view", rec["patient_id"], {"run": str(run_id)})
    return _run_detail(run)


@router.get("/recordings/{recording_id}/quality")
def latest_quality(recording_id: uuid.UUID, db: Database = Depends(get_db), user: User = Depends(read_proc)):
    _load(db, user, recording_id)
    run = db.processing_runs.find_one({"recording_id": recording_id, "status": "succeeded"}, sort=[("created_at", -1)])
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No quality report yet: run the processing pipeline first")
    return {"run_id": run["_id"], "qc_status": run["qc_status"], **run["quality"]}


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, db: Database = Depends(get_db), user: User = Depends(read_proc)):
    job = db.jobs.find_one({"_id": job_id})
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job["created_by"] != user.id:
        _patient(db, user, job["target"]["patient_id"], write=False)  # otherwise: only people on the care team
    return {"id": job["_id"], "type": job["type"], "status": job["status"], "progress": job["progress"],
            "message": job["message"], "error": job.get("error"), "run_id": job["target"].get("run_id")}


# ---- QC sign-off ---------------------------------------------------------------------------------------------

@router.post("/runs/{run_id}/qc")
def qc_decision(run_id: uuid.UUID, body: QcBody, request: Request, db: Database = Depends(get_db),
                user: User = Depends(write_proc)):
    run, rec = _load_run(db, user, run_id, write=True)
    if run["status"] != "succeeded":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a finished run can be reviewed")
    note = (body.note or "").strip()
    if body.decision == "reject" and not note:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Say why the run is rejected")
    if body.decision == "approve" and run["quality"]["grade"] == "poor" and not note:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "This run has poor signal quality: explain why it is acceptable to approve it")
    decision = "approved" if body.decision == "approve" else "rejected"
    sets = {"qc_status": decision, "qc_by": user.id, "qc_at": utcnow(), "qc_note": note or None}
    if decision == "rejected":
        sets["is_current"] = False
    db.processing_runs.update_one({"_id": run_id}, {"$set": sets})
    if decision == "approved" and not db.processing_runs.find_one(
            {"recording_id": rec["_id"], "is_current": True, "qc_status": "approved"}, {"_id": 1}):
        db.processing_runs.update_one({"_id": run_id}, {"$set": {"is_current": True}})  # first approved run becomes current
    db.recordings.update_one({"_id": rec["_id"]}, {"$set": {"qc_status": decision}})
    audit_patient_access(request, db, user, f"processing.qc_{decision}", rec["patient_id"],
                         {"run": str(run_id), "grade": run["quality"]["grade"]})
    return _run_summary(db.processing_runs.find_one({"_id": run_id}))


@router.post("/runs/{run_id}/current")
def make_current(run_id: uuid.UUID, request: Request, db: Database = Depends(get_db),
                 user: User = Depends(require_roles(CLINICIAN))):
    run, rec = _load_run(db, user, run_id, write=True)
    if run["qc_status"] != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an approved run can feed trends and comparisons")
    db.processing_runs.update_many({"recording_id": rec["_id"]}, {"$set": {"is_current": False}})
    db.processing_runs.update_one({"_id": run_id}, {"$set": {"is_current": True}})
    audit_patient_access(request, db, user, "processing.run_set_current", rec["patient_id"], {"run": str(run_id)})
    return _run_summary(db.processing_runs.find_one({"_id": run_id}))


@router.get("/patients/{patient_id}/analysis-runs")
def analysis_runs(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db),
                  user: User = Depends(read_proc)):
    """The runs that downstream analysis (features, trends, comparisons) may use: approved AND current only."""
    _patient(db, user, patient_id, write=False)
    audit_patient_access(request, db, user, "processing.analysis_runs_view", patient_id)
    rows = db.processing_runs.find({"patient_id": patient_id, "qc_status": "approved", "is_current": True,
                                    "status": "succeeded"}).sort("created_at", -1)
    return [_run_summary(r) for r in rows]


# ---- Annotations: artefacts and interactive segment selection ---------------------------------------------------

def _ann_out(a: dict) -> dict:
    return {"id": a["_id"], "onset_s": a["onset_s"], "duration_s": a["duration_s"], "label": a["label"],
            "note": a.get("note"), "source": a["source"], "author_id": a["author_id"], "created_at": a["created_at"]}


@router.get("/recordings/{recording_id}/annotations")
def list_annotations(recording_id: uuid.UUID, db: Database = Depends(get_db), user: User = Depends(read_proc)):
    _load(db, user, recording_id)
    return [_ann_out(a) for a in db.segment_annotations.find({"recording_id": recording_id}).sort("onset_s", 1)]


@router.post("/recordings/{recording_id}/annotations", status_code=status.HTTP_201_CREATED)
def add_annotation(recording_id: uuid.UUID, body: AnnotationIn, request: Request, db: Database = Depends(get_db),
                   user: User = Depends(write_proc)):
    rec, _ = _load(db, user, recording_id, write=True)
    if body.onset_s + body.duration_s > rec["duration_s"] + 1e-6:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The segment extends beyond the end of the recording")
    if db.segment_annotations.count_documents({"recording_id": recording_id}) >= MAX_ANNOTATIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Too many annotations on this recording")
    doc = {"_id": uuid.uuid4(), "recording_id": recording_id, "patient_id": rec["patient_id"], **body.model_dump(),
           "author_id": user.id, "created_at": utcnow()}
    db.segment_annotations.insert_one(doc)
    audit_patient_access(request, db, user, "recording.annotation_add", rec["patient_id"],
                         {"recording": str(recording_id), "label": body.label})
    return _ann_out(doc)


@router.delete("/recordings/{recording_id}/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_annotation(recording_id: uuid.UUID, annotation_id: uuid.UUID, request: Request,
                      db: Database = Depends(get_db), user: User = Depends(write_proc)):
    rec, _ = _load(db, user, recording_id, write=True)
    if db.segment_annotations.delete_one({"_id": annotation_id, "recording_id": recording_id}).deleted_count != 1:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    audit_patient_access(request, db, user, "recording.annotation_delete", rec["patient_id"], {"recording": str(recording_id)})


_ = TECHNOLOGIST  # roles are referenced through READERS/WRITERS
