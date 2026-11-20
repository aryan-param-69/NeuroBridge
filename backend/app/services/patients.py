"""Patient business logic on MongoDB: IDs, encrypted identifiers, blind-index search, duplicates, history."""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.core.security import blind_index, decrypt_secret, encrypt_secret
from app.db.base import utcnow
from app.models import User
from app.models.patients import STATUS_TRANSITIONS
from app.schemas.patients import PatientCreate, PatientUpdate

MAX_TOKEN = 24
MIN_PREFIX = 3

PLAIN_FIELDS = (
    "sex", "etiology", "etiology_other", "date_of_injury", "admission_date",
    "clinical_status_label", "craniectomy", "consent_clinical", "consent_research", "tags", "status",
)
NOT_CLEARABLE = ("sex", "etiology", "craniectomy", "consent_clinical", "consent_research", "tags", "status")


# ---- Normalisation and blind indexes ---------------------------------------

def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def normalize_mrn(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()


def name_token_hashes(name: str) -> set[str]:
    """Hash every prefix (>= 3 chars) of every name token, so 'ram' finds 'Ramirez'."""
    out: set[str] = set()
    for tok in normalize_name(name).split():
        tok = tok[:MAX_TOKEN]
        out.update(blind_index(tok[:i], "nametok") for i in range(MIN_PREFIX, len(tok) + 1))
    return out


def query_token_hashes(q: str) -> list[str]:
    return [blind_index(t[:MAX_TOKEN], "nametok")
            for t in normalize_name(q).split() if len(t) >= MIN_PREFIX]


def identity_hash(name: str, dob: date) -> str:
    return blind_index(f"{normalize_name(name)}|{dob.isoformat()}", "identity")


def mrn_hash(mrn: str) -> str:
    return blind_index(normalize_mrn(mrn), "mrn")


def _iso(v):
    return v.isoformat() if isinstance(v, date) else v


# ---- Display IDs -----------------------------------------------------------

def next_display_id(db: Database) -> str:
    year = utcnow().year
    for _ in range(5):
        try:  # atomic increment; the first call of a year upserts the counter
            doc = db.counters.find_one_and_update(
                {"_id": f"patient:{year}"}, {"$inc": {"last": 1}}, upsert=True,
                return_document=ReturnDocument.AFTER)
            return f"NB-{year}-{doc['last']:06d}"
        except DuplicateKeyError:  # two first-of-year upserts raced; the next attempt increments
            continue
    raise RuntimeError("Could not allocate a patient ID")


# ---- Duplicates ------------------------------------------------------------

@dataclass
class Duplicate:
    patient_id: uuid.UUID
    display_id: str
    match: str  # "name_dob" | "mrn"


def find_duplicates(db: Database, full_name: str, dob: date, mrn: str | None,
                    exclude: uuid.UUID | None = None) -> list[Duplicate]:
    want = identity_hash(full_name, dob)
    ors = [{"search.identity_hash": want}]
    if mrn:
        ors.append({"search.mrn_hash": mrn_hash(mrn)})
    q: dict = {"$or": ors}
    if exclude:
        q["_id"] = {"$ne": exclude}
    return [Duplicate(d["_id"], d["display_id"], "name_dob" if d["search"]["identity_hash"] == want else "mrn")
            for d in db.patients.find(q, {"display_id": 1, "search.identity_hash": 1})]


# ---- Create / read identifiers --------------------------------------------

def _encrypted_identifiers(name: str, dob: date, mrn: str | None, guardian: str | None) -> dict:
    return {
        "full_name_enc": encrypt_secret(name),
        "dob_enc": encrypt_secret(dob.isoformat()),
        "mrn_enc": encrypt_secret(mrn) if mrn else None,
        "guardian_contact_enc": encrypt_secret(guardian) if guardian else None,
    }


def create_patient(db: Database, user: User, data: PatientCreate) -> tuple[dict, dict]:
    """Insert a patient. Returns (patient_doc, identifiers_doc).

    Identifiers are written FIRST: if the patient insert then fails, only an invisible orphan identifier
    remains (cleaned up here), never a visible patient without identifiers.
    """
    pid = uuid.uuid4()
    ident = {"_id": pid, **_encrypted_identifiers(data.full_name, data.date_of_birth, data.mrn,
                                                  data.guardian_contact)}
    now = utcnow()
    patient = {
        "_id": pid, "display_id": next_display_id(db), "sex": data.sex, "birth_year": data.date_of_birth.year,
        "etiology": data.etiology, "etiology_other": data.etiology_other,
        "date_of_injury": _iso(data.date_of_injury), "admission_date": _iso(data.admission_date),
        "clinical_status_label": data.clinical_status_label, "status": "active",
        "consent_clinical": data.consent_clinical, "consent_research": data.consent_research,
        "consent_research_date": date.today().isoformat() if data.consent_research else None,
        "craniectomy": data.craniectomy,
        "tags": sorted({t.strip() for t in data.tags if t.strip()}),
        "created_by": user.id, "created_at": now, "updated_at": now,
        "care_team": [{"user_id": user.id, "access_level": "owner", "granted_by": user.id, "granted_at": now}],
        "search": {
            "name_tokens": sorted(name_token_hashes(data.full_name)),
            "identity_hash": identity_hash(data.full_name, data.date_of_birth),
            "mrn_hash": mrn_hash(data.mrn) if data.mrn else None,
        },
    }
    db.patient_identifiers.insert_one(ident)
    try:
        db.patients.insert_one(patient)
    except Exception:
        db.patient_identifiers.delete_one({"_id": pid})
        raise
    return patient, ident


def reveal(ident: dict) -> dict:
    """Decrypt identifiers. Call only for roles that may see them."""
    return {
        "full_name": decrypt_secret(bytes(ident["full_name_enc"])),
        "date_of_birth": date.fromisoformat(decrypt_secret(bytes(ident["dob_enc"]))),
        "mrn": decrypt_secret(bytes(ident["mrn_enc"])) if ident.get("mrn_enc") else None,
        "guardian_contact": (decrypt_secret(bytes(ident["guardian_contact_enc"]))
                             if ident.get("guardian_contact_enc") else None),
    }


def identifiers_for(db: Database, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    return {d["_id"]: d for d in db.patient_identifiers.find({"_id": {"$in": ids}})}


# ---- Update ----------------------------------------------------------------

class UpdateError(ValueError):
    pass


def update_patient(db: Database, user: User, p: dict, data: PatientUpdate) -> list[str]:
    """Validate everything, then apply a partial update and write field history.

    All checks run before the first write, so a rejected edit changes nothing. Returns changed field names.
    """
    sent = data.model_fields_set
    today = date.today()
    updates: dict = {}
    history: list[dict] = []

    def log(field: str, old, new) -> None:
        history.append({"patient_id": p["_id"], "field": field, "old_value": old, "new_value": new,
                        "changed_by": user.id, "changed_at": utcnow()})

    # Plain (non-identifying) fields: old and new values are recorded.
    for f in PLAIN_FIELDS:
        if f not in sent:
            continue
        new = getattr(data, f)
        if new is None and f in NOT_CLEARABLE:
            raise UpdateError(f"{f} cannot be cleared")
        new = _iso(new)
        if f == "tags":
            new = sorted({t.strip() for t in new if t.strip()})
        old = p.get(f)
        if new == old:
            continue
        if f == "status" and new not in STATUS_TRANSITIONS[old]:
            raise UpdateError(f"Status cannot change from '{old}' to '{new}'")
        if f == "date_of_injury" and new and date.fromisoformat(new) > today:
            raise UpdateError("Date of injury cannot be in the future")
        updates[f] = new
        if f == "consent_research":
            updates["consent_research_date"] = today.isoformat() if new else None
        log(f, old, new)

    if updates.get("etiology", p["etiology"]) == "other" and not updates.get("etiology_other", p.get("etiology_other")):
        raise UpdateError("Describe the etiology when 'other' is selected")

    # Identifier fields: only the fact of change is recorded, never the values.
    ident = db.patient_identifiers.find_one({"_id": p["_id"]})
    cur = reveal(ident)
    name = data.full_name if "full_name" in sent and data.full_name else cur["full_name"]
    dob = data.date_of_birth if "date_of_birth" in sent and data.date_of_birth else cur["date_of_birth"]
    mrn = data.mrn if "mrn" in sent else cur["mrn"]
    guardian = data.guardian_contact if "guardian_contact" in sent else cur["guardian_contact"]
    if dob > today:
        raise UpdateError("Date of birth cannot be in the future")
    injury = updates.get("date_of_injury", p.get("date_of_injury"))
    if injury and date.fromisoformat(injury) < dob:
        raise UpdateError("Date of injury cannot be before date of birth")

    ident_changed = [f for f, new in (("full_name", name), ("date_of_birth", dob), ("mrn", mrn),
                                      ("guardian_contact", guardian)) if new != cur[f]]
    ident_set: dict = {}
    if ident_changed:
        ident_set = _encrypted_identifiers(name, dob, mrn, guardian)
        updates["birth_year"] = dob.year
        updates["search.identity_hash"] = identity_hash(name, dob)
        updates["search.mrn_hash"] = mrn_hash(mrn) if mrn else None
        if "full_name" in ident_changed:
            updates["search.name_tokens"] = sorted(name_token_hashes(name))
        for f in ident_changed:
            log(f, {"changed": True}, {"changed": True})

    changed = [h["field"] for h in history]
    if not changed:
        return []
    if ident_set:
        db.patient_identifiers.update_one({"_id": p["_id"]}, {"$set": ident_set})
    db.patients.update_one({"_id": p["_id"]}, {"$set": {**updates, "updated_at": utcnow()}})
    db.patient_field_history.insert_many(history)
    return changed


# ---- Search ----------------------------------------------------------------

def build_search(q: str | None) -> dict | None:
    """Mongo filter for the free-text box: NB-ID prefix, exact MRN, or name-token prefixes (all must match)."""
    if not q or not q.strip():
        return None
    q = q.strip()
    ors: list[dict] = []
    if q.upper().startswith("NB-"):
        ors.append({"display_id": {"$regex": "^" + re.escape(q.upper())}})
    ors.append({"search.mrn_hash": mrn_hash(q)})
    hashes = query_token_hashes(q)
    if hashes:
        ors.append({"search.name_tokens": {"$all": hashes}})
    return {"$or": ors}
