"""Domain constants and the lightweight User object (documents live in MongoDB)."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

ROLE_NAMES = ("admin", "clinician", "eeg_technologist", "researcher", "viewer")


@dataclass
class User:
    id: uuid.UUID
    email: str
    full_name: str
    password_hash: str | None = None
    is_active: bool = True
    failed_logins: int = 0
    locked_until: datetime | None = None
    last_login_at: datetime | None = None
    roles: list[str] = field(default_factory=list)

    @property
    def role_names(self) -> list[str]:
        return sorted(self.roles)

    @classmethod
    def from_doc(cls, d: dict | None) -> "User | None":
        if d is None:
            return None
        return cls(
            id=d["_id"], email=d["email"], full_name=d["full_name"], password_hash=d.get("password_hash"),
            is_active=d.get("is_active", True),
            failed_logins=d.get("failed_logins", 0), locked_until=d.get("locked_until"),
            last_login_at=d.get("last_login_at"), roles=list(d.get("roles", [])),
        )
