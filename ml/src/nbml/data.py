"""Load EEGMMIDB EDF files with MNE, apply the integrity rule, and cache epochs.

Preprocessing (identical for every subject; nothing is fitted on labels):
  1. standardise channel names (mne.datasets.eegbci.standardize), keep 64 EEG channels
  2. common average reference
  3. zero-phase FIR band-pass 8-30 Hz on the CONTINUOUS run (no epoch-edge artefacts)
  4. cut one epoch per T1/T2 cue: [0.5, 4.0] s after cue onset (560 samples at 160 Hz)
"""

import hashlib
import json
from dataclasses import dataclass, field

import mne
import numpy as np
from joblib import Parallel, delayed
from mne.datasets import eegbci

from . import config as C

mne.set_log_level("ERROR")


@dataclass
class SubjectResult:
    subject: int
    ok: bool
    reasons: list[str] = field(default_factory=list)
    X: np.ndarray | None = None  # (n_epochs, 64, 560) float32
    y: np.ndarray | None = None
    run: np.ndarray | None = None


def _edf(subject: int, run: int):
    return C.DATASET_DIR / f"S{subject:03d}" / f"S{subject:03d}R{run:02d}.edf"


def load_subject(subject: int, task: str) -> SubjectResult:
    runs = C.TASKS[task]["runs"]
    res = SubjectResult(subject, True)
    Xs, ys, rs = [], [], []
    for run in runs:
        path = _edf(subject, run)
        if not path.exists():
            res.ok = False
            res.reasons.append(f"run {run}: file missing")
            continue
        raw = mne.io.read_raw_edf(path, preload=True)
        if raw.info["sfreq"] != C.SFREQ:
            res.ok = False
            res.reasons.append(f"run {run}: sfreq {raw.info['sfreq']} != {C.SFREQ}")
            continue
        eegbci.standardize(raw)
        if len(raw.ch_names) != C.N_CHANNELS:
            res.ok = False
            res.reasons.append(f"run {run}: {len(raw.ch_names)} channels != {C.N_CHANNELS}")
            continue
        cues = [a for a in raw.annotations if a["description"] in ("T1", "T2")]
        durs = [a["duration"] for a in cues]
        if len(cues) != C.EXPECTED_TRIALS_PER_RUN:
            res.ok = False
            res.reasons.append(f"run {run}: {len(cues)} cues != {C.EXPECTED_TRIALS_PER_RUN}")
            continue
        lo, hi = C.TRIAL_DURATION_RANGE_S
        if min(durs) < lo or max(durs) > hi:
            res.ok = False
            res.reasons.append(f"run {run}: trial duration {min(durs):.2f}-{max(durs):.2f} s outside {lo}-{hi}")
            continue
        raw.set_eeg_reference("average", projection=False)
        raw.filter(*C.FILTER_BAND, fir_design="firwin", phase="zero", verbose=False)
        data = raw.get_data().astype(np.float32)
        for a in cues:
            s0 = int(round(a["onset"] * C.SFREQ + C.EPOCH_START_S * C.SFREQ))
            seg = data[:, s0 : s0 + C.N_SAMPLES]
            if seg.shape[1] != C.N_SAMPLES:
                res.ok = False
                res.reasons.append(f"run {run}: cue too close to end of recording")
                break
            Xs.append(seg)
            ys.append(0 if a["description"] == "T1" else 1)
            rs.append(run)
    if res.ok:
        res.X, res.y, res.run = np.stack(Xs), np.array(ys, dtype=np.int8), np.array(rs, dtype=np.int8)
    return res


def build_dataset(task: str, n_jobs: int = 8, force: bool = False) -> dict:
    """Return {'X','y','subject','run','excluded'} and cache it as a compressed npz."""
    cache = C.CACHE_DIR / f"{task}.npz"
    meta = C.CACHE_DIR / f"{task}.manifest.json"
    if cache.exists() and meta.exists() and not force:
        z = np.load(cache)
        return {**{k: z[k] for k in ("X", "y", "subject", "run")}, "excluded": json.loads(meta.read_text())["excluded"]}

    C.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    results = Parallel(n_jobs=n_jobs)(delayed(load_subject)(s, task) for s in range(1, 110))
    kept = [r for r in results if r.ok]
    excluded = [{"subject": r.subject, "reasons": r.reasons} for r in results if not r.ok]
    X = np.concatenate([r.X for r in kept])
    y = np.concatenate([r.y for r in kept])
    run = np.concatenate([r.run for r in kept])
    subject = np.concatenate([np.full(len(r.y), r.subject, dtype=np.int16) for r in kept])
    np.savez_compressed(cache, X=X, y=y, subject=subject, run=run)
    manifest = {
        "task": task, "classes": C.TASKS[task]["classes"], "n_subjects_kept": len(kept),
        "n_epochs": len(y), "class_counts": {str(k): int(v) for k, v in zip(*np.unique(y, return_counts=True), strict=True)},
        "excluded": excluded, "epoch_window_s": [C.EPOCH_START_S, C.EPOCH_END_S],
        "filter_hz": list(C.FILTER_BAND), "reference": "common average",
        "mne_version": mne.__version__, "x_sha256": hashlib.sha256(X.tobytes()).hexdigest(),
    }
    meta.write_text(json.dumps(manifest, indent=2))
    return {"X": X, "y": y, "subject": subject, "run": run, "excluded": excluded}
