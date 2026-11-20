"""Cross-patient lists for the sidebar pages: all recordings and an assessment overview for the patients on the user's care team."""

import re
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pymongo.database import Database

from app.api.deps import CLINICIAN, TECHNOLOGIST, VIEWER, require_roles
from app.api.v1.assessments import _latest
from app.api.v1.recordings import _summary
from app.core.config import get_settings
from app.db.base import utcnow
from app.db.mongo import get_db
from app.models import User
from app.services.audit import write_audit

router = APIRouter(prefix="/overview", tags=["overview"])
read_clin = require_roles(CLINICIAN, TECHNOLOGIST, VIEWER)


def _team_patients(db: Database, user: User, *, active_only: bool = False) -> dict:
    q: dict = {"care_team.user_id": user.id}
    if active_only:
        q["status"] = "active"
    return {p["_id"]: p for p in db.patients.find(q, {"display_id": 1, "status": 1, "date_of_injury": 1})}


@router.get("/recordings")
def all_recordings(
    request: Request, qc_status: Literal["pending", "approved", "rejected"] | None = None,
    quality: Literal["good", "fair", "poor"] | None = None, q: str | None = Query(None, max_length=40),
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    db: Database = Depends(get_db), user: User = Depends(read_clin),
):
    pats = _team_patients(db, user)
    cond: dict = {"patient_id": {"$in": list(pats)}}
    if qc_status:
        cond["qc_status"] = qc_status
    if quality:
        cond["quality_grade"] = quality
    if q:
        needle = q.strip().upper()[:40]
        ids = [pid for pid, p in pats.items() if needle in p["display_id"].upper()]
        cond["$or"] = [{"patient_id": {"$in": ids}}, {"recording_code": {"$regex": f"^{re.escape(needle)}", "$options": "i"}}]
    total = db.recordings.count_documents(cond)
    rows = list(db.recordings.find(cond).sort("created_at", -1).skip((page - 1) * page_size).limit(page_size))
    write_audit(db, "overview.recordings", user_id=user.id, request=request, entity_type="overview", details={"count": len(rows)})
    return {"total": total, "page": page, "page_size": page_size,
            "items": [{**_summary(r), "display_id": pats[r["patient_id"]]["display_id"]} for r in rows],
            "counts": {s: db.recordings.count_documents({"patient_id": {"$in": list(pats)}, "qc_status": s}) for s in ("pending", "approved", "rejected")}}


@router.get("/assessments")
def assessment_overview(request: Request, db: Database = Depends(get_db), user: User = Depends(read_clin)):
    """One row per ACTIVE patient on the user's team: latest signed CRS-R, how many in the reminder window, whether a repeat is due."""
    s = get_settings()
    pats = _team_patients(db, user, active_only=True)
    docs = list(db.assessments.find({"patient_id": {"$in": list(pats)}}))
    by_patient: dict = {pid: [] for pid in pats}
    for d in docs:
        by_patient[d["patient_id"]].append(d)
    since = utcnow() - timedelta(days=s.reminder_window_days)
    rows = []
    for pid, p in pats.items():
        signed = _latest(by_patient[pid], signed_only=True)
        recent = [d for d in signed if d["assessed_at"] >= since]
        mine = sum(1 for d in by_patient[pid] if d["status"] == "draft" and d["assessor_id"] == user.id)
        last = signed[0] if signed else None
        rows.append({"patient_id": pid, "display_id": p["display_id"], "n_signed": len(signed), "count_in_window": len(recent),
                     "due": len(recent) < s.reminder_min_assessments, "my_drafts": mine,
                     "last": None if last is None else {"id": last["_id"], "assessed_at": last["assessed_at"], "total": last["total"],
                                                         "category": last.get("clinician_category"), "version": last["version"]}})
    rows.sort(key=lambda r: (not r["due"], r["last"]["assessed_at"].timestamp() if r["last"] else 0))
    write_audit(db, "overview.assessments", user_id=user.id, request=request, entity_type="overview", details={"patients": len(rows)})
    return {"window_days": s.reminder_window_days, "required": s.reminder_min_assessments, "items": rows,
            "due_count": sum(1 for r in rows if r["due"]),
            "basis": "Repeated behavioural assessment lowers the risk of misdiagnosis (e.g. Wannez et al., 2017). The window and the number are "
                     "site settings; check them against current guidance."}


@router.get("/reports")
def all_reports(request: Request, db: Database = Depends(get_db), user: User = Depends(read_clin)):
    """Latest version of every report for the user's patients (drafts are visible to clinicians only)."""
    pats = _team_patients(db, user)
    docs = list(db.reports.find({"patient_id": {"$in": list(pats)}}, {"snapshot": 0}))
    if CLINICIAN not in user.role_names:
        docs = [d for d in docs if d["status"] == "signed"]
    best: dict = {}
    for d in docs:
        if d["report_id"] not in best or d["version"] > best[d["report_id"]]["version"]:
            best[d["report_id"]] = d
    rows = sorted(best.values(), key=lambda d: d["created_at"], reverse=True)
    write_audit(db, "overview.reports", user_id=user.id, request=request, entity_type="overview", details={"count": len(rows)})
    return {"items": [{"id": d["_id"], "report_id": d["report_id"], "patient_id": d["patient_id"], "display_id": pats[d["patient_id"]]["display_id"],
                       "title": d["title"], "version": d["version"], "status": d["status"], "created_at": d["created_at"], "signed_at": d.get("signed_at")} for d in rows]}
