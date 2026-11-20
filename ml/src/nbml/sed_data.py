"""Loader for the Cambridge propofol-sedation resting-EEG dataset (Chennu et al. 2016, CC BY 2.0 UK).

Source:  https://www.repository.cam.ac.uk/handle/1810/252736   (DOI 10.17863/CAM.68959)
Content: 20 healthy adults x 4 recordings (baseline, mild, moderate sedation, recovery), 91 channels, 250 Hz, ~39 epochs of 10 s each.
The authors' files are ALREADY band-pass filtered 0.5-45 Hz, artefact-cleaned (ICA, bad epochs removed) and average-referenced, so
this dataset cannot test blink removal; it can only test what is learned from clean EEG.

Files are MATLAB v7.3 (.set, read with h5py) with the samples in a separate float32 .fdt file (channels x time x epochs, column-major).
`datainfo.mat` has, for each of the 80 recordings in subject order: name, sedation level (1 baseline, 2 mild, 3 moderate,
4 recovery), plasma propofol (ug/L), mean reaction time (ms, NaN = no valid responses), correct responses (of 40).
"""

from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np
import scipy.io as sio

from .config import CACHE_DIR, DATA_DIR
from .sed_features import STANDARD_19

RAW_DIR = DATA_DIR / "sedation_cambridge" / "raw" / "Sedation-RestingState"
CACHE = CACHE_DIR / "sedation_features_v1.npz"
LEVELS = {1: "baseline", 2: "mild", 3: "moderate", 4: "recovery"}
# The authors labelled channels with the old 10-20 names for some sites; modern equivalents:
LEGACY = {"T3": "T7", "T4": "T8", "T5": "P7", "T6": "P8"}
FS = 250.0
CHANCE_UPPER_CORRECT = 26  # of 40 two-choice trials: P(X >= 26 | chance) < 0.05 (binomial), i.e. above-chance performance


@dataclass
class Recording:
    name: str
    subject: int
    level: int
    propofol_ugl: float
    rt_ms: float
    correct: int


def datainfo() -> list[Recording]:
    rows = sio.loadmat(RAW_DIR / "datainfo.mat", squeeze_me=True)["datainfo"]
    recs = [Recording(str(r[0]), i // 4, int(r[1]), float(r[2]), float(r[3]), int(r[4])) for i, r in enumerate(rows)]
    for s in range(20):  # 4 consecutive rows are one person, in level order (checked, not assumed)
        if [r.level for r in recs if r.subject == s] != [1, 2, 3, 4]:
            raise ValueError(f"datainfo.mat rows for subject {s} are not levels 1,2,3,4 in order")
    return recs


def _ref_str(f: h5py.File, ref) -> str:
    return "".join(chr(int(c)) for c in f[ref][()].ravel())


def load_recording(name: str) -> tuple[np.ndarray, list[str]]:
    """Return (epochs x 19 standard channels x samples) in microvolts, re-referenced to the mean of those 19 channels."""
    with h5py.File(RAW_DIR / f"{name}.set", "r") as f:
        e = f["EEG"]
        nb, pnts, trials = (int(e[k][()].ravel()[0]) for k in ("nbchan", "pnts", "trials"))
        if float(e["srate"][()].ravel()[0]) != FS:
            raise ValueError(f"{name}: unexpected sampling rate")
        labels = [LEGACY.get(x, x) for x in (_ref_str(f, r[0]) for r in e["chanlocs"]["labels"])]
        dat = "".join(chr(int(c)) for c in e["datfile"][()].ravel())
    raw = np.fromfile(RAW_DIR / dat, dtype="<f4")
    if raw.size != nb * pnts * trials:
        raise ValueError(f"{name}: data file has {raw.size} values, expected {nb * pnts * trials}")
    x = raw.reshape(trials, pnts, nb).transpose(0, 2, 1)  # (epochs, channels, samples)
    idx = [labels.index(c) for c in STANDARD_19]
    x19 = x[:, idx, :].astype(np.float64)
    x19 -= x19.mean(axis=1, keepdims=True)  # average reference over the 19 channels (same as the clinical pipeline's)
    return x19, [labels[i] for i in idx]


def build(force: bool = False, progress=print) -> dict:
    """Compute features for every epoch of every recording (cached). Returns the arrays used for modelling."""
    from .sed_features import extract

    if CACHE.exists() and not force:
        z = np.load(CACHE, allow_pickle=True)
        return {k: z[k] for k in z.files}
    recs = datainfo()
    feats, names, meta = [], None, {k: [] for k in ("subject", "level", "propofol", "rt", "correct", "recording", "epoch")}
    for i, r in enumerate(recs):
        x, _ = load_recording(r.name)
        a, names = extract(x, FS)
        feats.append(a.astype(np.float32))
        n = len(a)
        for k, v in (("subject", r.subject), ("level", r.level), ("propofol", r.propofol_ugl), ("rt", r.rt_ms),
                     ("correct", r.correct), ("recording", i)):
            meta[k] += [v] * n
        meta["epoch"] += list(range(n))
        if progress and i % 10 == 0:
            progress(f"  {i + 1}/{len(recs)} recordings")
    out = {"X": np.concatenate(feats), "features": np.array(names), **{k: np.array(v) for k, v in meta.items()}}
    np.savez_compressed(CACHE, **out)
    return out


def available() -> bool:
    return Path(RAW_DIR / "datainfo.mat").exists()
