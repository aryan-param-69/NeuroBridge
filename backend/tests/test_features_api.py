"""Phase 4 API: feature extraction jobs, summaries, CSV = API, PSD, spectrogram, topomap, access, failures."""

import csv
import io
import uuid
from pathlib import Path

import numpy as np
import pytest

from app import storage
from app.api.v1.features import _safe
from app.core.config import get_settings
from app.db.mongo import get_db
from app.eeg.features.catalog import CATALOG
from tests.test_patients import H, register, share, team  # noqa: F401
from tests.test_processing import run_ok  # noqa: F401
from tests.test_recordings import isolated_storage, make_edf, patient, rec, upload  # noqa: F401

API = "/api/v1"
DATA = Path(__file__).resolve().parents[2] / "data" / "eegmmidb" / "files"


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(get_settings(), "jobs_inline", True)


@pytest.fixture
def run(client, team, rec):  # noqa: F811
    return run_ok(client, team, rec["id"])


def compute(client, team, run_id, who="tech"):  # noqa: F811
    return client.post(f"{API}/runs/{run_id}/features", headers=H(team, who))


@pytest.fixture
def done(client, team, run):  # noqa: F811
    r = compute(client, team, run["id"])
    assert r.status_code == 202, r.text
    return run


def feats(client, team, run_id, who="viewer", **params):  # noqa: F811
    return client.get(f"{API}/runs/{run_id}/features", params=params, headers=H(team, who))


# ---- compute and summaries ------------------------------------------------------------------------------------------

def test_compute_runs_a_job_and_stores_summaries(client, team, run):  # noqa: F811
    assert client.get(f"{API}/runs/{run['id']}/feature-run", headers=H(team, "viewer")).json()["status"] == "none"
    assert feats(client, team, run["id"]).status_code == 404  # nothing computed yet
    r = compute(client, team, run["id"])
    assert r.status_code == 202
    job = client.get(f"{API}/jobs/{r.json()['job_id']}", headers=H(team, "tech")).json()
    assert job["status"] == "succeeded", job
    st = client.get(f"{API}/runs/{run['id']}/feature-run", headers=H(team, "viewer")).json()
    assert st["status"] == "succeeded" and st["n_epochs"] >= 120 and len(st["features_hash"]) == 64 and st["unreviewed"] is True
    assert set(st["bands"]) == {"delta", "theta", "alpha", "beta", "gamma_low"}
    j = feats(client, team, run["id"], level="global").json()
    assert j["unreviewed"] is True and j["low_confidence"] is False and j["features_version"] == "1.0.0" and len(j["rows"]) > 40
    r0 = next(x for x in j["rows"] if x["feature"] == "rel_power" and x["band"] == "alpha")
    assert r0["ci_low"] <= r0["mean"] <= r0["ci_high"] and r0["n_epochs"] >= 120


def test_filters_levels_and_the_partition_property(client, team, done):  # noqa: F811
    j = feats(client, team, done["id"], level="channel", names="rel_power", channels="Fp1,C3").json()
    assert {r["name"] for r in j["rows"]} == {"Fp1", "C3"} and {r["feature"] for r in j["rows"]} == {"rel_power"}
    for ch in ("Fp1", "C3"):
        assert sum(r["mean"] for r in j["rows"] if r["name"] == ch) == pytest.approx(1.0, abs=1e-3)  # bands partition 1-45 Hz
    reg = feats(client, team, done["id"], level="region", names="abs_power", bands="alpha").json()["rows"]
    assert {"frontal_L", "central_R", "occipital_R"} <= {r["name"] for r in reg}
    assert feats(client, team, done["id"], level="pair", names="asymmetry_abs_power", bands="alpha").json()["rows"][0]["name"].count("-") == 1
    assert feats(client, team, done["id"], level="nonsense").status_code == 422


def test_every_stored_feature_is_in_the_catalogue_with_a_description(client, team, done):  # noqa: F811
    cat = client.get(f"{API}/features/catalog", headers=H(team, "viewer")).json()
    assert cat["version"] == "1.0.0" and {f["name"] for f in cat["features"]} == set(CATALOG)
    assert all(len(f["description"]) > 25 and f["how"] for f in cat["features"])
    assert set(cat["bands"]["alpha"]) == {"range_hz", "plain"}
    stored = {r["feature"] for r in get_db().feature_summaries.find({"run_id": uuid.UUID(done["id"])}, {"feature": 1})}
    assert stored and stored <= {f["name"] for f in cat["features"]}  # 100 % coverage


def test_csv_export_matches_the_api_values_exactly(client, team, done):  # noqa: F811
    api_rows = feats(client, team, done["id"], level="all").json()["rows"]
    r = client.get(f"{API}/runs/{done['id']}/features/export.csv", headers=H(team, "viewer"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    got = list(csv.DictReader(io.StringIO(r.text)))
    assert len(got) == len(api_rows)
    key = lambda d: (d["level"], d["name"], d["feature"], d["band"] or "")  # noqa: E731
    csv_by = {key(d): d for d in got}
    fl = lambda s: None if s == "" else float(s)  # noqa: E731
    for a in api_rows:
        c = csv_by[key(a)]
        for col in ("mean", "median", "sd", "ci_low", "ci_high"):
            assert fl(c[col]) == a[col], (key(a), col)
        assert int(c["n_epochs"]) == a["n_epochs"] and c["unit"] == a["unit"]
    assert got[0]["qc_status"] == "pending" and got[0]["recording_code"].endswith("-R01") and got[0]["pipeline"] == "resting v1.0.0"
    assert client.get(f"{API}/runs/{done['id']}/features/export.csv", params={"level": "global"}, headers=H(team, "viewer")).text.count("\n") < len(got)


def test_csv_cells_cannot_be_formulas():
    assert _safe("=1+1") == "'=1+1" and _safe("@cmd") == "'@cmd" and _safe("-5") == "'-5" and _safe("Fp1") == "Fp1" and _safe(None) == ""


def test_recomputing_gives_identical_numbers_and_replaces_old_rows(client, team, done):  # noqa: F811
    h1 = client.get(f"{API}/runs/{done['id']}/feature-run", headers=H(team, "viewer")).json()["features_hash"]
    n1 = get_db().feature_summaries.count_documents({"run_id": uuid.UUID(done["id"])})
    assert compute(client, team, done["id"]).status_code == 202
    st = client.get(f"{API}/runs/{done['id']}/feature-run", headers=H(team, "viewer")).json()
    assert st["features_hash"] == h1 and get_db().feature_summaries.count_documents({"run_id": uuid.UUID(done["id"])}) == n1


def test_review_status_follows_the_run_and_masks_are_respected(client, team, patient, rec):  # noqa: F811
    client.post(f"{API}/recordings/{rec['id']}/annotations", headers=H(team, "tech"),
                json={"onset_s": 10, "duration_s": 6, "label": "artifact_movement"})
    run = run_ok(client, team, rec["id"])
    compute(client, team, run["id"])
    n = client.get(f"{API}/runs/{run['id']}/feature-run", headers=H(team, "viewer")).json()["n_epochs"]
    assert n == run["n_epochs_kept"] and n < run["n_epochs_total"]  # annotation-excluded epochs are not used
    assert feats(client, team, run["id"]).json()["unreviewed"] is True
    client.post(f"{API}/runs/{run['id']}/qc", json={"decision": "approve"}, headers=H(team, "tech"))
    j = feats(client, team, run["id"]).json()
    assert j["unreviewed"] is False and j["qc_status"] == "approved"


# ---- visualisation ------------------------------------------------------------------------------------------------------

def test_psd_endpoint(client, team, done):  # noqa: F811
    url = f"{API}/runs/{done['id']}/psd"
    j = client.get(url, params={"channels": "Fp1,O1", "groups": "global,occipital_L"}, headers=H(team, "viewer")).json()
    f = np.array(j["freqs_hz"])
    assert f[0] >= 0.5 and f[-1] <= 45 and set(j["channels"]) == {"Fp1", "O1"} and set(j["groups"]) == {"global", "occipital_L"}
    assert all(len(v) == len(f) for v in j["channels"].values())
    g = j["groups"]["global"]
    lo, m, hi = (np.array(g[k], dtype=float) for k in ("ci_low", "mean", "ci_high"))
    assert np.all(lo <= m + 1e-9) and np.all(m <= hi + 1e-9) and j["unit"] == "uV^2/Hz" and "global" in j["available_groups"]
    narrow = client.get(url, params={"fmin": 8, "fmax": 13}, headers=H(team, "viewer")).json()
    assert min(narrow["freqs_hz"]) >= 8 and max(narrow["freqs_hz"]) <= 13
    assert client.get(url, params={"channels": "Nope"}, headers=H(team, "viewer")).status_code == 422
    assert client.get(url, params={"fmax": 500}, headers=H(team, "viewer")).status_code == 422


def test_spectrogram_endpoint(client, team, done):  # noqa: F811
    j = client.get(f"{API}/runs/{done['id']}/spectrogram", params={"channel": "Fp1"}, headers=H(team, "viewer")).json()
    P = np.array(j["log10_power"])
    assert P.shape == (len(j["freqs_hz"]), len(j["times_s"])) and max(j["freqs_hz"]) <= 45 and j["unreviewed"] is True
    assert 100 < j["times_s"][-1] < 135 and np.isfinite(P).all()
    assert client.get(f"{API}/runs/{done['id']}/spectrogram", params={"channel": "Nope"}, headers=H(team, "viewer")).status_code == 422
    assert client.get(f"{API}/runs/{done['id']}/spectrogram", headers=H(team, "viewer")).status_code == 422


def test_topomap_endpoint_returns_an_svg_without_channel_names(client, team, done):  # noqa: F811
    r = client.get(f"{API}/runs/{done['id']}/topomap", params={"feature": "abs_power", "band": "alpha"}, headers=H(team, "viewer"))
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml" and r.headers["x-unreviewed"] == "true"
    assert "<svg" in r.text[:600] and "Fp1" not in r.text and "<script" not in r.text.lower()
    assert client.get(f"{API}/runs/{done['id']}/topomap", params={"feature": "rel_power", "band": "beta", "vmin": 0, "vmax": 0.5},
                      headers=H(team, "viewer")).status_code == 200
    for params, code in (({"feature": "asymmetry_abs_power", "band": "alpha"}, 422), ({"feature": "nope"}, 422),
                         ({"feature": "rel_power", "band": "nope"}, 404)):
        assert client.get(f"{API}/runs/{done['id']}/topomap", params=params, headers=H(team, "viewer")).status_code == code, params


def test_aperiodic_endpoint_reports_fit_quality(client, team, done):  # noqa: F811
    j = client.get(f"{API}/runs/{done['id']}/aperiodic", headers=H(team, "viewer")).json()
    g = next(f for f in j["fits"] if f["level"] == "global")
    assert {"exponent", "offset", "r2", "error", "good_fit"} <= set(g) and j["fit_range_hz"] == [1.0, 40.0]
    assert all(isinstance(f["good_fit"], bool) for f in j["fits"])


# ---- state, failures, access, audit --------------------------------------------------------------------------------------

def test_features_need_a_finished_run_and_cannot_run_twice_at_once(client, team, run):  # noqa: F811
    db = get_db()
    rid = uuid.UUID(run["id"])
    db.processing_runs.update_one({"_id": rid}, {"$set": {"status": "failed"}})
    assert compute(client, team, run["id"]).status_code == 409
    db.processing_runs.update_one({"_id": rid}, {"$set": {"status": "succeeded"}})
    db.feature_runs.insert_one({"_id": rid, "status": "running"})
    assert compute(client, team, run["id"]).status_code == 409


def test_a_failing_extraction_is_recorded_clearly(client, team, patient, run):  # noqa: F811
    storage.processed_path(uuid.UUID(patient["id"]), uuid.UUID(run["id"])).unlink()
    r = compute(client, team, run["id"])
    assert r.status_code == 202
    st = client.get(f"{API}/runs/{run['id']}/feature-run", headers=H(team, "viewer")).json()
    assert st["status"] == "failed" and st["error"]
    assert client.get(f"{API}/jobs/{r.json()['job_id']}", headers=H(team, "tech")).json()["status"] == "failed"
    assert feats(client, team, run["id"]).status_code == 404


def test_who_may_compute_and_read(client, team, run, patient):  # noqa: F811
    rid = run["id"]
    assert compute(client, team, rid, "viewer").status_code == 403
    assert compute(client, team, rid, "researcher").status_code == 403
    assert compute(client, team, rid, "clin_b").status_code == 404
    assert compute(client, team, rid, "admin").status_code == 403
    assert compute(client, team, rid, "clin_a").status_code == 202
    for who, code in (("viewer", 200), ("tech", 200), ("clin_b", 404), ("researcher", 403), ("admin", 403)):
        assert feats(client, team, rid, who).status_code == code, who
        assert client.get(f"{API}/runs/{rid}/features/export.csv", headers=H(team, who)).status_code == code, who
        assert client.get(f"{API}/runs/{rid}/psd", headers=H(team, who)).status_code == code, who
        assert client.get(f"{API}/runs/{rid}/topomap", params={"band": "alpha"}, headers=H(team, who)).status_code == code, who
    assert client.get(f"{API}/features/catalog").status_code == 401
    assert client.get(f"{API}/runs/{uuid.uuid4()}/features", headers=H(team, "viewer")).status_code == 404


def test_feature_actions_are_audited_without_phi(client, team, patient, done):  # noqa: F811
    feats(client, team, done["id"])
    client.get(f"{API}/runs/{done['id']}/features/export.csv", headers=H(team, "viewer"))
    rows = list(get_db().audit_log.find({"patient_id": uuid.UUID(patient["id"])}))
    assert {"features.compute", "features.view", "features.export_csv"} <= {r["action"] for r in rows}
    assert "Doe" not in str([r["details"] for r in rows])


# ---- real public data ----------------------------------------------------------------------------------------------------

def _real(subject: str, run: str):
    """Pipeline + feature extraction on one real EEGMMIDB file (no database)."""
    import mne

    from app.eeg import pipelines
    from app.eeg import preprocess as pp
    from app.eeg.channels import normalize_all
    from app.eeg.features import extract as E

    raw = mne.io.read_raw_edf(DATA / subject / f"{subject}{run}.edf", preload=True, infer_types=False, verbose="ERROR")
    raw.rename_channels({o: n["name"] for o, n in zip(raw.ch_names, normalize_all(raw.ch_names), strict=True)})
    meta = [{"name": n, "physical_min": -8092, "physical_max": 8092} for n in raw.ch_names]
    params = pipelines.resolve("resting", None, 60, None, ["clinician"])["params"]
    res = pp.run_pipeline(raw, meta, params, set(), set(), [], [])
    ep = res["epochs"]
    kept = np.flatnonzero(np.asarray(ep["status"]) == 0)
    to_kept = {int(e): i for i, e in enumerate(kept)}
    masked = {(to_kept[e], c) for e, c in zip(ep["masked"]["epoch"], ep["masked"]["channel"], strict=True) if e in to_kept}
    out = E.compute_run_features(res["processed"].get_data() * 1e6, res["sfreq_hz"], res["processed"].ch_names,
                                 np.asarray(ep["onset_s"])[kept], ep["length_s"], masked)
    return res, out


def _occ_alpha(out):
    v = {(r["level"], r["name"], r["feature"], r["band"]): r["mean"] for r in out["rows"]}
    return np.mean([v[("channel", c, "rel_power", "alpha")] for c in ("O1", "Oz", "O2")])


@pytest.mark.skipif(not (DATA / "S005" / "S005R02.edf").exists(), reason="EEGMMIDB dataset not available")
def test_real_eyes_closed_increases_occipital_alpha_in_most_subjects():
    """Berger effect as a physiological sanity check. EXPECTATION FIXED BEFORE LOOKING AT THE DATA: in at least 4 of 5
    subjects, occipital relative alpha is higher with eyes closed (run R02) than eyes open (R01). Not a validation of
    the pipeline, a check that nothing is grossly wrong."""
    gains = []
    for s in ("S001", "S002", "S003", "S004", "S005"):
        _, eo = _real(s, "R01")
        _, ec = _real(s, "R02")
        gains.append(_occ_alpha(ec) - _occ_alpha(eo))
    print("eyes-closed minus eyes-open occipital relative alpha:", [round(float(g), 3) for g in gains])
    assert sum(g > 0 for g in gains) >= 4, gains


@pytest.mark.skipif(not (DATA / "S001" / "S001R02.edf").exists(), reason="EEGMMIDB dataset not available")
def test_real_band_power_agrees_with_an_independent_multitaper_estimate():
    """TOLERANCE FIXED IN ADVANCE: relative band power from our per-epoch Hann periodograms must be within 0.03 (absolute,
    averaged over channels) of an independent MNE multitaper estimate on the same clean epochs (per-epoch relative power, then mean over epochs on both sides), and the channel ranking
    must agree (Spearman >= 0.9) for alpha and beta."""
    import mne
    from scipy.stats import spearmanr

    from app.eeg.features.catalog import band_edges

    res, out = _real("S001", "R02")
    data = res["processed"].get_data() * 1e6
    ep = res["epochs"]
    sf, L = res["sfreq_hz"], int(round(ep["length_s"] * res["sfreq_hz"]))
    kept = [int(round(o * sf)) for o, s in zip(ep["onset_s"], ep["status"], strict=True) if s == 0]
    X = np.stack([data[:, s:s + L] for s in kept])
    psd, f = mne.time_frequency.psd_array_multitaper(X, sf, fmin=1.0, fmax=45.0, bandwidth=2.0, verbose=False)  # (epochs, ch, freqs)
    bands = band_edges(sf)
    tot = psd[..., f < max(hi for _, hi in bands.values())].sum(axis=-1)  # like-for-like: same [1, 45) total as our definition
    v = {(r["name"], r["band"]): r["mean"] for r in out["rows"] if r["level"] == "channel" and r["feature"] == "rel_power"}
    names = res["processed"].ch_names
    for b, (lo, hi) in bands.items():
        mt = (psd[..., (f >= lo) & (f < hi)].sum(axis=-1) / tot).mean(axis=0)  # per-epoch ratio, then mean over epochs (as ours)
        ours = np.array([v[(n, b)] for n in names])
        assert np.mean(np.abs(ours - mt)) < 0.03, (b, float(np.mean(np.abs(ours - mt))))
        if b in ("alpha", "beta"):
            assert spearmanr(ours, mt).statistic >= 0.9, b
