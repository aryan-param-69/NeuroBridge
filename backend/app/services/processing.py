"""Processing runs: creation, execution with full provenance, and QC state."""

import platform
import sys
import uuid

import mne
import numpy as np
import scipy
from pymongo.database import Database

from app import storage
from app.core.config import get_settings
from app.db.base import utcnow
from app.eeg import preprocess as pp
from app.eeg import quality as Q
from app.eeg import signal as sig


def software_versions() -> dict:
    return {"python": sys.version.split()[0], "mne": mne.__version__, "numpy": np.__version__,
            "scipy": scipy.__version__, "app": get_settings().app_version, "quality_algorithm": Q.QUALITY_VERSION,
            "platform": platform.platform()}


def create_run_doc(db: Database, *, recording: dict, user_id: uuid.UUID, name: str, version: str, params: dict,
                   params_hash: str) -> uuid.UUID:
    run_id = uuid.uuid4()
    db.processing_runs.insert_one({
        "_id": run_id, "recording_id": recording["_id"], "patient_id": recording["patient_id"],
        "pipeline_name": name, "pipeline_version": version, "params": params, "params_hash": params_hash,
        "input_sha256": recording["sha256"], "software_versions": software_versions(), "status": "queued",
        "steps": [], "qc_status": "pending", "qc_by": None, "qc_at": None, "qc_note": None, "is_current": False,
        "created_by": user_id, "created_at": utcnow(), "started_at": None, "finished_at": None, "error": None,
    })
    return run_id


def _segments(db: Database, recording_id: uuid.UUID) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    excluded, selected = [], []
    for a in db.segment_annotations.find({"recording_id": recording_id}):
        span = (a["onset_s"], a["onset_s"] + a["duration_s"])
        if a["label"] == pp.SELECT_LABEL:
            selected.append(span)
        elif a["label"] in pp.EXCLUDE_LABELS:
            excluded.append(span)
    return excluded, selected


def execute_run(db: Database, run_id: uuid.UUID, progress) -> None:
    run = db.processing_runs.find_one({"_id": run_id})
    rec = db.recordings.find_one({"_id": run["recording_id"]})
    db.processing_runs.update_one({"_id": run_id}, {"$set": {"status": "running", "started_at": utcnow()}})
    try:
        eeg = [c for c in rec["channels"] if c["ch_type"] == "eeg"]
        if not eeg:
            raise pp.PipelineFailure("The recording has no EEG channels")
        n_samples = int(rec["duration_s"] * max(c["sfreq_hz"] for c in eeg))
        if n_samples * len(eeg) * 8 * 2 > get_settings().max_process_bytes:
            raise pp.PipelineFailure("This recording is too large to process in memory with this server configuration")

        progress(2, "Loading the recording")
        src = storage.deid_path(rec["patient_id"], rec["_id"])
        raw = mne.io.read_raw_edf(src, preload=True, infer_types=False, verbose="ERROR")
        raw.pick([raw.ch_names[c["index"]] for c in eeg])
        raw.rename_channels({raw.ch_names[i]: c["name"] for i, c in enumerate(eeg)})

        user_bad = {c["name"] for c in eeg if c.get("user_flag") == "bad"}
        user_ok = {c["name"] for c in eeg if c.get("user_flag") == "ok"}
        excluded, selected = _segments(db, rec["_id"])
        res = pp.run_pipeline(raw, eeg, run["params"], user_bad, user_ok, excluded, selected, progress)

        progress(92, "Saving processed data")
        out = storage.processed_path(rec["patient_id"], run_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        res["processed"].save(out, fmt="single", overwrite=True, verbose=False)
        sig.forget(out)

        q = res["quality"]
        db.processing_runs.update_one({"_id": run_id}, {"$set": {
            "status": "succeeded", "finished_at": utcnow(), "steps": res["steps"], "quality": q,
            "bad_channels": res["bad_channels"], "interpolated_channels": res["interpolated"],
            "dropped_channels": res["dropped"], "sfreq_hz": res["sfreq_hz"], "output_sha256": res["output_sha256"],
            "low_confidence": res["low_confidence"], "warnings": res["warnings"], "channels": res["channels"],
            "epochs": res["epochs"], "n_epochs_total": res["epochs"]["summary"]["total"],
            "n_epochs_kept": res["epochs"]["summary"]["kept"], "segments_proposed": q["segments"],
        }})
        # Make the viewer's per-channel flags reflect the new assessment (user decisions are kept separately).
        by_name = {c["name"]: c for c in q["channels"]}
        sets = {f"channels.{i}.auto_flag": ("ok" if not by_name[c["name"]]["bad"] else by_name[c["name"]]["flags"][0])
                for i, c in enumerate(rec["channels"]) if c["ch_type"] == "eeg" and c["name"] in by_name}
        sets["quality_grade"] = q["grade"]
        sets["quality_score"] = q["overall_score"]
        db.recordings.update_one({"_id": rec["_id"]}, {"$set": sets})
        progress(100, "Finished")
    except Exception as e:
        msg = str(e) if isinstance(e, (pp.PipelineFailure, Q.QualityError)) else f"Processing failed: {str(e)[:200]}"
        db.processing_runs.update_one({"_id": run_id}, {"$set": {"status": "failed", "error": msg, "finished_at": utcnow()}})
        raise
