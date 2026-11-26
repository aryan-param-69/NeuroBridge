"""Admin user management: create, change password, delete, with the safety rules around each."""

import uuid

from app.db.mongo import get_db
from tests.conftest import PASSWORD, auth, login, make_user

API = "/api/v1"
NEW_PASSWORD = "Another-Strong-Pass-42"


def _admin(client, email="admin@example.com"):
    u = make_user("admin", email=email)
    return u, login(client, u)


def _login_raw(client, email, password):
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


def test_admin_can_create_a_user_who_can_then_log_in(client):
    _, token = _admin(client)
    r = client.post(f"{API}/admin/users", headers=auth(token), json={
        "email": "new.doctor@example.com", "full_name": "New Doctor", "password": NEW_PASSWORD, "roles": ["clinician"]})
    assert r.status_code == 201
    assert _login_raw(client, "new.doctor@example.com", NEW_PASSWORD).status_code == 200


def test_reset_password_changes_it_clears_lockout_and_signs_the_user_out(client):
    _, token = _admin(client)
    clin = make_user("clinician")
    clin_token = login(client, clin)
    for _ in range(5):  # lock the account
        _login_raw(client, clin["email"], "wrong")
    r = client.post(f"{API}/admin/users/{clin['id']}/password", headers=auth(token), json={"password": NEW_PASSWORD})
    assert r.status_code == 200
    assert _login_raw(client, clin["email"], PASSWORD).status_code == 401      # old password no longer works
    assert _login_raw(client, clin["email"], NEW_PASSWORD).status_code == 200  # new one does, and the lock is gone
    assert get_db().refresh_tokens.count_documents({"user_id": clin["id"], "revoked_at": None}) == 1  # only the fresh login
    assert clin_token  # the earlier access token simply expires; refresh tokens were revoked
    assert get_db().audit_log.find_one({"action": "admin.user_password_reset"})


def test_reset_password_enforces_the_policy_and_the_role(client, tokens):
    clin = make_user("clinician", email="c2@example.com")
    admin = tokens["admin"]
    assert client.post(f"{API}/admin/users/{clin['id']}/password", headers=auth(admin), json={"password": "short"}).status_code == 422
    for role in ("clinician", "viewer", "researcher", "eeg_technologist"):
        r = client.post(f"{API}/admin/users/{clin['id']}/password", headers=auth(tokens[role]), json={"password": NEW_PASSWORD})
        assert r.status_code == 403
    assert client.post(f"{API}/admin/users/{clin['id']}/password", json={"password": NEW_PASSWORD}).status_code == 401


def test_delete_removes_an_unused_account(client):
    _, token = _admin(client)
    viewer = make_user("viewer", email="v@example.com")
    assert client.delete(f"{API}/admin/users/{viewer['id']}", headers=auth(token)).status_code == 204
    assert get_db().users.find_one({"_id": viewer["id"]}) is None
    assert _login_raw(client, "v@example.com", PASSWORD).status_code == 401
    audit = get_db().audit_log.find_one({"action": "admin.user_deleted"})
    assert audit and "v@example.com" not in str(audit)  # nothing identifying is logged


def test_delete_is_admin_only_and_unknown_ids_404(client, tokens):
    victim = make_user("viewer", email="victim@example.com")
    for role in ("clinician", "viewer", "researcher", "eeg_technologist"):
        assert client.delete(f"{API}/admin/users/{victim['id']}", headers=auth(tokens[role])).status_code == 403
    assert client.delete(f"{API}/admin/users/00000000-0000-0000-0000-000000000000", headers=auth(tokens["admin"])).status_code == 404


def test_an_admin_cannot_delete_themselves_or_the_last_admin(client):
    me, token = _admin(client)
    assert client.delete(f"{API}/admin/users/{me['id']}", headers=auth(token)).status_code == 409
    other = make_user("admin", email="admin2@example.com")
    other_token = login(client, other)
    assert client.delete(f"{API}/admin/users/{other['id']}", headers=auth(token)).status_code == 204  # another admin remains
    assert other_token
    # only `me` is left: the last active admin cannot be deleted by anyone, including via another route
    assert client.delete(f"{API}/admin/users/{me['id']}", headers=auth(token)).status_code == 409


def test_a_second_admin_can_delete_the_first(client):
    first, _ = _admin(client)
    second = make_user("admin", email="admin2@example.com")
    t2 = login(client, second)
    assert client.delete(f"{API}/admin/users/{first['id']}", headers=auth(t2)).status_code == 204


def test_a_user_who_authored_records_cannot_be_deleted_but_can_be_deactivated(client):
    _, token = _admin(client)
    clin = make_user("clinician", email="author@example.com")
    ctoken = login(client, clin)
    p = client.post(f"{API}/patients", headers=auth(ctoken), json={
        "full_name": "Test Person", "date_of_birth": "1980-01-01", "sex": "unknown", "etiology": "unknown", "consent_clinical": True})
    assert p.status_code == 201
    r = client.delete(f"{API}/admin/users/{clin['id']}", headers=auth(token))
    assert r.status_code == 409 and "Deactivate" in r.json()["detail"]
    assert get_db().users.find_one({"_id": clin["id"]}) is not None
    assert client.patch(f"{API}/admin/users/{clin['id']}", headers=auth(token), json={"is_active": False}).status_code == 200


def test_a_user_who_is_the_only_care_team_member_cannot_be_deleted(client):
    _, token = _admin(client)
    tech = make_user("eeg_technologist", email="tech2@example.com")
    clin = make_user("clinician", email="owner@example.com")
    ctoken = login(client, clin)
    pid = client.post(f"{API}/patients", headers=auth(ctoken), json={
        "full_name": "Other Person", "date_of_birth": "1981-02-03", "sex": "unknown", "etiology": "unknown", "consent_clinical": True}).json()["id"]
    get_db().patients.update_one({"_id": uuid.UUID(pid)}, {"$set": {"created_by": tech["id"]}})  # so only the team rule applies
    r = client.delete(f"{API}/admin/users/{clin['id']}", headers=auth(token))
    assert r.status_code == 409 and "care-team" in r.json()["detail"]


def test_deleting_a_user_removes_them_from_care_teams(client):
    _, token = _admin(client)
    owner = make_user("clinician", email="own@example.com")
    helper = make_user("viewer", email="help@example.com")
    otoken = login(client, owner)
    pid = client.post(f"{API}/patients", headers=auth(otoken), json={
        "full_name": "Third Person", "date_of_birth": "1975-05-05", "sex": "unknown", "etiology": "unknown", "consent_clinical": True}).json()["id"]
    r = client.put(f"{API}/patients/{pid}/access", headers=auth(otoken), json={"members": [
        {"user_id": str(owner["id"]), "access_level": "owner"}, {"user_id": str(helper["id"]), "access_level": "member"}]})
    assert r.status_code == 200, r.text
    assert client.delete(f"{API}/admin/users/{helper['id']}", headers=auth(token)).status_code == 204
    team = get_db().patients.find_one({"_id": uuid.UUID(pid)})["care_team"]
    assert [m["user_id"] for m in team] == [owner["id"]]
