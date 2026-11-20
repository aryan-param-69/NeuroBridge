"""Constants for the EEGMMIDB motor-imagery experiments.

EEGMMIDB (Schalk et al., 2004; PhysioNet). 109 healthy volunteers, 64 channels, 160 Hz.
Per subject 14 runs; the imagery runs used here (T0 = rest, T1/T2 = cue):
  runs 4, 8, 12  : imagine opening/closing LEFT fist (T1) or RIGHT fist (T2)
  runs 6, 10, 14 : imagine BOTH fists (T1) or BOTH feet (T2)
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # .../EegNeuro
DATA_DIR = ROOT / "data"  # git-ignored; see scripts/download_datasets.py
DATASET_DIR = DATA_DIR / "eegmmidb" / "files"
ML_DIR = ROOT / "ml"
CACHE_DIR = DATA_DIR / "cache"
REPORT_DIR = ML_DIR / "reports"

SFREQ = 160.0
N_CHANNELS = 64
EPOCH_START_S = 0.5  # skip the first 0.5 s after the cue (reaction time)
EPOCH_END_S = 4.0
N_SAMPLES = int(round((EPOCH_END_S - EPOCH_START_S) * SFREQ))  # 560
FILTER_BAND = (8.0, 30.0)  # mu + beta, the motor-imagery band
BANDS = {"mu": (8.0, 13.0), "beta_low": (13.0, 20.0), "beta_high": (20.0, 30.0)}

TASKS = {
    # name: (runs, label for T1, label for T2)
    "left_vs_right": {"runs": (4, 8, 12), "classes": ("left_hand", "right_hand")},
    "fists_vs_feet": {"runs": (6, 10, 14), "classes": ("both_fists", "both_feet")},
}

# Pre-specified, label-agnostic data-integrity rule (written BEFORE looking at any model results).
EXPECTED_TRIALS_PER_RUN = 15
TRIAL_DURATION_RANGE_S = (3.9, 4.4)

SEED = 20260101
HOLDOUT_FRACTION = 0.20
OUTER_FOLDS = 5
INNER_FOLDS = 3
