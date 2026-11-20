"""Phase 3 API: processing runs, provenance, QC sign-off, downstream gating, viewer, failures, access."""

import json
import os
import time
import uuid
from pathlib import Path

import numpy as np
import pytest

from app import storage
from app.core.config import get_settings
from app.db.mongo import get_db
from app.jobs import fail_stale_jobs
from tests.test_patients import H, register, share, team  # noqa: F401
from tests.test_recordings import isolated_storage, make_edf, patient, rec, upload  # noqa: F401

API = "/api/v1"
DATA = Path(__file__).resolve().parents[2] / "data" / "eegmmidb" / "files"
GOLDEN = Path(__file__).parent / "golden" / "preprocess_eegmmidb.json"


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(get_settings(), "jobs_inline", True)


def start(client, team, rid, who="tech", **body):  # noqa: F811
    return client.post(f"{API}/recordings/{rid}/runs", json=body, headers=H(team, who))


def run_ok(client, team, rid, who="tech", **body):  # noqa: F811
    r = start(client, team, rid, who, **body)
    assert r.status_code == 202, r.text
    run = client.get(f"{API}/runs/{r.json()['run_id']}", headers=H(team, "clin_a")).json()
    assert run["status"] == "succeeded", run.get("error")
    return run


# ---- End to end + provenance ---------------------------------------------------------------------------------

def test_run_produces_quality_epochs_and_full_provenance(client, team, rec):  # noqa: F811
    r = start(client, team, rec["id"])
    assert r.status_code == 202 and len(r.json()["params_hash"]) == 64
    job = client.get(f"{API}/jobs/{r.json()['job_id']}", headers=H(team, "tech")).json()
    assert job["status"] == "succeeded" and job["progress"] == 100
    run = client.get(f"{API}/runs/{r.json()['run_id']}", headers=H(team, "clin_a")).json()

    assert run["pipeline_name"] == "resting" and run["pipeline_version"] == "1.0.0" and run["qc_status"] == "pending"
    assert {"mne", "numpy", "scipy", "python", "app", "quality_algorithm"} <= set(run["software_versions"])
    assert run["input_sha256"] == rec["sha256"] and len(run["output_sha256"]) == 64
    assert run["params"]["bandpass_hz"] == [0.5, 45.0] and run["params"]["line_freq_hz"] == 50
    assert [s["name"] for s in run["steps"]][:3] == ["load", "quality_assessment", "bad_channels"]
    assert run["bad_channels"] == ["T8", "O2"] or set(run["bad_channels"]) == {"T8", "O2"}
    assert set(run["interpolated_channels"]) == {"T8", "O2"} and run["dropped_channels"] == []
    assert run["n_epochs_total"] == 129 and run["n_epochs_kept"] >= 120 and run["low_confidence"] is False
    q = run["quality"]
    assert q["grade"] in ("good", "fair") and 0 <= q["overall_score"] <= 100 and "calibration_note" in q
    flags = {c["name"]: c["flags"] for c in q["channels"]}
    assert "flat" in flags["T8"] and "noisy" in flags["O2"] and flags["Fp1"] == []
    # the recording's channel flags and grade now reflect the new assessment
    detail = client.get(f"{API}/recordings/{rec['id']}", headers=H(team, "clin_a")).json()
    assert {c["name"]: c["auto_flag"] for c in detail["channels"]}["T8"] == "flat"
    assert client.get(f"{API}/recordings/{rec['id']}/quality", headers=H(team, "viewer")).json()["grade"] == q["grade"]
    assert storage.processed_path(uuid.UUID(rec["patient_id"]), uuid.UUID(run["id"])).exists()


def test_same_input_and_parameters_reproduce_the_same_output(client, team, rec):  # noqa: F811
    a, b = run_ok(client, team, rec["id"]), run_ok(client, team, rec["id"])
    assert a["params_hash"] == b["params_hash"] and a["output_sha256"] == b["output_sha256"]
    assert a["id"] != b["id"] and a["quality"]["overall_score"] == b["quality"]["overall_score"]


def test_overrides_change_the_hash_and_are_role_restricted(client, team, rec):  # noqa: F811
    base = run_ok(client, team, rec["id"])
    r60 = run_ok(client, team, rec["id"], overrides={"line_freq_hz": 60})
    assert r60["params"]["line_freq_hz"] == 60 and r60["params_hash"] != base["params_hash"]
    assert start(client, team, rec["id"], "tech", overrides={"epoching.reject_ptp_uv": 100}).status_code == 422
    assert start(client, team, rec["id"], "tech", overrides={"reference": "none"}).status_code == 422
    assert start(client, team, rec["id"], "tech", pipeline_name="nope").status_code == 422
    ok = run_ok(client, team, rec["id"], "clin_a", overrides={"epoching.reject_ptp_uv": 100})
    assert ok["params"]["epoching"]["reject_ptp_uv"] == 100
    assert client.get(f"{API}/pipelines", headers=H(team, "viewer")).json()["pipelines"][0]["version"] == "1.0.0"


# ---- QC gating --------------------------------------------------------------------------------------------------

def test_unapproved_runs_never_reach_downstream_analysis(client, team, patient, rec):  # noqa: F811
    run = run_ok(client, team, rec["id"])
    url = f"{API}/patients/{patient['id']}/analysis-runs"
    assert client.get(url, headers=H(team, "clin_a")).json() == []                       # pending: excluded
    qc = f"{API}/runs/{run['id']}/qc"
    assert client.post(qc, json={"decision": "reject"}, headers=H(team, "tech")).status_code == 422   # reason required
    assert client.post(qc, json={"decision": "reject", "note": "too noisy"}, headers=H(team, "tech")).status_code == 200
    assert client.get(url, headers=H(team, "clin_a")).json() == []                       # rejected: excluded
    assert client.get(f"{API}/recordings/{rec['id']}", headers=H(team, "clin_a")).json()["qc_status"] == "rejected"
    run2 = run_ok(client, team, rec["id"])
    assert client.post(f"{API}/runs/{run2['id']}/current", headers=H(team, "clin_a")).status_code == 409  # not approved
    assert client.post(f"{API}/runs/{run2['id']}/qc", json={"decision": "approve"}, headers=H(team, "tech")).status_code == 200
    rows = client.get(url, headers=H(team, "viewer")).json()
    assert [r["id"] for r in rows] == [run2["id"]] and rows[0]["is_current"] is True     # approved: included
    assert client.get(f"{API}/recordings/{rec['id']}", headers=H(team, "clin_a")).json()["qc_status"] == "approved"


def test_only_one_run_per_recording_is_current_and_only_clinicians_choose(client, team, patient, rec):  # noqa: F811
    a, b = run_ok(client, team, rec["id"]), run_ok(client, team, rec["id"])
    for r in (a, b):
        client.post(f"{API}/runs/{r['id']}/qc", json={"decision": "approve"}, headers=H(team, "tech"))
    cur = lambda: [x["id"] for x in client.get(f"{API}/patients/{patient['id']}/analysis-runs", headers=H(team, "clin_a")).json()]  # noqa: E731
    assert cur() == [a["id"]]  # the first approved run became current
    assert client.post(f"{API}/runs/{b['id']}/current", headers=H(team, "tech")).status_code == 403
    assert client.post(f"{API}/runs/{b['id']}/current", headers=H(team, "clin_a")).status_code == 200
    assert cur() == [b["id"]]


def test_approving_a_poor_quality_run_needs_a_reason(client, team, rec):  # noqa: F811
    run = run_ok(client, team, rec["id"])
    get_db().processing_runs.update_one({"_id": uuid.UUID(run["id"])}, {"$set": {"quality.grade": "poor"}})
    qc = f"{API}/runs/{run['id']}/qc"
    assert client.post(qc, json={"decision": "approve"}, headers=H(team, "tech")).status_code == 422
    assert client.post(qc, json={"decision": "approve", "note": "only the blink segments matter"}, headers=H(team, "tech")).status_code == 200


def test_qc_actions_are_audited_without_phi(client, team, patient, rec):  # noqa: F811
    run = run_ok(client, team, rec["id"])
    client.post(f"{API}/runs/{run['id']}/qc", json={"decision": "approve"}, headers=H(team, "tech"))
    rows = list(get_db().audit_log.find({"patient_id": uuid.UUID(patient["id"])}))
    actions = {r["action"] for r in rows}
    assert {"processing.run_started", "processing.qc_approved"} <= actions
    assert "Doe" not in json.dumps([r["details"] for r in rows])


# ---- Processed data in the viewer -------------------------------------------------------------------------------------

def test_processed_signal_is_average_referenced_and_marks_review_status(client, team, rec):  # noqa: F811
    run = run_ok(client, team, rec["id"])
    url = f"{API}/recordings/{rec['id']}/signal"
    p = {"stage": "processed", "run_id": run["id"], "start": 20, "duration": 4, "montage": "referential"}
    j = client.get(url, params=p, headers=H(team, "viewer")).json()
    assert j["stage"] == "processed" and j["unreviewed"] is True and len(j["channels"]) == 12
    assert np.abs(np.array(j["data"]).sum(axis=0)).max() < 0.05  # average reference: channels sum to zero
    assert set(j["interpolated"]) == {"T8", "O2"} and j["bad_channels"] == []
    client.post(f"{API}/runs/{run['id']}/qc", json={"decision": "approve"}, headers=H(team, "tech"))
    assert client.get(url, params=p, headers=H(team, "viewer")).json()["unreviewed"] is False
    raw = client.get(url, params={"start": 20, "duration": 4, "channels": "T8"}, headers=H(team, "viewer")).json()
    assert raw["stage"] == "raw" and np.abs(np.array(raw["data"])).max() < 0.2  # the raw T8 is still flat


def test_processed_signal_needs_a_valid_run_of_the_same_recording(client, team, patient, rec):  # noqa: F811
    run = run_ok(client, team, rec["id"])
    other = upload(client, team, "clin_a", patient["id"], make_edf(seed=9)).json()
    url = f"{API}/recordings/{other['id']}/signal"
    for params in ({"stage": "processed"}, {"stage": "processed", "run_id": run["id"]},
                   {"stage": "processed", "run_id": str(uuid.uuid4())}):
        assert client.get(url, params=params, headers=H(team, "clin_a")).status_code == 404


def test_epoch_rejections_are_returned_for_shading(client, team, rec):  # noqa: F811
    client.post(f"{API}/recordings/{rec['id']}/annotations", headers=H(team, "tech"),
                json={"onset_s": 8, "duration_s": 4, "label": "artifact_movement"})
    run = run_ok(client, team, rec["id"])
    j = client.get(f"{API}/recordings/{rec['id']}/signal", headers=H(team, "viewer"),
                   params={"stage": "processed", "run_id": run["id"], "start": 5, "duration": 10}).json()
    assert [e["onset"] for e in j["epochs"]] == [7.0, 8.0, 9.0, 10.0, 11.0]  # 2 s epochs overlapping 8-12 s
    assert all(e["status"] == 2 and e["duration"] == 2.0 for e in j["epochs"])
    far = client.get(f"{API}/recordings/{rec['id']}/signal", headers=H(team, "viewer"),
                     params={"stage": "processed", "run_id": run["id"], "start": 60, "duration": 10}).json()
    assert far["epochs"] == []


# ---- Annotations and selection ---------------------------------------------------------------------------------------

def test_annotations_validation_crud_and_effect_on_epochs(client, team, rec):  # noqa: F811
    base = f"{API}/recordings/{rec['id']}/annotations"
    assert client.post(base, json={"onset_s": 10, "duration_s": 4, "label": "artifact_eye"}, headers=H(team, "viewer")).status_code == 403
    assert client.post(base, json={"onset_s": 125, "duration_s": 20, "label": "excluded"}, headers=H(team, "tech")).status_code == 422
    assert client.post(base, json={"onset_s": 10, "duration_s": 4, "label": "bogus"}, headers=H(team, "tech")).status_code == 422
    a = client.post(base, json={"onset_s": 10, "duration_s": 4, "label": "artifact_eye", "note": "blinks"}, headers=H(team, "tech")).json()
    assert [x["id"] for x in client.get(base, headers=H(team, "viewer")).json()] == [a["id"]]
    run = run_ok(client, team, rec["id"])
    assert run["epochs"]["summary"]["rejected_annotation"] == 5
    assert client.delete(f"{base}/{a['id']}", headers=H(team, "tech")).status_code == 204
    assert client.delete(f"{base}/{a['id']}", headers=H(team, "tech")).status_code == 404
    assert run_ok(client, team, rec["id"])["epochs"]["summary"]["rejected_annotation"] == 0
    client.post(base, json={"onset_s": 20, "duration_s": 30, "label": "selected_for_analysis"}, headers=H(team, "clin_a"))
    assert run_ok(client, team, rec["id"])["n_epochs_kept"] == 29  # only epochs fully inside the selected 30 s


# ---- Failures, jobs ---------------------------------------------------------------------------------------------------

def test_a_failing_run_is_recorded_clearly_and_nothing_else_changes(client, team, patient, rec):  # noqa: F811
    storage.deid_path(uuid.UUID(patient["id"]), uuid.UUID(rec["id"])).unlink()
    r = start(client, team, rec["id"])
    assert r.status_code == 202
    run = client.get(f"{API}/runs/{r.json()['run_id']}", headers=H(team, "clin_a")).json()
    assert run["status"] == "failed" and run["error"]
    job = client.get(f"{API}/jobs/{r.json()['job_id']}", headers=H(team, "tech")).json()
    assert job["status"] == "failed" and job["error"]
    assert client.get(f"{API}/patients/{patient['id']}/analysis-runs", headers=H(team, "clin_a")).json() == []
    assert client.post(f"{API}/runs/{run['id']}/qc", json={"decision": "approve"}, headers=H(team, "tech")).status_code == 409


def test_a_recording_cannot_be_processed_twice_at_once(client, team, rec):  # noqa: F811
    get_db().processing_runs.insert_one({"_id": uuid.uuid4(), "recording_id": uuid.UUID(rec["id"]), "status": "running",
                                         "created_at": None})
    assert start(client, team, rec["id"]).status_code == 409


def test_stale_jobs_are_failed_on_startup():
    db = get_db()
    jid, rid = uuid.uuid4(), uuid.uuid4()
    db.jobs.insert_one({"_id": jid, "status": "running", "created_by": uuid.uuid4(), "target": {}})
    db.processing_runs.insert_one({"_id": rid, "status": "running"})
    assert fail_stale_jobs(db) == 1
    assert db.jobs.find_one({"_id": jid})["status"] == "failed" and db.processing_runs.find_one({"_id": rid})["status"] == "failed"


def test_background_worker_path_runs_and_reports_progress(client, team, rec, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "jobs_inline", False)
    r = start(client, team, rec["id"])
    assert r.status_code == 202
    t0 = time.time()
    while time.time() - t0 < 90:
        job = client.get(f"{API}/jobs/{r.json()['job_id']}", headers=H(team, "tech")).json()
        if job["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.5)
    assert job["status"] == "succeeded", job
    assert client.get(f"{API}/runs/{r.json()['run_id']}", headers=H(team, "clin_a")).json()["status"] == "succeeded"


# ---- Access control ---------------------------------------------------------------------------------------------------

def test_who_may_process_review_and_read(client, team, rec):  # noqa: F811
    rid = rec["id"]
    assert start(client, team, rid, "viewer").status_code == 403
    assert start(client, team, rid, "researcher").status_code == 403
    assert start(client, team, rid, "clin_b").status_code == 404
    assert start(client, team, rid, "admin").status_code == 403
    run = run_ok(client, team, rid)
    for who, code in (("viewer", 200), ("tech", 200), ("clin_b", 404), ("researcher", 403)):
        assert client.get(f"{API}/runs/{run['id']}", headers=H(team, who)).status_code == code, who
        assert client.get(f"{API}/recordings/{rid}/runs", headers=H(team, who)).status_code == code, who
    assert client.post(f"{API}/runs/{run['id']}/qc", json={"decision": "approve"}, headers=H(team, "viewer")).status_code == 403
    job_id = client.get(f"{API}/runs/{run['id']}", headers=H(team, "clin_a")).json()["id"]
    assert job_id and client.get(f"{API}/jobs/{uuid.uuid4()}", headers=H(team, "tech")).status_code == 404


def test_quality_report_requires_a_run(client, team, rec):  # noqa: F811
    assert client.get(f"{API}/recordings/{rec['id']}/quality", headers=H(team, "viewer")).status_code == 404


# ---- Real public data: regression ("golden") tests ------------------------------------------------------------------

REAL = [("S001", "R01"), ("S001", "R02"), ("S002", "R01")]
real_present = all((DATA / s / f"{s}{r}.edf").exists() for s, r in REAL)


def summary(run):
    q = run["quality"]
    return {"n_channels": len(run["channels"]), "sfreq_hz": run["sfreq_hz"], "n_epochs_total": run["n_epochs_total"],
            "n_epochs_kept": run["n_epochs_kept"], "bad_channels": run["bad_channels"],
            "interpolated": run["interpolated_channels"], "dropped": run["dropped_channels"],
            "grade": q["grade"], "score": round(q["overall_score"], 1), "low_confidence": run["low_confidence"]}


@pytest.mark.skipif(not real_present, reason="EEGMMIDB dataset not available")
def test_real_recordings_match_the_stored_regression_summaries(client, team, patient, monkeypatch):  # noqa: F811
    """These golden values were produced by this code on this data: they detect UNINTENDED changes of the pipeline.
    They are a regression guard, not independent ground truth. Regenerate deliberately with NB_UPDATE_GOLDEN=1
    (and release a new pipeline version if the change is intended)."""
    monkeypatch.setattr(get_settings(), "chunk_size", 8 * 1024 * 1024)
    got = {}
    for subj, run_name in REAL:
        data = (DATA / subj / f"{subj}{run_name}.edf").read_bytes()
        rec_ = upload(client, team, "clin_a", patient["id"], data)
        assert rec_.status_code == 201, rec_.text
        run = run_ok(client, team, rec_.json()["id"], "clin_a")
        again = run_ok(client, team, rec_.json()["id"], "clin_a")
        assert run["output_sha256"] == again["output_sha256"]  # deterministic on real data too
        got[f"{subj}{run_name}"] = summary(run)
    if os.environ.get("NB_UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=2))
    want = json.loads(GOLDEN.read_text())
    for k, v in got.items():
        w = want[k]
        assert abs(v.pop("score") - w.pop("score")) <= 0.5, k
        assert v == w, k
    assert got["S001R01"]["low_confidence"] is True  # one-minute baseline: 59 epochs < 60
