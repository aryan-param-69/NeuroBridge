"""Channel-name normalisation: vendor labels -> standard 10-05 names, and channel-type detection.

Examples:  "EEG Fp1-REF" -> Fp1   "Fc5." -> FC5   "T3" -> T7 (legacy name)   "EOG L" -> type eog
Unrecognised labels are kept (as typed) with type "misc" and has_position False so they are never silently
used for topographic maps or comparisons.
"""

import re
from functools import lru_cache

LEGACY = {"t3": "T7", "t4": "T8", "t5": "P7", "t6": "P8"}
REFERENCE_SUFFIXES = {"ref", "le", "re", "av", "avg", "a1", "a2", "m1", "m2", "cz", "linked", "ears"}
NON_EEG_PATTERNS = (
    ("eog", re.compile(r"\b(eog|veog|heog|loc|roc)\b", re.I)),
    ("ecg", re.compile(r"\b(ecg|ekg)\b", re.I)),
    ("emg", re.compile(r"\bemg\b", re.I)),
    ("resp", re.compile(r"\b(resp|airflow|thor|abdo)", re.I)),
    ("stim", re.compile(r"(annotation|trigger|status|stim)", re.I)),
)


@lru_cache(maxsize=1)
def _standard() -> dict[str, str]:
    import mne

    names = mne.channels.make_standard_montage("standard_1005").ch_names
    return {n.lower(): n for n in names}


def _clean(label: str) -> str:
    s = label.strip()
    s = re.sub(r"^eeg[\s_\-:]+", "", s, flags=re.I)
    parts = re.split(r"[-_ ]", s)
    if len(parts) >= 2 and parts[-1].lower() in REFERENCE_SUFFIXES and len(parts) == 2:
        s = parts[0]
    return s.rstrip(".").strip()


def normalize_channel(label: str) -> dict:
    """Return {name, ch_type, has_position, recognised}."""
    for ch_type, pat in NON_EEG_PATTERNS:
        if pat.search(label):
            return {"name": label.strip(), "ch_type": ch_type, "has_position": False, "recognised": True}
    cleaned = _clean(label)
    key = LEGACY.get(cleaned.lower(), cleaned).lower()
    std = _standard().get(key)
    if std:
        return {"name": std, "ch_type": "eeg", "has_position": True, "recognised": True}
    return {"name": cleaned or label.strip(), "ch_type": "misc", "has_position": False, "recognised": False}


def normalize_all(labels: list[str]) -> list[dict]:
    """Normalise every label and make the resulting names unique (duplicates get -2, -3, ...)."""
    out = [normalize_channel(lab) | {"original_name": lab} for lab in labels]
    seen: dict[str, int] = {}
    for ch in out:
        n = seen.get(ch["name"], 0) + 1
        seen[ch["name"]] = n
        if n > 1:
            ch["name"] = f"{ch['name']}-{n}"
            ch["recognised"] = False
    return out


@lru_cache(maxsize=1)
def _montage_positions() -> dict[str, tuple[float, float, float]]:
    import mne

    mont = mne.channels.make_standard_montage("standard_1005")
    return {n: tuple(float(v) for v in xyz) for n, xyz in mont.get_positions()["ch_pos"].items()}


def positions(names: list[str]) -> dict[str, tuple[float, float, float]]:
    """3-D electrode positions (metres) for the names that are standard 10-05 electrodes."""
    pos = _montage_positions()
    return {n: pos[n] for n in names if n in pos}
