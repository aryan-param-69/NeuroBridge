"""Run the sedation-level model on a processed run and store an immutable prediction record."""

import uuid

import mne
import numpy as np
from pymongo.database import Database

from app import storage
from app.core.config import get_settings
from app.db.base import utcnow
from app.ml import registry
from app.ml import sed_predict as P
from app.ml.sed_features import STANDARD_19

WINDOW_S = 10.0
MIN_WINDOWS = 6  # one minute of clean data

DISCLAIMER = ("Decision support for research use, not a diagnosis. The model was trained only on healthy volunteers under propofol "
              "(40 people from two laboratories) and has never been validated on patients. It describes how much this recording resembles awake or "
              "moderately sedated EEG of those volunteers; it does not measure consciousness, covert awareness or recovery.")


def model_dir_name() -> str:
    return get_settings().sedation_model


class AiError(ValueError):
    """A problem the user can act on (shown in the UI)."""


def clean_windows(run: dict, n_samples: int, fs: float) -> list[tuple[int, int]]:
    """Non-overlapping 10 s windows that touch no rejected, annotated or unselected 2 s epoch of the processing run."""
    ep = run["epochs"]
    onset, status, length = np.asarray(ep["onset_s"]), np.asarray(ep["status"]), float(ep["length_s"])
    bad = [(o, o + length) for o, s in zip(onset, status, strict=True) if s != 0]
    total_s = n_samples / fs
    out = []
    t = 0.0
    while t + WINDOW_S <= total_s + 1e-9:
        if not any(a < t + WINDOW_S and b > t for a, b in bad):
            out.append((int(round(t * fs)), int(round((t + WINDOW_S) * fs))))
        t += WINDOW_S
    return out


def execute_sedation(db: Database, run_id: uuid.UUID, user_id: uuid.UUID, progress) -> uuid.UUID:
    run = db.processing_runs.find_one({"_id": run_id})
    rec = db.recordings.find_one({"_id": run["recording_id"]})
    progress(5, "Loading the model")
    try:
        bundle, card = registry.load(model_dir_name())
    except registry.ModelError as e:
        raise AiError(f"Model unavailable: {e}") from e
    progress(15, "Reading the processed recording")
    raw = mne.io.read_raw_fif(storage.processed_path(run["patient_id"], run_id), preload=True, verbose="ERROR")
    names = list(raw.ch_names)
    missing = [c for c in STANDARD_19 if c not in names]
    if missing:
        raise AiError(f"The model needs the 19 standard 10-20 channels; missing in this run: {', '.join(missing)}")
    fs = float(raw.info["sfreq"])
    x = raw.get_data(picks=STANDARD_19) * 1e6
    wins = clean_windows(run, x.shape[1], fs)
    if len(wins) < MIN_WINDOWS:
        raise AiError(f"Only {len(wins)} clean 10-second windows are available; at least {MIN_WINDOWS} (one minute) are needed")
    ep = np.stack([x[:, a:b] for a, b in wins])
    ep = ep - ep.mean(axis=1, keepdims=True)  # average reference over the 19 channels, as in training
    progress(45, "Computing features and predicting")
    out = P.predict_epochs(bundle, ep, fs)
    progress(80, "Explaining the prediction")
    F = out.pop("feature_matrix")
    out.pop("feature_names")
    try:
        expl = P.explain(bundle, F, top=6)
    except Exception:  # an explanation must never block a result
        expl = []
    q = run.get("quality") or {}
    cond = rec.get("condition") or {}
    doc = {
        "_id": uuid.uuid4(), "run_id": run_id, "recording_id": rec["_id"], "patient_id": run["patient_id"], "task": "sedation_level",
        "model": {"name": card["name"], "version": card["version"], "status": card.get("status", "staging"), "sha256": card["model_sha256"],
                  "algorithm": card.get("model")},
        "created_by": user_id, "created_at": utcnow(), **{k: out[k] for k in (
            "p_sedated", "prediction_set", "verdict", "n_epochs", "unfamiliar_fraction", "unfamiliar", "epoch_spread_sd", "coverage_target")},
        "p_epoch": [round(v, 4) for v in out["p_epoch"]], "explanation": expl, "n_windows_used": len(wins),
        "interpolated_channels": run.get("interpolated_channels", []), "low_confidence": bool(run.get("low_confidence")),
        "qc_status": run["qc_status"], "quality_grade": q.get("grade"), "pipeline": f"{run['pipeline_name']} v{run['pipeline_version']}",
        "context": {"condition": cond.get("condition"), "patient_state": cond.get("patient_state"),
                    "sedation": [d.get("agent") for d in (cond.get("sedation") or [])]},
        "disclaimer": DISCLAIMER,
    }
    db.ai_predictions.insert_one(doc)
    progress(100, "Done")
    return doc["_id"]
