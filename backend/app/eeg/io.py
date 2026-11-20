"""EDF / EDF+ reading: header parsing, validation, identity check and de-identification.

Header layout (EDF spec): bytes 0-8 version, 8-88 patient id, 88-168 recording id, 168-176 start date,
176-184 start time, 192-236 reserved ("EDF+C" continuous / "EDF+D" discontinuous), 236-244 data records,
244-252 record duration, 252-256 number of signals.
"""

import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import edfio

from app.eeg.channels import normalize_all

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
PLACEHOLDERS = {"x", "m", "f", "male", "female", "unknown", "anonymous", "anon", "na", "n", "a", "none",
                "patient", "subject", "test"}
MIN_EEG_CHANNELS = 8
MIN_DURATION_S = 60.0
MIN_ANALYSIS_DURATION_S = 120.0
MAX_STORED_ANNOTATIONS = 5000


class InvalidEdf(ValueError):
    """The file is not a readable EDF/EDF+ recording."""


@dataclass
class RawHeader:
    version_ok: bool
    patient_field: str
    recording_field: str
    reserved: str
    n_records: int
    record_duration: float
    n_signals: int

    @property
    def edf_plus(self) -> bool:
        return self.reserved.upper().startswith("EDF+")

    @property
    def file_format(self) -> str:
        if self.reserved.upper().startswith("EDF+D"):
            return "EDF+D"
        if self.reserved.upper().startswith("EDF+"):
            return "EDF+C"
        return "EDF"


def read_raw_header(path: Path) -> RawHeader:
    with open(path, "rb") as f:
        h = f.read(256)
    if len(h) < 256 or h[:8] != b"0       ":
        raise InvalidEdf("Not an EDF file (missing EDF header)")
    try:
        text = h.decode("ascii")
        return RawHeader(
            version_ok=True, patient_field=text[8:88].strip(), recording_field=text[88:168].strip(),
            reserved=text[192:236].strip(), n_records=int(text[236:244]),
            record_duration=float(text[244:252]), n_signals=int(text[252:256]),
        )
    except (UnicodeDecodeError, ValueError):
        raise InvalidEdf("EDF header is corrupt") from None


# ---- Identity check ----------------------------------------------------------------------------

def _tokens(s: str) -> list[str]:
    s = re.sub(r"[^A-Za-z0-9\-]+", " ", s)
    return [t for t in s.split() if t]


def _parse_birthdate(tok: str) -> date | None:
    m = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", tok)
    if m and m.group(2).upper() in MONTHS:
        try:
            return date(int(m.group(3)), MONTHS[m.group(2).upper()], int(m.group(1)))
        except ValueError:
            return None
    return None


def identity_check(patient_field: str, registered_name_tokens: set[str], registered_dob: date) -> tuple[str, list[str]]:
    """Compare the EDF 'patient identification' field with the registered patient.

    Returns ("blank" | "match" | "mismatch", reasons). `registered_name_tokens` are normalised lower-case words.
    Header text is only compared in memory; it is never stored or logged.
    """
    toks = _tokens(patient_field)
    content, header_dob = [], None
    for t in toks:
        d = _parse_birthdate(t)
        if d:
            header_dob = d
        elif t.lower() not in PLACEHOLDERS and not t.isdigit():
            content.extend(w for w in re.split(r"[_\-]", t.lower()) if len(w) >= 2)
    content = [w for w in content if w not in PLACEHOLDERS]
    if not content and header_dob is None:
        return "blank", []
    reasons: list[str] = []
    if content and not (set(content) & registered_name_tokens):
        reasons.append("name in the file does not match the registered patient")
    if header_dob and header_dob != registered_dob:
        reasons.append("birth date in the file does not match the registered patient")
    return ("mismatch", reasons) if reasons else ("match", [])


# ---- De-identification --------------------------------------------------------------------------

def deidentify_copy(src: Path, dst: Path, header: RawHeader) -> None:
    """Copy the EDF, blanking the patient and recording identification fields in the header.

    Signal data, channel labels and the start date/time (bytes 168-184) are kept: the recording time is
    needed for the clinical timeline. EDF+ recording fields keep their mandatory 'Startdate dd-MMM-yyyy'.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    patient = "X X X X"
    rec_tokens = header.recording_field.split()
    if header.edf_plus and len(rec_tokens) >= 2 and rec_tokens[0] == "Startdate":
        recording = f"Startdate {rec_tokens[1]} X X X"
    else:
        recording = "X X X X"
    with open(dst, "r+b") as f:
        f.seek(8)
        f.write(patient.ljust(80).encode("ascii"))
        f.write(recording.ljust(80).encode("ascii"))


# ---- Metadata extraction ------------------------------------------------------------------------

@dataclass
class EdfSummary:
    file_format: str
    start_time: datetime | None
    duration_s: float
    n_signals: int
    sfreq_hz: float
    channels: list[dict]
    annotations: dict[str, int]
    annotation_list: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def summarize(path: Path, header: RawHeader) -> EdfSummary:
    """Parse with edfio (strict structure check) and collect channel metadata and warnings."""
    try:
        edf = edfio.read_edf(path, lazy_load_data=True)
    except Exception as e:  # edfio raises several types for corrupt files
        raise InvalidEdf(f"EDF could not be read: {str(e)[:150]}") from None

    sigs = [s for s in edf.signals if "annotation" not in s.label.lower()]
    if not sigs:
        raise InvalidEdf("The file contains no data channels")
    norm = normalize_all([s.label for s in sigs])
    channels = []
    for i, (s, n) in enumerate(zip(sigs, norm, strict=True)):
        channels.append({
            "index": i, "original_name": s.label, "name": n["name"], "ch_type": n["ch_type"],
            "has_position": n["has_position"], "recognised": n["recognised"],
            "sfreq_hz": float(s.sampling_frequency), "unit": (s.physical_dimension or "").strip(),
            "physical_min": float(s.physical_min), "physical_max": float(s.physical_max),
            "prefilter": (s.prefiltering or "").strip(), "auto_flag": "unchecked", "user_flag": None,
        })

    eeg = [c for c in channels if c["ch_type"] == "eeg"]
    rates = sorted({c["sfreq_hz"] for c in eeg} or {c["sfreq_hz"] for c in channels})
    sfreq = rates[-1]
    duration = float(edf.duration)
    warnings: list[str] = []
    if len(eeg) < MIN_EEG_CHANNELS:
        warnings.append(f"Only {len(eeg)} recognised EEG channels (at least {MIN_EEG_CHANNELS} recommended)")
    if len(rates) > 1:
        warnings.append(f"Channels have different sampling rates: {', '.join(f'{r:g}' for r in rates)} Hz")
    if duration < MIN_DURATION_S:
        warnings.append(f"Recording is only {duration:.0f} s long")
    elif duration < MIN_ANALYSIS_DURATION_S:
        warnings.append("Shorter than 2 minutes: spectral features will be low-confidence")
    if header.file_format == "EDF+D":
        warnings.append("Discontinuous recording (EDF+D): gaps between segments are not yet handled")
    unrec = [c["original_name"] for c in channels if not c["recognised"] and c["ch_type"] == "misc"]
    if unrec:
        warnings.append(f"{len(unrec)} channel(s) not recognised as standard 10-05 electrodes: "
                        + ", ".join(unrec[:6]) + ("..." if len(unrec) > 6 else ""))
    no_unit = [c["original_name"] for c in channels if c["ch_type"] == "eeg" and c["unit"].lower() not in ("uv", "µv", "μv")]
    if no_unit:
        warnings.append(f"Unexpected amplitude unit on {len(no_unit)} EEG channel(s) (expected uV)")
    start = None
    try:
        start = datetime.combine(edf.startdate, edf.starttime)
        if start.year < 1990:
            warnings.append("The start date looks like a default value; confirm the recording date")
    except Exception:
        warnings.append("The file has no valid start date/time")

    ann: dict[str, int] = {}
    ann_list: list[dict] = []
    for a in edf.annotations:
        ann[a.text] = ann.get(a.text, 0) + 1
        if len(ann_list) < MAX_STORED_ANNOTATIONS:
            ann_list.append({"onset": float(a.onset), "duration": float(a.duration or 0), "text": a.text[:80]})
    if sum(ann.values()) > MAX_STORED_ANNOTATIONS:
        warnings.append(f"Only the first {MAX_STORED_ANNOTATIONS} annotations are shown in the viewer")
    return EdfSummary(header.file_format, start, duration, len(channels), sfreq, channels, ann, ann_list, warnings)
