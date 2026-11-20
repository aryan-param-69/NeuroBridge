"""Pooled two-site data for the multi-site sedation experiment (Cambridge + Oslo). See reports/sedation_multisite/PRESPEC.md."""

import numpy as np

from . import oslo_data, sed_data

OSLO_REC_OFFSET = 1000
FEATURESETS = {"primary": lambda n: ":log_power" not in n, "full": lambda n: True}  # primary drops the six amplitude-dependent features


def pooled(featureset: str = "primary") -> dict:
    """Arrays: X, y-ready `level` (1 baseline/awake, 2 mild, 3 sedated, 4 recovery), subject (unique), recording (unique), site (0 Cambridge, 1 Oslo)."""
    c, o = sed_data.build(), oslo_data.build()
    if list(c["features"]) != list(o["features"]):
        raise ValueError("feature order differs between sites")
    names = [n for n in c["features"] if FEATURESETS[featureset](n)]
    keep = [i for i, n in enumerate(c["features"]) if FEATURESETS[featureset](n)]
    X = np.concatenate([c["X"][:, keep], o["X"][:, keep]]).astype(np.float64)
    level = np.concatenate([c["level"], np.where(o["label"] == 1, 3, 1)])
    subject = np.concatenate([c["subject"], o["subject"]])
    recording = np.concatenate([c["recording"], o["recording"] + OSLO_REC_OFFSET])
    site = np.concatenate([np.zeros(len(c["X"]), int), np.ones(len(o["X"]), int)])
    return {"X": X, "features": names, "level": level, "subject": subject, "recording": recording, "site": site,
            "propofol": np.concatenate([c["propofol"], np.full(len(o["X"]), np.nan)])}
