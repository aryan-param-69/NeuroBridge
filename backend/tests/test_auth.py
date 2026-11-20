from app.db.mongo import get_db
from tests.conftest import PASSWORD, auth, login, make_user

API = "/api/v1"


def test_login_with_password_and_me(client):
    u = make_user("clinician")
    token = login(client, u)
    r = client.get(f"{API}/auth/me", headers=auth(token))
    assert r.status_code == 200
    assert r.json()["roles"] == ["clinician"]


def test_wrong_password_and_unknown_user_give_same_error(client):
    make_user("clinician")
    a = client.post(f"{API}/auth/login", json={"email": "clinician@example.com", "password": "x"})
    b = client.post(f"{API}/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_lockout_after_repeated_failures(client):
    make_user("clinician")
    for _ in range(5):
        client.post(f"{API}/auth/login", json={"email": "clinician@example.com", "password": "bad"})
    r = client.post(f"{API}/auth/login",
                    json={"email": "clinician@example.com", "password": PASSWORD})
    assert r.status_code == 401  # locked even with the right password


def test_login_returns_a_session_directly_and_there_is_no_second_factor(client):
    u = make_user("clinician")
    r = client.post(f"{API}/auth/login", json={"email": u["email"], "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"access_token", "token_type", "expires_in"} and "nb_refresh" in r.cookies  # password -> session, nothing else
    assert client.get(f"{API}/auth/me", headers=auth(body["access_token"])).status_code == 200
    for gone in ("mfa/setup", "mfa/verify"):
        assert client.post(f"{API}/auth/{gone}", json={}).status_code in (404, 405)
    assert "mfa_enabled" not in client.get(f"{API}/auth/me", headers=auth(body["access_token"])).json()


def test_login_is_audited_and_records_the_time(client):
    u = make_user("clinician")
    client.post(f"{API}/auth/login", json={"email": u["email"], "password": PASSWORD})
    assert get_db().audit_log.count_documents({"action": "auth.login", "user_id": u["id"]}) == 1
    assert get_db().users.find_one({"_id": u["id"]})["last_login_at"] is not None


def test_refresh_rotation_and_reuse_detection(client):
    login(client, make_user("clinician"))
    old_cookie = client.cookies.get("nb_refresh")
    assert client.post(f"{API}/auth/refresh").status_code == 200
    new_cookie = client.cookies.get("nb_refresh")
    assert new_cookie != old_cookie
    # Replaying the already-rotated token must fail and revoke the whole family.
    client.cookies.set("nb_refresh", old_cookie, path="/api/v1/auth")
    assert client.post(f"{API}/auth/refresh").status_code == 401
    client.cookies.set("nb_refresh", new_cookie, path="/api/v1/auth")
    assert client.post(f"{API}/auth/refresh").status_code == 401


def test_logout_revokes_refresh(client):
    login(client, make_user("clinician"))
    cookie = client.cookies.get("nb_refresh")
    assert client.post(f"{API}/auth/logout").status_code == 204
    client.cookies.set("nb_refresh", cookie, path="/api/v1/auth")
    assert client.post(f"{API}/auth/refresh").status_code == 401


def test_deactivated_user_loses_access(client, tokens):
    admin = tokens["admin"]
    users = client.get(f"{API}/admin/users", headers=auth(admin)).json()
    clin = next(x for x in users if x["email"] == "clinician@example.com")
    client.patch(f"{API}/admin/users/{clin['id']}", json={"is_active": False}, headers=auth(admin))
    assert client.get(f"{API}/auth/me", headers=auth(tokens["clinician"])).status_code == 401


def test_password_policy_on_user_creation(client, tokens):
    r = client.post(f"{API}/admin/users", headers=auth(tokens["admin"]),
                    json={"email": "n@example.com", "full_name": "N", "password": "short",
                          "roles": ["viewer"]})
    assert r.status_code == 422


def test_failed_logins_are_audited(client):
    make_user("clinician")
    client.post(f"{API}/auth/login", json={"email": "clinician@example.com", "password": "bad"})
    assert get_db().audit_log.find_one({"action": "auth.login_failed"})


def test_query_operator_injection_is_rejected(client):
    """NoSQL injection: operators sent where a string is expected must never reach the database."""
    make_user("clinician")
    for body in ({"email": {"$ne": None}, "password": "x"},
                 {"email": "clinician@example.com", "password": {"$ne": ""}},
                 {"email": {"$gt": ""}, "password": {"$gt": ""}}):
        assert client.post(f"{API}/auth/login", json=body).status_code == 422
    # Operator-looking text in a valid string field is just text and fails as a normal bad login.
    r = client.post(f"{API}/auth/login", json={"email": "clinician@example.com", "password": '{"$ne": ""}'})
    assert r.status_code == 401
