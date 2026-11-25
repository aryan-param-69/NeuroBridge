"""Restore a backup made by scripts.backup after verifying every checksum.

Usage (from backend/):
    uv run python -m scripts.restore --backup ../backups/nb-backup-20260101T000000Z --db neurobridge_restored
    uv run python -m scripts.restore --backup <folder> --db neurobridge --files-to ./storage --force

Safety rules
  * Every file in the backup is checked against MANIFEST.json first; any mismatch stops the restore before anything is written.
  * The target database must be empty unless --force is given (then existing collections of the same name are replaced).
  * Files are extracted only into --files-to (default: nowhere). Archive entries that try to leave that folder are refused.
  * The same SECRET_KEY / ENCRYPTION_KEY / HMAC_KEY as at backup time must be configured, or identifiers and original EEG
    files stay unreadable.
"""

import argparse
import gzip
import json
import tarfile
from pathlib import Path

from bson import json_util

from app.db.mongo import ensure_indexes, get_client
from scripts.backup import JSON_OPTS, sha256_file


class RestoreError(Exception):
    pass


def verify(backup_dir: Path) -> dict:
    mf = backup_dir / "MANIFEST.json"
    if not mf.is_file():
        raise RestoreError("MANIFEST.json not found: not a NeuroTrace backup")
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    for rel, meta in manifest["files"].items():
        p = backup_dir / rel
        if not p.is_file():
            raise RestoreError(f"missing file listed in manifest: {rel}")
        if sha256_file(p) != meta["sha256"]:
            raise RestoreError(f"checksum mismatch (file changed or damaged): {rel}")
    return manifest


def restore(backup_dir: Path, db_name: str, files_to: Path | None = None, force: bool = False) -> dict:
    manifest = verify(backup_dir)
    db = get_client()[db_name]
    existing = db.list_collection_names()
    if existing and not force:
        raise RestoreError(f"target database '{db_name}' is not empty ({len(existing)} collections); "
                           "use --force to replace same-named collections")
    restored = {}
    for name, n in manifest["collections"].items():
        if force:
            db[name].drop()
        docs = []
        with gzip.open(backup_dir / "db" / f"{name}.jsonl.gz", "rt", encoding="utf-8") as f:
            for line in f:
                docs.append(json_util.loads(line, json_options=JSON_OPTS))
                if len(docs) >= 1000:
                    db[name].insert_many(docs)
                    docs = []
        if docs:
            db[name].insert_many(docs)
        if db[name].count_documents({}) != n:
            raise RestoreError(f"document count mismatch in '{name}'")
        restored[name] = n
    ensure_indexes(db)
    if files_to is not None and manifest["includes_files"]:
        files_to.mkdir(parents=True, exist_ok=True)
        with tarfile.open(backup_dir / "files.tar.gz", "r:gz") as tar:
            tar.extractall(files_to.parent, filter="data", members=_inside(tar, files_to))  # "data" filter refuses absolute paths and ..
    return restored


def _inside(tar: tarfile.TarFile, target: Path):
    """Entries are stored under 'storage/'; map them to the chosen folder name and refuse anything else."""
    for m in tar.getmembers():
        if not (m.name == "storage" or m.name.startswith("storage/")):
            raise RestoreError(f"unexpected archive entry: {m.name}")
        m.name = target.name + m.name[len("storage"):]
        yield m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backup", required=True, type=Path)
    ap.add_argument("--db", required=True, help="target database name")
    ap.add_argument("--files-to", type=Path, default=None, help="folder to extract the file storage into")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    try:
        r = restore(a.backup, a.db, a.files_to, a.force)
    except RestoreError as e:
        raise SystemExit(f"restore stopped: {e}") from None
    extra = f"; files into {a.files_to}" if a.files_to else ""
    print(f"restored {sum(r.values())} documents in {len(r)} collections into '{a.db}'{extra}")


if __name__ == "__main__":
    main()
