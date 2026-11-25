"""Create demo accounts and one clearly fictional patient so a fresh install can be shown straight away.

Usage (PowerShell, from backend/):
    $env:NB_DEMO_PASSWORD = "a-long-demo-password"; uv run python -m scripts.seed_demo

Creates (skips those that exist): admin@example.com (admin) and clinician@example.com (clinician), both with the
password from NB_DEMO_PASSWORD (12+ characters), and the fictional patient "Demo Patient". The patient is
created through the normal service layer, so its identifiers are encrypted and its search tokens are computed
with the current keys (re-run after changing the HMAC/secret key). No EEG is included: upload a public
recording (for example an EEGMMIDB EDF) from the web app.
"""

import os
import sys
from datetime import date

from pymongo.errors import DuplicateKeyError

from app.core.security import hash_password, validate_password_strength
from app.db.mongo import ensure_indexes, get_db
from app.schemas.patients import PatientCreate
from app.services import patients as patients_svc
from app.services import users as users_repo

ACCOUNTS = [("admin@example.com", "Demo Admin", "admin"), ("clinician@example.com", "Demo Clinician", "clinician")]


def main() -> None:
    pw = os.environ.get("NB_DEMO_PASSWORD", "")
    problems = validate_password_strength(pw) if pw else ["set NB_DEMO_PASSWORD"]
    if problems:
        sys.exit("; ".join(problems))
    db = get_db()
    ensure_indexes(db)
    clinician = None
    for email, name, role in ACCOUNTS:
        try:
            u = users_repo.create(db, email=email, full_name=name, password_hash=hash_password(pw), roles=[role])
            print(f"created {role}: {email}")
        except DuplicateKeyError:
            u = users_repo.get_by_email(db, email)
            print(f"exists  {role}: {email}")
        if role == "clinician":
            clinician = u
    data = PatientCreate(full_name="Demo Patient", date_of_birth=date(1980, 1, 1), sex="unknown", mrn="DEMO-0001",
                         etiology="unknown", consent_clinical=True, consent_research=True, tags=["demo"])
    if patients_svc.find_duplicates(db, data.full_name, data.date_of_birth, data.mrn, None):
        print("exists  patient: Demo Patient")
    else:
        patient, _ = patients_svc.create_patient(db, clinician, data)
        print(f"created patient: {patient['display_id']} (fictional)")


if __name__ == "__main__":
    main()
