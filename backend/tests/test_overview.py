"""Sidebar pages: cross-patient recordings list and assessment overview respect the care team."""

import uuid  # noqa: F401

from tests.test_assessments import signed
from tests.test_patients import H, register, share, team  # noqa: F401
from tests.test_recordings import isolated_storage, make_edf, upload  # noqa: F401

API = "/api/v1"


def test_recordings_list_only_shows_the_users_patients(client, team):  # noqa: F811
    a = register(client, team, full_name="Jane Doe", mrn=None)
    share(client, team, a["id"], "tech", "viewer")
    upload(client, team, "clin_a", a["id"], make_edf())
    for who, n in (("clin_a", 1), ("tech", 1), ("viewer", 1), ("clin_b", 0)):
        r = client.get(f"{API}/overview/recordings", headers=H(team, who))
        assert r.status_code == 200 and r.json()["total"] == n, who
    r = client.get(f"{API}/overview/recordings", headers=H(team, "clin_a")).json()
    assert r["items"][0]["display_id"] == a["display_id"] and r["counts"]["pending"] == 1
    assert client.get(f"{API}/overview/recordings", params={"qc_status": "approved"}, headers=H(team, "clin_a")).json()["total"] == 0
    assert client.get(f"{API}/overview/recordings", params={"q": a["display_id"][-6:]}, headers=H(team, "clin_a")).json()["total"] == 1
    assert client.get(f"{API}/overview/recordings", params={"q": "ZZZ"}, headers=H(team, "clin_a")).json()["total"] == 0
    assert client.get(f"{API}/overview/recordings", params={"q": ".*"}, headers=H(team, "clin_a")).json()["total"] == 0  # regex is escaped
    assert client.get(f"{API}/overview/recordings", params={"quality": "bad"}, headers=H(team, "clin_a")).status_code == 422
    assert client.get(f"{API}/overview/recordings", headers=H(team, "researcher")).status_code == 403
    assert client.get(f"{API}/overview/recordings").status_code == 401


def test_assessment_overview_flags_patients_due_for_a_repeat(client, team):  # noqa: F811
    a = register(client, team, full_name="Jane Doe", mrn=None)
    signed(client, team, a["id"])
    r = client.get(f"{API}/overview/assessments", headers=H(team, "clin_a")).json()
    row = next(x for x in r["items"] if x["patient_id"] == a["id"])
    assert row["due"] is True and row["n_signed"] == 1 and row["last"]["total"] == 4 and r["required"] == 5 and r["due_count"] >= 1
    assert client.get(f"{API}/overview/assessments", headers=H(team, "clin_b")).json()["items"] == []  # not on that team
    assert client.get(f"{API}/overview/assessments", headers=H(team, "researcher")).status_code == 403


def test_reports_overview_respects_status_and_team(client, team):  # noqa: F811
    a = register(client, team, full_name="Jane Doe", mrn=None)
    share(client, team, a["id"], "tech", "viewer")
    r = client.post(f"{API}/patients/{a['id']}/reports", json={"clinical_summary": "Text."}, headers=H(team, "clin_a")).json()
    assert [x["status"] for x in client.get(f"{API}/overview/reports", headers=H(team, "clin_a")).json()["items"]] == ["draft"]
    assert client.get(f"{API}/overview/reports", headers=H(team, "tech")).json()["items"] == []  # drafts are for clinicians
    assert client.get(f"{API}/overview/reports", headers=H(team, "clin_b")).json()["items"] == []  # not on that team
    client.post(f"{API}/reports/{r['id']}/sign", json={}, headers=H(team, "clin_a"))
    got = client.get(f"{API}/overview/reports", headers=H(team, "viewer")).json()["items"]
    assert len(got) == 1 and got[0]["status"] == "signed" and got[0]["display_id"] == a["display_id"]
    assert client.get(f"{API}/overview/reports", headers=H(team, "researcher")).status_code == 403
