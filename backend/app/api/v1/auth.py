from datetime import timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pymongo.database import Database

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.security import (
    create_token,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.db.base import as_utc, utcnow
from app.db.mongo import get_db
from app.models import User
from app.schemas import LoginRequest, TokenResponse, UserOut
from app.services import users as users_repo
from app.services.audit import write_audit

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "nb_refresh"
BAD_CREDENTIALS = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")


def user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id, email=u.email, full_name=u.full_name, roles=u.role_names,
        is_active=u.is_active, last_login_at=u.last_login_at,
    )


def _issue_session(db: Database, user: User, response: Response) -> TokenResponse:
    s = get_settings()
    access = create_token(str(user.id), "access", s.access_token_minutes)
    raw, token_hash = new_refresh_token()
    db.refresh_tokens.insert_one({
        "user_id": user.id, "token_hash": token_hash, "created_at": utcnow(), "revoked_at": None,
        "expires_at": utcnow() + timedelta(days=s.refresh_token_days),
    })
    response.set_cookie(
        REFRESH_COOKIE, raw, max_age=s.refresh_token_days * 86400, httponly=True,
        secure=s.cookie_secure, samesite="strict", path="/api/v1/auth",
    )
    return TokenResponse(access_token=access, expires_in=s.access_token_minutes * 60)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, response: Response, db: Database = Depends(get_db)):
    s = get_settings()
    email = body.email.lower()
    user = users_repo.get_by_email(db, email)

    locked = bool(user and user.locked_until and as_utc(user.locked_until) > utcnow())
    ok = verify_password(body.password, user.password_hash if user else None)  # constant-ish time

    if user is None or locked or not user.is_active or not ok:
        if user is not None and not locked and not ok:
            failed = user.failed_logins + 1
            if failed >= s.max_failed_logins:
                users_repo.update(db, user.id, {
                    "failed_logins": 0, "locked_until": utcnow() + timedelta(minutes=s.lockout_minutes)})
            else:
                users_repo.update(db, user.id, {"failed_logins": failed})
        write_audit(db, "auth.login_failed", user_id=user.id if user else None, request=request,
                    details={"locked": locked})
        raise BAD_CREDENTIALS  # same message for every failure: no account enumeration

    users_repo.update(db, user.id, {"failed_logins": 0, "last_login_at": utcnow()})
    tokens = _issue_session(db, user, response)
    write_audit(db, "auth.login", user_id=user.id, request=request)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Database = Depends(get_db),
            nb_refresh: str | None = Cookie(default=None)):
    if not nb_refresh:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No session")
    row = db.refresh_tokens.find_one({"token_hash": hash_refresh_token(nb_refresh)})
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
    if row["revoked_at"] is not None:
        # A rotated token was presented again: assume theft, revoke the whole family.
        db.refresh_tokens.update_many({"user_id": row["user_id"], "revoked_at": None},
                                      {"$set": {"revoked_at": utcnow()}})
        write_audit(db, "auth.refresh_reuse_detected", user_id=row["user_id"], request=request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
    if as_utc(row["expires_at"]) < utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    user = users_repo.get_by_id(db, row["user_id"])
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
    # Atomically claim the token: if two requests race, only one rotation succeeds.
    claimed = db.refresh_tokens.update_one({"_id": row["_id"], "revoked_at": None},
                                           {"$set": {"revoked_at": utcnow()}})
    if claimed.modified_count != 1:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
    return _issue_session(db, user, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Database = Depends(get_db),
           nb_refresh: str | None = Cookie(default=None)):
    if nb_refresh:
        row = db.refresh_tokens.find_one_and_update(
            {"token_hash": hash_refresh_token(nb_refresh), "revoked_at": None},
            {"$set": {"revoked_at": utcnow()}})
        if row:
            write_audit(db, "auth.logout", user_id=row["user_id"], request=request)
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user_out(user)
