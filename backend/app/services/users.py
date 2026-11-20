"""User persistence."""

import uuid

from pymongo.database import Database

from app.db.base import utcnow
from app.models import User


def get_by_id(db: Database, user_id: uuid.UUID) -> User | None:
    return User.from_doc(db.users.find_one({"_id": user_id}))


def get_by_email(db: Database, email: str) -> User | None:
    return User.from_doc(db.users.find_one({"email": email}))


def create(db: Database, *, email: str, full_name: str, password_hash: str, roles: list[str]) -> User:
    now = utcnow()
    doc = {
        "_id": uuid.uuid4(), "email": email, "full_name": full_name, "password_hash": password_hash,
        "is_active": True, "failed_logins": 0,
        "locked_until": None, "last_login_at": None, "roles": sorted(set(roles)),
        "created_at": now, "updated_at": now,
    }
    db.users.insert_one(doc)  # unique index on email raises DuplicateKeyError on a race
    return User.from_doc(doc)


def update(db: Database, user_id: uuid.UUID, fields: dict) -> None:
    db.users.update_one({"_id": user_id}, {"$set": {**fields, "updated_at": utcnow()}})


def list_all(db: Database) -> list[User]:
    return [User.from_doc(d) for d in db.users.find().sort("email", 1)]


def get_many(db: Database, ids: list[uuid.UUID], *, active_only: bool = True) -> dict[uuid.UUID, User]:
    q: dict = {"_id": {"$in": ids}}
    if active_only:
        q["is_active"] = True
    return {d["_id"]: User.from_doc(d) for d in db.users.find(q)}
