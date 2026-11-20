"""Append-only audit log with a SHA-256 hash chain (MongoDB).

Each entry stores the hash of the previous entry, so editing or deleting any row breaks every later
hash and is detected by `verify_chain`. A unique index on `seq` makes concurrent appends safe: if two
writers pick the same next `seq`, one insert fails and retries against the new chain head.
Details must never contain PHI values (names, dates of birth, MRNs).
"""

import hashlib
import json
import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Request
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.db.base import utcnow

GENESIS_HASH = "0" * 64
MAX_RETRIES = 200


def _norm_ts(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.isoformat(timespec="milliseconds")


def _canonical(row: dict, prev_hash: str) -> str:
    body = {
        "prev": prev_hash,
        "seq": row["seq"],
        "at": _norm_ts(row["occurred_at"]),
        "user": str(row["user_id"]) if row.get("user_id") else None,
        "action": row["action"],
        "etype": row.get("entity_type"),
        "eid": row.get("entity_id"),
        "patient": str(row["patient_id"]) if row.get("patient_id") else None,
        "ip": row.get("ip_address"),
        "ua": row.get("user_agent"),
        "details": row.get("details") or {},
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(row: dict, prev_hash: str) -> str:
    return hashlib.sha256(_canonical(row, prev_hash).encode()).hexdigest()


def client_info(request: Request | None) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    ip = request.client.host if request.client else None
    ua = (request.headers.get("user-agent") or "")[:300] or None
    return ip, ua


def write_audit(
    db: Database,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    request: Request | None = None,
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    patient_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> dict:
    ip, ua = client_info(request)
    for attempt in range(MAX_RETRIES):
        if attempt:  # lost a race for this seq: back off with jitter so contending writers spread out
            time.sleep(random.uniform(0, 0.002 * min(attempt, 10)))
        head = db.audit_log.find_one(sort=[("seq", -1)], projection={"seq": 1, "row_hash": 1})
        prev = head["row_hash"] if head else GENESIS_HASH
        row = {
            "seq": (head["seq"] + 1) if head else 1,
            "occurred_at": utcnow(), "user_id": user_id, "action": action,
            "entity_type": entity_type, "entity_id": str(entity_id) if entity_id is not None else None,
            "patient_id": patient_id, "ip_address": ip, "user_agent": ua,
            "details": details or {}, "prev_hash": prev,
        }
        row["row_hash"] = compute_hash(row, prev)
        try:
            db.audit_log.insert_one(row)
            return row
        except DuplicateKeyError:
            continue  # another writer took this seq; retry against the new head
    raise RuntimeError("Could not append to the audit log")


@dataclass
class ChainResult:
    ok: bool
    checked: int
    first_bad_id: int | None = None
    reason: str | None = None


def verify_chain(db: Database) -> ChainResult:
    prev = GENESIS_HASH
    expected_seq = 0
    checked = 0
    for row in db.audit_log.find().sort("seq", 1):
        checked += 1
        expected_seq += 1
        if row["seq"] != expected_seq:
            return ChainResult(False, checked, row["seq"], "sequence gap (an entry was removed)")
        if row["prev_hash"] != prev:
            return ChainResult(False, checked, row["seq"], "previous-hash link broken (row removed or reordered)")
        if compute_hash(row, prev) != row["row_hash"]:
            return ChainResult(False, checked, row["seq"], "row content does not match its hash (row edited)")
        prev = row["row_hash"]
    return ChainResult(True, checked)
