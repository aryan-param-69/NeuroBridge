"""Build the demo recordings used by backend/scripts/seed_demo_cases.py from the PUBLIC datasets in data/.

Run from the repo root with the ML environment (needs h5py, edfio):
    ml/.venv/Scripts/python.exe scripts/make_demo_edfs.py [output_dir]      (default: data/demo_cases)

Sedation recordings (Cambridge propofol dataset, Chennu et al. 2016, CC BY 2.0 UK) are converted from the authors' files to EDF: 19 standard
channels, 250 Hz, the authors' cleaned 10-second epochs joined end to end (so they contain small discontinuities every 10 s). Awake recordings
(PhysioNet EEG Motor Movement/Imagery, 64 channels, 160 Hz) are copied unchanged. Nothing here is patient data: the "patients" seeded from
these files are fictional labels for healthy volunteers.
"""

import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml" / "src"))
from nbml import sed_data  # noqa: E402

CAMBRIDGE_SUBJECTS = (0, 1, 2, 3)        # people in the Cambridge dataset used for the sedation cases
EEGMMIDB_SUBJECTS = (1, 2, 3, 4, 5, 6)   # people in the PhysioNet dataset used for the awake cases
LEVEL_MINUTES = {1: 0, 2: 40, 3: 80, 4: 150}  # staggered clock times within the session day (illustrative)


def write_edf(path: Path, x: np.ndarray, labels: list[str], fs: float, start: datetime) -> None:
    from edfio import Edf, EdfSignal

    n = (x.shape[1] // int(fs)) * int(fs)  # whole seconds
    sigs = [EdfSignal(x[i, :n].astype(np.float64), sampling_frequency=fs, label=lab, physical_dimension="uV")
            for i, lab in enumerate(labels)]
    edf = Edf(sigs)
    edf.startdate = start.date()
    edf.starttime = start.time().replace(microsecond=0)
    edf.write(path)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "demo_cases"
    out.mkdir(parents=True, exist_ok=True)
    recs = sed_data.datainfo()
    for s in CAMBRIDGE_SUBJECTS:
        for r in (r for r in recs if r.subject == s):
            m = re.search(r"(\d{8})", r.name)
            day = datetime.strptime(m.group(1), "%Y%m%d").replace(hour=9)
            x, labels = sed_data.load_recording(r.name)            # epochs x channels x samples, microvolts
            cont = np.concatenate(list(x), axis=1)                 # channels x time
            dst = out / f"cam_s{s}_L{r.level}_{sed_data.LEVELS[r.level]}_{round(r.propofol_ugl)}ugL.edf"
            write_edf(dst, cont, labels, sed_data.FS, day + timedelta(minutes=LEVEL_MINUTES[r.level]))
            print(dst.name, cont.shape)
    base = ROOT / "data" / "eegmmidb" / "files"
    for s in EEGMMIDB_SUBJECTS:
        for run, name in ((1, "eyes_open"), (2, "eyes_closed")):
            src = base / f"S{s:03d}" / f"S{s:03d}R{run:02d}.edf"
            dst = out / f"mmi_s{s}_{name}.edf"
            shutil.copy(src, dst)
            print(dst.name)


if __name__ == "__main__":
    main()
