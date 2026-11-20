"""Loader for OpenNeuro ds004504 (Miltiadous et al.; CC0): routine eyes-closed EEG of 88 people, 19 channels, 500 Hz, Nihon Kohden clinical device.
Groups: A = Alzheimer's disease (36), F = frontotemporal dementia (23), C = healthy controls (29); MMSE = cognitive score (30 = normal).
Raw (unprocessed) recordings are used and prepared with `nbml.prep` (no EOG channels exist, so no ocular regression). Nobody in this set is sedated."""

import csv

import mne
import numpy as np

from . import prep
from .config import CACHE_DIR, DATA_DIR
from .sed_features import extract

ROOT = DATA_DIR / "dementia_ds004504"
CACHE = CACHE_DIR / "dementia_features_v1.npz"
LINE_HZ = 50.0  # Greece
GROUPS = {"A": "Alzheimer", "F": "Frontotemporal", "C": "Healthy"}


def participants() -> list[dict]:
    return [{"id": r["participant_id"], "group": r["Group"], "age": float(r["Age"]), "mmse": float(r["MMSE"]), "sex": r["Gender"]}
            for r in csv.DictReader((ROOT / "participants.tsv").open(encoding="utf-8"), delimiter="\t")]


def build(force: bool = False, progress=print) -> dict:
    if CACHE.exists() and not force:
        z = np.load(CACHE, allow_pickle=True)
        return {k: z[k] for k in z.files}
    feats, rows, names = [], [], None
    for i, p in enumerate(participants()):
        f = ROOT / p["id"] / "eeg" / f"{p['id']}_task-eyesclosed_eeg.set"
        raw = mne.io.read_raw_eeglab(f, preload=True)
        ep, info = prep.prepare_epochs(raw, LINE_HZ, eog=None)
        rows.append({**p, **info})
        if len(ep) == 0:
            continue
        a, names = extract(ep, prep.FS_OUT)
        feats.append(a.astype(np.float32))
        rows[-1]["first_row"] = int(sum(len(x) for x in feats[:-1]))
        rows[-1]["n_rows"] = len(a)
        if progress and i % 10 == 0:
            progress(f"  {i + 1}/88")
    out = {"X": np.concatenate(feats), "features": np.array(names), "meta": np.array([str(r) for r in rows])}
    np.savez_compressed(CACHE, **out)
    return out
