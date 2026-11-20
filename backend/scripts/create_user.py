"""Create a user with one role (MongoDB).

Usage (PowerShell):
    $env:NB_USER_PASSWORD = "..."; uv run python -m scripts.create_user EMAIL "Full Name" clinician
Roles: admin, clinician, eeg_technologist, researcher, viewer. The password comes from the environment.
"""

import os
import sys

from pymongo.errors import DuplicateKeyError

from app.core.security import hash_password, validate_password_strength
from app.db.mongo import ensure_indexes, get_db
from app.models import ROLE_NAMES
from app.services import users as users_repo


def main() -> None:
    if len(sys.argv) != 4 or not os.environ.get("NB_USER_PASSWORD"):
        sys.exit("Usage: set NB_USER_PASSWORD, then: python -m scripts.create_user EMAIL NAME ROLE")
    email, name, role = sys.argv[1].lower(), sys.argv[2], sys.argv[3]
    if role not in ROLE_NAMES:
        sys.exit(f"Role must be one of: {', '.join(ROLE_NAMES)}")
    problems = validate_password_strength(os.environ["NB_USER_PASSWORD"])
    if problems:
        sys.exit("; ".join(problems))
    db = get_db()
    ensure_indexes(db)
    try:
        users_repo.create(db, email=email, full_name=name,
                          password_hash=hash_password(os.environ["NB_USER_PASSWORD"]), roles=[role])
    except DuplicateKeyError:
        sys.exit("User already exists")
    print(f"Created {role} {email}. Sign in with the e-mail and the password you set.")


if __name__ == "__main__":
    main()
