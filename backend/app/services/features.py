"""Feature extraction for a processing run: read processed data, compute, store summaries in MongoDB."""

import uuid

import mne
import numpy as np
from pymongo.database import Database

from app import storage
from app.db.base import utcnow
from app.eeg.features import extract as E
from app.services.processing import software_versions


def create_feature_run(db: Database, run: dict, user_id: uuid.UUID, job_id: uuid.UUID) -> None:
    db.feature_runs.replace_one({"_id": run["_id"]}, {
        "_id": run["_id"], "run_id": run["_id"], "recording_id": run["recording_id"], "patient_id": run["patient_id"],
        "status": "queued", "created_by": user_id, "created_at": utcnow(), "job_id": job_id, "error": None,
    }, upsert=True)


def execute_features(db: Database, run_id: uuid.UUID, progress) -> None:
    run = db.processing_runs.find_one({"_id": run_id})
    db.feature_runs.update_one({"_id": run_id}, {"$set": {"status": "running", "started_at": utcnow()}})
    try:
        progress(2, "Loading processed data")
        path = storage.processed_path(run["patient_id"], run_id)
        raw = mne.io.read_raw_fif(path, preload=True, verbose="ERROR")
        names = list(raw.ch_names)
        if names != [c["name"] for c in run["channels"]]:
            raise E.FeatureError("Processed data and run record disagree about the channels")
        data = raw.get_data() * 1e6
        ep = run["epochs"]
        status = np.asarray(ep["status"])
        kept = np.flatnonzero(status == 0)
        to_kept = {int(e): i for i, e in enumerate(kept)}
        masked = {(to_kept[e], c) for e, c in zip(ep["masked"]["epoch"], ep["masked"]["channel"], strict=True) if e in to_kept}
        res = E.compute_run_features(data, float(raw.info["sfreq"]), names, np.asarray(ep["onset_s"])[kept], ep["length_s"],
                                     masked, progress=progress)

        progress(96, "Saving")
        db.feature_summaries.delete_many({"run_id": run_id})
        rows = [{**r, "run_id": run_id} for r in res["rows"]]
        for i in range(0, len(rows), 2000):
            db.feature_summaries.insert_many(rows[i:i + 2000])
        out = storage.processed_path(run["patient_id"], run_id).parent / "features_epochs.npz"
        np.savez_compressed(out, onsets_s=np.asarray(ep["onset_s"])[kept], channels=np.array(names),
                            **{k.replace("|", "__") or "x": v.astype(np.float32) for k, v in res["epoch_values"].items()})
        db.feature_runs.update_one({"_id": run_id}, {"$set": {
            "status": "succeeded", "finished_at": utcnow(), "version": res["version"], "n_epochs": res["n_epochs"],
            "n_channels": res["n_channels"], "n_rows": len(rows), "features_hash": res["features_hash"], "bands": res["bands"],
            "psd": res["psd"], "config": res["config"], "software_versions": software_versions(),
            "low_confidence": bool(run.get("low_confidence")), "epoch_file": out.name,
        }})
    except Exception as e:
        msg = str(e) if isinstance(e, E.FeatureError) else f"Feature extraction failed: {str(e)[:200]}"
        db.feature_runs.update_one({"_id": run_id}, {"$set": {"status": "failed", "error": msg, "finished_at": utcnow()}})
        raise
