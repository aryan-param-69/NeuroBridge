"""Phase 5: versioned clinician notes and the five-lane patient timeline."""

import json
import uuid
from datetime import UTC, datetime

import pytest

from app.db.mongo import get_db
from tests.test_assessments import signed  # noqa: F401
from tests.test_patients import H, register, share, team  # noqa: F401
from tests.test_recordings import isolated_storage, make_edf, patient, rec, upload  # noqa: F401

API = "/api/v1"


@pytest.fixture
def pt(client, team):  # noqa: F811
    p = register(client, team, full_name="Jane Doe", mrn=None)
    share(client, team, p["id"], "clin_b", "tech", "viewer")
    return p


def note(client, team, pid, who="clin_a", body="Eyes open spontaneously", kind="observation", **kw):  # noqa: F811
    return client.post(f"{API}/patients/{pid}/notes", json={"note_type": kind, "body": body, **kw}, headers=H(team, who))


def test_note_edit_adds_a_version_and_never_overwrites(client, team, pt):  # noqa: F811
    n = note(client, team, pt["id"]).json()
    assert n["version"] == 1 and n["n_versions"] == 1
    edit = client.post(f"{API}/notes/{n['note_id']}/versions", json={"body": "Eyes open spontaneously; no tracking"}, headers=H(team, "clin_a"))
    assert edit.status_code == 201 and edit.json()["version"] == 2
    vs = client.get(f"{API}/notes/{n['note_id']}/versions", headers=H(team, "viewer")).json()
    assert [(v["version"], v["body"]) for v in vs] == [(1, "Eyes open spontaneously"), (2, "Eyes open spontaneously; no tracking")]
    assert get_db().clinician_notes.count_documents({"note_id": uuid.UUID(n["note_id"])}) == 2
    lst = client.get(f"{API}/patients/{pt['id']}/notes", headers=H(team, "viewer")).json()
    assert len(lst) == 1 and lst[0]["version"] == 2 and lst[0]["n_versions"] == 2
    assert lst[0]["first_written_at"] == n["first_written_at"]


def test_note_rules(client, team, pt):  # noqa: F811
    n = note(client, team, pt["id"]).json()
    url = f"{API}/notes/{n['note_id']}/versions"
    assert client.post(url, json={"body": "other"}, headers=H(team, "clin_b")).status_code == 403  # only the author edits
    assert client.post(url, json={"body": "other"}, headers=H(team, "tech")).status_code == 403
    assert client.post(url, json={"body": "Eyes open spontaneously"}, headers=H(team, "clin_a")).status_code == 422  # no change
    assert client.post(url, json={"body": ""}, headers=H(team, "clin_a")).status_code == 422
    assert client.post(url, json={"body": "x", "author_id": str(uuid.uuid4())}, headers=H(team, "clin_a")).status_code == 422
    assert note(client, team, pt["id"], who="tech").status_code == 403
    assert note(client, team, pt["id"], who="researcher").status_code == 403
    assert note(client, team, pt["id"], kind="diagnosis").status_code == 422
    assert note(client, team, pt["id"], body="x" * 5001).status_code == 422
    assert client.post(f"{API}/notes/{uuid.uuid4()}/versions", json={"body": "x"}, headers=H(team, "clin_a")).status_code == 404
    assert client.get(f"{API}/patients/{pt['id']}/notes", headers=H(team, "researcher")).status_code == 403


def test_notes_are_care_team_scoped_and_filterable(client, team, pt):  # noqa: F811
    other = register(client, team, full_name="Other Person", mrn=None)
    n = note(client, team, other["id"]).json()
    assert client.get(f"{API}/notes/{n['note_id']}/versions", headers=H(team, "clin_b")).status_code == 404
    assert client.get(f"{API}/patients/{other['id']}/notes", headers=H(team, "clin_b")).status_code == 404
    note(client, team, pt["id"], kind="plan", body="Repeat CRS-R tomorrow")
    note(client, team, pt["id"], kind="observation")
    got = client.get(f"{API}/patients/{pt['id']}/notes", params={"note_type": "plan"}, headers=H(team, "viewer")).json()
    assert [x["note_type"] for x in got] == ["plan"]


def test_note_links_must_belong_to_the_same_patient(client, team, pt):  # noqa: F811
    assert note(client, team, pt["id"], recording_id=str(uuid.uuid4())).status_code == 422
    assert note(client, team, pt["id"], run_id=str(uuid.uuid4())).status_code == 422
    other = register(client, team, full_name="Other Person", mrn=None)
    r = upload(client, team, "clin_a", other["id"], make_edf(), confirm=True).json()
    assert note(client, team, pt["id"], recording_id=r["id"]).status_code == 422  # someone else's recording
    mine = upload(client, team, "clin_a", pt["id"], make_edf(), confirm=True).json()
    ok = note(client, team, pt["id"], recording_id=mine["id"])
    assert ok.status_code == 201 and ok.json()["recording_id"] == mine["id"]
    got = client.get(f"{API}/patients/{pt['id']}/notes", params={"recording_id": mine["id"]}, headers=H(team, "viewer")).json()
    assert len(got) == 1


def test_notes_audited_without_text(client, team, pt):  # noqa: F811
    n = note(client, team, pt["id"], body="Distinctive confidential sentence").json()
    client.post(f"{API}/notes/{n['note_id']}/versions", json={"body": "Another confidential edit"}, headers=H(team, "clin_a"))
    rows = list(get_db().audit_log.find({"patient_id": uuid.UUID(pt["id"])}))
    assert {"note.create", "note.edit"} <= {r["action"] for r in rows}
    assert "confidential" not in json.dumps([r["details"] for r in rows])


# ---- timeline ------------------------------------------------------------------------------------------------------------------

def seed_context(pid):
    db = get_db()
    p = uuid.UUID(pid)
    db.clinical_events.insert_one({"_id": uuid.uuid4(), "patient_id": p, "event_type": "seizure", "description": "Focal seizure, 2 min",
                                   "occurred_at": datetime(2026, 3, 2, 8, 0, tzinfo=UTC), "deleted_at": None})
    db.clinical_events.insert_one({"_id": uuid.uuid4(), "patient_id": p, "event_type": "seizure", "description": "deleted",
                                   "occurred_at": datetime(2026, 3, 2, 9, 0, tzinfo=UTC), "deleted_at": datetime(2026, 3, 3, tzinfo=UTC)})
    db.medical_history_items.insert_one({"_id": uuid.uuid4(), "patient_id": p, "category": "medication", "description": "Levetiracetam",
                                         "start_date": "2026-02-20", "end_date": "2026-03-10", "is_active": False, "deleted_at": None})
    db.medical_history_items.insert_one({"_id": uuid.uuid4(), "patient_id": p, "category": "diagnosis", "description": "Not a medication",
                                         "start_date": "2026-02-20", "is_active": True, "deleted_at": None})


def test_timeline_has_all_five_lanes_in_time_order(client, team, pt):  # noqa: F811
    upload(client, team, "clin_a", pt["id"], make_edf())  # 2026-03-01 09:30
    signed(client, team, pt["id"], when="2026-03-01T12:00:00Z")
    note(client, team, pt["id"])
    seed_context(pt["id"])
    t = client.get(f"{API}/patients/{pt['id']}/timeline", headers=H(team, "viewer")).json()
    c = t["counts"]
    assert c["recording"] == 1 and c["assessment"] == 1 and c["medication"] == 1 and c["note"] == 1
    assert c["event"] == 3  # the seizure plus injury and admission milestones; the soft-deleted event is hidden
    titles = [i["title"] for i in t["items"] if i["lane"] == "event"]
    assert "seizure" in titles and "injury" in titles and "deleted" not in json.dumps(t)
    assert "Not a medication" not in json.dumps(t)
    starts = [i["start"] for i in t["items"]]
    assert starts == sorted(starts)
    rec_item = next(i for i in t["items"] if i["lane"] == "recording")
    assert rec_item["end"] > rec_item["start"] and rec_item["link"].startswith("/recordings/")
    med = next(i for i in t["items"] if i["lane"] == "medication")
    assert med["title"] == "Levetiracetam" and med["end"] is not None
    assert next(i for i in t["items"] if i["lane"] == "assessment")["title"].startswith("CRS-R ")


def test_timeline_filters(client, team, pt):  # noqa: F811
    signed(client, team, pt["id"], when="2026-03-01T12:00:00Z")
    note(client, team, pt["id"])
    seed_context(pt["id"])
    h = H(team, "viewer")
    only = client.get(f"{API}/patients/{pt['id']}/timeline", params={"lanes": "assessment,note"}, headers=h).json()
    assert {i["lane"] for i in only["items"]} == {"assessment", "note"} and only["lanes"] == ["assessment", "note"]
    assert client.get(f"{API}/patients/{pt['id']}/timeline", params={"lanes": "assessment,bogus"}, headers=h).status_code == 422
    win = client.get(f"{API}/patients/{pt['id']}/timeline", params={"lanes": "event,medication", "from": "2026-03-01T00:00:00Z",
                                                                    "to": "2026-03-05T00:00:00Z"}, headers=h).json()
    names = {i["title"] for i in win["items"]}
    assert "seizure" in names and "Levetiracetam" in names  # the medication span overlaps the window
    assert "injury" not in names and "admission" not in names  # milestones before it are left out
    late = client.get(f"{API}/patients/{pt['id']}/timeline", params={"lanes": "medication", "from": "2026-03-11T00:00:00Z"}, headers=h).json()
    assert late["items"] == []  # the span ended on 10 March


def test_timeline_hides_drafts_from_non_clinicians_and_respects_access(client, team, pt):  # noqa: F811
    from tests.test_assessments import create

    create(client, team, pt["id"])  # draft
    for who, n in (("clin_a", 1), ("clin_b", 1), ("tech", 0), ("viewer", 0)):
        t = client.get(f"{API}/patients/{pt['id']}/timeline", params={"lanes": "assessment"}, headers=H(team, who)).json()
        assert t["counts"]["assessment"] == n, who
    assert client.get(f"{API}/patients/{pt['id']}/timeline", headers=H(team, "researcher")).status_code == 403
    outsider = register(client, team, full_name="Other Person", mrn=None)
    assert client.get(f"{API}/patients/{outsider['id']}/timeline", headers=H(team, "clin_b")).status_code == 404
    assert client.get(f"{API}/patients/{pt['id']}/timeline").status_code == 401


def test_timeline_edited_notes_appear_once_at_their_first_time(client, team, pt):  # noqa: F811
    n = note(client, team, pt["id"]).json()
    client.post(f"{API}/notes/{n['note_id']}/versions", json={"body": "Revised"}, headers=H(team, "clin_a"))
    t = client.get(f"{API}/patients/{pt['id']}/timeline", params={"lanes": "note"}, headers=H(team, "viewer")).json()
    assert len(t["items"]) == 1 and t["items"][0]["subtitle"] == "Revised" and t["items"][0]["badges"] == ["v2"]
    assert t["items"][0]["start"] == n["first_written_at"]


def test_recording_dated_before_the_injury_is_flagged(client, team, pt):  # noqa: F811
    upload(client, team, "clin_a", pt["id"], make_edf())  # the EDF header says 2026-03-01; the injury is 2026-02-02: plausible
    t = client.get(f"{API}/patients/{pt['id']}/timeline", params={"lanes": "recording"}, headers=H(team, "viewer")).json()
    assert "dated before injury" not in t["items"][0]["badges"]
    get_db().patients.update_one({"_id": uuid.UUID(pt["id"])}, {"$set": {"date_of_injury": "2026-04-01"}})
    t = client.get(f"{API}/patients/{pt['id']}/timeline", params={"lanes": "recording"}, headers=H(team, "viewer")).json()
    assert "dated before injury" in t["items"][0]["badges"]  # header dates can be wrong; the doctor is told, not corrected
