"""Phase 6: longitudinal comparison and feature trends: QC gate, version gate, context warnings, uncertainty, access."""

import uuid

import pytest

from app.core.config import get_settings
from app.db.mongo import get_db
from tests.test_assessments import signed
from tests.test_patients import H, register, share, team  # noqa: F401
from tests.test_processing import run_ok  # noqa: F401
from tests.test_recordings import isolated_storage, make_edf, upload  # noqa: F401

API = "/api/v1"
COND = {"condition": "resting_eyes_closed", "patient_state": "awake", "setting": "icu", "time_of_day": "09:30"}


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(get_settings(), "jobs_inline", True)


@pytest.fixture
def pt(client, team):  # noqa: F811
    p = register(client, team, full_name="Jane Doe", mrn=None)
    share(client, team, p["id"], "tech", "viewer")
    return p


def make_run(client, team, pid, seed, cond=None, approve=True, feats=True, who="tech", **start_body):  # noqa: F811
    r = upload(client, team, "clin_a", pid, make_edf(seed=seed), confirm=True)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert client.put(f"{API}/recordings/{rec['id']}/condition", json=cond or COND, headers=H(team, "tech")).status_code == 200
    run = run_ok(client, team, rec["id"], who=who, **start_body)
    if approve:
        q = client.post(f"{API}/runs/{run['id']}/qc", json={"decision": "approve", "note": "ok for test"}, headers=H(team, "tech"))
        assert q.status_code == 200, q.text
    if feats:
        assert client.post(f"{API}/runs/{run['id']}/features", headers=H(team, "tech")).status_code == 202
    return rec, run


def cmp(client, team, pid, base, target, who="viewer", **params):  # noqa: F811
    return client.get(f"{API}/patients/{pid}/comparison", params={"base": base, "target": target, **params}, headers=H(team, who))


def test_candidates_list_only_approved_runs_with_features(client, team, pt):  # noqa: F811
    _, a = make_run(client, team, pt["id"], 1)
    make_run(client, team, pt["id"], 2, approve=False)  # QC pending: excluded
    make_run(client, team, pt["id"], 3, feats=False)  # no features: excluded
    c = client.get(f"{API}/patients/{pt['id']}/comparison-candidates", headers=H(team, "viewer")).json()
    assert [x["run_id"] for x in c["runs"]] == [a["id"]]
    assert c["default_target"] == a["id"] and c["runs"][0]["pipeline"].startswith("resting v")


def test_comparison_numbers_intervals_and_validation(client, team, pt):  # noqa: F811
    _, a = make_run(client, team, pt["id"], 1)
    _, b = make_run(client, team, pt["id"], 2)
    j = cmp(client, team, pt["id"], a["id"], b["id"], level="global").json()
    assert j["n_features"] > 30 and j["base"]["run_id"] == a["id"] and "does not say whether the patient is improving" in j["note"]
    ra = {(i["feature"], i["band"], i["name"]): i for i in j["items"]}
    x = ra[("rel_power", "alpha", "global")]
    assert x["delta"] == pytest.approx(x["target"] - x["base"]) and x["has_ci"] and x["base_ci"][0] <= x["base"] <= x["base_ci"][1]
    s = get_db().feature_summaries.find_one({"run_id": uuid.UUID(a["id"]), "level": "global", "feature": "rel_power", "band": "alpha"})
    assert x["base"] == s["mean"] and x["base_ci"] == [s["ci_low"], s["ci_high"]]  # the numbers are exactly the stored summaries
    for i in j["items"]:
        if i["has_ci"]:
            assert i["interval_separated"] == (i["target_ci"][0] > i["base_ci"][1] or i["target_ci"][1] < i["base_ci"][0])
    only = cmp(client, team, pt["id"], a["id"], b["id"], level="region", feature="rel_power").json()
    assert {i["feature"] for i in only["items"]} == {"rel_power"} and len(only["items"]) > 10
    assert cmp(client, team, pt["id"], a["id"], a["id"]).status_code == 422
    assert cmp(client, team, pt["id"], a["id"], b["id"], level="nope").status_code == 422
    assert cmp(client, team, pt["id"], a["id"], str(uuid.uuid4())).status_code == 404


def test_unapproved_or_featureless_runs_cannot_be_compared(client, team, pt):  # noqa: F811
    _, a = make_run(client, team, pt["id"], 1)
    _, pending = make_run(client, team, pt["id"], 2, approve=False)
    _, nofeat = make_run(client, team, pt["id"], 3, feats=False)
    assert cmp(client, team, pt["id"], a["id"], pending["id"]).status_code == 404
    assert cmp(client, team, pt["id"], a["id"], nofeat["id"]).status_code == 404


def test_context_differences_are_reported_next_to_the_numbers(client, team, pt):  # noqa: F811
    _, a = make_run(client, team, pt["id"], 1)
    sedated = {**COND, "condition": "resting_eyes_open", "patient_state": "sedated", "time_of_day": "21:00",
               "sedation": [{"agent": "propofol", "dose": 2.5, "unit": "mg/kg/h"}]}
    _, b = make_run(client, team, pt["id"], 2, cond=sedated)
    j = cmp(client, team, pt["id"], a["id"], b["id"]).json()
    keys = {w["key"] for w in j["warnings"]}
    assert {"condition", "patient_state", "sedation", "time_of_day"} <= keys
    assert "propofol" in next(w["message"] for w in j["warnings"] if w["key"] == "sedation")
    _, c = make_run(client, team, pt["id"], 3)
    assert cmp(client, team, pt["id"], a["id"], c["id"]).json()["warnings"] == []  # same context: no warnings


def test_runs_processed_differently_are_not_compared(client, team, pt):  # noqa: F811
    _, a = make_run(client, team, pt["id"], 1)
    _, b = make_run(client, team, pt["id"], 2, who="clin_a", overrides={"epoching.reject_ptp_uv": 200})
    r = cmp(client, team, pt["id"], a["id"], b["id"])
    assert r.status_code == 409 and "cannot be compared" in r.json()["detail"]["message"] and r.json()["detail"]["reasons"]


def test_comparison_includes_nearby_signed_assessments(client, team, pt):  # noqa: F811
    _, a = make_run(client, team, pt["id"], 1)
    _, b = make_run(client, team, pt["id"], 2)
    signed(client, team, pt["id"], when="2026-03-01T10:30:00Z")  # one hour after both recordings' EDF start (09:30, site time zone UTC)
    j = cmp(client, team, pt["id"], a["id"], b["id"]).json()
    assert j["assessments"]["base"]["total"] == 4 and j["assessments"]["base"]["hours_from_recording"] == 1.0
    assert j["days_between"] == 0.0


def test_feature_trend_orders_recordings_and_flags_mixed_context(client, team, pt):  # noqa: F811
    make_run(client, team, pt["id"], 1)
    make_run(client, team, pt["id"], 2, cond={**COND, "condition": "resting_eyes_open"})
    t = client.get(f"{API}/patients/{pt['id']}/feature-trend", params={"feature": "rel_power", "band": "alpha"}, headers=H(team, "viewer")).json()
    assert len(t["points"]) == 2 and t["mixed_context"] is True and "do not read a trend across different conditions" in t["note"]
    assert all(p["ci_low"] <= p["mean"] <= p["ci_high"] for p in t["points"])
    assert [p["start"] for p in t["points"]] == sorted(p["start"] for p in t["points"])
    empty = client.get(f"{API}/patients/{pt['id']}/feature-trend", params={"feature": "no_such_feature"}, headers=H(team, "viewer")).json()
    assert empty["points"] == []


def test_comparison_access_rules(client, team, pt):  # noqa: F811
    _, a = make_run(client, team, pt["id"], 1)
    _, b = make_run(client, team, pt["id"], 2)
    assert cmp(client, team, pt["id"], a["id"], b["id"], who="researcher").status_code == 403
    assert cmp(client, team, pt["id"], a["id"], b["id"], who="clin_b").status_code == 404  # not on the care team
    assert client.get(f"{API}/patients/{pt['id']}/comparison", params={"base": a["id"], "target": b["id"]}).status_code == 401
    assert client.get(f"{API}/patients/{pt['id']}/feature-trend", params={"feature": "rel_power"}, headers=H(team, "clin_b")).status_code == 404
    cmp(client, team, pt["id"], a["id"], b["id"])
    acts = {r["action"] for r in get_db().audit_log.find({"patient_id": uuid.UUID(pt["id"])})}
    assert "comparison.view" in acts
