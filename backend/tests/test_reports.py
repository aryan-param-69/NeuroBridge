"""Phase 7: reports are snapshots + clinician text, signed then immutable, versioned, tamper-evident, escaped on export, access-controlled."""

import json
import uuid

import pytest

from app.core.config import get_settings
from app.db.mongo import get_db
from tests.test_assessments import create, signed
from tests.test_comparisons import make_run
from tests.test_patients import H, register, share, team  # noqa: F401
from tests.test_recordings import isolated_storage  # noqa: F401

API = "/api/v1"


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(get_settings(), "jobs_inline", True)


@pytest.fixture
def pt(client, team):  # noqa: F811
    p = register(client, team, full_name="Jane Doe", mrn="MRN-77", consent_research=True)
    share(client, team, p["id"], "clin_b", "tech", "viewer")
    return p


def mk(client, team, pid, who="clin_a", **body):  # noqa: F811
    return client.post(f"{API}/patients/{pid}/reports", json=body, headers=H(team, who))


def test_snapshot_contents_and_privacy(client, team, pt):  # noqa: F811
    signed(client, team, pt["id"], when="2026-03-01T10:00:00Z")
    create(client, team, pt["id"], when="2026-03-02T10:00:00Z")  # an unsigned draft must NOT appear in a report
    _, a = make_run(client, team, pt["id"], 1)
    _, b = make_run(client, team, pt["id"], 2)
    r = mk(client, team, pt["id"], clinical_summary="Stable examination.", base_run=a["id"], target_run=b["id"], include_notes=False)
    assert r.status_code == 201, r.text
    j = r.json()
    s = j["snapshot"]
    assert j["status"] == "draft" and j["version"] == 1 and j["integrity_ok"] is True and len(j["content_sha256"]) == 64
    assert len(s["assessments"]) == 1 and s["assessments"][0]["total"] == 4 and len(s["recordings"]) == 2
    assert s["comparison"]["n_features"] > 30 and "does not say whether the patient is improving" in s["comparison"]["note"]
    assert s["patient"]["display_id"] == pt["display_id"] and "ai" not in s and "notes" not in s
    blob = json.dumps(s, default=str)
    for private in ("Jane", "Doe", "MRN-77", "1972", "mirror", "fixation"):  # no identifiers, no free-text observations
        assert private not in blob, private
    assert any("not a diagnosis" in x for x in s["limitations"]) and any("covert-awareness" in x for x in s["limitations"])


def test_partial_comparison_and_validation(client, team, pt):  # noqa: F811
    _, a = make_run(client, team, pt["id"], 1)
    assert mk(client, team, pt["id"], base_run=a["id"]).status_code == 422  # both or neither
    assert mk(client, team, pt["id"], title="x").status_code == 422
    assert mk(client, team, pt["id"], clinical_summary="x" * 4001).status_code == 422
    assert mk(client, team, pt["id"], unexpected=1).status_code == 422
    assert mk(client, team, pt["id"], base_run=a["id"], target_run=str(uuid.uuid4())).status_code in (404, 422)


def test_sign_rules_and_immutability(client, team, pt):  # noqa: F811
    j = mk(client, team, pt["id"]).json()
    url = f"{API}/reports/{j['id']}"
    assert client.post(f"{url}/sign", json={}, headers=H(team, "clin_a")).status_code == 422  # clinician must write the summary
    assert client.patch(url, json={"clinical_summary": "Examination unchanged."}, headers=H(team, "clin_b")).status_code == 403
    ok = client.patch(url, json={"clinical_summary": "Examination unchanged.", "title": "Weekly summary"}, headers=H(team, "clin_a")).json()
    assert ok["integrity_ok"] and ok["content_sha256"] != j["content_sha256"]
    assert client.post(f"{url}/sign", json={}, headers=H(team, "clin_b")).status_code == 403  # only the author signs
    assert client.post(f"{url}/sign", json={}, headers=H(team, "tech")).status_code == 403
    s = client.post(f"{url}/sign", json={}, headers=H(team, "clin_a"))
    assert s.status_code == 200 and s.json()["status"] == "signed" and s.json()["signed_by"] and s.json()["integrity_ok"]
    assert client.post(f"{url}/sign", json={}, headers=H(team, "clin_a")).status_code == 409
    assert client.patch(url, json={"title": "Changed after signing"}, headers=H(team, "clin_a")).status_code == 409
    assert client.delete(url, headers=H(team, "clin_a")).status_code == 409
    assert client.post(f"{url}/refresh", headers=H(team, "clin_a")).status_code == 409


def test_amendment_creates_a_new_version_and_leaves_the_signed_one_untouched(client, team, pt):  # noqa: F811
    j = mk(client, team, pt["id"], clinical_summary="First version.").json()
    client.post(f"{API}/reports/{j['id']}/sign", json={}, headers=H(team, "clin_a"))
    before = get_db().reports.find_one({"_id": uuid.UUID(j["id"])})
    assert client.post(f"{API}/reports/{j['id']}/amend", headers=H(team, "viewer")).status_code == 403
    a2 = client.post(f"{API}/reports/{j['id']}/amend", headers=H(team, "clin_b"))  # any care-team clinician may correct a report
    assert a2.status_code == 201 and a2.json()["version"] == 2 and a2.json()["status"] == "draft" and a2.json()["amends"] == j["id"]
    assert client.post(f"{API}/reports/{j['id']}/amend", headers=H(team, "clin_a")).status_code == 409  # newer version exists
    v2 = a2.json()
    client.patch(f"{API}/reports/{v2['id']}", json={"clinical_summary": "Corrected wording."}, headers=H(team, "clin_b"))
    assert client.post(f"{API}/reports/{v2['id']}/sign", json={}, headers=H(team, "clin_b")).status_code == 422  # reason required
    done = client.post(f"{API}/reports/{v2['id']}/sign", json={"amendment_reason": "Typographical correction"}, headers=H(team, "clin_b"))
    assert done.status_code == 200 and done.json()["amendment_reason"].startswith("Typo")
    assert get_db().reports.find_one({"_id": uuid.UUID(j["id"])}) == before  # version 1 is untouched, byte for byte
    detail = client.get(f"{API}/reports/{j['id']}", headers=H(team, "viewer")).json()
    assert [(v["version"], v["status"]) for v in detail["versions"]] == [(1, "signed"), (2, "signed")]
    lst = client.get(f"{API}/patients/{pt['id']}/reports", headers=H(team, "viewer")).json()
    assert len(lst) == 1 and lst[0]["version"] == 2 and lst[0]["clinical_summary"] == "Corrected wording."


def test_drafts_are_only_for_clinicians_and_signed_reports_for_the_team(client, team, pt):  # noqa: F811
    d = mk(client, team, pt["id"], clinical_summary="Draft text").json()
    for who, code in (("clin_a", 200), ("clin_b", 200), ("tech", 404), ("viewer", 404)):
        assert client.get(f"{API}/reports/{d['id']}", headers=H(team, who)).status_code == code, who
    assert client.get(f"{API}/patients/{pt['id']}/reports", headers=H(team, "tech")).json() == []
    client.post(f"{API}/reports/{d['id']}/sign", json={}, headers=H(team, "clin_a"))
    for who in ("tech", "viewer"):
        assert client.get(f"{API}/reports/{d['id']}", headers=H(team, who)).status_code == 200
        assert mk(client, team, pt["id"], who=who).status_code == 403
    assert client.get(f"{API}/reports/{d['id']}", headers=H(team, "researcher")).status_code == 403
    outsider = register(client, team, full_name="Other Person", mrn=None)
    assert mk(client, team, outsider["id"], who="clin_b").status_code == 404
    assert client.get(f"{API}/reports/{uuid.uuid4()}", headers=H(team, "clin_a")).status_code == 404
    assert client.get(f"{API}/patients/{pt['id']}/reports").status_code == 401


def test_draft_refresh_rebuilds_the_snapshot(client, team, pt):  # noqa: F811
    d = mk(client, team, pt["id"], clinical_summary="x draft").json()
    assert d["snapshot"]["assessments"] == []
    signed(client, team, pt["id"])
    r = client.post(f"{API}/reports/{d['id']}/refresh", headers=H(team, "clin_a")).json()
    assert len(r["snapshot"]["assessments"]) == 1 and r["integrity_ok"] and r["content_sha256"] != d["content_sha256"]
    assert client.post(f"{API}/reports/{d['id']}/refresh", headers=H(team, "clin_b")).status_code == 409
    assert client.delete(f"{API}/reports/{d['id']}", headers=H(team, "clin_b")).status_code == 403
    assert client.delete(f"{API}/reports/{d['id']}", headers=H(team, "clin_a")).status_code == 204


def test_tampering_with_the_database_is_detected_and_blocks_export(client, team, pt):  # noqa: F811
    j = mk(client, team, pt["id"], clinical_summary="Original text.").json()
    client.post(f"{API}/reports/{j['id']}/sign", json={}, headers=H(team, "clin_a"))
    assert client.get(f"{API}/reports/{j['id']}", headers=H(team, "viewer")).json()["integrity_ok"] is True
    get_db().reports.update_one({"_id": uuid.UUID(j["id"])}, {"$set": {"clinical_summary": "Silently changed."}})
    assert client.get(f"{API}/reports/{j['id']}", headers=H(team, "viewer")).json()["integrity_ok"] is False
    r = client.get(f"{API}/reports/{j['id']}/export.html", headers=H(team, "viewer"))
    assert r.status_code == 409 and "no longer matches" in r.json()["detail"]


def test_export_is_escaped_safe_and_states_its_status(client, team, pt):  # noqa: F811
    signed(client, team, pt["id"])
    evil = "<script>alert(1)</script>"
    j = mk(client, team, pt["id"], title="Report <b>x</b>", clinical_summary=evil + " & text").json()
    r = client.get(f"{API}/reports/{j['id']}/export.html", headers=H(team, "clin_a"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and "attachment" in r.headers["content-disposition"]
    body = r.text
    assert "<script>" not in body and "&lt;script&gt;" in body and "&lt;b&gt;x&lt;/b&gt;" in body and "&amp; text" in body
    assert "NOT SIGNED" not in body and "DRAFT" in body and "not signed, not valid as a record" in body
    assert pt["display_id"] in body and "Jane" not in body and "CRS-R assessments" in body and "Limitations" in body and j["content_sha256"] in body
    assert "default-src 'none'" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    client.post(f"{API}/reports/{j['id']}/sign", json={}, headers=H(team, "clin_a"))
    assert "SIGNED" in client.get(f"{API}/reports/{j['id']}/export.html", params={"disposition": "inline"}, headers=H(team, "viewer")).text
    assert client.get(f"{API}/reports/{j['id']}/export.html", params={"disposition": "bogus"}, headers=H(team, "viewer")).status_code == 422


def test_report_actions_are_audited_without_content(client, team, pt):  # noqa: F811
    j = mk(client, team, pt["id"], clinical_summary="Secret clinical wording.").json()
    client.post(f"{API}/reports/{j['id']}/sign", json={}, headers=H(team, "clin_a"))
    client.get(f"{API}/reports/{j['id']}/export.html", headers=H(team, "viewer"))
    rows = list(get_db().audit_log.find({"patient_id": uuid.UUID(pt["id"])}))
    assert {"report.create", "report.sign", "report.export"} <= {r["action"] for r in rows}
    assert "Secret clinical wording" not in json.dumps([r["details"] for r in rows], default=str)
