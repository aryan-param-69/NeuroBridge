"""AI analysis: sedation-level model predictions for processed runs (decision support, never a diagnosis)."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo.database import Database

from app import jobs
from app.api.deps import ADMIN, CLINICIAN, RESEARCHER, TECHNOLOGIST, VIEWER, audit_patient_access, require_roles
from app.api.v1.assessments import _latest
from app.api.v1.patients import load_patient
from app.api.v1.processing import _load_run
from app.core.config import get_settings
from app.db.base import edf_local_to_utc, utcnow
from app.db.mongo import get_db
from app.ml import registry
from app.models import User
from app.services import ai as svc
from app.services.audit import write_audit

router = APIRouter(tags=["ai"])
read_ai = require_roles(CLINICIAN, TECHNOLOGIST, VIEWER)
write_ai = require_roles(CLINICIAN)
any_ai = require_roles(CLINICIAN, TECHNOLOGIST, VIEWER, RESEARCHER, ADMIN)


def _out(d: dict, rec: dict | None = None) -> dict:
    out = {k: d[k] for k in ("p_sedated", "prediction_set", "verdict", "n_epochs", "n_windows_used", "unfamiliar_fraction", "unfamiliar",
                             "epoch_spread_sd", "coverage_target", "explanation", "interpolated_channels", "low_confidence", "qc_status",
                             "quality_grade", "pipeline", "context", "model", "disclaimer", "created_at")}
    out.update(id=d["_id"], run_id=d["run_id"], recording_id=d["recording_id"], p_epoch=d.get("p_epoch", []),
               unreviewed=d["qc_status"] != "approved", reliability=_reliability(d))
    if rec:
        out["recording_code"] = rec["recording_code"]
    return out


def _reliability(d: dict) -> list[str]:
    """Plain-language reasons to be careful with this result."""
    w = []
    if d["unfamiliar"]:
        w.append(f"{round(100 * d['unfamiliar_fraction'])} % of this recording's 10 s windows look unlike the EEG the model was trained on "
                 "(other device, montage, population or recording condition). Treat the answer as unreliable.")
    if d["verdict"] == "uncertain":
        w.append("The model cannot separate the two classes for this recording at the stated confidence level.")
    if d["qc_status"] != "approved":
        w.append("The processing run has not been approved by QC.")
    if d["low_confidence"]:
        w.append("Few clean epochs were available.")
    if d["interpolated_channels"]:
        w.append(f"Channels were interpolated: {', '.join(d['interpolated_channels'])}.")
    if d["model"]["status"] != "approved":
        w.append(f"The model is registered as '{d['model']['status']}': not yet reviewed or approved for clinical use.")
    return w


@router.get("/ai/models")
def models(request: Request, db: Database = Depends(get_db), user: User = Depends(any_ai)):
    write_audit(db, "ai.models_view", user_id=user.id, request=request, entity_type="ai")
    return {"models": registry.list_models(), "disclaimer": svc.DISCLAIMER}


@router.post("/runs/{run_id}/ai/sedation", status_code=status.HTTP_202_ACCEPTED)
def run_sedation(run_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(write_ai)):
    run, rec = _load_run(db, user, run_id, write=True)
    if run["status"] != "succeeded":
        raise HTTPException(status.HTTP_409_CONFLICT, "The model can only be run on a finished processing run")
    job_id = jobs.create_job(db, job_type="ai_sedation", user_id=user.id, target={"run_id": run_id, "recording_id": rec["_id"], "patient_id": rec["patient_id"]})
    audit_patient_access(request, db, user, "ai.run", rec["patient_id"], {"run": str(run_id), "model": svc.model_dir_name()})

    def work(d, progress):
        try:
            svc.execute_sedation(d, run_id, user.id, progress)
        except svc.AiError as e:
            d.jobs.update_one({"_id": job_id}, {"$set": {"user_error": str(e)}})
            raise

    jobs.submit(job_id, work)
    return {"job_id": job_id, "run_id": run_id}


@router.get("/runs/{run_id}/ai/sedation")
def latest_prediction(run_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(read_ai)):
    run, rec = _load_run(db, user, run_id)
    d = db.ai_predictions.find_one({"run_id": run_id, "task": "sedation_level"}, sort=[("created_at", -1)])
    audit_patient_access(request, db, user, "ai.view", rec["patient_id"], {"run": str(run_id)})
    if d is None:
        return {"status": "none", "run_id": run_id, "qc_status": run["qc_status"]}
    return {"status": "done", **_out(d, rec)}


@router.get("/patients/{patient_id}/ai/sedation")
def patient_predictions(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(read_ai)):
    load_patient(db, user, patient_id)
    tz = get_settings().site_timezone
    latest: dict = {}
    for d in db.ai_predictions.find({"patient_id": patient_id, "task": "sedation_level"}).sort("created_at", 1):
        latest[d["run_id"]] = d
    rows = []
    for d in latest.values():
        rec = db.recordings.find_one({"_id": d["recording_id"]})
        start = edf_local_to_utc(rec["start_time"], tz) if rec and rec.get("start_time") else None
        rows.append({**_out(d, rec), "recording_start": start})
    rows.sort(key=lambda r: r["recording_start"] or r["created_at"])
    crsr = [{"assessed_at": a["assessed_at"], "total": a["total"]}
            for a in sorted(_latest(list(db.assessments.find({"patient_id": patient_id})), signed_only=True), key=lambda a: a["assessed_at"])]
    audit_patient_access(request, db, user, "ai.patient_view", patient_id)
    return {"items": rows, "crsr": crsr,
            "note": "Each point is the model's output for one recording. A change in this output between recordings made under different conditions "
                    "or sedation is not evidence of a change in the patient."}


@router.get("/ai/predictions")
def all_predictions(request: Request, db: Database = Depends(get_db), user: User = Depends(any_ai)):
    """Clinical roles: predictions for patients on their care team. Researchers: pseudonymised, consented patients only (no dates)."""
    roles = set(user.role_names)
    if roles & {CLINICIAN, TECHNOLOGIST, VIEWER}:
        pats = {p["_id"]: p for p in db.patients.find({"care_team.user_id": user.id}, {"display_id": 1})}
        pseudo = False
    elif RESEARCHER in roles:
        pats = {p["_id"]: p for p in db.patients.find({"consent_research": True}, {"display_id": 1, "date_of_injury": 1})}
        pseudo = True
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    latest: dict = {}
    for d in db.ai_predictions.find({"patient_id": {"$in": list(pats)}, "task": "sedation_level"}).sort("created_at", 1):
        latest[d["run_id"]] = d
    items = []
    for d in latest.values():
        p = pats[d["patient_id"]]
        base = {"id": d["_id"], "patient": p["display_id"], "p_sedated": d["p_sedated"], "verdict": d["verdict"], "unfamiliar": d["unfamiliar"],
                "model_version": d["model"]["version"], "condition": d["context"]["condition"], "unreviewed": d["qc_status"] != "approved"}
        if pseudo:
            inj = p.get("date_of_injury")
            base["days_since_injury"] = (d["created_at"].date() - date.fromisoformat(inj)).days if inj else None
        else:
            base.update(patient_id=d["patient_id"], run_id=d["run_id"], recording_id=d["recording_id"], created_at=d["created_at"])
        items.append(base)
    write_audit(db, "ai.predictions_view", user_id=user.id, request=request, entity_type="ai", details={"count": len(items), "pseudonymised": pseudo})
    return {"items": items, "pseudonymised": pseudo, "disclaimer": svc.DISCLAIMER, "as_of": utcnow()}
