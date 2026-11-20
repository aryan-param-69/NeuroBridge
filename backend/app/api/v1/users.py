from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.api.deps import ADMIN, CLINICIAN, require_roles
from app.db.mongo import get_db
from app.models import User
from app.services import users as users_repo

router = APIRouter(prefix="/users", tags=["users"])

CARE_TEAM_ROLES = {"clinician", "eeg_technologist", "viewer"}


@router.get("/directory")
def directory(db: Database = Depends(get_db), _: User = Depends(require_roles(CLINICIAN, ADMIN))):
    """Names of staff who can be assigned to a care team. No e-mail addresses are exposed."""
    users = [u for u in users_repo.list_all(db) if u.is_active and CARE_TEAM_ROLES & set(u.role_names)]
    users.sort(key=lambda u: u.full_name.lower())
    return [{"id": u.id, "full_name": u.full_name, "roles": u.role_names} for u in users]
