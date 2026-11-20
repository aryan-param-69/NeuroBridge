"""Common, label-agnostic preparation of an EEG recording for the sedation models.

Every dataset goes through the same steps so that the 74 features mean the same thing everywhere:
  1. keep the 19 standard 10-20 channels (old names T3/T4/T5/T6 mapped to T7/T8/P7/P8);
  2. optional mains notch (50 or 60 Hz);
  3. zero-phase 4th-order Butterworth band-pass 0.5-45 Hz (same filter as pipeline `resting` v1.1.0);
  4. resample to 250 Hz;
  5. optional ocular-artefact regression when EOG channels exist (mne EOGRegression);
  6. average reference over the 19 channels;
  7. cut into 10 s epochs and drop epochs whose peak-to-peak amplitude exceeds `reject_uv` in more than `reject_fraction` of the channels
     (fixed numbers chosen before looking at any model result; the same rule for awake and sedated recordings).
Nothing here uses a label.
"""

import mne
import numpy as np

from .sed_features import STANDARD_19

mne.set_log_level("ERROR")
LEGACY = {"T3": "T7", "T4": "T8", "T5": "P7", "T6": "P8"}
FS_OUT = 250.0
EPOCH_S = 10.0
REJECT_UV = 200.0
REJECT_FRACTION = 0.1


def standardise_names(raw: mne.io.BaseRaw) -> None:
    """Strip prefixes/suffixes such as 'EEG ', '-Ref', '.' and map legacy names; leaves unknown channels alone."""
    ren = {}
    for c in raw.ch_names:
        n = c.replace("EEG ", "").replace("-Ref1", "").replace("-REF", "").strip(" .")
        n = LEGACY.get(n, n)
        if n != c and n not in raw.ch_names:
            ren[c] = n
    if ren:
        raw.rename_channels(ren)


def prepare_epochs(raw: mne.io.BaseRaw, line_hz: float | None, eog: list[str] | None = None) -> tuple[np.ndarray, dict]:
    """Return (epochs x 19 x samples in microvolts at 250 Hz, info). `raw` must be preloaded. Epoch counts are reported, never hidden."""
    standardise_names(raw)
    missing = [c for c in STANDARD_19 if c not in raw.ch_names]
    if missing:
        raise ValueError(f"missing standard channels: {missing}")
    eog = [c for c in (eog or []) if c in raw.ch_names]
    raw.pick(STANDARD_19 + eog)
    if eog:
        raw.set_channel_types({c: "eog" for c in eog})
    sf = float(raw.info["sfreq"])
    if line_hz and line_hz < 0.45 * sf:
        raw.notch_filter([line_hz], method="iir", verbose=False)
    raw.filter(0.5, 45.0, method="iir", iir_params={"order": 4, "ftype": "butter", "output": "sos"}, phase="zero", verbose=False)
    if sf != FS_OUT:
        raw.resample(FS_OUT, verbose=False)
    if eog:
        raw.set_eeg_reference("average", projection=True, verbose=False)  # EOGRegression requires an average reference
        raw.apply_proj(verbose=False)
        weights = mne.preprocessing.EOGRegression(picks="eeg", picks_artifact="eog").fit(raw)
        raw = weights.apply(raw, copy=True)
        raw.pick("eeg")
    raw.reorder_channels(STANDARD_19)
    x = raw.get_data() * 1e6
    x -= x.mean(axis=0, keepdims=True)  # average reference over the 19 channels
    L = int(EPOCH_S * FS_OUT)
    n = x.shape[1] // L
    ep = np.stack([x[:, i * L:(i + 1) * L] for i in range(n)]) if n else np.empty((0, 19, L))
    if n:
        ptp = ep.max(axis=2) - ep.min(axis=2)  # (epochs, channels)
        keep = (ptp > REJECT_UV).mean(axis=1) <= REJECT_FRACTION
    else:
        keep = np.zeros(0, dtype=bool)
    return ep[keep], {"n_epochs_total": int(n), "n_epochs_kept": int(keep.sum()), "used_eog_regression": bool(eog), "sfreq_in": sf}
