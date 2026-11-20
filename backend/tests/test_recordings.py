"""Phase 2: EDF upload, validation, de-identification, encryption at rest and viewer data.

EDF files here are generated with known content (labelled synthetic; test fixtures only, never evidence).
"""

import datetime as dt
import hashlib
import io
import uuid
from pathlib import Path

import mne
import numpy as np
import pytest
from edfio import Edf, EdfSignal, Patient, Recording

from app import storage
from app.core.config import get_settings
from app.db.mongo import get_db
from tests.test_patients import H, payload, register, share, team  # noqa: F401  (fixture re-export)

API = "/api/v1"
FS = 250
SECONDS = 130
LABELS = ["EEG Fp1-REF", "EEG Fp2-REF", "EEG F3-REF", "EEG F4-REF", "EEG C3-REF", "EEG C4-REF",
          "EEG P3-REF", "EEG P4-REF", "EEG O1-REF", "EEG O2-REF", "EEG T3-REF", "EEG T4-REF"]
NAMES = ["Fp1", "Fp2", "F3", "F4", "C3", "C4", "P3", "P4", "O1", "O2", "T7", "T8"]
REAL_EDF = Path(__file__).resolve().parents[2] / "data" / "eegmmidb" / "files" / "S001" / "S001R04.edf"


def make_edf(patient_name="Doe_Jane", birthdate=dt.date(1972, 4, 10), seconds=SECONDS, flat="EEG T4-REF",
             noisy="EEG O2-REF", seed=0) -> bytes:
    rng = np.random.default_rng(seed)
    t = np.arange(0, seconds, 1 / FS)
    sigs = []
    for lab in LABELS:
        if lab == flat:
            x = np.zeros_like(t)
        elif lab == noisy:
            x = rng.normal(0, 400, t.size)
        else:
            x = 50 * np.sin(2 * np.pi * 10 * t) + rng.normal(0, 5, t.size)  # 10 Hz "alpha" 50 uV + noise
        sigs.append(EdfSignal(x, FS, label=lab, physical_dimension="uV", physical_range=(-3200, 3200),
                              prefiltering="HP:0.5Hz LP:70Hz"))
    edf = Edf(sigs, patient=Patient(code="X", sex="F", birthdate=birthdate, name=patient_name),
              recording=Recording(startdate=dt.date(2026, 3, 1), hospital_administration_code="X",
                                  investigator_technician_code="X", equipment_code="Dev1"),
              starttime=dt.time(9, 30, 0))
    buf = io.BytesIO()
    path = Path(storage.root()) / f"_gen_{uuid.uuid4().hex}.edf"
    edf.write(path)
    buf.write(path.read_bytes())
    path.unlink()
    return buf.getvalue()


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "storage_dir", str(tmp_path))
    monkeypatch.setattr(s, "chunk_size", 100_000)  # force multi-chunk uploads in tests
    return tmp_path


def start(client, team, who, pid, size, name="x.edf"):
    return client.post(f"{API}/patients/{pid}/recordings/uploads", json={"filename": name, "size": size},
                       headers=H(team, who))


def upload(client, team, who, pid, data: bytes, *, confirm=False, sha=None):
    h = H(team, who)
    r = start(client, team, who, pid, len(data))
    assert r.status_code == 201, r.text
    up = r.json()
    cs = up["chunk_size"]
    for i in range(up["n_chunks"]):
        rr = client.put(f"{API}/uploads/{up['upload_id']}/chunks/{i}", content=data[i * cs:(i + 1) * cs], headers=h)
        assert rr.status_code == 204, rr.text
    return client.post(f"{API}/uploads/{up['upload_id']}/complete", headers=h, json={
        "sha256": sha or hashlib.sha256(data).hexdigest(), "confirm_identity_mismatch": confirm})


@pytest.fixture
def patient(client, team):  # noqa: F811
    p = register(client, team, full_name="Jane Doe", mrn=None)
    share(client, team, p["id"], "tech", "viewer")
    return p


@pytest.fixture
def rec(client, team, patient):  # noqa: F811
    r = upload(client, team, "clin_a", patient["id"], make_edf())
    assert r.status_code == 201, r.text
    return r.json()


# ---- Upload happy path, normalisation, de-identification, encryption ------------------------------------

def test_upload_parses_normalises_and_flags(rec):
    assert rec["recording_code"].endswith("-R01") and rec["file_format"] == "EDF"
    assert rec["sfreq_hz"] == FS and rec["n_eeg_channels"] == 12 and round(rec["duration_s"]) == SECONDS
    assert [c["name"] for c in rec["channels"]] == NAMES  # "EEG T3-REF" -> T7 (legacy name), prefixes stripped
    assert all(c["ch_type"] == "eeg" and c["has_position"] for c in rec["channels"])
    flags = {c["name"]: c["auto_flag"] for c in rec["channels"]}
    assert flags["T8"] == "flat" and flags["O2"] == "noisy" and flags["Fp1"] == "ok"
    assert rec["identity_check"] == "match"
    assert any("flat" in w for w in rec["warnings"]) and any("noisy" in w for w in rec["warnings"])
    assert set(rec["bad_channels"]) == {"T8", "O2"}


def test_original_is_encrypted_and_deidentified_copy_has_no_identity(client, team, patient, rec):
    data = make_edf()
    raw_file = storage.raw_path(uuid.UUID(patient["id"]), uuid.UUID(rec["id"]))
    deid_file = storage.deid_path(uuid.UUID(patient["id"]), uuid.UUID(rec["id"]))
    blob = raw_file.read_bytes()
    assert blob[:4] == b"NBE1" and b"Doe_Jane" not in blob and data[8:40] not in blob  # unreadable at rest
    assert hashlib.sha256(b"".join(storage.decrypt_stream(raw_file))).hexdigest() == hashlib.sha256(data).hexdigest()
    head = deid_file.read_bytes()[:256]
    assert head[8:88].decode().strip() == "X X X X"
    assert b"Doe" not in head and b"APR-1972" not in head and b"Jane" not in head
    raw = mne.io.read_raw_edf(deid_file, preload=False, verbose="ERROR")  # still a valid, readable EDF
    assert len(raw.ch_names) == 12 and raw.info["sfreq"] == FS


def test_filename_and_header_text_are_never_stored(client, team, patient):
    r = start(client, team, "clin_a", patient["id"], 1000, name="DOE_JANE_secret.edf")
    assert r.status_code == 201
    upload(client, team, "clin_a", patient["id"], make_edf())
    db = get_db()
    dump = " ".join(repr(list(db[c].find())) for c in ("upload_sessions", "recordings", "audit_log"))
    assert "DOE_JANE" not in dump and "secret" not in dump and "Doe_Jane" not in dump


def test_recording_codes_increment_and_list_is_ordered(client, team, patient):
    a = upload(client, team, "clin_a", patient["id"], make_edf(seed=1)).json()
    b = upload(client, team, "clin_a", patient["id"], make_edf(seed=2)).json()
    assert a["recording_code"].endswith("-R01") and b["recording_code"].endswith("-R02")
    lst = client.get(f"{API}/patients/{patient['id']}/recordings", headers=H(team, "viewer")).json()
    assert len(lst) == 2 and "channels" not in lst[0]


# ---- Integrity and validation ------------------------------------------------------------------------

def test_checksum_mismatch_is_rejected_and_nothing_is_kept(client, team, patient):
    r = upload(client, team, "clin_a", patient["id"], make_edf(), sha="0" * 64)
    assert r.status_code == 400 and "Checksum" in r.json()["detail"]
    assert get_db().recordings.count_documents({}) == 0
    assert not list((Path(get_settings().storage_dir) / "tmp").glob("*/*"))  # staging cleaned up


def test_exact_duplicate_is_rejected(client, team, patient):
    data = make_edf()
    assert upload(client, team, "clin_a", patient["id"], data).status_code == 201
    r = upload(client, team, "clin_a", patient["id"], data)
    assert r.status_code == 409 and "already uploaded" in r.json()["detail"]["message"]


@pytest.mark.parametrize("data", [b"this is not an EEG file" * 50, b"0       " + b"\x00" * 400,
                                  make_edf()[:3000]], ids=["text", "bad-header", "truncated"])
def test_invalid_files_are_rejected_with_a_clear_message(client, team, patient, data):
    r = upload(client, team, "clin_a", patient["id"], data)
    assert r.status_code == 422, r.text
    assert get_db().recordings.count_documents({}) == 0


def test_only_edf_extension_and_size_limit(client, team, patient, monkeypatch):
    assert start(client, team, "clin_a", patient["id"], 100, name="notes.pdf").status_code == 422
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1000)
    assert start(client, team, "clin_a", patient["id"], 5000).status_code == 413


def test_chunk_rules_resume_and_ownership(client, team, patient):
    data = make_edf()
    up = start(client, team, "clin_a", patient["id"], len(data)).json()
    uid, cs, h = up["upload_id"], up["chunk_size"], H(team, "clin_a")
    assert client.put(f"{API}/uploads/{uid}/chunks/0", content=b"short", headers=h).status_code == 422
    assert client.put(f"{API}/uploads/{uid}/chunks/99", content=data[:cs], headers=h).status_code == 422
    assert client.put(f"{API}/uploads/{uid}/chunks/0", content=data[:cs], headers=h).status_code == 204
    assert client.get(f"{API}/uploads/{uid}", headers=h).json()["received"] == [0]  # lets a client resume
    # Another user cannot touch this upload.
    assert client.put(f"{API}/uploads/{uid}/chunks/1", content=data[cs:2 * cs], headers=H(team, "tech")).status_code == 404
    # Completing with chunks missing is refused and says which are missing.
    r = client.post(f"{API}/uploads/{uid}/complete", headers=h, json={"sha256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 409 and r.json()["detail"]["missing"][0] == 1


# ---- Identity check -------------------------------------------------------------------------------------

def test_identity_mismatch_blocks_until_confirmed_and_is_recorded(client, team, patient):
    data = make_edf(patient_name="Smith_John")
    r = upload(client, team, "clin_a", patient["id"], data)
    assert r.status_code == 409 and r.json()["detail"]["identity_check"] == "mismatch"
    assert get_db().recordings.count_documents({}) == 0
    r = upload(client, team, "clin_a", patient["id"], data, confirm=True)
    assert r.status_code == 201 and r.json()["identity_check"] == "mismatch_confirmed"


def test_birthdate_mismatch_is_caught_even_when_the_name_matches(client, team, patient):
    r = upload(client, team, "clin_a", patient["id"], make_edf(birthdate=dt.date(1980, 1, 1)))
    assert r.status_code == 409 and any("birth" in x for x in r.json()["detail"]["reasons"])


def test_anonymous_header_is_accepted_as_blank(client, team, patient):
    r = upload(client, team, "clin_a", patient["id"], make_edf(patient_name="X", birthdate=None))
    assert r.status_code == 201 and r.json()["identity_check"] == "blank"


# ---- Access control -----------------------------------------------------------------------------------------

def test_who_may_upload_view_and_download(client, team, patient, rec):
    pid, rid = patient["id"], rec["id"]
    data = make_edf(seed=5)
    assert start(client, team, "viewer", pid, len(data)).status_code == 403
    assert start(client, team, "researcher", pid, len(data)).status_code == 403
    assert start(client, team, "clin_b", pid, len(data)).status_code == 404  # not on the care team: no existence leak
    assert upload(client, team, "tech", pid, data).status_code == 201  # technologists may upload
    for who, code in (("clin_a", 200), ("tech", 200), ("viewer", 200), ("clin_b", 404), ("researcher", 403), ("admin", 403)):
        assert client.get(f"{API}/recordings/{rid}", headers=H(team, who)).status_code == code, who
    assert client.get(f"{API}/patients/{pid}/recordings", headers=H(team, "clin_b")).status_code == 404
    assert client.get(f"{API}/recordings/{rid}/download?variant=original", headers=H(team, "tech")).status_code == 403
    assert client.get(f"{API}/recordings/{rid}/download", headers=H(team, "viewer")).status_code == 403


def test_original_download_is_clinician_only_identical_and_audited(client, team, patient, rec):
    data = make_edf()
    r = client.get(f"{API}/recordings/{rec['id']}/download?variant=original", headers=H(team, "clin_a"))
    assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == hashlib.sha256(data).hexdigest()
    r2 = client.get(f"{API}/recordings/{rec['id']}/download", headers=H(team, "tech"))
    assert r2.status_code == 200 and r2.content[8:88].decode().strip() == "X X X X"
    actions = {a["action"] for a in get_db().audit_log.find({"patient_id": uuid.UUID(patient["id"])})}
    assert {"recording.upload", "recording.download_original", "recording.download_deidentified"} <= actions


def test_tampered_or_truncated_encrypted_file_is_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CHUNK", 1024)
    src, enc = tmp_path / "a.bin", tmp_path / "a.nbe"
    src.write_bytes(np.random.default_rng(0).bytes(5000))
    storage.encrypt_file(src, enc)
    assert b"".join(storage.decrypt_stream(enc)) == src.read_bytes()
    blob = enc.read_bytes()
    flipped = bytearray(blob)
    flipped[200] ^= 1
    for name, bad in (("flip", bytes(flipped)), ("truncate", blob[: len(blob) - 600])):
        (tmp_path / f"{name}.nbe").write_bytes(bad)
        with pytest.raises(storage.StorageError):
            b"".join(storage.decrypt_stream(tmp_path / f"{name}.nbe"))


# ---- Metadata edits -----------------------------------------------------------------------------------------

def test_condition_validation_and_storage(client, team, rec):
    url, h = f"{API}/recordings/{rec['id']}/condition", H(team, "tech")
    base = {"condition": "resting_eyes_closed", "patient_state": "awake", "setting": "icu", "time_of_day": "09:30"}
    assert client.put(url, json={**base, "patient_state": "sedated"}, headers=h).status_code == 422  # sedation unlisted
    assert client.put(url, json={**base, "time_of_day": "25:00"}, headers=h).status_code == 422
    assert client.put(url, json={**base, "condition": "bogus"}, headers=h).status_code == 422
    ok = client.put(url, headers=h, json={**base, "patient_state": "sedated",
                                          "sedation": [{"agent": "propofol", "dose": 2.5, "unit": "mg/kg/h"}]})
    assert ok.status_code == 200 and ok.json()["condition"]["sedation"][0]["agent"] == "propofol"
    assert client.put(url, json=base, headers=H(team, "viewer")).status_code == 403


def test_channel_edits_and_baseline_rules(client, team, rec):
    url = f"{API}/recordings/{rec['id']}/channels"
    r = client.patch(url, headers=H(team, "tech"), json={"channels": [{"index": 0, "user_flag": "bad"}]})
    assert "Fp1" in r.json()["bad_channels"]
    r = client.patch(url, headers=H(team, "tech"), json={"channels": [{"index": 0, "clear_flag": True}]})
    assert "Fp1" not in r.json()["bad_channels"]
    assert client.patch(url, headers=H(team, "tech"), json={"channels": [{"index": 99, "user_flag": "bad"}]}).status_code == 422
    b = f"{API}/recordings/{rec['id']}/baseline"
    assert client.post(b, json={"is_baseline": True}, headers=H(team, "clin_a")).status_code == 422  # reason required
    assert client.post(b, json={"is_baseline": True, "reason": "first stable awake EEG"}, headers=H(team, "tech")).status_code == 403
    ok = client.post(b, json={"is_baseline": True, "reason": "first stable awake EEG"}, headers=H(team, "clin_a"))
    assert ok.json()["is_baseline"] is True


# ---- Viewer data ---------------------------------------------------------------------------------------------

def sig(client, team, rid, who="viewer", **params):
    return client.get(f"{API}/recordings/{rid}/signal", params=params, headers=H(team, who))


def test_signal_window_has_correct_values_and_shape(client, team, rec):
    r = sig(client, team, rec["id"], start=20, duration=4, channels="Fp1,C3")
    assert r.status_code == 200
    j = r.json()
    assert j["channels"] == ["Fp1", "C3"] and j["unit"] == "uV" and not j["decimated"]
    assert len(j["times"]) == 4 * FS == len(j["data"][0])
    assert abs(j["times"][0] - 20) < 1e-6
    peak = np.abs(np.array(j["data"][0])).max()
    assert 48 < peak < 70  # 50 uV sine + 5 uV noise
    assert j["total_duration"] == pytest.approx(SECONDS, abs=1)
    assert sig(client, team, rec["id"], channels="T8").json()["bad_channels"] == ["T8"]
    assert np.abs(np.array(sig(client, team, rec["id"], channels="T8").json()["data"])).max() < 0.2  # flat channel (EDF 16-bit quantisation step is ~0.1 uV)


def test_decimation_keeps_peaks(client, team, rec):
    full = np.array(sig(client, team, rec["id"], start=10, duration=10, channels="Fp1").json()["data"][0])
    dec = sig(client, team, rec["id"], start=10, duration=10, channels="Fp1", max_points=200).json()
    assert dec["decimated"] and len(dec["data"][0]) <= 200 and len(dec["times"]) == len(dec["data"][0])
    d = np.array(dec["data"][0])
    assert d.max() == pytest.approx(full.max(), abs=0.01) and d.min() == pytest.approx(full.min(), abs=0.01)


def test_average_montage_sums_to_zero_over_good_channels(client, team, rec):
    good = [n for n in NAMES if n not in ("T8", "O2")]
    j = sig(client, team, rec["id"], start=15, duration=2, montage="average", channels=",".join(good)).json()
    assert np.abs(np.array(j["data"]).sum(axis=0)).max() < 0.05  # bad channels are excluded from the reference


def test_bipolar_montage_equals_channel_difference(client, team, rec):
    ref = sig(client, team, rec["id"], start=15, duration=2, channels="Fp1,F3").json()["data"]
    bip = sig(client, team, rec["id"], start=15, duration=2, montage="bipolar_longitudinal").json()
    assert "Fp1-F3" in bip["channels"]
    got = np.array(bip["data"][bip["channels"].index("Fp1-F3")])
    assert np.allclose(got, np.array(ref[0]) - np.array(ref[1]), atol=0.02)


def test_signal_parameter_validation(client, team, rec):
    rid = rec["id"]
    for params in ({"duration": 500}, {"duration": 0}, {"start": 9999}, {"channels": "Nope"},
                   {"montage": "weird"}, {"max_points": 5}, {"start": -1}):
        assert sig(client, team, rid, **params).status_code == 422, params
    assert client.get(f"{API}/recordings/{rid}/overview", headers=H(team, "viewer")).json()["bin_s"] >= 1


def test_overview_is_available_and_signal_views_are_audited_once_per_session(client, team, patient, rec):
    ov = client.get(f"{API}/recordings/{rec['id']}/overview", headers=H(team, "viewer")).json()
    assert len(ov["global_sd_uv"]) > 0 and ov["duration_s"] == pytest.approx(SECONDS, abs=1)
    for _ in range(5):
        sig(client, team, rec["id"], who="tech", start=5)
    n = get_db().audit_log.count_documents({"action": "recording.signal_view", "patient_id": uuid.UUID(patient["id"])})
    assert n == 1


# ---- Real public data (skipped when the dataset is not on this machine) ---------------------------------------

@pytest.mark.skipif(not REAL_EDF.exists(), reason="EEGMMIDB dataset not available")
def test_real_physionet_file_ingests(client, team, patient, monkeypatch):
    monkeypatch.setattr(get_settings(), "chunk_size", 8 * 1024 * 1024)
    r = upload(client, team, "clin_a", patient["id"], REAL_EDF.read_bytes())
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["file_format"] == "EDF+C" and j["n_eeg_channels"] == 64 and j["sfreq_hz"] == 160
    assert j["identity_check"] == "blank" and round(j["duration_s"]) == 125
    names = {c["name"] for c in j["channels"]}
    assert {"FC5", "Cz", "O2", "Iz"} <= names and all(c["recognised"] for c in j["channels"])  # "Fc5." -> FC5
    assert sum(j["annotations"].values()) == 30 and set(j["annotations"]) == {"T0", "T1", "T2"}
    w = sig(client, team, j["id"], who="clin_a", start=10, duration=5, montage="average", channels="C3,C4").json()
    assert len(w["data"]) == 2 and len(w["times"]) == 5 * 160
    assert any(a["text"] in ("T1", "T2") for a in sig(client, team, j["id"], who="clin_a", start=0, duration=20).json()["annotations"])
