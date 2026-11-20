import uuid

import pytest

from app.db.mongo import get_db
from app.models import ROLE_NAMES
from app.services.audit import verify_chain, write_audit
from tests.conftest import auth

API = "/api/v1"
PID = str(uuid.uuid4())

# (method, path, roles that MUST be allowed). Everyone else must get 403.
MATRIX = [
    ("GET", f"{API}/admin/users", {"admin"}),
    ("GET", f"{API}/audit", {"admin"}),
    ("GET", f"{API}/audit/verify", {"admin"}),
    ("GET", f"{API}/patients/{PID}/stub", {"clinician", "eeg_technologist", "viewer"}),
    ("GET", f"{API}/research/stub", {"researcher", "clinician", "admin"}),
    ("GET", f"{API}/version", set(ROLE_NAMES)),
    ("GET", f"{API}/auth/me", set(ROLE_NAMES)),
]


@pytest.mark.parametrize("method,path,allowed", MATRIX)
def test_rbac_matrix(client, tokens, method, path, allowed):
    for role in ROLE_NAMES:
        r = client.request(method, path, headers=auth(tokens[role]))
        if role in allowed:
            assert r.status_code == 200, f"{role} should be allowed on {path}: {r.text}"
        else:
            assert r.status_code == 403, f"{role} should be denied on {path}"


@pytest.mark.parametrize("method,path,allowed", MATRIX)
def test_unauthenticated_is_rejected(client, method, path, allowed):
    assert client.request(method, path).status_code == 401


def test_admin_has_no_default_clinical_access(client, tokens):
    assert client.get(f"{API}/patients/{PID}/stub", headers=auth(tokens["admin"])).status_code == 403


def test_every_patient_scoped_request_writes_audit_row(client, tokens):
    for _ in range(3):
        r = client.get(f"{API}/patients/{PID}/stub", headers=auth(tokens["clinician"]))
        assert r.status_code == 200
    rows = list(get_db().audit_log.find({"action": "patient.view"}))
    assert len(rows) == 3
    assert all(str(r["patient_id"]) == PID and r["user_id"] for r in rows)


def test_audit_chain_verifies_when_untouched(client, tokens):
    client.get(f"{API}/patients/{PID}/stub", headers=auth(tokens["clinician"]))
    r = client.get(f"{API}/audit/verify", headers=auth(tokens["admin"]))
    assert r.json()["ok"] is True and r.json()["checked"] > 0


def test_audit_chain_detects_edit(client, tokens):
    for _ in range(3):
        client.get(f"{API}/patients/{PID}/stub", headers=auth(tokens["clinician"]))
    audit = get_db().audit_log
    target = audit.find_one({"action": "patient.view"}, sort=[("seq", 1)])["seq"]
    audit.update_one({"seq": target}, {"$set": {"action": "patient.nothing_to_see"}})
    r = client.get(f"{API}/audit/verify", headers=auth(tokens["admin"])).json()
    assert r["ok"] is False and r["first_bad_id"] == target


def test_audit_chain_detects_deletion(client, tokens):
    for _ in range(3):
        client.get(f"{API}/patients/{PID}/stub", headers=auth(tokens["clinician"]))
    audit = get_db().audit_log
    seqs = [r["seq"] for r in audit.find({"action": "patient.view"}).sort("seq", 1)]
    audit.delete_one({"seq": seqs[1]})
    r = client.get(f"{API}/audit/verify", headers=auth(tokens["admin"])).json()
    assert r["ok"] is False
    assert r["first_bad_id"] == seqs[2]


def test_audit_details_never_hold_phi_fields(client, tokens):
    client.get(f"{API}/patients/{PID}/stub", headers=auth(tokens["clinician"]))
    for row in get_db().audit_log.find():
        assert not ({"name", "full_name", "dob", "mrn"} & set((row.get("details") or {}).keys()))


def test_concurrent_audit_appends_keep_the_chain_valid():
    """Many writers appending at once: unique seq + retry must yield one gap-free, valid chain."""
    from concurrent.futures import ThreadPoolExecutor

    db = get_db()
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda i: write_audit(db, "test.concurrent", details={"i": i}), range(80)))
    assert db.audit_log.count_documents({}) == 80
    result = verify_chain(db)
    assert result.ok and result.checked == 80
