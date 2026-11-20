import uuid
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo.database import Database

from app.core.security import decode_token
from app.db.mongo import get_db
from app.models import User
from app.services import users as users_repo
from app.services.audit import write_audit

_bearer = HTTPBearer(auto_error=False)

ADMIN = "admin"
CLINICIAN = "clinician"
TECHNOLOGIST = "eeg_technologist"
RESEARCHER = "researcher"
VIEWER = "viewer"


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Database = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"}
    )
    if creds is None:
        raise unauthorized
    payload = decode_token(creds.credentials, "access")
    if payload is None:
        raise unauthorized
    try:
        user = users_repo.get_by_id(db, uuid.UUID(payload["sub"]))
    except (ValueError, KeyError):
        raise unauthorized from None
    if user is None or not user.is_active:
        raise unauthorized
    return user


def require_roles(*allowed: str) -> Callable[..., User]:
    """Dependency factory: user must hold at least one of the allowed roles."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if not set(user.role_names) & set(allowed):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return checker


def audit_patient_access(
    request: Request, db: Database, user: User, action: str, patient_id: uuid.UUID,
    details: dict | None = None,
) -> None:
    """Record access to patient-scoped data. Call from every patient-scoped endpoint."""
    write_audit(
        db, action, user_id=user.id, request=request, entity_type="patient",
        entity_id=patient_id, patient_id=patient_id, details=details,
    )
