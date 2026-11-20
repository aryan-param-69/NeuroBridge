"""Phase 0 stub endpoints.

They exist only to prove the access-control and audit machinery end to end. They contain no clinical data.
"""

import uuid

from fastapi import APIRouter, Depends, Request
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
from app.db.mongo import get_db
from app.models import User

router = APIRouter(tags=["phase0-stubs"])


@router.get("/patients/{patient_id}/stub")
def patient_scoped_stub(
    patient_id: uuid.UUID, request: Request, db: Database = Depends(get_db),
    user: User = Depends(require_roles(CLINICIAN, TECHNOLOGIST, VIEWER)),
):
    audit_patient_access(request, db, user, "patient.view", patient_id)
    return {"patient_id": str(patient_id), "stub": True}


@router.get("/research/stub")
def research_stub(user: User = Depends(require_roles(RESEARCHER, CLINICIAN, ADMIN))):
    return {"stub": True, "pseudonymised_only": True}
