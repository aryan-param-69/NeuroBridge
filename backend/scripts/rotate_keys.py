"""Rotate the ENCRYPTION_KEY: re-encrypt stored identifiers and original EEG files under a new key.

Procedure
  1. Generate a new key:   uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  2. Put it in ENCRYPTION_KEY and move the OLD value to ENCRYPTION_KEY_PREVIOUS (comma-separated if there are several). Restart the API:
     everything keeps working because old keys can still decrypt.
  3. Dry run (default):    uv run python -m scripts.rotate_keys          (counts what still uses an old key; changes nothing)
  4. Apply:                uv run python -m scripts.rotate_keys --apply   (take a backup first: scripts.backup)
  5. Run the dry run again: it must report 0 remaining. Only then remove the old key from ENCRYPTION_KEY_PREVIOUS.

Not covered: HMAC_KEY (search tokens must be re-computed; see scripts.seed_demo for how they are made) and SECRET_KEY
(changing it signs everyone out).
"""

import argparse

from cryptography.fernet import InvalidToken

from app import storage
from app.core.security import is_under_current_key, rotate_secret
from app.db.mongo import get_db


def rotate_identifiers(db, apply: bool) -> tuple[int, int]:
    """Return (identifier fields not under the current key, fields examined)."""
    stale = seen = 0
    for doc in db.patient_identifiers.find():
        update = {}
        for field, value in doc.items():
            if not field.endswith("_enc") or value is None:
                continue
            seen += 1
            token = bytes(value)
            if is_under_current_key(token):
                continue
            stale += 1
            try:
                update[field] = rotate_secret(token)
            except InvalidToken:
                raise SystemExit(f"cannot decrypt {field} of patient_identifiers {doc['_id']} with any configured key: "
                                 "stop and check ENCRYPTION_KEY / ENCRYPTION_KEY_PREVIOUS") from None
        if update and apply:
            db.patient_identifiers.update_one({"_id": doc["_id"]}, {"$set": update})
    return stale, seen


def rotate_files(apply: bool) -> tuple[int, int]:
    stale = seen = 0
    for p in sorted((storage.root() / "raw").rglob("*.nbe")):
        seen += 1
        if not storage.uses_current_key(p):
            stale += 1
            if apply:
                storage.rotate_file(p)
    return stale, seen


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="really re-encrypt (default is a dry run)")
    a = ap.parse_args()
    db = get_db()
    s_i, n_i = rotate_identifiers(db, a.apply)
    s_f, n_f = rotate_files(a.apply)
    verb = "re-encrypted" if a.apply else "still under an old key"
    print(f"identifier fields: {s_i} of {n_i} {verb}\noriginal EEG files: {s_f} of {n_f} {verb}")
    if not a.apply and (s_i or s_f):
        print("dry run: nothing changed; run again with --apply after taking a backup")


if __name__ == "__main__":
    main()
