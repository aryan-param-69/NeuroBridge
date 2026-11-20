"""Phase 6: longitudinal comparison of one patient's EEG feature summaries (descriptive, with uncertainty and context warnings).

Rules that matter scientifically:
  - only runs that are approved by QC AND current may be compared (same gate as every downstream analysis);
  - runs made with different pipeline or feature versions are NOT compared (409), because the numbers would not mean the same thing;
  - differences in recording context (task, patient state, sedation, quality, channels, time of day) are always shown next to the numbers;
  - "interval-separated" only says that the two 95 % bootstrap intervals do not overlap. It is a screening flag, not a significance test,
    and with many features some separations are expected by chance.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pymongo.database import Database

from app.api.deps import CLINICIAN, TECHNOLOGIST, VIEWER, audit_patient_access, require_roles
from app.api.v1.assessments import _latest
from app.api.v1.patients import load_patient
from app.core.config import get_settings
from app.db.base import edf_local_to_utc
from app.db.mongo import get_db
from app.models import User

router = APIRouter(tags=["comparisons"])
read_cmp = require_roles(CLINICIAN, TECHNOLOGIST, VIEWER)
Level = Literal["global", "region", "channel", "pair"]
MAX_PAIR_HOURS_FOR_CRSR = 48


def _start_utc(rec: dict):
    return edf_local_to_utc(rec["start_time"], get_settings().site_timezone) if rec.get("start_time") else None


def usable_runs(db: Database, patient_id: uuid.UUID) -> list[dict]:
    """Approved + current + succeeded runs that also have finished features, newest first, with their recording."""
    out = []
    for run in db.processing_runs.find({"patient_id": patient_id, "qc_status": "approved", "is_current": True, "status": "succeeded"}):
        fr = db.feature_runs.find_one({"_id": run["_id"], "status": "succeeded"}, {"psd": 0})
        rec = db.recordings.find_one({"_id": run["recording_id"]})
        if fr and rec:
            out.append({"run": run, "fr": fr, "rec": rec})
    out.sort(key=lambda x: (_start_utc(x["rec"]) or x["run"]["created_at"]), reverse=True)
    return out


def _brief(x: dict) -> dict:
    run, fr, rec = x["run"], x["fr"], x["rec"]
    cond = rec.get("condition") or {}
    q = run.get("quality") or {}
    return {"run_id": run["_id"], "recording_id": rec["_id"], "recording_code": rec["recording_code"], "start": _start_utc(rec),
            "is_baseline": rec.get("is_baseline", False), "condition": cond.get("condition"), "patient_state": cond.get("patient_state"),
            "sedation": [f"{d.get('agent')} {d.get('dose')} {d.get('unit')}".strip() for d in (cond.get("sedation") or [])], "time_of_day": cond.get("time_of_day"), "setting": cond.get("setting"),
            "quality_grade": q.get("grade"), "pipeline": f"{run['pipeline_name']} v{run['pipeline_version']}", "features_version": fr.get("version"),
            "n_epochs": fr.get("n_epochs"), "low_confidence": bool(run.get("low_confidence")),
            "interpolated": run.get("interpolated_channels", []), "dropped": run.get("dropped_channels", [])}


def context_warnings(a: dict, b: dict) -> list[dict]:
    """Differences between two runs that can explain a feature difference without any change in the brain."""
    w: list[dict] = []

    def add(key, msg):
        w.append({"key": key, "message": msg})

    if a["condition"] != b["condition"]:
        add("condition", f"Different recording condition ({a['condition'] or 'unknown'} vs {b['condition'] or 'unknown'}); eyes open and eyes closed differ strongly in alpha.")
    if a["patient_state"] != b["patient_state"]:
        add("patient_state", f"Different patient state ({a['patient_state'] or 'unknown'} vs {b['patient_state'] or 'unknown'}).")
    if set(a["sedation"]) != set(b["sedation"]):
        add("sedation", f"Different sedation or sedating drugs ({', '.join(a['sedation']) or 'none'} vs {', '.join(b['sedation']) or 'none'}); sedatives change EEG power and connectivity.")
    if a["quality_grade"] != b["quality_grade"]:
        add("quality", f"Different recording quality ({a['quality_grade']} vs {b['quality_grade']}).")
    if set(a["interpolated"]) | set(a["dropped"]) != set(b["interpolated"]) | set(b["dropped"]):
        add("channels", "Different channels were interpolated or dropped, so regional values are not based on the same electrodes.")
    if a["low_confidence"] or b["low_confidence"]:
        add("low_confidence", "At least one recording has few clean epochs; its intervals are wide and values less reliable.")
    if a["time_of_day"] and b["time_of_day"]:
        try:
            h = lambda t: int(t[:2]) + int(t[3:5]) / 60  # noqa: E731
            d = abs(h(a["time_of_day"]) - h(b["time_of_day"]))
            if min(d, 24 - d) > 4:
                add("time_of_day", f"Recorded at different times of day ({a['time_of_day']} vs {b['time_of_day']}); vigilance varies over the day.")
        except ValueError:
            pass
    return w


def _runs_for(db: Database, user: User, patient_id: uuid.UUID, run_ids: list[uuid.UUID]) -> list[dict]:
    load_patient(db, user, patient_id)
    avail = {x["run"]["_id"]: x for x in usable_runs(db, patient_id)}
    missing = [r for r in run_ids if r not in avail]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found, or not approved, not current, or without finished features")
    return [avail[r] for r in run_ids]


@router.get("/patients/{patient_id}/comparison-candidates")
def candidates(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(read_cmp)):
    load_patient(db, user, patient_id)
    rows = [_brief(x) for x in usable_runs(db, patient_id)]
    audit_patient_access(request, db, user, "comparison.candidates", patient_id)
    return {"runs": rows, "default_base": next((r["run_id"] for r in rows if r["is_baseline"]), rows[-1]["run_id"] if rows else None),
            "default_target": rows[0]["run_id"] if rows else None,
            "note": "Only runs approved by QC, current, and with finished features can be compared."}


def _rows(db: Database, run_id: uuid.UUID, level: str) -> dict:
    return {(r["feature"], r["band"], r["name"]): r for r in db.feature_summaries.find({"run_id": run_id, "level": level})}


def _assessment_near(db: Database, patient_id: uuid.UUID, when):
    if when is None:
        return None
    docs = _latest(list(db.assessments.find({"patient_id": patient_id})), signed_only=True)
    best = min(docs, key=lambda d: abs((d["assessed_at"] - when).total_seconds()), default=None)
    if best is None or abs((best["assessed_at"] - when).total_seconds()) > MAX_PAIR_HOURS_FOR_CRSR * 3600:
        return None
    return {"id": best["_id"], "total": best["total"], "category": best.get("clinician_category"), "assessed_at": best["assessed_at"],
            "hours_from_recording": round((best["assessed_at"] - when).total_seconds() / 3600, 1)}


def build_comparison(db: Database, user: User, patient_id: uuid.UUID, base: uuid.UUID, target: uuid.UUID, level: str = "global",
                     feature: str | None = None) -> dict:
    """The comparison itself (shared by the endpoint and by reports). Raises HTTPException for every refusal."""
    if base == target:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose two different recordings to compare")
    xb, xt = _runs_for(db, user, patient_id, [base, target])
    b, t = _brief(xb), _brief(xt)
    blockers = []
    if b["pipeline"] != t["pipeline"]:
        blockers.append(f"Different pre-processing pipelines ({b['pipeline']} vs {t['pipeline']})")
    if xb["run"]["params_hash"] != xt["run"]["params_hash"] and b["pipeline"] == t["pipeline"]:
        blockers.append("Same pipeline version but different parameters (for example a different band-pass or reject threshold)")
    if b["features_version"] != t["features_version"]:
        blockers.append(f"Different feature versions ({b['features_version']} vs {t['features_version']})")
    if blockers:
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "These recordings were not processed the same way, so their numbers cannot be compared.", "reasons": blockers})
    rb, rt = _rows(db, base, level), _rows(db, target, level)
    items = []
    for key in sorted(set(rb) & set(rt)):
        if feature and key[0] != feature:
            continue
        x, y = rb[key], rt[key]
        if x["mean"] is None or y["mean"] is None:
            continue
        delta = y["mean"] - x["mean"]
        has_ci = None not in (x["ci_low"], x["ci_high"], y["ci_low"], y["ci_high"])
        separated = bool(has_ci and (y["ci_low"] > x["ci_high"] or y["ci_high"] < x["ci_low"]))
        items.append({"feature": key[0], "band": key[1], "name": key[2], "unit": y["unit"], "base": x["mean"], "target": y["mean"],
                      "base_ci": [x["ci_low"], x["ci_high"]], "target_ci": [y["ci_low"], y["ci_high"]], "delta": delta,
                      "relative": None if abs(x["mean"]) < 1e-12 else delta / abs(x["mean"]), "has_ci": has_ci, "interval_separated": separated})
    days = None if not (b["start"] and t["start"]) else round((t["start"] - b["start"]).total_seconds() / 86400, 2)
    return {"base": b, "target": t, "level": level, "days_between": days, "warnings": context_warnings(b, t), "items": items,
            "n_features": len(items), "n_separated": sum(1 for i in items if i["interval_separated"]),
            "assessments": {"base": _assessment_near(db, patient_id, b["start"]), "target": _assessment_near(db, patient_id, t["start"])},
            "note": ("'Interval-separated' means the two 95 % block-bootstrap intervals do not overlap. It is a screening flag, not a significance test: "
                     "with many features some will separate by chance, and context differences listed above can explain a difference without any "
                     "change in the brain. This view describes change; it does not say whether the patient is improving or recovering.")}


@router.get("/patients/{patient_id}/comparison")
def compare(patient_id: uuid.UUID, request: Request, base: uuid.UUID, target: uuid.UUID, level: Level = "global",
            feature: str | None = Query(None, max_length=60), db: Database = Depends(get_db), user: User = Depends(read_cmp)):
    out = build_comparison(db, user, patient_id, base, target, level, feature)
    audit_patient_access(request, db, user, "comparison.view", patient_id, {"base": str(base), "target": str(target), "level": level})
    return out


@router.get("/patients/{patient_id}/feature-trend")
def feature_trend(patient_id: uuid.UUID, request: Request, feature: str = Query(..., max_length=60), level: Level = "global",
                  name: str = Query("global", max_length=40), band: str | None = Query(None, max_length=30),
                  db: Database = Depends(get_db), user: User = Depends(read_cmp)):
    load_patient(db, user, patient_id)
    pts = []
    for x in reversed(usable_runs(db, patient_id)):
        r = db.feature_summaries.find_one({"run_id": x["run"]["_id"], "level": level, "feature": feature, "name": name, "band": band})
        if r is None or r["mean"] is None:
            continue
        brief = _brief(x)
        pts.append({"start": brief["start"], "recording_code": brief["recording_code"], "run_id": brief["run_id"], "mean": r["mean"],
                    "ci_low": r["ci_low"], "ci_high": r["ci_high"], "unit": r["unit"], "condition": brief["condition"], "sedation": brief["sedation"],
                    "quality_grade": brief["quality_grade"], "low_confidence": brief["low_confidence"], "is_baseline": brief["is_baseline"]})
    docs = _latest(list(db.assessments.find({"patient_id": patient_id})), signed_only=True)
    crsr = [{"assessed_at": d["assessed_at"], "total": d["total"]} for d in sorted(docs, key=lambda d: d["assessed_at"])]
    mixed = len({(p["condition"], tuple(sorted(p["sedation"]))) for p in pts}) > 1
    audit_patient_access(request, db, user, "comparison.trend", patient_id, {"feature": feature})
    return {"feature": feature, "level": level, "name": name, "band": band, "points": pts, "crsr": crsr, "mixed_context": mixed,
            "note": ("Points are recordings approved by QC, ordered by recording time. Recordings made under different conditions or sedation are "
                     "marked; do not read a trend across different conditions as a change in the patient.")}


__all__ = ["router", "usable_runs", "context_warnings", "build_comparison"]
