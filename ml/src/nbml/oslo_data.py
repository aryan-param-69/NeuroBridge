"""Loader for the Oslo propofol dataset (OpenNeuro ds005620, Bajwa et al., CC BY 4.0).

20 healthy adults. Per person: eyes-closed awake rest (`task-awake_acq-EC`, first 120 s were downloaded) and 1-minute resting EEG under propofol
sedation recorded just before an awakening (`task-sed2_acq-rest`, runs 1-3). Brain Products system, 65 channels (19 standard + 10-10 sites +
VEOG, HEOG, EMG), 5 kHz. Labels used here: awake = 0, sedated (propofol) = 1. The sedation depth is not graded and is probably deeper than the
Cambridge 'moderate' level (participants were awakened to be asked about experiences); the two datasets are therefore related, not identical, tasks.
"""

import csv
import re

import mne
import numpy as np

from . import prep
from .config import CACHE_DIR, DATA_DIR
from .sed_features import extract

ROOT = DATA_DIR / "oslo_propofol"
CACHE = CACHE_DIR / "sedation_oslo_features_v1.npz"
LINE_HZ = 50.0  # Norway
EOG = ["VEOG", "HEOG"]


def participants() -> list[str]:
    rows = list(csv.DictReader((ROOT / "participants.tsv").open(encoding="utf-8"), delimiter="\t"))
    return [r["participant_id"].replace("sub-SD_", "") for r in rows if r["excluded"] != "True"]


def recordings() -> list[dict]:
    out = []
    for sid in participants():
        eeg = ROOT / f"sub-{sid}" / "eeg"
        for f in sorted(eeg.glob("*_eeg.vhdr")):
            m = re.search(r"task-(awake_acq-EC|sed2_acq-rest(?:_run-\d)?)_eeg", f.name)
            if m:
                out.append({"subject": int(sid), "file": f, "label": 0 if m.group(1).startswith("awake") else 1, "task": m.group(1)})
    return out


def build(force: bool = False, progress=print) -> dict:
    if CACHE.exists() and not force:
        z = np.load(CACHE, allow_pickle=True)
        return {k: z[k] for k in z.files}
    feats, meta, names, log = [], {"subject": [], "label": [], "recording": [], "epoch": []}, None, []
    recs = recordings()
    for i, r in enumerate(recs):
        raw = mne.io.read_raw_brainvision(r["file"], preload=True)
        ep, info = prep.prepare_epochs(raw, LINE_HZ, eog=EOG)
        log.append({"file": r["file"].name, "task": r["task"], **info})
        if len(ep) == 0:
            continue
        a, names = extract(ep, prep.FS_OUT)
        feats.append(a.astype(np.float32))
        n = len(a)
        meta["subject"] += [r["subject"]] * n
        meta["label"] += [r["label"]] * n
        meta["recording"] += [i] * n
        meta["epoch"] += list(range(n))
        if progress and i % 10 == 0:
            progress(f"  {i + 1}/{len(recs)} recordings")
    out = {"X": np.concatenate(feats), "features": np.array(names), **{k: np.array(v) for k, v in meta.items()}, "log": np.array([str(x) for x in log])}
    np.savez_compressed(CACHE, **out)
    return out
