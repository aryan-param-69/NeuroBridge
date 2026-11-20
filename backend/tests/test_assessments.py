"""Phase 5: CRS-R assessments (draft/sign/amend), trend, reminder, EEG pairing, notes, timeline, access."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.db.mongo import get_db
from tests.test_patients import H, register, share, team  # noqa: F401
from tests.test_recordings import isolated_storage, make_edf, patient, rec, upload  # noqa: F401

API = "/api/v1"
ZERO = {"auditory": 0, "visual": 0, "motor": 0, "oromotor_verbal": 0, "communication": 0, "arousal": 0}


def payload(when="2026-03-01T10:00:00Z", **over):
    base = {"assessed_at": when, "setting": "icu", "scores": {**ZERO, "visual": 2, "arousal": 2}, "arousal_facilitation_used": True,
            "sedation_status": "none", "confounders": ["fatigue"],
            "observations": {"visual": "Sustained fixation on a mirror, twice", "auditory": "Startle only"}}
    return {**base, **over}


def create(client, team, pid, who="clin_a", **over):  # noqa: F811
    return client.post(f"{API}/patients/{pid}/assessments", json=payload(**over), headers=H(team, who))


def signed(client, team, pid, who="clin_a", category="MCS-", **over):  # noqa: F811
    r = create(client, team, pid, who, clinician_category=category, **over)
    assert r.status_code == 201, r.text
    s = client.post(f"{API}/assessments/{r.json()['id']}/sign", json={}, headers=H(team, who))
    assert s.status_code == 200, s.text
    return s.json()


@pytest.fixture
def pt(client, team):  # noqa: F811
    p = register(client, team, full_name="Jane Doe", mrn=None, consent_research=True)
    share(client, team, p["id"], "clin_b", "tech", "viewer")
    return p


# ---- definition and validation ------------------------------------------------------------------------------------------

def test_definition_is_available_to_clinical_roles_and_requires_login(client, team):  # noqa: F811
    d = client.get(f"{API}/assessments/crsr-definition", headers=H(team, "viewer")).json()
    assert d["max_total"] == 23 and len(d["subscales"]) == 6 and "official" in d["verify"].lower()
    assert client.get(f"{API}/assessments/crsr-definition", headers=H(team, "researcher")).status_code == 200
    assert client.get(f"{API}/assessments/crsr-definition").status_code == 401


def test_total_is_computed_by_the_server_and_client_totals_are_refused(client, team, pt):  # noqa: F811
    r = create(client, team, pt["id"], scores={**ZERO, "auditory": 4, "visual": 5, "motor": 6, "oromotor_verbal": 3, "communication": 2, "arousal": 3})
    assert r.status_code == 201 and r.json()["total"] == 23 and r.json()["status"] == "draft"
    assert create(client, team, pt["id"], total=99).status_code == 422
    bad = {**payload(), "scores": {**ZERO, "total": 5}}
    assert client.post(f"{API}/patients/{pt['id']}/assessments", json=bad, headers=H(team, "clin_a")).status_code == 422


@pytest.mark.parametrize("key,value", [("auditory", 5), ("visual", 6), ("motor", 7), ("oromotor_verbal", 4), ("communication", 3), ("arousal", 4),
                                       ("auditory", -1), ("visual", 1.5), ("motor", "3"), ("arousal", True), ("communication", None)])
def test_invalid_scores_are_rejected(client, team, pt, key, value):  # noqa: F811
    assert create(client, team, pt["id"], scores={**ZERO, key: value}).status_code == 422


def test_time_rules(client, team, pt):  # noqa: F811
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    assert create(client, team, pt["id"], when=future).status_code == 422
    assert create(client, team, pt["id"], when="2026-01-15T10:00:00Z").status_code == 422  # before the date of injury (2026-02-02)
    assert create(client, team, pt["id"], when="2026-03-01T10:00:00").status_code == 422  # no time zone
    assert create(client, team, pt["id"], when="not a date").status_code == 422


def test_the_category_is_never_filled_in_automatically(client, team, pt):  # noqa: F811
    r = create(client, team, pt["id"], scores={**ZERO, "auditory": 3}).json()
    assert r["clinician_category"] is None and r["pattern"]["pattern"] == "MCS+"  # the pattern is shown, not applied
    mismatch = create(client, team, pt["id"], scores={**ZERO, "auditory": 3}, clinician_category="UWS/VS").json()
    assert mismatch["pattern"]["differs_from_entered"] and "judgement" in mismatch["pattern"]["message"]
    assert mismatch["clinician_category"] == "UWS/VS"  # still exactly what the clinician entered


# ---- drafts, signing, immutability --------------------------------------------------------------------------------------------

def test_drafts_are_visible_only_to_clinicians_and_editable_only_by_their_author(client, team, pt):  # noqa: F811
    a = create(client, team, pt["id"]).json()
    url = f"{API}/assessments/{a['id']}"
    for who, code in (("clin_a", 200), ("clin_b", 200), ("tech", 404), ("viewer", 404)):
        assert client.get(url, headers=H(team, who)).status_code == code, who
    assert [x["id"] for x in client.get(f"{API}/patients/{pt['id']}/assessments", headers=H(team, "tech")).json()] == []
    assert len(client.get(f"{API}/patients/{pt['id']}/assessments", headers=H(team, "clin_b")).json()) == 1
    assert client.patch(url, json={"setting": "ward"}, headers=H(team, "clin_b")).status_code == 403  # not the author
    r = client.patch(url, json={"scores": {**ZERO, "motor": 3, "arousal": 3}, "setting": "ward"}, headers=H(team, "clin_a"))
    assert r.status_code == 200 and r.json()["total"] == 6 and r.json()["setting"] == "ward"
    assert client.patch(url, json={"total": 1}, headers=H(team, "clin_a")).status_code == 422
    assert client.patch(url, json={"scores": None}, headers=H(team, "clin_a")).status_code == 422
    assert client.patch(url, json={"setting": "ward"}, headers=H(team, "tech")).status_code == 403  # role gate: technologists cannot write
    assert client.delete(url, headers=H(team, "clin_b")).status_code == 403
    assert client.delete(url, headers=H(team, "clin_a")).status_code == 204
    assert client.get(url, headers=H(team, "clin_a")).status_code == 404


def test_signing_rules(client, team, pt):  # noqa: F811
    a = create(client, team, pt["id"]).json()
    url = f"{API}/assessments/{a['id']}/sign"
    assert client.post(url, json={}, headers=H(team, "clin_a")).status_code == 422  # category required
    client.patch(f"{API}/assessments/{a['id']}", json={"clinician_category": "other"}, headers=H(team, "clin_a"))
    assert client.post(url, json={}, headers=H(team, "clin_a")).status_code == 422  # 'other' needs a description
    client.patch(f"{API}/assessments/{a['id']}", json={"category_note": "Locked-in suspected"}, headers=H(team, "clin_a"))
    assert client.post(url, json={}, headers=H(team, "clin_b")).status_code == 403  # only the author signs
    assert client.post(url, json={}, headers=H(team, "tech")).status_code == 403
    s = client.post(url, json={}, headers=H(team, "clin_a"))
    assert s.status_code == 200 and s.json()["status"] == "signed" and s.json()["signed_by"] and s.json()["signed_at"]
    assert client.post(url, json={}, headers=H(team, "clin_a")).status_code == 409  # already signed


def test_signed_assessments_are_immutable_and_amendments_create_new_versions(client, team, pt):  # noqa: F811
    v1 = signed(client, team, pt["id"])
    db = get_db()
    before = db.assessments.find_one({"_id": uuid.UUID(v1["id"])})
    url = f"{API}/assessments/{v1['id']}"
    assert client.patch(url, json={"setting": "home"}, headers=H(team, "clin_a")).status_code == 409
    assert client.delete(url, headers=H(team, "clin_a")).status_code == 409
    assert client.post(f"{url}/amend", headers=H(team, "viewer")).status_code == 403

    a2 = client.post(f"{url}/amend", headers=H(team, "clin_b"))  # any clinician on the care team may start a correction
    assert a2.status_code == 201 and a2.json()["version"] == 2 and a2.json()["status"] == "draft" and a2.json()["amends"] == v1["id"]
    assert client.post(f"{url}/amend", headers=H(team, "clin_a")).status_code == 409  # a newer version exists
    v2 = a2.json()
    client.patch(f"{API}/assessments/{v2['id']}", json={"scores": {**ZERO, "visual": 3, "arousal": 2}}, headers=H(team, "clin_b"))
    sign = f"{API}/assessments/{v2['id']}/sign"
    assert client.post(sign, json={}, headers=H(team, "clin_b")).status_code == 422  # reason required
    assert client.post(sign, json={"amendment_reason": "ok"}, headers=H(team, "clin_b")).status_code == 422  # too short
    done = client.post(sign, json={"amendment_reason": "Pursuit was seen in the second trial"}, headers=H(team, "clin_b"))
    assert done.status_code == 200 and done.json()["amendment_reason"].startswith("Pursuit")

    assert db.assessments.find_one({"_id": uuid.UUID(v1["id"])}) == before  # version 1 is untouched, byte for byte
    detail = client.get(url, headers=H(team, "viewer")).json()
    assert [(v["version"], v["total"]) for v in detail["versions"]] == [(1, 4), (2, 5)]  # the whole history stays visible
    lst = client.get(f"{API}/patients/{pt['id']}/assessments", headers=H(team, "viewer")).json()
    assert [(x["version"], x["total"]) for x in lst] == [(2, 5)]  # lists show the latest signed version
    assert client.post(f"{API}/assessments/{v2['id']}/amend", headers=H(team, "clin_a")).status_code == 201  # chain continues


def test_an_unsigned_amendment_does_not_replace_the_signed_value_for_others(client, team, pt):  # noqa: F811
    v1 = signed(client, team, pt["id"])
    client.post(f"{API}/assessments/{v1['id']}/amend", headers=H(team, "clin_a"))
    for who in ("tech", "viewer"):
        row = client.get(f"{API}/patients/{pt['id']}/assessments", headers=H(team, who)).json()[0]
        assert row["version"] == 1 and row["status"] == "signed"
    row = client.get(f"{API}/patients/{pt['id']}/assessments", headers=H(team, "clin_a")).json()[0]
    assert row["version"] == 2 and row["status"] == "draft"  # the clinician sees the pending amendment
    trend = client.get(f"{API}/patients/{pt['id']}/assessments/trend", headers=H(team, "viewer")).json()
    assert [p["version"] for p in trend["points"]] == [1]  # trends only use signed values


# ---- trend, reminder, pairing -----------------------------------------------------------------------------------------------

def test_trend_orders_signed_assessments_and_leaves_out_drafts(client, team, pt):  # noqa: F811
    signed(client, team, pt["id"], when="2026-03-03T10:00:00Z", scores={**ZERO, "visual": 3})
    signed(client, team, pt["id"], when="2026-03-01T10:00:00Z", scores={**ZERO, "visual": 1})
    create(client, team, pt["id"], when="2026-03-05T10:00:00Z")  # draft: excluded
    t = client.get(f"{API}/patients/{pt['id']}/assessments/trend", headers=H(team, "viewer")).json()
    assert [p["total"] for p in t["points"]] == [1, 3] and t["points"][0]["assessed_at"] < t["points"][1]["assessed_at"]
    assert t["max_total"] == 23 and "not a diagnosis" in t["note"]


def test_reminder_counts_recent_signed_assessments(client, team, pt, monkeypatch):  # noqa: F811
    url = f"{API}/patients/{pt['id']}/assessments/reminder"
    r = client.get(url, headers=H(team, "viewer")).json()
    assert r["due"] is True and r["count_in_window"] == 0 and "No CRS-R" in r["message"] and r["required"] == 5 and r["window_days"] == 10
    now = datetime.now(UTC)
    for i in range(4):
        signed(client, team, pt["id"], when=(now - timedelta(days=i)).isoformat().replace("+00:00", "Z"))
    signed(client, team, pt["id"], when=(now - timedelta(days=30)).isoformat().replace("+00:00", "Z"))  # outside the window
    r = client.get(url, headers=H(team, "viewer")).json()
    assert r["due"] is True and r["count_in_window"] == 4 and "literature" in r["message"]
    signed(client, team, pt["id"], when=(now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"))
    r = client.get(url, headers=H(team, "viewer")).json()
    assert r["due"] is False and r["count_in_window"] == 5
    monkeypatch.setattr(get_settings(), "reminder_min_assessments", 8)
    assert client.get(url, headers=H(team, "viewer")).json()["due"] is True  # the thresholds are settings
    client.patch(f"{API}/patients/{pt['id']}", json={"status": "discharged"}, headers=H(team, "clin_a"))
    assert client.get(url, headers=H(team, "viewer")).json()["due"] is False  # no reminders for discharged patients


def test_eeg_assessment_pairs_use_the_site_time_zone(client, team, pt, monkeypatch):  # noqa: F811
    r = upload(client, team, "clin_a", pt["id"], make_edf())  # EDF start 2026-03-01 09:30 (wall clock, no zone)
    assert r.status_code == 201
    signed(client, team, pt["id"], when="2026-03-01T10:30:00Z")  # one hour after the recording if the site is UTC
    signed(client, team, pt["id"], when="2026-03-10T10:30:00Z")  # far away
    j = client.get(f"{API}/patients/{pt['id']}/eeg-assessment-pairs", headers=H(team, "viewer")).json()
    assert len(j["pairs"]) == 1 and j["pairs"][0]["hours_between"] == 1.0 and len(j["unpaired_assessments"]) == 1
    assert j["pairs"][0]["recording"]["recording_code"].endswith("-R01") and "exploratory" in j["note"].lower()
    monkeypatch.setattr(get_settings(), "site_timezone", "America/New_York")  # 09:30 local = 14:30 UTC (EST in March before DST)
    j = client.get(f"{API}/patients/{pt['id']}/eeg-assessment-pairs", headers=H(team, "viewer")).json()
    assert j["pairs"][0]["hours_between"] == -4.0
    assert client.get(f"{API}/patients/{pt['id']}/eeg-assessment-pairs", params={"hours": 0.5}, headers=H(team, "viewer")).json()["pairs"] == []
    assert client.get(f"{API}/patients/{pt['id']}/eeg-assessment-pairs", params={"hours": 0}, headers=H(team, "viewer")).status_code == 422
    row = client.get(f"{API}/patients/{pt['id']}/assessments", headers=H(team, "viewer")).json()
    assert any(x["nearest_recording"] for x in row)


# ---- access, privacy, audit -----------------------------------------------------------------------------------------------------

def test_researchers_see_pseudonymised_scores_only(client, team, pt):  # noqa: F811
    a = signed(client, team, pt["id"], category="MCS-")
    h = H(team, "researcher")
    rows = client.get(f"{API}/patients/{pt['id']}/assessments", headers=h).json()
    assert rows == [{"id": a["id"], "scores": {**ZERO, "visual": 2, "arousal": 2}, "total": 4, "clinician_category": "MCS-",
                     "days_since_injury": (datetime(2026, 3, 1).date() - datetime(2026, 2, 2).date()).days}]
    text = json.dumps(client.get(f"{API}/patients/{pt['id']}/assessments/trend", headers=h).json())
    for banned in ("assessed_at", "assessor", "observations", "mirror", "sedation", "2026-03"):
        assert banned not in text, banned
    assert client.get(f"{API}/assessments/{a['id']}", headers=h).status_code == 403  # no detail view
    assert create(client, team, pt["id"], who="researcher").status_code == 403
    assert client.get(f"{API}/patients/{pt['id']}/assessments/reminder", headers=h).status_code == 403
    client.patch(f"{API}/patients/{pt['id']}", json={"consent_research": False}, headers=H(team, "clin_a"))
    assert client.get(f"{API}/patients/{pt['id']}/assessments", headers=h).status_code == 404  # consent withdrawn


def test_who_may_do_what(client, team, pt):  # noqa: F811
    a = signed(client, team, pt["id"])
    for who in ("tech", "viewer"):
        assert create(client, team, pt["id"], who=who).status_code == 403
        assert client.get(f"{API}/assessments/{a['id']}", headers=H(team, who)).status_code == 200
        assert client.post(f"{API}/assessments/{a['id']}/amend", headers=H(team, who)).status_code == 403
    outsider = register(client, team, full_name="Other Person", mrn=None)  # clin_a's patient; clin_b is not on that team
    assert create(client, team, outsider["id"], who="clin_b").status_code == 404
    assert client.get(f"{API}/patients/{outsider['id']}/assessments", headers=H(team, "clin_b")).status_code == 404
    assert create(client, team, pt["id"], who="admin").status_code == 403
    assert client.get(f"{API}/assessments/{uuid.uuid4()}", headers=H(team, "clin_a")).status_code == 404
    assert client.get(f"{API}/patients/{pt['id']}/assessments").status_code == 401


def test_actions_are_audited_without_free_text(client, team, pt):  # noqa: F811
    a = signed(client, team, pt["id"])
    client.get(f"{API}/assessments/{a['id']}", headers=H(team, "viewer"))
    rows = list(get_db().audit_log.find({"patient_id": uuid.UUID(pt["id"])}))
    assert {"assessment.create", "assessment.sign", "assessment.view"} <= {r["action"] for r in rows}
    blob = json.dumps([r["details"] for r in rows])
    assert "mirror" not in blob and "fixation" not in blob  # observations are never copied into the audit log


def test_observations_round_trip_and_are_length_limited(client, team, pt):  # noqa: F811
    a = create(client, team, pt["id"]).json()
    got = client.get(f"{API}/assessments/{a['id']}", headers=H(team, "clin_a")).json()
    assert got["observations"]["visual"].startswith("Sustained fixation") and got["confounders"] == ["fatigue"]
    assert create(client, team, pt["id"], observations={"visual": "x" * 2001}).status_code == 422
    assert create(client, team, pt["id"], confounders=["nonsense"]).status_code == 422
