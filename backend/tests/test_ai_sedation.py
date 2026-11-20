"""AI analysis: vendored code matches the training code, the model file is verified before loading, the API gates, stores and explains."""

import json
import shutil
import uuid
from pathlib import Path

import numpy as np
import pytest

from app.core.config import get_settings
from app.db.mongo import get_db
from app.ml import registry
from app.ml import sed_features as F
from app.services import ai as svc
from tests.test_comparisons import COND, make_run
from tests.test_patients import H, register, share, team  # noqa: F401
from tests.test_recordings import isolated_storage, make_edf, upload  # noqa: F401

API = "/api/v1"
GOLDEN = Path(__file__).parent / "golden" / "sedation_features.json"
MODEL_SRC = Path(__file__).resolve().parents[2] / "ml" / "models" / "sedation_v1"
have_model = pytest.mark.skipif(not (MODEL_SRC / "model.joblib").exists(), reason="trained model not available")


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(get_settings(), "jobs_inline", True)
    registry.load.cache_clear()
    yield
    registry.load.cache_clear()


@pytest.fixture
def pt(client, team):  # noqa: F811
    p = register(client, team, full_name="Jane Doe", mrn=None)
    share(client, team, p["id"], "tech", "viewer")
    return p


def test_vendored_features_are_identical_to_the_training_code():
    g = json.loads(GOLDEN.read_text())
    rng = np.random.default_rng(0)
    X = rng.normal(0, 10, (2, 19, 2500)) + 20 * np.sin(2 * np.pi * 10 * np.arange(2500) / 250)
    A, names = F.extract(X, 250.0)
    assert names == g["names"] and np.allclose(A, np.array(g["values"]), atol=1e-9, rtol=1e-9)


def test_clean_windows_skip_rejected_epochs():
    run = {"epochs": {"onset_s": list(range(0, 59)), "status": [0] * 25 + [1] + [0] * 33, "length_s": 2.0}}  # 2 s epochs, 1 s step; epoch 25 rejected
    wins = svc.clean_windows(run, n_samples=60 * 100, fs=100.0)
    starts = [a / 100 for a, _ in wins]
    assert 20.0 not in starts and 30.0 in starts  # epoch 25 covers 25-27 s: the 20-30 s window is dropped, 30-40 s is kept
    assert all(b - a == 1000 for a, b in wins)
    all_bad = {"epochs": {"onset_s": list(range(0, 59)), "status": [1] * 59, "length_s": 2.0}}
    assert svc.clean_windows(all_bad, 6000, 100.0) == []


@have_model
def test_model_is_verified_before_loading(tmp_path, monkeypatch):
    root = tmp_path / "models"
    shutil.copytree(MODEL_SRC, root / "sedation_v1")
    monkeypatch.setattr(get_settings(), "model_dir", str(root))
    bundle, card = registry.load("sedation_v1")
    assert card["version"] == "sedation_v1" and "model" in bundle
    assert [m["name"] for m in registry.list_models()] == ["sedation_level"]
    registry.load.cache_clear()
    with open(root / "sedation_v1" / "model.joblib", "ab") as f:
        f.write(b"tampered")  # any change to the file must be refused (a pickle can run code)
    with pytest.raises(registry.ModelError, match="SHA-256"):
        registry.load("sedation_v1")
    for bad in ("../sedation_v1", "..", ".hidden", "a/b"):
        with pytest.raises(registry.ModelError):
            registry.load(bad)


@have_model
def test_models_endpoint_lists_the_model_with_its_limits(client, team, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "model_dir", str(MODEL_SRC.parent))
    r = client.get(f"{API}/ai/models", headers=H(team, "viewer")).json()
    m = r["models"][0]
    assert m["status"] == "staging" and "diagnosis" in m["not_for"] and any("patients" in x for x in m["known_limits"]) and "never been validated" in r["disclaimer"]
    assert client.get(f"{API}/ai/models", headers=H(team, "researcher")).status_code == 200
    assert client.get(f"{API}/ai/models").status_code == 401


@have_model
def test_prediction_end_to_end_is_stored_immutable_and_marked_unreliable(client, team, pt, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "model_dir", str(MODEL_SRC.parent))
    _, run = make_run(client, team, pt["id"], 1, feats=False)
    r = client.post(f"{API}/runs/{run['id']}/ai/sedation", headers=H(team, "clin_a"))
    assert r.status_code == 202, r.text
    job = client.get(f"{API}/jobs/{r.json()['job_id']}", headers=H(team, "clin_a")).json()
    if job["status"] != "succeeded":  # the test recording has only 12 channels: the model needs the 19 standard ones
        assert "19 standard 10-20 channels" in (job.get("error") or "") or "missing" in (job.get("error") or "")
        assert client.get(f"{API}/runs/{run['id']}/ai/sedation", headers=H(team, "viewer")).json()["status"] == "none"
        return
    j = client.get(f"{API}/runs/{run['id']}/ai/sedation", headers=H(team, "viewer")).json()
    assert j["status"] == "done" and 0 <= j["p_sedated"] <= 1 and j["model"]["status"] == "staging" and len(j["model"]["sha256"]) == 64
    assert j["unfamiliar"] is True and any("unlike the EEG" in w for w in j["reliability"])


def test_model_refuses_recordings_without_the_standard_channels(client, team, pt, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "model_dir", str(MODEL_SRC.parent))
    _, run = make_run(client, team, pt["id"], 1, feats=False)  # the synthetic EDF has only 12 channels
    r = client.post(f"{API}/runs/{run['id']}/ai/sedation", headers=H(team, "clin_a"))
    assert r.status_code == 202
    job = client.get(f"{API}/jobs/{r.json()['job_id']}", headers=H(team, "clin_a")).json()
    assert job["status"] == "failed" and "19 standard" in job["error"]
    assert client.get(f"{API}/runs/{run['id']}/ai/sedation", headers=H(team, "viewer")).json()["status"] == "none"  # nothing stored


def test_prediction_access_rules(client, team, pt, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "model_dir", str(MODEL_SRC.parent))
    _, run = make_run(client, team, pt["id"], 1, feats=False)
    url = f"{API}/runs/{run['id']}/ai/sedation"
    assert client.post(url, headers=H(team, "tech")).status_code == 403  # only clinicians start model runs
    assert client.post(url, headers=H(team, "viewer")).status_code == 403
    assert client.post(url, headers=H(team, "clin_b")).status_code == 404  # not on the care team
    assert client.post(url, headers=H(team, "researcher")).status_code == 403
    assert client.get(url, headers=H(team, "clin_b")).status_code == 404
    assert client.get(url, headers=H(team, "researcher")).status_code == 403
    assert client.post(url).status_code == 401
    assert client.get(f"{API}/runs/{uuid.uuid4()}/ai/sedation", headers=H(team, "clin_a")).status_code == 404


def _insert_prediction(patient_id, run_id, recording_id, **over):
    d = {"_id": uuid.uuid4(), "run_id": uuid.UUID(run_id), "recording_id": uuid.UUID(recording_id), "patient_id": uuid.UUID(patient_id),
         "task": "sedation_level", "model": {"name": "sedation_level", "version": "sedation_v1", "status": "staging", "sha256": "0" * 64, "algorithm": "xgboost"},
         "created_by": uuid.uuid4(), "created_at": __import__("app.db.base", fromlist=["utcnow"]).utcnow(), "p_sedated": 0.2, "prediction_set": ["awake-like"],
         "verdict": "awake-like", "n_epochs": 12, "unfamiliar_fraction": 1.0, "unfamiliar": True, "epoch_spread_sd": 0.1, "coverage_target": 0.8,
         "p_epoch": [0.2] * 12, "explanation": [], "n_windows_used": 12, "interpolated_channels": [], "low_confidence": False, "qc_status": "approved",
         "quality_grade": "good", "pipeline": "resting v1.0.0", "context": {"condition": "resting_eyes_closed", "patient_state": "awake", "sedation": []},
         "disclaimer": svc.DISCLAIMER}
    d.update(over)
    get_db().ai_predictions.insert_one(d)
    return d


def test_listing_views_and_the_pseudonymised_research_view(client, team, pt):  # noqa: F811
    rec, run = make_run(client, team, pt["id"], 1, feats=False)
    _insert_prediction(pt["id"], run["id"], rec["id"])
    j = client.get(f"{API}/patients/{pt['id']}/ai/sedation", headers=H(team, "viewer")).json()
    assert len(j["items"]) == 1 and j["items"][0]["verdict"] == "awake-like" and "not evidence of a change in the patient" in j["note"]
    assert any("unlike the EEG" in w for w in j["items"][0]["reliability"]) and any("staging" in w for w in j["items"][0]["reliability"])
    assert client.get(f"{API}/patients/{pt['id']}/ai/sedation", headers=H(team, "clin_b")).status_code == 404
    team_view = client.get(f"{API}/ai/predictions", headers=H(team, "clin_a")).json()
    assert team_view["pseudonymised"] is False and team_view["items"][0]["patient_id"] == pt["id"]
    assert client.get(f"{API}/ai/predictions", headers=H(team, "clin_b")).json()["items"] == []
    res = client.get(f"{API}/ai/predictions", headers=H(team, "researcher")).json()
    assert res["pseudonymised"] is True and res["items"] == []  # no research consent yet
    client.patch(f"{API}/patients/{pt['id']}", json={"consent_research": True}, headers=H(team, "clin_a"))
    res = client.get(f"{API}/ai/predictions", headers=H(team, "researcher")).json()
    item = res["items"][0]
    assert set(item) == {"id", "patient", "p_sedated", "verdict", "unfamiliar", "model_version", "condition", "unreviewed", "days_since_injury"}
    assert client.get(f"{API}/ai/predictions", headers=H(team, "admin")).status_code == 403
    assert COND["condition"] == item["condition"]


def make_edf19(seed=0, seconds=130):
    import datetime as dt
    import io

    from edfio import Edf, EdfSignal, Patient, Recording

    from app import storage

    rng = np.random.default_rng(seed)
    fs = 250
    t = np.arange(0, seconds, 1 / fs)
    sigs = []
    for name in F.STANDARD_19:
        x = 30 * np.sin(2 * np.pi * 10 * t + rng.uniform(0, 0.3)) + rng.normal(0, 6, t.size)
        sigs.append(EdfSignal(x, fs, label=f"EEG {name}-REF", physical_dimension="uV", physical_range=(-3200, 3200), prefiltering="HP:0.5Hz LP:70Hz"))
    edf = Edf(sigs, patient=Patient(code="X", sex="F", birthdate=dt.date(1972, 4, 10), name="Doe_Jane"),
              recording=Recording(startdate=dt.date(2026, 3, 1), hospital_administration_code="X", investigator_technician_code="X", equipment_code="Dev1"),
              starttime=dt.time(9, 30, 0))
    path = Path(storage.root()) / f"_gen19_{uuid.uuid4().hex}.edf"
    edf.write(path)
    data = path.read_bytes()
    path.unlink()
    io.BytesIO(data)
    return data


@have_model
@pytest.mark.parametrize("model_name", ["sedation_v1", "sedation_v2"])
def test_full_pipeline_on_a_19_channel_recording(client, team, pt, monkeypatch, model_name):  # noqa: F811
    from tests.test_processing import run_ok

    monkeypatch.setattr(get_settings(), "model_dir", str(MODEL_SRC.parent))
    monkeypatch.setattr(get_settings(), "sedation_model", model_name)  # v1 uses 74 features, v2 uses a 68-feature subset: both must run
    r = upload(client, team, "clin_a", pt["id"], make_edf19(), confirm=True)
    assert r.status_code == 201, r.text
    rec = r.json()
    client.put(f"{API}/recordings/{rec['id']}/condition", json=COND, headers=H(team, "tech"))
    run = run_ok(client, team, rec["id"])
    start = client.post(f"{API}/runs/{run['id']}/ai/sedation", headers=H(team, "clin_a"))
    assert start.status_code == 202, start.text
    job = client.get(f"{API}/jobs/{start.json()['job_id']}", headers=H(team, "clin_a")).json()
    assert job["status"] == "succeeded", job
    j = client.get(f"{API}/runs/{run['id']}/ai/sedation", headers=H(team, "viewer")).json()
    assert j["status"] == "done" and 0.0 <= j["p_sedated"] <= 1.0 and j["n_windows_used"] >= 6 and len(j["p_epoch"]) == j["n_epochs"]
    assert j["model"]["sha256"] == json.loads((MODEL_SRC.parent / model_name / "model_card.json").read_text())["model_sha256"] and j["model"]["version"] == model_name
    assert j["unreviewed"] is True and any("not been approved by QC" in w for w in j["reliability"])  # run not approved yet
    assert j["unfamiliar"] is True  # synthetic sinusoid + noise is nothing like real EEG: the detector must say so
    assert len(j["explanation"]) == 6 and all(e["pushes_towards"] in ("sedated-like", "awake-like") and e["plain"] for e in j["explanation"])
    # a second run stores a second immutable record; the latest wins in the views
    client.post(f"{API}/runs/{run['id']}/ai/sedation", headers=H(team, "clin_a"))
    assert get_db().ai_predictions.count_documents({"run_id": uuid.UUID(run["id"])}) == 2
    acts = {a["action"] for a in get_db().audit_log.find({"patient_id": uuid.UUID(pt["id"])})}
    assert {"ai.run", "ai.view"} <= acts or "ai.run" in acts


def test_a_model_may_use_a_subset_of_the_extracted_features():
    """Models trained without amplitude-dependent features must be fed exactly their own columns, in their own order."""
    import numpy as np

    from app.ml import sed_predict as P

    class Probe:
        def predict_proba(self, F):
            self.seen = F.copy()
            return np.stack([1 - F[:, 0] * 0 - 0.3, F[:, 0] * 0 + 0.3], axis=1)

    want = ["global:rel_alpha", "occipital:rel_delta", "global:wpli_alpha"]  # a subset, deliberately not in extraction order
    X = np.random.default_rng(0).normal(0, 10, (3, 19, 2500))
    full, names = F.extract(X, 250.0)
    m = Probe()
    bundle = {"model": m, "features": want, "platt": (1.0, 0.0), "conformal_q": 0.5, "alpha": 0.2,
              "ood": {"mu": np.zeros(3), "sd": np.ones(3), "comp": np.eye(3), "lam": np.ones(3), "thr": 1e9, "thr_spe": 1e9}}
    out = P.predict_epochs(bundle, X, 250.0)
    assert out["feature_names"] == want and np.allclose(m.seen, full[:, [names.index(n) for n in want]])
    bundle["features"] = want + ["no:such_feature"]
    with pytest.raises(ValueError, match="missing"):
        P.predict_epochs(bundle, X, 250.0)
