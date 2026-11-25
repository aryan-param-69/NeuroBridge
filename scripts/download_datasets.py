# ruff: noqa: E501
"""List the public datasets the project uses, show which are present under data/, and download the ones that have a simple direct download.

    python scripts/download_datasets.py --list                    # what exists, where it must go, licence, how to get it
    python scripts/download_datasets.py --download mendeley_doc_psg   # verified direct download (checks every SHA-256)

Only the Mendeley patient set can be fetched automatically (its public API lists a SHA-256 for every file). The others are large archives on
PhysioNet, OpenNeuro, the Cambridge repository and Figshare: this script prints the exact folder each loader in ml/ expects and a standard
command, and you run it yourself (those commands were not run by this script). Licences are the dataset authors'; cite them when you use the data.
Nothing here is needed to RUN the platform: the trained models are in ml/models/. Datasets are only needed to retrain or re-run the experiments.
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

DATASETS = {
    "eegmmidb": {
        "name": "EEG Motor Movement/Imagery Dataset (PhysioNet EEGMMIDB v1.0.0)", "licence": "ODC-By 1.0 (open)", "size": "about 3.4 GB",
        "folder": DATA / "eegmmidb" / "files", "check": DATA / "eegmmidb" / "files" / "S001" / "S001R01.edf",
        "url": "https://physionet.org/content/eegmmidb/1.0.0/",
        "how": "wget -r -N -c -np https://physionet.org/files/eegmmidb/1.0.0/   then move the S001...S109 folders into data/eegmmidb/files/",
        "used_for": "motor-imagery benchmark, pipeline tests, awake-people check",
    },
    "sedation_cambridge": {
        "name": "Propofol sedation resting EEG (Chennu et al. 2016, University of Cambridge)", "licence": "CC BY 2.0 UK", "size": "about 3.7 GB",
        "folder": DATA / "sedation_cambridge" / "raw" / "Sedation-RestingState", "check": DATA / "sedation_cambridge" / "raw" / "Sedation-RestingState" / "datainfo.mat",
        "url": "https://www.repository.cam.ac.uk/handle/1810/252736",
        "how": "download the files from the repository page and extract so that datainfo.mat sits in the folder shown",
        "used_for": "sedation models v1 and v2",
    },
    "oslo_propofol": {
        "name": "Propofol sedation EEG, Oslo (OpenNeuro ds005620)", "licence": "CC BY 4.0", "size": "about 7 GB (20 people used)",
        "folder": DATA / "oslo_propofol", "check": DATA / "oslo_propofol" / "participants.tsv",
        "url": "https://openneuro.org/datasets/ds005620",
        "how": "aws s3 sync --no-sign-request s3://openneuro.org/ds005620 data/oslo_propofol    (or the openneuro CLI)",
        "used_for": "sedation model v2 (second laboratory)",
    },
    "dementia_ds004504": {
        "name": "Routine EEG in Alzheimer's, frontotemporal dementia and controls (OpenNeuro ds004504)", "licence": "CC0", "size": "about 2.7 GB",
        "folder": DATA / "dementia_ds004504", "check": DATA / "dementia_ds004504" / "participants.tsv",
        "url": "https://openneuro.org/datasets/ds004504",
        "how": "aws s3 sync --no-sign-request s3://openneuro.org/ds004504 data/dementia_ds004504",
        "used_for": "evaluation only (false 'sedated' alarms on clinical EEG), unfamiliar-EEG reference",
    },
    "doc_figshare": {
        "name": "Resting EEG of patients with prolonged disorders of consciousness (Figshare, no labels)", "licence": "CC BY 4.0", "size": "about 683 MB",
        "folder": DATA / "doc_figshare", "check": None, "glob": "*.vhdr",
        "url": "https://doi.org/10.6084/m9.figshare.23552964",
        "how": "download from the Figshare page into the folder shown (BrainVision .vhdr/.eeg/.vmrk files)",
        "used_for": "pipeline behaviour check only: there are no diagnosis labels",
    },
    "mendeley_doc_psg": {
        "name": "Polysomnographic records of patients with disorders of consciousness (Mendeley Data 6wx4n25h4v; Nekrasova et al.)",
        "licence": "CC BY 4.0", "size": "2.77 GB, 42 EDF files", "folder": DATA / "mendeley_doc_psg", "check": None, "glob": "*.edf",
        "url": "https://data.mendeley.com/datasets/6wx4n25h4v", "auto": True,
        "api": "https://data.mendeley.com/public-api/datasets/6wx4n25h4v",
        "how": "python scripts/download_datasets.py --download mendeley_doc_psg",
        "used_for": "pilot: vegetative vs minimally conscious (negative result), see ml/reports/psg_doc",
    },
}


def present(d: dict) -> bool:
    if d.get("check"):
        return Path(d["check"]).exists()
    return d["folder"].exists() and any(d["folder"].glob(d["glob"]))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def list_all() -> None:
    for key, d in DATASETS.items():
        state = "PRESENT" if present(d) else "missing"
        print(f"[{state:7}] {key}\n    {d['name']}\n    licence: {d['licence']} | size: {d['size']} | used for: {d['used_for']}")
        print(f"    folder: {d['folder'].relative_to(ROOT)}\n    source: {d['url']}\n    how: {d['how']}\n")


def download_mendeley(key: str) -> None:
    d = DATASETS[key]
    out: Path = d["folder"]
    out.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(d["api"], headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        meta = json.load(r)
    ok = bad = 0
    for f in meta["files"]:
        name = f["filename"].replace(" ", "_")
        want = f["content_details"]["sha256_hash"]
        dest = out / name
        if dest.exists() and sha256_file(dest) == want:
            ok += 1
            continue
        req = urllib.request.Request(f["content_details"]["download_url"], headers={"User-Agent": "Mozilla/5.0"})
        h = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as w:
            for block in iter(lambda: r.read(1 << 20), b""):
                w.write(block)
                h.update(block)
        if h.hexdigest() == want:
            ok += 1
            print("ok ", name)
        else:
            bad += 1
            dest.unlink(missing_ok=True)
            print("CHECKSUM MISMATCH, removed:", name)
    print(f"done: {ok} verified, {bad} failed. Licence CC BY 4.0: credit Nekrasova, Kanarskii, Yankevich, Borisov, Pradhan.")
    if bad:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show datasets, licences, folders and how to get them")
    ap.add_argument("--download", choices=[k for k, d in DATASETS.items() if d.get("auto")], help="download a dataset that supports it")
    a = ap.parse_args()
    if a.download:
        download_mendeley(a.download)
    else:
        list_all()


if __name__ == "__main__":
    main()
