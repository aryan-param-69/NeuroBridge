import json
import re
import time
import uuid

import pytest

from app.db.mongo import get_db
from app.models import User
from app.schemas.patients import PatientCreate
from app.services import patients as svc
from tests.conftest import auth, login, make_user

API = "/api/v1"


def payload(**over) -> dict:
    base = {
        "full_name": "María José Ramírez", "date_of_birth": "1972-04-10", "sex": "female",
        "mrn": "MRN-00123", "guardian_contact": "Spouse, +1 555 0100", "etiology": "traumatic",
        "date_of_injury": "2026-02-02", "admission_date": "2026-02-02", "craniectomy": False,
        "consent_clinical": True, "consent_research": False, "tags": ["icu"],
    }
    return {**base, **over}


@pytest.fixture
def team(client):
    """Logged-in users: clinician A (creates patients), clinician B, technologist, viewer, researcher, admin."""
    users = {
        "clin_a": make_user("clinician", email="a@example.com"),
        "clin_b": make_user("clinician", email="b@example.com"),
        "tech": make_user("eeg_technologist"),
        "viewer": make_user("viewer"),
        "researcher": make_user("researcher"),
        "admin": make_user("admin"),
    }
    return {k: {**u, "token": login(client, u)} for k, u in users.items()}


def H(team, who):
    return auth(team[who]["token"])


def register(client, team, who="clin_a", **over):
    r = client.post(f"{API}/patients", json=payload(**over), headers=H(team, who))
    assert r.status_code == 201, r.text
    return r.json()


def share(client, team, pid, *whos, owner="clin_a"):
    members = [{"user_id": str(team[owner]["id"]), "access_level": "owner"}]
    members += [{"user_id": str(team[w]["id"]), "access_level": "member"} for w in whos]
    r = client.put(f"{API}/patients/{pid}/access", json={"members": members}, headers=H(team, owner))
    assert r.status_code == 200, r.text


# ---- Registration -----------------------------------------------------------

def test_register_generates_sequential_display_ids(client, team):
    a = register(client, team, full_name="Anna Alpha", mrn=None)
    b = register(client, team, full_name="Boris Beta", mrn=None)
    year = re.match(r"NB-(\d{4})-(\d{6})", a["display_id"])
    assert year and b["display_id"].endswith(f"{int(a['display_id'][-6:]) + 1:06d}")


def test_registration_validation(client, team):
    for bad in ({"date_of_birth": "2999-01-01"}, {"date_of_injury": "1960-01-01"},
                {"etiology": "other"}, {"full_name": "x"}, {"sex": "robot"}):
        r = client.post(f"{API}/patients", json=payload(**bad), headers=H(team, "clin_a"))
        assert r.status_code == 422, bad


def test_duplicate_name_dob_warns_then_confirms(client, team):
    register(client, team)
    r = client.post(f"{API}/patients", json=payload(mrn="OTHER"), headers=H(team, "clin_a"))
    assert r.status_code == 409
    d = r.json()["detail"]["duplicates"]
    assert d[0]["match"] == "name_dob" and d[0]["id"]  # creator has access, so the id is returned
    # Accent / case / spacing differences must not hide a duplicate.
    r = client.post(f"{API}/patients", headers=H(team, "clin_a"),
                    json=payload(full_name="  maria   JOSE ramirez ", mrn=None))
    assert r.status_code == 409
    r = client.post(f"{API}/patients", headers=H(team, "clin_a"), json=payload(mrn="OTHER", confirm_duplicate=True))
    assert r.status_code == 201


def test_duplicate_mrn_warns_and_hides_ids_the_user_cannot_access(client, team):
    register(client, team)
    r = client.post(f"{API}/patients", headers=H(team, "clin_b"),
                    json=payload(full_name="Someone Else", date_of_birth="1990-01-01", mrn="mrn 00123"))
    assert r.status_code == 409
    d = r.json()["detail"]["duplicates"][0]
    assert d["match"] == "mrn" and d["id"] is None and d["display_id"].startswith("NB-")


# ---- PHI protection ---------------------------------------------------------

def test_identifiers_are_encrypted_at_rest(client, team):
    p = register(client, team)
    db = get_db()
    # Scan EVERY collection: the clear-text identifiers must appear nowhere in the database.
    dump = " ".join(repr(list(db[c].find())) for c in db.list_collection_names()).lower()
    for secret in ("ramírez", "ramirez", "maría", "maria", "1972-04-10", "mrn-00123", "555 0100"):
        assert secret not in dump, secret
    assert db.patient_identifiers.count_documents({}) == 1
    assert p["full_name"] == "María José Ramírez"  # but the API decrypts for permitted users


def test_audit_log_and_history_never_contain_phi(client, team):
    p = register(client, team)
    client.patch(f"{API}/patients/{p['id']}", json={"full_name": "Maria Jose Ramirez-Soto", "mrn": "NEW-9"},
                 headers=H(team, "clin_a"))
    client.get(f"{API}/patients?q=ramirez", headers=H(team, "clin_a"))
    db = get_db()
    blob = json.dumps([r.get("details") for r in db.audit_log.find()])
    blob += json.dumps([[r["old_value"], r["new_value"]] for r in db.patient_field_history.find()])
    for secret in ("ramirez", "maria", "mrn-00123", "new-9", "1972"):
        assert secret not in blob.lower()


# ---- Access control ---------------------------------------------------------

def test_patients_are_only_visible_to_the_care_team(client, team):
    p = register(client, team)
    assert client.get(f"{API}/patients/{p['id']}", headers=H(team, "clin_b")).status_code == 404
    assert client.get(f"{API}/patients", headers=H(team, "clin_b")).json()["total"] == 0
    share(client, team, p["id"], "clin_b", "tech")
    assert client.get(f"{API}/patients/{p['id']}", headers=H(team, "clin_b")).status_code == 200
    assert client.get(f"{API}/patients", headers=H(team, "tech")).json()["total"] == 1
    assert client.get(f"{API}/patients/{p['id']}", headers=H(team, "viewer")).status_code == 404


def test_only_clinicians_write_and_viewers_do_not_see_guardian_contact(client, team):
    p = register(client, team)
    share(client, team, p["id"], "tech", "viewer")
    assert client.post(f"{API}/patients", json=payload(full_name="Zed Zulu"), headers=H(team, "tech")).status_code == 403
    r = client.patch(f"{API}/patients/{p['id']}", json={"sex": "male"}, headers=H(team, "viewer"))
    assert r.status_code == 403
    v = client.get(f"{API}/patients/{p['id']}", headers=H(team, "viewer")).json()
    assert v["full_name"] and "guardian_contact" not in v
    assert client.get(f"{API}/patients/{p['id']}", headers=H(team, "clin_a")).json()["guardian_contact"]


def test_admin_has_no_clinical_access_but_can_manage_care_team(client, team):
    p = register(client, team)
    assert client.get(f"{API}/patients", headers=H(team, "admin")).status_code == 403
    assert client.get(f"{API}/patients/{p['id']}", headers=H(team, "admin")).status_code == 403
    r = client.get(f"{API}/patients/{p['id']}/access", headers=H(team, "admin"))
    assert r.status_code == 200 and "full_name" in r.json()[0]
    assert client.get(f"{API}/users/directory", headers=H(team, "admin")).status_code == 200


def test_care_team_rules(client, team):
    p = register(client, team)
    bad_roles = {"members": [{"user_id": str(team["clin_a"]["id"]), "access_level": "owner"},
                             {"user_id": str(team["researcher"]["id"]), "access_level": "member"}]}
    assert client.put(f"{API}/patients/{p['id']}/access", json=bad_roles,
                      headers=H(team, "clin_a")).status_code == 422
    no_owner = {"members": [{"user_id": str(team["clin_a"]["id"]), "access_level": "member"}]}
    assert client.put(f"{API}/patients/{p['id']}/access", json=no_owner,
                      headers=H(team, "clin_a")).status_code == 422
    # A clinician outside the team cannot manage it.
    assert client.get(f"{API}/patients/{p['id']}/access", headers=H(team, "clin_b")).status_code == 404


# ---- Researcher view --------------------------------------------------------

IDENTIFIER_KEYS = {"full_name", "date_of_birth", "mrn", "guardian_contact", "age", "date_of_injury",
                   "admission_date", "clinical_status_label", "etiology_other", "tags"}


def test_researcher_sees_only_consented_patients_with_no_identifiers(client, team):
    consented = register(client, team, full_name="Cora Consent", mrn=None, consent_research=True)
    register(client, team, full_name="Nora Nope", mrn=None, consent_research=False)
    lst = client.get(f"{API}/patients", headers=H(team, "researcher")).json()
    assert lst["total"] == 1
    item = lst["items"][0]
    assert not IDENTIFIER_KEYS & set(item) and item["birth_year"] == 1972
    one = client.get(f"{API}/patients/{consented['id']}", headers=H(team, "researcher")).json()
    assert not IDENTIFIER_KEYS & set(one)
    assert "Cora" not in json.dumps(lst) and "Consent" not in json.dumps(one)
    # Researchers cannot search by name or touch clinical sub-resources.
    assert client.get(f"{API}/patients?q=cora", headers=H(team, "researcher")).json()["total"] == 1
    for path in ("medical-history", "events", "history", "access"):
        assert client.get(f"{API}/patients/{consented['id']}/{path}",
                          headers=H(team, "researcher")).status_code == 403
    assert client.patch(f"{API}/patients/{consented['id']}", json={"sex": "male"},
                        headers=H(team, "researcher")).status_code == 403


def test_revoking_research_consent_removes_researcher_access_immediately(client, team):
    p = register(client, team, consent_research=True)
    assert client.get(f"{API}/patients/{p['id']}", headers=H(team, "researcher")).status_code == 200
    client.patch(f"{API}/patients/{p['id']}", json={"consent_research": False}, headers=H(team, "clin_a"))
    assert client.get(f"{API}/patients/{p['id']}", headers=H(team, "researcher")).status_code == 404


# ---- Editing and history ----------------------------------------------------

def test_edit_records_field_history_without_phi(client, team):
    p = register(client, team)
    r = client.patch(f"{API}/patients/{p['id']}", headers=H(team, "clin_a"),
                     json={"etiology": "anoxic", "full_name": "Maria Ramirez", "craniectomy": True})
    assert r.status_code == 200 and r.json()["etiology"] == "anoxic" and r.json()["full_name"] == "Maria Ramirez"
    hist = client.get(f"{API}/patients/{p['id']}/history", headers=H(team, "clin_a")).json()
    by_field = {h["field"]: h for h in hist}
    assert by_field["etiology"]["old_value"] == "traumatic" and by_field["etiology"]["new_value"] == "anoxic"
    assert by_field["full_name"]["old_value"] == {"changed": True}
    assert all(h["changed_by"] == str(team["clin_a"]["id"]) for h in hist)
    # A no-op edit writes nothing.
    n = len(hist)
    client.patch(f"{API}/patients/{p['id']}", json={"etiology": "anoxic"}, headers=H(team, "clin_a"))
    assert len(client.get(f"{API}/patients/{p['id']}/history", headers=H(team, "clin_a")).json()) == n


def test_renaming_updates_search_index(client, team):
    p = register(client, team)
    client.patch(f"{API}/patients/{p['id']}", json={"full_name": "Zenobia Quartz"}, headers=H(team, "clin_a"))
    assert client.get(f"{API}/patients?q=zenob", headers=H(team, "clin_a")).json()["total"] == 1
    assert client.get(f"{API}/patients?q=ramirez", headers=H(team, "clin_a")).json()["total"] == 0


def test_status_lifecycle(client, team):
    p = register(client, team)
    url, h = f"{API}/patients/{p['id']}", H(team, "clin_a")
    assert client.patch(url, json={"status": "archived"}, headers=h).status_code == 422  # active -> archived
    assert client.patch(url, json={"status": "discharged"}, headers=h).status_code == 200
    assert client.patch(url, json={"status": "archived"}, headers=h).status_code == 200
    assert client.patch(url, json={"status": "active"}, headers=h).status_code == 422  # archived is terminal


def test_consent_date_and_clearing_required_fields(client, team):
    p = register(client, team)
    r = client.patch(f"{API}/patients/{p['id']}", json={"consent_research": True}, headers=H(team, "clin_a"))
    assert r.json()["consent_research_date"] is not None
    assert client.patch(f"{API}/patients/{p['id']}", json={"sex": None}, headers=H(team, "clin_a")).status_code == 422


# ---- Search -----------------------------------------------------------------

def test_search_by_name_prefix_accents_mrn_and_id(client, team):
    p = register(client, team)
    register(client, team, full_name="John Smith", date_of_birth="1980-01-01", mrn="ZZ-9", etiology="anoxic",
             date_of_injury=None, admission_date=None)
    h = H(team, "clin_a")

    def total(**params):
        return client.get(f"{API}/patients", params=params, headers=h).json()["total"]

    assert total(q="ram") == 1 and total(q="RAMIREZ") == 1 and total(q="maria ramirez") == 1
    assert total(q="ramírez josé") == 1
    assert total(q="ramirez smith") == 0  # all tokens must match
    assert total(q="mrn00123") == 1 and total(q="MRN-00123") == 1
    assert total(q=p["display_id"]) == 1 and total(q="NB-") == 2
    assert total(q="zz") == 0  # < 3 characters is not a name search
    assert total(etiology="anoxic") == 1 and total(status="discharged") == 0
    assert total(injury_from="2026-01-01", injury_to="2026-12-31") == 1  # Smith has no injury date


def test_pagination(client, team):
    for i in range(5):
        register(client, team, full_name=f"Patient Number{i}", mrn=None, date_of_birth=f"19{60 + i}-01-01")
    r = client.get(f"{API}/patients?page=2&page_size=2", headers=H(team, "clin_a")).json()
    assert r["total"] == 5 and len(r["items"]) == 2 and r["page"] == 2


def test_search_text_is_not_written_to_audit_log(client, team):
    register(client, team)
    client.get(f"{API}/patients?q=ramirez", headers=H(team, "clin_a"))
    row = get_db().audit_log.find_one({"action": "patient.search"}, sort=[("seq", -1)])
    assert row["details"] == {"filters": ["q"], "results": 1}


# ---- Medical history & events ----------------------------------------------

def test_medical_history_crud_and_soft_delete(client, team):
    p = register(client, team)
    url, h = f"{API}/patients/{p['id']}/medical-history", H(team, "clin_a")
    r = client.post(url, headers=h, json={"category": "medication", "description": "Propofol infusion",
                                          "start_date": "2026-02-03"})
    assert r.status_code == 201
    item = r.json()
    bad = client.post(url, headers=h, json={"category": "medication", "description": "x",
                                            "start_date": "2026-02-03", "end_date": "2026-02-01"})
    assert bad.status_code == 422
    client.patch(f"{url}/{item['id']}", json={"is_active": False, "end_date": "2026-02-10"}, headers=h)
    assert client.get(url, headers=h).json()[0]["is_active"] is False
    assert client.delete(f"{url}/{item['id']}", headers=h).status_code == 204
    assert client.get(url, headers=h).json() == []
    assert get_db().medical_history_items.count_documents({}) == 1  # retained, just hidden


def test_events_and_cross_patient_item_ids(client, team):
    p1, p2 = register(client, team), register(client, team, full_name="Other Person", mrn=None)
    h = H(team, "clin_a")
    ev = client.post(f"{API}/patients/{p1['id']}/events", headers=h, json={
        "event_type": "medication_change", "occurred_at": "2026-03-01T08:30:00Z",
        "description": "Sedation stopped"})
    assert ev.status_code == 201
    assert len(client.get(f"{API}/patients/{p1['id']}/events", headers=h).json()) == 1
    assert client.get(f"{API}/patients/{p2['id']}/events", headers=h).json() == []
    item = client.post(f"{API}/patients/{p1['id']}/medical-history", headers=h,
                       json={"category": "diagnosis", "description": "TBI"}).json()
    # An item of patient 1 cannot be modified through patient 2's URL.
    assert client.delete(f"{API}/patients/{p2['id']}/medical-history/{item['id']}", headers=h).status_code == 404


def test_unknown_patient_and_bad_uuid(client, team):
    assert client.get(f"{API}/patients/{uuid.uuid4()}", headers=H(team, "clin_a")).status_code == 404
    assert client.get(f"{API}/patients/not-a-uuid", headers=H(team, "clin_a")).status_code == 422


def test_every_patient_endpoint_requires_login(client):
    for method, path in (("GET", "/patients"), ("POST", "/patients"), ("GET", f"/patients/{uuid.uuid4()}"),
                         ("GET", "/users/directory")):
        assert client.request(method, API + path).status_code == 401


def test_patient_actions_are_audited(client, team):
    p = register(client, team)
    client.get(f"{API}/patients/{p['id']}", headers=H(team, "clin_a"))
    client.patch(f"{API}/patients/{p['id']}", json={"sex": "male"}, headers=H(team, "clin_a"))
    db = get_db()
    actions = {r["action"] for r in db.audit_log.find({"patient_id": uuid.UUID(p["id"])})}
    assert {"patient.create", "patient.view", "patient.update"} <= actions
    assert db.audit_log.find_one({"action": "patient.update"})["details"] == {"fields": ["sex"]}


# ---- Phase 1 completion criterion: 1000 patients, search < 200 ms ----------

def test_search_over_1000_patients_is_fast(client, team):
    first = ["Anna", "Boris", "Chen", "Dalia", "Emre", "Fatima", "Goran", "Hana", "Ivan", "Jun"]
    last = ["Alvarez", "Bianchi", "Chowdhury", "Dubois", "Eriksen", "Fischer", "Gonzalez", "Haddad",
            "Ito", "Jovanovic"]
    db = get_db()
    user = User.from_doc(db.users.find_one({"email": "a@example.com"}))
    for i in range(1000):  # synthetic test identities, never used as research evidence
        data = PatientCreate(
            full_name=f"{first[i % 10]} {last[(i // 10) % 10]}{i}", date_of_birth="1970-01-01",
            sex="unknown", etiology="unknown", mrn=f"T{i:05d}")
        svc.create_patient(db, user, data)
    h = H(team, "clin_a")
    for params in ({"q": "alvar"}, {"q": "chen bianchi"}, {"q": "T00500"}, {}, {"q": "ito", "status": "active"}):
        client.get(f"{API}/patients", params=params, headers=h)  # warm-up
        t0 = time.perf_counter()
        r = client.get(f"{API}/patients", params=params, headers=h)
        ms = (time.perf_counter() - t0) * 1000
        assert r.status_code == 200
        assert ms < 200, f"{params} took {ms:.0f} ms"
    assert client.get(f"{API}/patients", params={"q": "T00500"}, headers=h).json()["total"] == 1
    assert client.get(f"{API}/patients", params={"q": "alvarez"}, headers=h).json()["total"] == 100


def test_search_text_is_treated_as_text_not_as_a_query(client, team):
    register(client, team)
    h = H(team, "clin_a")
    for q in ("NB-.*", ".*", '{"$ne": null}', "$where: 1", "ram|smith"):
        r = client.get(f"{API}/patients", params={"q": q}, headers=h)
        assert r.status_code == 200 and r.json()["total"] == 0, q
    # Operator syntax in the query string is ignored, never applied as a filter: if `$ne` had been
    # injected, the (active) patient would be excluded.
    r = client.get(f"{API}/patients?status[$ne]=active", headers=h)
    assert r.status_code == 200 and r.json()["total"] == 1
    assert client.get(f"{API}/patients?status=active", headers=h).json()["total"] == 1


def test_concurrent_registrations_get_unique_sequential_ids(client, team):
    from concurrent.futures import ThreadPoolExecutor

    db = get_db()
    user = User.from_doc(db.users.find_one({"email": "a@example.com"}))

    def make(i):
        data = PatientCreate(full_name=f"Parallel Person{i}", date_of_birth=f"19{50 + i}-01-01",
                             sex="unknown", etiology="unknown")
        return svc.create_patient(db, user, data)[0]["display_id"]

    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(make, range(30)))
    assert len(set(ids)) == 30
    assert sorted(int(i[-6:]) for i in ids) == list(range(1, 31))  # no gaps, no duplicates
