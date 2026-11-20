import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pymongo.database import Database

from app.api.deps import (
    ADMIN,
    CLINICIAN,
    RESEARCHER,
    TECHNOLOGIST,
    VIEWER,
    audit_patient_access,
    require_roles,
)
from app.db.base import utcnow
from app.db.mongo import get_db
from app.models import User
from app.schemas.patients import (
    AccessOut,
    AccessUpdate,
    EventIn,
    EventOut,
    HistoryOut,
    MedicalHistoryIn,
    MedicalHistoryOut,
    MedicalHistoryPatch,
    PatientCreate,
    PatientUpdate,
)
from app.services import patients as svc
from app.services import users as users_repo
from app.services.audit import write_audit

router = APIRouter(prefix="/patients", tags=["patients"])

CLINICAL_READERS = (CLINICIAN, TECHNOLOGIST, VIEWER)
read_clinical = require_roles(*CLINICAL_READERS)
read_any = require_roles(*CLINICAL_READERS, RESEARCHER)
write_clinical = require_roles(CLINICIAN)


# ---- Views -----------------------------------------------------------------

def view_level(user: User) -> str:
    roles = set(user.role_names)
    if CLINICIAN in roles:
        return "full"
    if roles & {TECHNOLOGIST, VIEWER}:
        return "standard"
    return "research"


def _years(since: date, until: date) -> int:
    return until.year - since.year - ((until.month, until.day) < (since.month, since.day))


def patient_view(p: dict, ident: dict | None, level: str) -> dict:
    today = date.today()
    injury = date.fromisoformat(p["date_of_injury"]) if p.get("date_of_injury") else None
    base = {
        "id": p["_id"], "display_id": p["display_id"], "sex": p["sex"], "etiology": p["etiology"],
        "status": p["status"], "craniectomy": p["craniectomy"], "view": level,
        "days_since_injury": (today - injury).days if injury else None,
    }
    if level == "research":
        # Pseudonymised: no name, no dates of birth/injury, no MRN, no free text.
        return {**base, "birth_year": p["birth_year"], "consent_research": p["consent_research"]}
    i = svc.reveal(ident)
    out = {
        **base,
        "full_name": i["full_name"], "date_of_birth": i["date_of_birth"],
        "age": _years(i["date_of_birth"], today), "mrn": i["mrn"],
        "etiology_other": p.get("etiology_other"), "date_of_injury": p.get("date_of_injury"),
        "admission_date": p.get("admission_date"), "clinical_status_label": p.get("clinical_status_label"),
        "consent_clinical": p["consent_clinical"], "consent_research": p["consent_research"],
        "consent_research_date": p.get("consent_research_date"), "tags": p.get("tags", []),
        "created_at": p["created_at"], "updated_at": p["updated_at"],
    }
    if level == "full":
        out["guardian_contact"] = i["guardian_contact"]
    return out


# ---- Access control --------------------------------------------------------

def _on_team(p: dict, user: User) -> bool:
    return any(m["user_id"] == user.id for m in p.get("care_team", []))


def load_patient(db: Database, user: User, patient_id: uuid.UUID, *, write: bool = False,
                 write_roles: tuple[str, ...] = (CLINICIAN,)) -> dict:
    """Fetch a patient the user may see. Returns 404 (not 403) when not allowed: no existence leak."""
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    p = db.patients.find_one({"_id": patient_id}, {"search": 0})
    if p is None:
        raise not_found
    roles = set(user.role_names)
    if roles & set(CLINICAL_READERS):
        if not _on_team(p, user):
            raise not_found
        if write and not roles & set(write_roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    elif RESEARCHER in roles:
        if write or not p["consent_research"]:
            raise not_found
    else:
        raise not_found
    return p


def _audit(db, request, user, action, patient_id, details=None):
    audit_patient_access(request, db, user, action, patient_id, details)


def _view_one(db: Database, p: dict, user: User) -> dict:
    level = view_level(user)
    ident = None if level == "research" else db.patient_identifiers.find_one({"_id": p["_id"]})
    return patient_view(p, ident, level)


# ---- Search / create -------------------------------------------------------

@router.get("")
def list_patients(
    request: Request,
    q: str | None = Query(None, max_length=100),
    status_: str | None = Query(None, alias="status", pattern="^(active|discharged|archived)$"),
    etiology: str | None = Query(None, max_length=20),
    clinician_id: uuid.UUID | None = None,
    injury_from: date | None = None,
    injury_to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Database = Depends(get_db),
    user: User = Depends(read_any),
):
    level = view_level(user)
    conds: list[dict] = []
    if level == "research":
        conds.append({"consent_research": True})
    else:
        conds.append({"care_team.user_id": user.id})
        if clinician_id:
            conds.append({"care_team.user_id": clinician_id})
        search = svc.build_search(q)
        if search:
            conds.append(search)
    if status_:
        conds.append({"status": status_})
    if etiology:
        conds.append({"etiology": etiology})
    if injury_from:
        conds.append({"date_of_injury": {"$gte": injury_from.isoformat()}})
    if injury_to:
        conds.append({"date_of_injury": {"$lte": injury_to.isoformat()}})
    flt = {"$and": conds}

    total = db.patients.count_documents(flt)
    rows = list(db.patients.find(flt, {"search": 0}).sort([("created_at", -1), ("_id", 1)])
                .skip((page - 1) * page_size).limit(page_size))
    idents = svc.identifiers_for(db, [r["_id"] for r in rows]) if level != "research" else {}

    used = [k for k, v in (("q", q), ("status", status_), ("etiology", etiology), ("clinician_id", clinician_id),
                           ("injury_from", injury_from), ("injury_to", injury_to)) if v]
    write_audit(db, "patient.search", user_id=user.id, request=request, entity_type="patient",
                details={"filters": used, "results": len(rows)})  # never the query text
    return {"items": [patient_view(r, idents.get(r["_id"]), level) for r in rows], "total": total,
            "page": page, "page_size": page_size}


@router.post("", status_code=status.HTTP_201_CREATED)
def register_patient(body: PatientCreate, request: Request, db: Database = Depends(get_db),
                     user: User = Depends(write_clinical)):
    dupes = svc.find_duplicates(db, body.full_name, body.date_of_birth, body.mrn)
    if dupes and not body.confirm_duplicate:
        write_audit(db, "patient.duplicate_warning", user_id=user.id, request=request,
                    details={"matches": len(dupes)})
        accessible = {d["_id"] for d in db.patients.find(
            {"_id": {"$in": [d.patient_id for d in dupes]}, "care_team.user_id": user.id}, {"_id": 1})}
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "message": "A patient with matching details may already be registered",
            "duplicates": [{"display_id": d.display_id, "match": d.match,
                            "id": str(d.patient_id) if d.patient_id in accessible else None} for d in dupes],
        })
    p, ident = svc.create_patient(db, user, body)
    write_audit(db, "patient.create", user_id=user.id, request=request, entity_type="patient",
                entity_id=p["_id"], patient_id=p["_id"], details={"duplicate_confirmed": bool(dupes)})
    return patient_view(p, ident, view_level(user))


@router.get("/{patient_id}")
def get_patient(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db),
                user: User = Depends(read_any)):
    p = load_patient(db, user, patient_id)
    _audit(db, request, user, "patient.view", patient_id)
    return _view_one(db, p, user)


@router.patch("/{patient_id}")
def edit_patient(patient_id: uuid.UUID, body: PatientUpdate, request: Request,
                 db: Database = Depends(get_db), user: User = Depends(write_clinical)):
    p = load_patient(db, user, patient_id, write=True)
    try:
        changed = svc.update_patient(db, user, p, body)
    except svc.UpdateError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    _audit(db, request, user, "patient.update", patient_id, {"fields": changed})  # field names only
    return _view_one(db, db.patients.find_one({"_id": patient_id}, {"search": 0}), user)


@router.get("/{patient_id}/history", response_model=list[HistoryOut])
def field_history(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db),
                  user: User = Depends(write_clinical)):
    load_patient(db, user, patient_id)
    _audit(db, request, user, "patient.history_view", patient_id)
    rows = db.patient_field_history.find({"patient_id": patient_id}).sort([("changed_at", -1), ("_id", -1)])
    return [HistoryOut(id=str(r["_id"]), field=r["field"], old_value=r["old_value"], new_value=r["new_value"],
                       changed_by=r["changed_by"], changed_at=r["changed_at"]) for r in rows]


# ---- Medical history -------------------------------------------------------

def _history_out(d: dict) -> MedicalHistoryOut:
    return MedicalHistoryOut(
        id=d["_id"], category=d["category"], description=d["description"], code_system=d.get("code_system"),
        code=d.get("code"), start_date=d.get("start_date"), end_date=d.get("end_date"),
        is_active=d["is_active"], created_at=d["created_at"])


@router.get("/{patient_id}/medical-history", response_model=list[MedicalHistoryOut])
def list_history_items(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db),
                       user: User = Depends(read_clinical)):
    load_patient(db, user, patient_id)
    _audit(db, request, user, "patient.medical_history_view", patient_id)
    rows = db.medical_history_items.find({"patient_id": patient_id, "deleted_at": None}).sort("created_at", -1)
    return [_history_out(d) for d in rows]


@router.post("/{patient_id}/medical-history", response_model=MedicalHistoryOut,
             status_code=status.HTTP_201_CREATED)
def add_history_item(patient_id: uuid.UUID, body: MedicalHistoryIn, request: Request,
                     db: Database = Depends(get_db), user: User = Depends(write_clinical)):
    load_patient(db, user, patient_id, write=True)
    doc = {"_id": uuid.uuid4(), "patient_id": patient_id, "created_by": user.id, "created_at": utcnow(),
           "deleted_at": None, **{k: (v.isoformat() if isinstance(v, date) else v)
                                  for k, v in body.model_dump().items()}}
    db.medical_history_items.insert_one(doc)
    _audit(db, request, user, "patient.medical_history_add", patient_id, {"category": body.category})
    return _history_out(doc)


def _history_item(db: Database, patient_id: uuid.UUID, item_id: uuid.UUID) -> dict:
    item = db.medical_history_items.find_one({"_id": item_id, "patient_id": patient_id, "deleted_at": None})
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item


@router.patch("/{patient_id}/medical-history/{item_id}", response_model=MedicalHistoryOut)
def update_history_item(patient_id: uuid.UUID, item_id: uuid.UUID, body: MedicalHistoryPatch,
                        request: Request, db: Database = Depends(get_db),
                        user: User = Depends(write_clinical)):
    load_patient(db, user, patient_id, write=True)
    item = _history_item(db, patient_id, item_id)
    changes = {k: (v.isoformat() if isinstance(v, date) else v)
               for k, v in body.model_dump(exclude_unset=True).items()}
    start, end = changes.get("start_date", item.get("start_date")), changes.get("end_date", item.get("end_date"))
    if start and end and end < start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "End date cannot be before start date")
    if changes:
        db.medical_history_items.update_one({"_id": item_id}, {"$set": changes})
    _audit(db, request, user, "patient.medical_history_update", patient_id)
    return _history_out({**item, **changes})


@router.delete("/{patient_id}/medical-history/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_history_item(patient_id: uuid.UUID, item_id: uuid.UUID, request: Request,
                        db: Database = Depends(get_db), user: User = Depends(write_clinical)):
    load_patient(db, user, patient_id, write=True)
    _history_item(db, patient_id, item_id)
    db.medical_history_items.update_one({"_id": item_id}, {"$set": {"deleted_at": utcnow()}})  # soft delete
    _audit(db, request, user, "patient.medical_history_delete", patient_id)


# ---- Clinical events -------------------------------------------------------

def _event_out(d: dict) -> EventOut:
    return EventOut(id=d["_id"], event_type=d["event_type"], occurred_at=d["occurred_at"],
                    description=d["description"], entered_by=d["entered_by"], created_at=d["created_at"])


@router.get("/{patient_id}/events", response_model=list[EventOut])
def list_events(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db),
                user: User = Depends(read_clinical)):
    load_patient(db, user, patient_id)
    _audit(db, request, user, "patient.events_view", patient_id)
    rows = db.clinical_events.find({"patient_id": patient_id, "deleted_at": None}).sort("occurred_at", -1)
    return [_event_out(d) for d in rows]


@router.post("/{patient_id}/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def add_event(patient_id: uuid.UUID, body: EventIn, request: Request, db: Database = Depends(get_db),
              user: User = Depends(write_clinical)):
    load_patient(db, user, patient_id, write=True)
    doc = {"_id": uuid.uuid4(), "patient_id": patient_id, "entered_by": user.id, "created_at": utcnow(),
           "deleted_at": None, **body.model_dump()}
    db.clinical_events.insert_one(doc)
    _audit(db, request, user, "patient.event_add", patient_id, {"event_type": body.event_type})
    return _event_out(doc)


# ---- Care team -------------------------------------------------------------

def _manage_access_guard(db: Database, user: User, patient_id: uuid.UUID) -> dict:
    """Clinicians on the care team, or admins (who manage assignments but see no clinical content)."""
    p = db.patients.find_one({"_id": patient_id}, {"care_team": 1})
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    if ADMIN in user.role_names:
        return p
    if CLINICIAN not in user.role_names or not _on_team(p, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return p


def _access_out(db: Database, members: list[dict]) -> list[AccessOut]:
    users = users_repo.get_many(db, [m["user_id"] for m in members], active_only=False)
    return [AccessOut(user_id=m["user_id"], full_name=users[m["user_id"]].full_name,
                      roles=users[m["user_id"]].role_names, access_level=m["access_level"])
            for m in members if m["user_id"] in users]


@router.get("/{patient_id}/access", response_model=list[AccessOut])
def get_access(patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db),
               user: User = Depends(require_roles(CLINICIAN, ADMIN))):
    p = _manage_access_guard(db, user, patient_id)
    _audit(db, request, user, "patient.access_view", patient_id)
    return _access_out(db, p["care_team"])


@router.put("/{patient_id}/access", response_model=list[AccessOut])
def set_access(patient_id: uuid.UUID, body: AccessUpdate, request: Request, db: Database = Depends(get_db),
               user: User = Depends(require_roles(CLINICIAN, ADMIN))):
    p = _manage_access_guard(db, user, patient_id)
    ids = [m.user_id for m in body.members]
    if len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Duplicate users in care team")
    if not any(m.access_level == "owner" for m in body.members):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "At least one owner is required")
    users = users_repo.get_many(db, ids)  # active users only
    for m in body.members:
        u = users.get(m.user_id)
        if u is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown or inactive user")
        if not set(u.role_names) & set(CLINICAL_READERS):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"{u.full_name} has no clinical role and cannot join a care team")
    existing = {m["user_id"]: m for m in p["care_team"]}
    now = utcnow()
    team = [{"user_id": m.user_id, "access_level": m.access_level,
             "granted_by": existing[m.user_id]["granted_by"] if m.user_id in existing else user.id,
             "granted_at": existing[m.user_id]["granted_at"] if m.user_id in existing else now}
            for m in body.members]
    db.patients.update_one({"_id": patient_id}, {"$set": {"care_team": team, "updated_at": now}})
    _audit(db, request, user, "patient.access_update", patient_id, {"members": len(team)})
    return _access_out(db, team)
