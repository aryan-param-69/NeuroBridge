"""Phase 7: clinical summary reports.

A report is a SNAPSHOT of what the platform knew at generation time (assessments, approved recordings, an optional comparison, model outputs)
plus a clinician-written summary. It is stored as a draft, signed by its author, and then never changed: an amendment is a new version.
Each version carries a SHA-256 of its content, so a later edit to the database would be detected. The stored snapshot contains the platform
patient ID only (no name, date of birth or MRN) and no free-text observations; the exported document is matched to the chart by that ID.
"""

import hashlib
import html
import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from pymongo.database import Database

from app.api.deps import CLINICIAN, TECHNOLOGIST, VIEWER, audit_patient_access, require_roles
from app.api.v1.assessments import _latest
from app.api.v1.comparisons import build_comparison, usable_runs
from app.api.v1.patients import load_patient
from app.core.config import get_settings
from app.db.base import edf_local_to_utc, utcnow
from app.db.mongo import get_db
from app.models import User
from app.services import users as users_repo

router = APIRouter(tags=["reports"])
read_rep = require_roles(CLINICIAN, TECHNOLOGIST, VIEWER)
write_rep = require_roles(CLINICIAN)

LIMITATIONS = [
    "This report summarises measurements and model outputs. It is not a diagnosis and does not state or imply that the patient is recovering or deteriorating.",
    "EEG features were computed by a versioned, automated pipeline after quality control; interpretation belongs to the responsible clinician.",
    "The CRS-R categories shown are those entered by the assessing clinician. The platform never assigns a category.",
    "Differences between recordings can arise from sedation, recording condition, quality or time of day rather than from the patient's brain; context differences are listed where present.",
    "The sedation-level model was trained on 2,814 ten-second EEG windows (about 8 hours) from 131 recordings of 40 healthy volunteers at two laboratories and tested on people it had never seen (balanced accuracy 0.97); patient validation is the next stage, so its output is research decision support only.",
    "Motor-imagery or sedation classifiers are not consciousness or covert-awareness detectors.",
]


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="EEG and assessment summary", min_length=3, max_length=120)
    clinical_summary: str = Field(default="", max_length=4000)
    include_assessments: bool = True
    include_recordings: bool = True
    include_ai: bool = False
    include_notes: bool = False
    base_run: uuid.UUID | None = None
    target_run: uuid.UUID | None = None


class ReportPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=3, max_length=120)
    clinical_summary: str | None = Field(default=None, max_length=4000)


class SignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amendment_reason: str | None = Field(default=None, max_length=500)


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)


def content_hash(doc: dict) -> str:
    return hashlib.sha256(_canon({"title": doc["title"], "clinical_summary": doc["clinical_summary"], "snapshot": doc["snapshot"]}).encode()).hexdigest()


def _snapshot(db: Database, user: User, patient: dict, body: ReportIn) -> dict:
    pid = patient["_id"]
    tz = get_settings().site_timezone
    snap: dict = {"generated_at": utcnow(), "generated_by": user.full_name, "limitations": LIMITATIONS,
                  "patient": {"display_id": patient["display_id"], "sex": patient["sex"], "etiology": patient["etiology"],
                              "date_of_injury": patient.get("date_of_injury"), "status": patient["status"]}}
    if body.include_assessments:
        docs = _latest(list(db.assessments.find({"patient_id": pid})), signed_only=True)
        snap["assessments"] = [{"assessed_at": d["assessed_at"], "version": d["version"], "total": d["total"], "scores": d["scores"],
                                "category": d.get("clinician_category"), "setting": d.get("setting"), "sedation_status": d.get("sedation_status"),
                                "confounders": d.get("confounders", []), "amended": d["version"] > 1} for d in sorted(docs, key=lambda d: d["assessed_at"])]
    if body.include_recordings:
        rows = []
        for x in reversed(usable_runs(db, pid)):
            run, rec = x["run"], x["rec"]
            cond = rec.get("condition") or {}
            g = {(r["feature"], r["band"]): r for r in db.feature_summaries.find({"run_id": run["_id"], "level": "global", "feature": "rel_power"})}
            rows.append({"recording_code": rec["recording_code"], "start": edf_local_to_utc(rec["start_time"], tz) if rec.get("start_time") else None,
                         "condition": cond.get("condition"), "patient_state": cond.get("patient_state"),
                         "sedation": [d.get("agent") for d in (cond.get("sedation") or [])], "quality_grade": (run.get("quality") or {}).get("grade"),
                         "pipeline": f"{run['pipeline_name']} v{run['pipeline_version']}", "params_hash": run["params_hash"], "features_version": x["fr"].get("version"),
                         "n_epochs": x["fr"].get("n_epochs"), "is_baseline": rec.get("is_baseline", False), "low_confidence": bool(run.get("low_confidence")),
                         "relative_power": {band: {"mean": r["mean"], "ci": [r["ci_low"], r["ci_high"]]} for (_, band), r in g.items()}})
        snap["recordings"] = rows
    if body.base_run and body.target_run:
        c = build_comparison(db, user, pid, body.base_run, body.target_run, "global")
        top = sorted((i for i in c["items"] if i["interval_separated"]), key=lambda i: -abs(i["relative"] or 0))[:15]
        snap["comparison"] = {"base": c["base"]["recording_code"], "target": c["target"]["recording_code"], "days_between": c["days_between"],
                              "warnings": [w["message"] for w in c["warnings"]], "n_features": c["n_features"], "n_separated": c["n_separated"],
                              "top_changes": top, "assessments": c["assessments"], "note": c["note"]}
    elif body.base_run or body.target_run:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose both a base and a target recording, or neither")
    if body.include_ai:
        latest: dict = {}
        for d in db.ai_predictions.find({"patient_id": pid, "task": "sedation_level"}).sort("created_at", 1):
            latest[d["run_id"]] = d
        snap["ai"] = [{"recording_id": d["recording_id"], "verdict": d["verdict"], "p_sedated": d["p_sedated"], "prediction_set": d["prediction_set"],
                       "unfamiliar": d["unfamiliar"], "model": d["model"], "qc_status": d["qc_status"], "created_at": d["created_at"]} for d in latest.values()]
    if body.include_notes:
        best: dict = {}
        for n in db.clinician_notes.find({"patient_id": pid}):
            if n["note_id"] not in best or n["version"] > best[n["note_id"]]["version"]:
                best[n["note_id"]] = n
        names = {u.id: u.full_name for u in users_repo.get_many(db, list({n["author_id"] for n in best.values()}), active_only=False).values()}
        snap["notes"] = [{"type": n["note_type"], "written_at": n["first_written_at"], "author": names.get(n["author_id"], "Unknown"), "body": n["body"]}
                         for n in sorted(best.values(), key=lambda n: n["first_written_at"])]
    return snap


def _out(d: dict, names: dict, with_snapshot: bool = True) -> dict:
    out = {"id": d["_id"], "report_id": d["report_id"], "version": d["version"], "status": d["status"], "title": d["title"],
           "clinical_summary": d["clinical_summary"], "author": {"id": d["author_id"], "name": names.get(d["author_id"], "Unknown")},
           "created_at": d["created_at"], "signed_at": d.get("signed_at"), "signed_by": names.get(d.get("signed_by")) if d.get("signed_by") else None,
           "amends": d.get("amends"), "amendment_reason": d.get("amendment_reason"), "content_sha256": d["content_sha256"],
           "integrity_ok": content_hash(d) == d["content_sha256"], "options": d["options"]}
    if with_snapshot:
        out["snapshot"] = d["snapshot"]
    return out


def _names(db: Database, docs: list[dict]) -> dict:
    ids = list({i for d in docs for i in (d["author_id"], d.get("signed_by")) if i})
    return {u.id: u.full_name for u in users_repo.get_many(db, ids, active_only=False).values()}


def _load(db: Database, user: User, report_id: uuid.UUID, *, write: bool = False) -> tuple[dict, dict]:
    d = db.reports.find_one({"_id": report_id})
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    patient = load_patient(db, user, d["patient_id"], write=write)
    if CLINICIAN not in user.role_names and d["status"] != "signed":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return d, patient


@router.post("/patients/{patient_id}/reports", status_code=status.HTTP_201_CREATED)
def create_report(patient_id: uuid.UUID, body: ReportIn, request: Request, db: Database = Depends(get_db), user: User = Depends(write_rep)):
    patient = load_patient(db, user, patient_id, write=True)
    doc = {"_id": uuid.uuid4(), "report_id": uuid.uuid4(), "version": 1, "patient_id": patient_id, "status": "draft", "author_id": user.id,
           "title": body.title, "clinical_summary": body.clinical_summary, "created_at": utcnow(), "options": body.model_dump(mode="json", exclude={"title", "clinical_summary"}),
           "snapshot": _snapshot(db, user, patient, body)}
    doc["content_sha256"] = content_hash(doc)
    db.reports.insert_one(doc)
    audit_patient_access(request, db, user, "report.create", patient_id, {"report": str(doc["report_id"])})
    return _out(doc, {user.id: user.full_name})


@router.get("/patients/{patient_id}/reports")
def list_reports(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(read_rep)):
    load_patient(db, user, patient_id)
    docs = list(db.reports.find({"patient_id": patient_id}))
    if CLINICIAN not in user.role_names:
        docs = [d for d in docs if d["status"] == "signed"]
    best: dict = {}
    for d in docs:
        if d["report_id"] not in best or d["version"] > best[d["report_id"]]["version"]:
            best[d["report_id"]] = d
    rows = sorted(best.values(), key=lambda d: d["created_at"], reverse=True)
    names = _names(db, rows)
    audit_patient_access(request, db, user, "report.list", patient_id)
    return [_out(d, names, with_snapshot=False) for d in rows]


@router.get("/reports/{report_id}")
def get_report(report_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(read_rep)):
    d, _ = _load(db, user, report_id)
    versions = list(db.reports.find({"report_id": d["report_id"]}).sort("version", 1))
    if CLINICIAN not in user.role_names:
        versions = [v for v in versions if v["status"] == "signed"]
    names = _names(db, versions)
    audit_patient_access(request, db, user, "report.view", d["patient_id"], {"report": str(d["report_id"]), "version": d["version"]})
    return {**_out(d, names), "versions": [{"id": v["_id"], "version": v["version"], "status": v["status"], "signed_at": v.get("signed_at"),
                                            "amendment_reason": v.get("amendment_reason")} for v in versions]}


@router.patch("/reports/{report_id}")
def edit_report(report_id: uuid.UUID, body: ReportPatch, request: Request, db: Database = Depends(get_db), user: User = Depends(write_rep)):
    d, _ = _load(db, user, report_id, write=True)
    if d["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "A signed report cannot be edited; amend it to start a new version")
    if d["author_id"] != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author can edit a draft")
    sets = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    d.update(sets)
    sets["content_sha256"] = content_hash(d)
    db.reports.update_one({"_id": report_id, "status": "draft"}, {"$set": sets})
    audit_patient_access(request, db, user, "report.edit", d["patient_id"], {"report": str(d["report_id"])})
    return _out(db.reports.find_one({"_id": report_id}), {user.id: user.full_name})


@router.post("/reports/{report_id}/refresh")
def refresh_snapshot(report_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(write_rep)):
    """Rebuild a DRAFT's snapshot from the current data (same options)."""
    d, patient = _load(db, user, report_id, write=True)
    if d["status"] != "draft" or d["author_id"] != user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only the author can refresh a draft")
    opts = ReportIn(title=d["title"], clinical_summary=d["clinical_summary"], **d["options"])
    d["snapshot"] = _snapshot(db, user, patient, opts)
    db.reports.update_one({"_id": report_id}, {"$set": {"snapshot": d["snapshot"], "content_sha256": content_hash(d)}})
    audit_patient_access(request, db, user, "report.refresh", d["patient_id"], {"report": str(d["report_id"])})
    return _out(db.reports.find_one({"_id": report_id}), {user.id: user.full_name})


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(report_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(write_rep)):
    d, _ = _load(db, user, report_id, write=True)
    if d["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "A signed report cannot be deleted")
    if d["author_id"] != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author can delete a draft")
    db.reports.delete_one({"_id": report_id, "status": "draft"})
    audit_patient_access(request, db, user, "report.delete_draft", d["patient_id"], {"report": str(d["report_id"])})


@router.post("/reports/{report_id}/sign")
def sign_report(report_id: uuid.UUID, body: SignBody, request: Request, db: Database = Depends(get_db), user: User = Depends(write_rep)):
    d, _ = _load(db, user, report_id, write=True)
    if d["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "This report is already signed")
    if d["author_id"] != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author can sign their report")
    if not d["clinical_summary"].strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Write the clinical summary before signing: the platform does not interpret results for you")
    reason = (body.amendment_reason or "").strip()
    if d["version"] > 1 and len(reason) < 5:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Say why this amendment was made (at least 5 characters)")
    sets = {"status": "signed", "signed_by": user.id, "signed_at": utcnow(), "content_sha256": content_hash(d)}
    if d["version"] > 1:
        sets["amendment_reason"] = reason
    res = db.reports.update_one({"_id": report_id, "status": "draft"}, {"$set": sets})
    if res.modified_count != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "This report is already signed")
    audit_patient_access(request, db, user, "report.sign", d["patient_id"], {"report": str(d["report_id"]), "version": d["version"]})
    return _out(db.reports.find_one({"_id": report_id}), {user.id: user.full_name})


@router.post("/reports/{report_id}/amend", status_code=status.HTTP_201_CREATED)
def amend_report(report_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(write_rep)):
    d, patient = _load(db, user, report_id, write=True)
    if d["status"] != "signed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a signed report is amended; edit the draft instead")
    if db.reports.find_one({"report_id": d["report_id"], "version": {"$gt": d["version"]}}, {"_id": 1}):
        raise HTTPException(status.HTTP_409_CONFLICT, "A newer version already exists")
    new = {**{k: v for k, v in d.items() if k not in ("_id", "signed_at", "signed_by", "amendment_reason")}, "_id": uuid.uuid4(), "version": d["version"] + 1,
           "status": "draft", "author_id": user.id, "created_at": utcnow(), "amends": d["_id"]}
    db.reports.insert_one(new)  # unique (report_id, version) stops two concurrent amendments
    audit_patient_access(request, db, user, "report.amend", d["patient_id"], {"report": str(d["report_id"]), "version": new["version"]})
    return _out(new, {user.id: user.full_name})


# ---- HTML export -------------------------------------------------------------------------------------------------------------

def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _day(v) -> str:
    return _e(v.strftime("%Y-%m-%d %H:%M") if hasattr(v, "strftime") else v)


def render_html(d: dict, signer: str | None) -> str:
    s = d["snapshot"]
    p = s["patient"]
    h = [f"<h1>{_e(d['title'])}</h1>",
         f"<p class='meta'>Platform patient ID <b>{_e(p['display_id'])}</b> · {_e(p['sex'])} · {_e(str(p['etiology']).replace('_', ' '))}"
         f"{' · injury ' + _e(p['date_of_injury']) if p.get('date_of_injury') else ''}<br>Generated {_day(s['generated_at'])} UTC by {_e(s['generated_by'])} · version {d['version']} · "
         f"<b>{_e(d['status'].upper())}</b>{' by ' + _e(signer) + ' on ' + _day(d.get('signed_at')) if d['status'] == 'signed' else ' (not signed, not valid as a record)'}</p>"]
    if d.get("amendment_reason"):
        h.append(f"<p class='note'>Amendment reason: {_e(d['amendment_reason'])}</p>")
    h.append(f"<h2>Clinical summary (written by the clinician)</h2><p style='white-space:pre-wrap'>{_e(d['clinical_summary']) or '<i>none</i>'}</p>")
    if "assessments" in s:
        h.append("<h2>CRS-R assessments (signed)</h2><table><tr><th>Date</th><th>Total</th><th>Aud</th><th>Vis</th><th>Mot</th><th>Oro</th><th>Com</th><th>Aro</th><th>Clinician category</th><th>Sedation</th></tr>")
        for a in s["assessments"]:
            sc = a["scores"]
            h.append(f"<tr><td>{_day(a['assessed_at'])}{' (amended)' if a['amended'] else ''}</td><td>{a['total']}/23</td>"
                     + "".join(f"<td>{sc[k]}</td>" for k in ("auditory", "visual", "motor", "oromotor_verbal", "communication", "arousal"))
                     + f"<td>{_e(a['category'])}</td><td>{_e(a['sedation_status'])}</td></tr>")
        h.append("</table>" if s["assessments"] else "<tr><td colspan='10'>None</td></tr></table>")
    if "recordings" in s:
        h.append("<h2>EEG recordings (approved by QC)</h2><table><tr><th>Recording</th><th>Date</th><th>Condition</th><th>Sedation</th><th>Quality</th><th>Pipeline</th>"
                 "<th>Relative power (global, 95 % CI): delta / theta / alpha / beta</th></tr>")
        for r in s["recordings"]:
            rp = r["relative_power"]
            pw = " / ".join(f"{rp[b]['mean']:.2f}" if b in rp else "–" for b in ("delta", "theta", "alpha", "beta"))
            h.append(f"<tr><td>{_e(r['recording_code'])}{' ★' if r['is_baseline'] else ''}</td><td>{_day(r['start'])}</td><td>{_e(str(r['condition']).replace('_', ' '))}</td>"
                     f"<td>{_e(', '.join(r['sedation']) or 'none')}</td><td>{_e(r['quality_grade'])}{' (few epochs)' if r['low_confidence'] else ''}</td><td>{_e(r['pipeline'])}</td><td>{pw}</td></tr>")
        h.append("</table>")
    if "comparison" in s:
        c = s["comparison"]
        h.append(f"<h2>Comparison: {_e(c['base'])} → {_e(c['target'])}</h2><p>{_e(c['days_between'])} days apart. {c['n_separated']} of {c['n_features']} global features have non-overlapping 95 % intervals.</p>")
        if c["warnings"]:
            h.append("<p class='note'><b>Context differences:</b></p><ul>" + "".join(f"<li>{_e(w)}</li>" for w in c["warnings"]) + "</ul>")
        h.append("<table><tr><th>Feature</th><th>Where</th><th>Base</th><th>Target</th><th>Change</th></tr>")
        for i in c["top_changes"]:
            h.append(f"<tr><td>{_e(i['feature'])}{' ' + _e(i['band']) if i['band'] else ''}</td><td>{_e(i['name'])}</td><td>{i['base']:.3g}</td><td>{i['target']:.3g}</td><td>{i['delta']:+.3g}</td></tr>")
        h.append(f"</table><p class='small'>{_e(c['note'])}</p>")
    if "ai" in s:
        h.append("<h2>Sedation-level model output (research decision support)</h2><table><tr><th>Output</th><th>P(sedated-like)</th><th>Unfamiliar EEG</th><th>Model</th></tr>")
        for a in s["ai"]:
            h.append(f"<tr><td>{_e(a['verdict'])}</td><td>{100 * a['p_sedated']:.0f} %</td><td>{'yes' if a['unfamiliar'] else 'no'}</td><td>{_e(a['model']['version'])} ({_e(a['model']['status'])}) {_e(a['model']['sha256'][:12])}</td></tr>")
        h.append("</table>")
    if "notes" in s:
        h.append("<h2>Clinician notes</h2>" + "".join(f"<p><b>{_e(n['type'])}</b> · {_e(n['author'])} · {_day(n['written_at'])}<br>{_e(n['body'])}</p>" for n in s["notes"]))
    h.append("<h2>Limitations</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in s["limitations"]) + "</ul>")
    h.append(f"<p class='small'>Content SHA-256: {_e(d['content_sha256'])}</p>")
    css = ("body{font-family:Inter,Arial,sans-serif;max-width:900px;margin:32px auto;color:#1B2733;line-height:1.45}h1{font-size:22px}h2{font-size:16px;margin-top:24px;border-bottom:1px solid #E3E8EF;padding-bottom:4px}"
           "table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid #E3E8EF;padding:5px 8px;text-align:left}.meta,.small{color:#5B6B7F;font-size:12px}.note{background:#FFF7E6;padding:8px;border-radius:6px}")
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{_e(d['title'])}</title><style>{css}</style></head><body>{''.join(h)}</body></html>"


@router.get("/reports/{report_id}/export.html")
def export_html(report_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(read_rep),
                disposition: Literal["attachment", "inline"] = Query("attachment")):
    d, _ = _load(db, user, report_id)
    if content_hash(d) != d["content_sha256"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "This report no longer matches its recorded hash and cannot be exported")
    signer = _names(db, [d]).get(d.get("signed_by")) if d.get("signed_by") else None
    audit_patient_access(request, db, user, "report.export", d["patient_id"], {"report": str(d["report_id"]), "version": d["version"]})
    return Response(render_html(d, signer), media_type="text/html; charset=utf-8", headers={
        "Content-Disposition": f'{disposition}; filename="report-{d["snapshot"]["patient"]["display_id"]}-v{d["version"]}.html"',
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'", "X-Content-Type-Options": "nosniff"})
