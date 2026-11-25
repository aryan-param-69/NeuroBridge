# ruff: noqa: E501
"""Fill a fresh install with ten clearly fictional demo patients, 22 recordings and signed reports.

The EEG comes from PUBLIC datasets (built by scripts/make_demo_edfs.py): healthy volunteers recorded awake, and healthy volunteers recorded
before, during and after propofol sedation with MEASURED plasma propofol levels (Cambridge dataset, CC BY 2.0 UK). The patient names, birth
dates and MRNs are invented placeholders. Nothing here is patient data and the sedation volunteers are part of the sedation model's training
data, so the model output on them is a demonstration, not a test.

Everything goes through the real HTTP API of the running server (localhost:8000), so validation, encryption, de-identification and the audit log behave exactly as
for a user. Everything is authored by a dedicated account, "Demo Seeder (script)", whose password is random and discarded, so nobody can log in
as it and nothing is attributed to a real person. QC approvals carry a note saying they were made by this script, not by a human review.
Real clinicians are then added to each patient's care team (--grant) so they can open the cases.

Usage (inside the API container, from /srv):
    python -m scripts.seed_demo_cases seed  --grant clinician@example.com [--dir /srv/demo_cases] [--only 3]
    python -m scripts.seed_demo_cases grant someone@example.com            # add a clinician/technologist/viewer to every demo patient
    python -m scripts.seed_demo_cases reset                                 # remove the demo patients (the audit log is kept)
"""

import hashlib
import json as _json
import os
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from app import storage
from app.core.security import create_token, hash_password
from app.db.mongo import get_db
from app.services import users as users_repo

BASE = os.environ.get("SEED_API", "http://localhost:8000")
API = "/api/v1"
NOTE_QC = "Approved by the demo seeding script for demonstration, not by a manual review of the signal."
DISCLOSURE = ("DEMO CASE: fictional patient identity; the EEG is a public-dataset recording of a healthy volunteer. ")

# kind "cam": Cambridge sedation volunteer (subject index, recording levels); kind "mmi": PhysioNet awake volunteer (subject, conditions)
CASES = [
    dict(first="Alpha", kind="cam", subject=0, levels=[1, 2, 3, 4]),
    dict(first="Bravo", kind="cam", subject=1, levels=[1, 2, 3, 4]),
    dict(first="Charlie", kind="cam", subject=2, levels=[1, 3]),
    dict(first="Delta", kind="cam", subject=3, levels=[1, 3, 4]),
    dict(first="Echo", kind="mmi", subject=1, conds=["closed", "open"]),
    dict(first="Foxtrot", kind="mmi", subject=2, conds=["closed", "open"]),
    dict(first="Golf", kind="mmi", subject=3, conds=["closed", "open"]),
    dict(first="Hotel", kind="mmi", subject=4, conds=["closed"]),
    dict(first="India", kind="mmi", subject=5, conds=["closed"]),
    dict(first="Juliet", kind="mmi", subject=6, conds=["closed"]),
]
LEVEL_NAME = {1: "baseline", 2: "mild", 3: "moderate", 4: "recovery"}


class Resp:
    def __init__(self, status: int, body: bytes):
        self.status_code, self.content = status, body
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return _json.loads(self.content)


class TestClient:  # minimal HTTP client over the running API (standard library only)
    def __init__(self):
        self.headers: dict[str, str] = {}

    def _send(self, method: str, path: str, data: bytes | None, ctype: str | None) -> Resp:
        h = dict(self.headers)
        if ctype:
            h["Content-Type"] = ctype
        req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return Resp(r.status, r.read())
        except urllib.error.HTTPError as e:
            return Resp(e.code, e.read())

    def get(self, path):
        return self._send("GET", path, None, None)

    def post(self, path, json=None):  # noqa: A002
        return self._send("POST", path, None if json is None else _json.dumps(json).encode(), "application/json")

    def put(self, path, json=None, content=None):  # noqa: A002
        if content is not None:
            return self._send("PUT", path, content, "application/octet-stream")
        return self._send("PUT", path, _json.dumps(json).encode(), "application/json")


def wait_job(c: TestClient, job_id: str, what: str, timeout: int = 900) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        j = ok(c.get(f"{API}/jobs/{job_id}"), f"{what} status")
        if j["status"] in ("succeeded", "failed"):
            if j["status"] == "failed":
                raise RuntimeError(f"{what} failed: {j.get('error')}")
            return j
        time.sleep(2)
    raise RuntimeError(f"{what} timed out")


SEEDER_EMAIL = "demo-seeder@example.com"


def seeder_client() -> tuple[TestClient, object]:
    """A dedicated clinician account that nobody can sign in to (random password, discarded)."""
    db = get_db()
    user = users_repo.get_by_email(db, SEEDER_EMAIL)
    if user is None:
        user = users_repo.create(db, email=SEEDER_EMAIL, full_name="Demo Seeder (script)",
                                 password_hash=hash_password(secrets.token_urlsafe(48) + "Aa1"), roles=["clinician"])
        print(f"created the {SEEDER_EMAIL} account (no usable password)")
    c = TestClient()
    c.headers["Authorization"] = "Bearer " + create_token(str(user.id), "access", 240)
    return c, user


def ok(r, what: str, allow=()) -> dict:
    if r.status_code >= 400 and r.status_code not in allow:
        raise RuntimeError(f"{what}: HTTP {r.status_code} {r.text[:400]}")
    return r.json() if r.content else {}


def upload(c: TestClient, patient_id: str, path: Path) -> dict:
    data = path.read_bytes()
    init = ok(c.post(f"{API}/patients/{patient_id}/recordings/uploads", json={"filename": path.name, "size": len(data)}), "upload start")
    size = init["chunk_size"]
    for i in range(init["n_chunks"]):
        ok(c.put(f"{API}/uploads/{init['upload_id']}/chunks/{i}", content=data[i * size:(i + 1) * size]), f"chunk {i}")
    return ok(c.post(f"{API}/uploads/{init['upload_id']}/complete",
                     json={"sha256": hashlib.sha256(data).hexdigest(), "confirm_identity_mismatch": True}), "upload complete")


def condition_for(case: dict, key) -> tuple[dict, str]:
    """(condition body, short label) for one recording."""
    if case["kind"] == "cam":
        level, ugl = key
        state = {1: "awake", 2: "sedated", 3: "sedated", 4: "drowsy"}[level]
        body = dict(condition="resting_unknown", patient_state=state, setting="lab", electrode_system="10-20",
                    device_model="Cambridge propofol dataset (19 standard channels used, 250 Hz)", reference_label="average reference",
                    sedation=[], anti_seizure_meds=[],
                    notes=DISCLOSURE + f"Session stage: {LEVEL_NAME[level]}; measured plasma propofol {ugl:.0f} ug/L. "
                          "The authors' cleaned 10 s epochs are joined end to end, so the signal has small discontinuities every 10 s.")
        if state == "sedated":
            body["sedation"] = [dict(agent="propofol", dose=float(ugl), unit="ug/L plasma", time="target-controlled infusion")]
        return body, LEVEL_NAME[level]
    name = "eyes closed" if key == "closed" else "eyes open"
    body = dict(condition="resting_eyes_closed" if key == "closed" else "resting_eyes_open", patient_state="awake", setting="lab",
                electrode_system="10-10", device_model="BCI2000, 64 channels, 160 Hz (PhysioNet EEGMMIDB)", reference_label="as recorded",
                sedation=[], anti_seizure_meds=[], notes=DISCLOSURE + f"One-minute resting baseline, {name}.")
    return body, name


def summary_for(case: dict, rows: list[dict]) -> str:
    """Descriptive text only: what was recorded and under what conditions. The platform never interprets for the clinician."""
    if case["kind"] == "cam":
        levels = ", ".join(f"{r['label']} ({r['ugl']:.0f} ug/L)" for r in rows)
        return (DISCLOSURE + f"{len(rows)} resting EEG recording(s) from one volunteer during a propofol sedation session: {levels}. "
                "Plasma propofol levels are the measured values published with the dataset. The sedation model was trained on this volunteer's "
                "recordings (among 40 people), so its output below illustrates the report format and is not evidence of accuracy. "
                "QC approvals were made by the demo seeding script.")
    labels = ", ".join(r["label"] for r in rows)
    return (DISCLOSURE + f"{len(rows)} one-minute resting EEG recording(s), awake: {labels}. 64-channel recording from another laboratory; the "
            "sedation model was not trained on this montage, so an 'unfamiliar EEG' warning and an uncertain output are the expected, correct "
            "behaviour here. QC approvals were made by the demo seeding script.")


def seed_case(c: TestClient, user, idx: int, case: dict, folder: Path) -> dict:
    db = get_db()
    name = f"{case['first']} Demo"
    mrn = f"DEMO-{idx + 1:03d}"
    if db.patients.find_one({"tags": "demo-seed", "clinical_status_label": f"Demo case {idx + 1}"}, {"_id": 1}):
        print(f"[{idx + 1}] exists: {name}")
        return {}
    p = ok(c.post(f"{API}/patients", json=dict(
        full_name=name, date_of_birth="1980-01-01", sex="unknown", mrn=mrn, etiology="unknown", consent_clinical=True, consent_research=True,
        clinical_status_label=f"Demo case {idx + 1}", tags=["demo", "demo-seed", "public-dataset"], confirm_duplicate=True)), "create patient")
    pid = p["id"]
    print(f"[{idx + 1}] {p['display_id']} {name}")

    rows: list[dict] = []
    for key, path in files_for(case, folder):
        cond, label = condition_for(case, key)
        rec = upload(c, pid, path)
        rid = rec["id"]
        ok(c.put(f"{API}/recordings/{rid}/condition", json=cond), "condition")
        started = ok(c.post(f"{API}/recordings/{rid}/runs", json={"pipeline_name": "resting"}), "start run")
        run_id = started["run_id"]
        wait_job(c, started["job_id"], "processing")
        run = ok(c.get(f"{API}/runs/{run_id}"), "run status")
        if run["status"] != "succeeded":
            print(f"    {rec['recording_code']}: processing {run['status']} ({run.get('error')}); skipped the rest for this recording")
            continue
        ok(c.post(f"{API}/runs/{run_id}/qc", json={"decision": "approve", "note": NOTE_QC}), "qc")
        feat = ok(c.post(f"{API}/runs/{run_id}/features"), "features")
        wait_job(c, feat["job_id"], "features")
        ai = c.post(f"{API}/runs/{run_id}/ai/sedation")
        ai_note = "model ran"
        if ai.status_code < 300:
            try:
                wait_job(c, ai.json()["job_id"], "model")
            except RuntimeError as e:
                ai_note = f"model: {str(e)[:80]}"
        else:
            ai_note = f"model HTTP {ai.status_code}"
        print(f"    {rec['recording_code']} {label}: grade {run.get('grade')}, features ok, {ai_note}")
        rows.append(dict(rid=rid, run_id=run_id, label=label, ugl=key[1] if case["kind"] == "cam" else 0))
    if not rows:
        return {"patient": pid}

    if case["kind"] == "cam":
        ok(c.post(f"{API}/recordings/{rows[0]['rid']}/baseline", json=dict(is_baseline=True, reason="Awake recording before the infusion")), "baseline")
    opts = dict(title=f"EEG summary: {name} (demo)", clinical_summary=summary_for(case, rows), include_assessments=False,
                include_recordings=True, include_ai=True)
    if len(rows) >= 2 and case["kind"] == "cam":
        opts.update(base_run=rows[0]["run_id"], target_run=rows[-1]["run_id"])
    r = c.post(f"{API}/patients/{pid}/reports", json=opts)
    if r.status_code >= 400 and "base_run" in opts:  # fall back to a report without the comparison
        print(f"    comparison not possible ({r.status_code}): {r.text[:150]}")
        opts.pop("base_run"), opts.pop("target_run")
        r = c.post(f"{API}/patients/{pid}/reports", json=opts)
    rep = ok(r, "create report")
    ok(c.post(f"{API}/reports/{rep['id']}/sign", json={}), "sign report")
    print(f"    report signed: {rep['title']}")
    return {"patient": pid}


def files_for(case: dict, folder: Path):
    if case["kind"] == "cam":
        for lvl in case["levels"]:
            f = next(folder.glob(f"cam_s{case['subject']}_L{lvl}_*.edf"))
            ugl = float(f.stem.rsplit("_", 1)[1].removesuffix("ugL"))
            yield (lvl, ugl), f
    else:
        for cond in case["conds"]:
            yield cond, folder / f"mmi_s{case['subject']}_eyes_{'closed' if cond == 'closed' else 'open'}.edf"


def cmd_seed(argv: list[str]) -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--grant", default="", help="comma-separated e-mails of clinicians to add to every demo patient's care team")
    ap.add_argument("--dir", default="/srv/demo_cases")
    ap.add_argument("--only", type=int, help="seed just this case number (1-10)")
    a = ap.parse_args(argv)
    c, user = seeder_client()
    for i, case in enumerate(CASES):
        if a.only and a.only != i + 1:
            continue
        try:
            seed_case(c, user, i, case, Path(a.dir))
        except Exception as e:  # keep going: one failed case should not stop the others
            print(f"[{i + 1}] FAILED: {e}")
    for email in filter(None, (x.strip() for x in a.grant.split(","))):
        cmd_grant([email])


def demo_patients(db) -> list[dict]:
    return list(db.patients.find({"tags": "demo-seed"}, {"_id": 1, "care_team": 1, "display_id": 1}))


def cmd_grant(argv: list[str]) -> None:
    db = get_db()
    user = users_repo.get_by_email(db, argv[0].lower())
    if user is None:
        sys.exit(f"No such user: {argv[0]}")
    if not set(user.role_names) & {"clinician", "eeg_technologist", "viewer"}:
        sys.exit(f"{argv[0]} has roles {user.role_names}: only clinician, eeg_technologist or viewer accounts can join a care team")
    n = 0
    for p in demo_patients(db):
        if any(m["user_id"] == user.id for m in p["care_team"]):
            continue
        owner = p["care_team"][0]["user_id"]
        db.patients.update_one({"_id": p["_id"]}, {"$push": {"care_team": {
            "user_id": user.id, "access_level": "owner", "granted_by": owner, "granted_at": datetime.now(UTC)}}})
        n += 1
    print(f"added {argv[0]} to {n} demo patient(s)")


def cmd_reset(_: list[str]) -> None:
    db = get_db()
    pats = demo_patients(db)
    skip = {"audit_log", "patients", "patient_identifiers", "counters"}
    for p in pats:
        pid = p["_id"]
        for name in db.list_collection_names():
            if name in skip:
                continue
            db[name].delete_many({"patient_id": pid})
        db.patient_identifiers.delete_one({"_id": pid})
        db.patients.delete_one({"_id": pid})
        db.counters.delete_one({"_id": f"recording:{pid}"})
        for sub in ("raw", "deid", "derived"):
            shutil.rmtree(storage.root() / sub / str(pid), ignore_errors=True)
    print(f"removed {len(pats)} demo patient(s)")


if __name__ == "__main__":
    cmds = {"seed": cmd_seed, "grant": cmd_grant, "reset": cmd_reset}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]](sys.argv[2:])
