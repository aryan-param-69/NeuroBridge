"""Back up the NeuroTrace database and file storage into one folder with a checksummed manifest.

Usage (from backend/):
    uv run python -m scripts.backup --out ../backups            # database + files
    uv run python -m scripts.backup --out ../backups --skip-files

Creates <out>/nb-backup-<UTC timestamp>/ containing
    db/<collection>.jsonl.gz     every document as one line of MongoDB Extended JSON (UUID, dates and binary are preserved)
    files.tar.gz                 the storage folder (encrypted originals, de-identified copies, processed signals)
    MANIFEST.json                SHA-256 and size of every file above, document counts, app version

IMPORTANT
  * Encryption keys (SECRET_KEY, ENCRYPTION_KEY, HMAC_KEY) are NEVER written to a backup. Keep them in a separate secret store:
    without them the encrypted identifiers and original EEG files in the backup cannot be read.
  * The de-identified copies and processed signals in files.tar.gz are not encrypted by the application: keep backups on an
    encrypted volume.
"""

import argparse
import gzip
import hashlib
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from bson import json_util

from app import storage
from app.core.config import get_settings
from app.db.mongo import get_db

JSON_OPTS = json_util.JSONOptions(json_mode=json_util.JSONMode.CANONICAL, uuid_representation=json_util.UuidRepresentation.STANDARD)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def backup(out_root: Path, include_files: bool = True, db=None) -> Path:
    db = db if db is not None else get_db()
    dest = out_root / f"nb-backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    (dest / "db").mkdir(parents=True)
    counts = {}
    for name in sorted(db.list_collection_names()):
        n = 0
        with gzip.open(dest / "db" / f"{name}.jsonl.gz", "wt", encoding="utf-8") as f:
            for doc in db[name].find():
                f.write(json_util.dumps(doc, json_options=JSON_OPTS) + "\n")
                n += 1
        counts[name] = n
    if include_files:
        with tarfile.open(dest / "files.tar.gz", "w:gz") as tar:
            tar.add(storage.root(), arcname="storage")
    files = {str(p.relative_to(dest)).replace("\\", "/"): {"sha256": sha256_file(p), "bytes": p.stat().st_size}
             for p in sorted(dest.rglob("*")) if p.is_file()}
    manifest = {"created_utc": datetime.now(UTC).isoformat(), "app_version": get_settings().app_version, "database": db.name,
                "collections": counts, "includes_files": include_files, "files": files}
    (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path, help="folder that will contain the backup folder")
    ap.add_argument("--skip-files", action="store_true", help="database only")
    a = ap.parse_args()
    dest = backup(a.out, include_files=not a.skip_files)
    m = json.loads((dest / "MANIFEST.json").read_text(encoding="utf-8"))
    n_docs, n_coll = sum(m["collections"].values()), len(m["collections"])
    print(f"backup written to {dest}\n  documents: {n_docs} in {n_coll} collections; files included: {m['includes_files']}")
    print("  reminder: encryption keys are not part of the backup")


if __name__ == "__main__":
    main()
