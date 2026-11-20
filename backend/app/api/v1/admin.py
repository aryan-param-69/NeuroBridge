import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.api.deps import ADMIN, require_roles
from app.api.v1.auth import user_out
from app.core.security import hash_password, validate_password_strength
from app.db.base import utcnow
from app.db.mongo import get_db
from app.models import ROLE_NAMES, User
from app.schemas import AuditOut, AuditVerifyOut, UserCreate, UserOut, UserUpdate
from app.services import users as users_repo
from app.services.audit import verify_chain, write_audit

router = APIRouter(tags=["admin"])
admin_only = require_roles(ADMIN)


def _check_roles(names: list[str]) -> list[str]:
    bad = set(names) - set(ROLE_NAMES)
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown roles: {sorted(bad)}")
    return sorted(set(names))


@router.get("/admin/users", response_model=list[UserOut])
def list_users(db: Database = Depends(get_db), admin: User = Depends(admin_only)):
    return [user_out(u) for u in users_repo.list_all(db)]


@router.post("/admin/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, request: Request, db: Database = Depends(get_db),
                admin: User = Depends(admin_only)):
    problems = validate_password_strength(body.password)
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problems)
    roles = _check_roles(body.roles)
    try:
        user = users_repo.create(db, email=body.email.lower(), full_name=body.full_name,
                                 password_hash=hash_password(body.password), roles=roles)
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this e-mail already exists") from None
    write_audit(db, "admin.user_created", user_id=admin.id, request=request,
                entity_type="user", entity_id=user.id, details={"roles": roles})
    return user_out(user)


@router.patch("/admin/users/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, request: Request,
                db: Database = Depends(get_db), admin: User = Depends(admin_only)):
    user = users_repo.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    fields: dict = {}
    changed: dict = {}
    if body.full_name is not None:
        fields["full_name"] = body.full_name
        changed["full_name"] = True
    if body.roles is not None:
        roles = _check_roles(body.roles)
        if user.id == admin.id and ADMIN not in roles:
            raise HTTPException(status.HTTP_409_CONFLICT, "You cannot remove your own admin role")
        fields["roles"] = roles
        changed["roles"] = roles
    if body.is_active is not None:
        if user.id == admin.id and not body.is_active:
            raise HTTPException(status.HTTP_409_CONFLICT, "You cannot deactivate yourself")
        fields["is_active"] = body.is_active
        changed["is_active"] = body.is_active
        if not body.is_active:
            db.refresh_tokens.update_many({"user_id": user.id, "revoked_at": None},
                                          {"$set": {"revoked_at": utcnow()}})
    if fields:
        users_repo.update(db, user.id, fields)
    write_audit(db, "admin.user_updated", user_id=admin.id, request=request,
                entity_type="user", entity_id=user.id, details=changed)
    return user_out(users_repo.get_by_id(db, user_id))


@router.get("/audit", response_model=list[AuditOut])
def list_audit(
    request: Request,
    user_id: uuid.UUID | None = None,
    patient_id: uuid.UUID | None = None,
    action: str | None = Query(None, max_length=80),
    limit: int = Query(100, ge=1, le=1000),
    db: Database = Depends(get_db),
    admin: User = Depends(admin_only),
):
    q: dict = {}
    if user_id:
        q["user_id"] = user_id
    if patient_id:
        q["patient_id"] = patient_id
    if action:
        q["action"] = action
    rows = list(db.audit_log.find(q).sort("seq", -1).limit(limit))
    write_audit(db, "audit.viewed", user_id=admin.id, request=request)
    return [AuditOut(id=r["seq"], occurred_at=r["occurred_at"], user_id=r.get("user_id"), action=r["action"],
                     entity_type=r.get("entity_type"), entity_id=r.get("entity_id"),
                     patient_id=r.get("patient_id"), ip_address=r.get("ip_address")) for r in rows]


@router.get("/audit/verify", response_model=AuditVerifyOut)
def audit_verify(db: Database = Depends(get_db), admin: User = Depends(admin_only)):
    r = verify_chain(db)
    return AuditVerifyOut(ok=r.ok, checked=r.checked, first_bad_id=r.first_bad_id, reason=r.reason)
