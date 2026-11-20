"""Feature extraction, summaries, CSV export, PSD, spectrogram and topographic maps for a processing run."""

import csv
import io
import time
import uuid
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pymongo.database import Database

from app import jobs, storage
from app.api.deps import audit_patient_access, require_roles
from app.api.v1.processing import _load_run
from app.api.v1.recordings import READERS, WRITERS
from app.db.mongo import get_db
from app.eeg import signal as sig
from app.eeg.features import viz
from app.eeg.features.catalog import BAND_PLAIN, BANDS, CATALOG, FEATURES_VERSION, catalog_rows
from app.models import User
from app.services import features as svc

router = APIRouter(tags=["features"])
read_feat = require_roles(*READERS)
write_feat = require_roles(*WRITERS)

LEVELS = ("channel", "region", "global", "pair")
_last_audit: dict[tuple, float] = {}
AUDIT_INTERVAL_S = 600


def _throttled_audit(request, db, user, run, action):
    key = (user.id, run["_id"], action)
    if time.monotonic() - _last_audit.get(key, 0) > AUDIT_INTERVAL_S:
        _last_audit[key] = time.monotonic()
        audit_patient_access(request, db, user, action, run["patient_id"], {"run": str(run["_id"])})


def _feature_run(db: Database, run_id: uuid.UUID) -> dict:
    fr = db.feature_runs.find_one({"_id": run_id})
    if fr is None or fr["status"] != "succeeded":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No features for this run yet: compute them first")
    return fr


def _context(run: dict, fr: dict) -> dict:
    return {"run_id": run["_id"], "qc_status": run["qc_status"], "unreviewed": run["qc_status"] != "approved",
            "low_confidence": bool(run.get("low_confidence")), "features_version": fr.get("version"),
            "n_epochs": fr.get("n_epochs"), "features_hash": fr.get("features_hash")}


# ---- Catalogue ------------------------------------------------------------------------------------------------

@router.get("/features/catalog")
def features_catalog(_: User = Depends(read_feat)):
    return {"version": FEATURES_VERSION, "bands": {k: {"range_hz": list(v), "plain": BAND_PLAIN[k]} for k, v in BANDS.items()},
            "features": catalog_rows()}


# ---- Compute / status -------------------------------------------------------------------------------------------

@router.post("/runs/{run_id}/features", status_code=status.HTTP_202_ACCEPTED)
def compute_features(run_id: uuid.UUID, request: Request, db: Database = Depends(get_db), user: User = Depends(write_feat)):
    run, rec = _load_run(db, user, run_id, write=True)
    if run["status"] != "succeeded":
        raise HTTPException(status.HTTP_409_CONFLICT, "Features can only be computed from a finished processing run")
    cur = db.feature_runs.find_one({"_id": run_id})
    if cur and cur["status"] in ("queued", "running"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Features are already being computed for this run")
    job_id = jobs.create_job(db, job_type="features", user_id=user.id,
                             target={"run_id": run_id, "recording_id": rec["_id"], "patient_id": rec["patient_id"]})
    svc.create_feature_run(db, run, user.id, job_id)
    audit_patient_access(request, db, user, "features.compute", rec["patient_id"], {"run": str(run_id)})
    jobs.submit(job_id, lambda d, progress: svc.execute_features(d, run_id, progress))
    return {"job_id": job_id, "run_id": run_id}


@router.get("/runs/{run_id}/feature-run")
def feature_run_status(run_id: uuid.UUID, db: Database = Depends(get_db), user: User = Depends(read_feat)):
    run, _ = _load_run(db, user, run_id)
    fr = db.feature_runs.find_one({"_id": run_id}, {"psd": 0})
    if fr is None:
        return {"status": "none", "run_id": run_id, "qc_status": run["qc_status"]}
    return {"status": fr["status"], "run_id": run_id, "error": fr.get("error"), "job_id": fr.get("job_id"),
            "version": fr.get("version"), "n_epochs": fr.get("n_epochs"), "n_rows": fr.get("n_rows"),
            "features_hash": fr.get("features_hash"), "bands": fr.get("bands"), "low_confidence": fr.get("low_confidence"),
            "qc_status": run["qc_status"], "unreviewed": run["qc_status"] != "approved",
            "software_versions": fr.get("software_versions"), "finished_at": fr.get("finished_at")}


# ---- Summaries --------------------------------------------------------------------------------------------------

def _query(run_id, names, bands, channels, level):
    q: dict = {"run_id": run_id}
    if level != "all":
        q["level"] = level
    if names:
        q["feature"] = {"$in": [n.strip() for n in names.split(",") if n.strip()]}
    if bands:
        q["band"] = {"$in": [b.strip() for b in bands.split(",") if b.strip()]}
    if channels:
        q["name"] = {"$in": [c.strip() for c in channels.split(",") if c.strip()]}
    return q


def _row_out(r: dict) -> dict:
    return {k: r[k] for k in ("level", "name", "feature", "band", "mean", "median", "sd", "ci_low", "ci_high", "n_epochs", "unit")}


@router.get("/runs/{run_id}/features")
def get_features(
    run_id: uuid.UUID, request: Request,
    names: str | None = Query(None, max_length=500), bands: str | None = Query(None, max_length=300),
    channels: str | None = Query(None, max_length=1000),
    level: Literal["channel", "region", "global", "pair", "all"] = "global",
    limit: int = Query(5000, ge=1, le=20000),
    db: Database = Depends(get_db), user: User = Depends(read_feat),
):
    run, _ = _load_run(db, user, run_id)
    fr = _feature_run(db, run_id)
    _throttled_audit(request, db, user, run, "features.view")
    rows = list(db.feature_summaries.find(_query(run_id, names, bands, channels, level)).sort(
        [("feature", 1), ("band", 1), ("level", 1), ("name", 1)]).limit(limit))
    return {**_context(run, fr), "level": level, "rows": [_row_out(r) for r in rows]}


def _safe(text: str | None) -> str:
    t = "" if text is None else str(text)
    return "'" + t if t[:1] in ("=", "+", "-", "@", "\t", "\r") else t  # spreadsheet formula-injection guard


@router.get("/runs/{run_id}/features/export.csv")
def export_csv(run_id: uuid.UUID, request: Request, level: Literal["channel", "region", "global", "pair", "all"] = "all",
               db: Database = Depends(get_db), user: User = Depends(read_feat)):
    run, rec = _load_run(db, user, run_id)
    fr = _feature_run(db, run_id)
    audit_patient_access(request, db, user, "features.export_csv", rec["patient_id"], {"run": str(run_id), "level": level})
    cols = ["recording_code", "run_id", "pipeline", "features_version", "qc_status", "low_confidence", "level", "name", "feature", "band",
            "mean", "median", "sd", "ci_low", "ci_high", "n_epochs", "unit"]
    ctx = [rec["recording_code"], str(run_id), f"{run['pipeline_name']} v{run['pipeline_version']}", fr.get("version"),
           run["qc_status"], bool(run.get("low_confidence"))]

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        yield buf.getvalue()
        for r in db.feature_summaries.find({"run_id": run_id, **({} if level == "all" else {"level": level})}).sort(
                [("level", 1), ("name", 1), ("feature", 1), ("band", 1)]):
            buf.seek(0), buf.truncate()
            w.writerow([*ctx, r["level"], _safe(r["name"]), r["feature"], _safe(r["band"]), r["mean"], r["median"], r["sd"],
                        r["ci_low"], r["ci_high"], r["n_epochs"], _safe(r["unit"])])
            yield buf.getvalue()

    return StreamingResponse(gen(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{rec["recording_code"]}_features.csv"'})


@router.get("/runs/{run_id}/aperiodic")
def aperiodic(run_id: uuid.UUID, db: Database = Depends(get_db), user: User = Depends(read_feat)):
    run, _ = _load_run(db, user, run_id)
    fr = _feature_run(db, run_id)
    rows = db.feature_summaries.find({"run_id": run_id, "feature": {"$regex": "^aperiodic_"}})
    by: dict[tuple, dict] = {}
    for r in rows:
        by.setdefault((r["level"], r["name"]), {"level": r["level"], "name": r["name"]})[r["feature"].replace("aperiodic_", "")] = r["mean"]
    out = []
    for v in by.values():
        v["good_fit"] = bool(v.get("r2") is not None and v["r2"] >= 0.9)
        out.append(v)
    return {**_context(run, fr), "fit_range_hz": fr["config"]["aperiodic_range_hz"], "fits": out,
            "note": "Fits with R² below 0.9 are marked as unreliable."}


# ---- Spectrum, spectrogram, topomap -------------------------------------------------------------------------------

@router.get("/runs/{run_id}/psd")
def psd(run_id: uuid.UUID, request: Request, channels: str | None = Query(None, max_length=1000),
        groups: str | None = Query(None, max_length=500), fmin: float = Query(0.5, ge=0), fmax: float = Query(45.0, le=100),
        db: Database = Depends(get_db), user: User = Depends(read_feat)):
    run, _ = _load_run(db, user, run_id)
    fr = _feature_run(db, run_id)
    _throttled_audit(request, db, user, run, "features.view")
    doc = fr["psd"]
    f = np.array(doc["freqs_hz"])
    m = (f >= fmin) & (f <= fmax)
    sel = lambda v: [x for x, k in zip(v, m, strict=True) if k]  # noqa: E731
    want_c = [c.strip() for c in channels.split(",") if c.strip()] if channels else []
    want_g = [g.strip() for g in groups.split(",") if g.strip()] if groups else ["global"]
    bad = [c for c in want_c if c not in doc["channels"]] + [g for g in want_g if g not in doc["groups"]]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown channel or group: {', '.join(bad[:5])}")
    return {**_context(run, fr), "unit": doc["unit"], "freqs_hz": sel(doc["freqs_hz"]),
            "channels": {c: sel(doc["channels"][c]) for c in want_c},
            "groups": {g: {k: sel(v) for k, v in doc["groups"][g].items()} for g in want_g},
            "available_channels": list(doc["channels"]), "available_groups": list(doc["groups"]),
            "bands": fr["bands"]}


@router.get("/runs/{run_id}/spectrogram")
def get_spectrogram(run_id: uuid.UUID, request: Request, channel: str = Query(..., max_length=40),
                    fmax: float = Query(45.0, gt=1, le=100), db: Database = Depends(get_db), user: User = Depends(read_feat)):
    run, _ = _load_run(db, user, run_id)
    names = [c["name"] for c in run["channels"]]
    if channel not in names:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown channel")
    _throttled_audit(request, db, user, run, "features.view")
    raw = sig.open_raw(storage.processed_path(run["patient_id"], run_id))
    x = raw.get_data(picks=[names.index(channel)])[0] * 1e6
    out = viz.make_spectrogram(x, float(raw.info["sfreq"]), fmax=min(fmax, 0.45 * float(raw.info["sfreq"])))
    ep = run["epochs"]
    rejected = [{"onset": o, "duration": ep["length_s"], "status": st}
                for o, st in zip(ep["onset_s"], ep["status"], strict=True) if st != 0]
    return {"run_id": run_id, "channel": channel, "qc_status": run["qc_status"], "unreviewed": run["qc_status"] != "approved",
            "rejected_epochs": rejected[:2000], **out}


@router.get("/runs/{run_id}/topomap")
def topomap(run_id: uuid.UUID, request: Request, band: str | None = Query(None, max_length=40),
            feature: str = Query("rel_power", max_length=40), vmin: float | None = None, vmax: float | None = None,
            db: Database = Depends(get_db), user: User = Depends(read_feat)):
    run, _ = _load_run(db, user, run_id)
    fr = _feature_run(db, run_id)
    if feature not in CATALOG or CATALOG[feature].domain == "asymmetry":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a channel-level feature from the catalogue")
    q = {"run_id": run_id, "level": "channel", "feature": feature, "band": band}
    vals = {r["name"]: r["mean"] for r in db.feature_summaries.find(q) if r["mean"] is not None}
    if not vals:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No values for this feature/band")
    log = feature == "abs_power"
    if log:
        vals = {k: float(np.log10(v)) for k, v in vals.items() if v > 0}
    _throttled_audit(request, db, user, run, "features.view")
    title = f"{feature.replace('_', ' ')}" + (f" · {band}" if band else "")
    label = ("log10 " if log else "") + (CATALOG[feature].unit or "")
    try:
        svg = viz.topomap_svg(vals, title=title, vlim=(vmin, vmax) if vmin is not None and vmax is not None else None, label=label.strip())
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    headers = {"X-Unreviewed": str(run["qc_status"] != "approved").lower(), "X-Low-Confidence": str(bool(fr.get("low_confidence"))).lower()}
    return Response(content=svg, media_type="image/svg+xml", headers=headers)
