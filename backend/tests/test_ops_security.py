"""Operations and security basics: rate limiting, request ids, readiness, key rotation, backup and restore."""

import gzip
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from starlette.requests import Request

from app import storage
from app.core.config import get_settings
from app.core.ratelimit import SlidingWindowLimiter, client_ip
from app.core.security import decrypt_secret, encrypt_secret, is_under_current_key, rotate_secret
from app.db.mongo import get_client, get_db
from app.main import app
from scripts import backup as backup_mod
from scripts import restore as restore_mod
from scripts import rotate_keys as rotate_mod


def req(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234), "method": "GET", "path": "/", "query_string": b""})


# ---------------------------------------------------------------- rate limiting
def test_sliding_window_blocks_then_recovers():
    lim = SlidingWindowLimiter(window_s=60)
    assert [lim.check("a", 3, now=t) for t in (0, 1, 2)] == [None, None, None]
    wait = lim.check("a", 3, now=3)
    assert wait is not None and 1 <= wait <= 61  # fourth request inside the window is refused with a wait time
    assert lim.check("b", 3, now=3) is None  # another address is unaffected
    assert lim.check("a", 3, now=61) is None  # the oldest hit has left the window


def test_client_ip_ignores_forged_forwarded_entries():
    assert client_ip(req("10.0.0.9", "1.2.3.4"), trusted_proxies=0) == "10.0.0.9"  # header ignored without a trusted proxy
    assert client_ip(req("172.18.0.3", "198.51.100.7"), trusted_proxies=1) == "198.51.100.7"
    assert client_ip(req("172.18.0.3", "6.6.6.6, 198.51.100.7"), trusted_proxies=1) == "198.51.100.7"  # a forged leading entry does not win
    assert client_ip(req("172.18.0.3", "198.51.100.7, 172.18.0.2"), trusted_proxies=2) == "198.51.100.7"
    assert client_ip(req("172.18.0.3"), trusted_proxies=1) == "172.18.0.3"  # no header: fall back to the peer


def test_login_is_rate_limited_per_address_and_429_keeps_security_headers(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_enabled", True)
    monkeypatch.setattr(s, "rate_limit_auth_per_minute", 3)
    app.state.limiter.reset()
    try:
        body = {"email": "nobody@example.com", "password": "Wrong-Password-1"}
        codes = [client.post("/api/v1/auth/login", json=body).status_code for _ in range(4)]
        assert codes[:3] == [401, 401, 401] and codes[3] == 429
        r = client.post("/api/v1/auth/login", json=body)
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
        assert r.headers["X-Content-Type-Options"] == "nosniff" and "X-Request-ID" in r.headers
        assert client.get("/api/v1/health").status_code == 200  # health checks are never limited
    finally:
        app.state.limiter.reset()


# ---------------------------------------------------------------- request ids and readiness
def test_request_id_is_echoed_when_valid_and_replaced_when_not(client):
    assert client.get("/api/v1/health", headers={"X-Request-ID": "trace-abc12345"}).headers["X-Request-ID"] == "trace-abc12345"
    bad = client.get("/api/v1/health", headers={"X-Request-ID": "x y\"<script>"}).headers["X-Request-ID"]
    assert bad != "x y\"<script>" and len(bad) == 32


def test_readiness_reports_checks_without_details(client):
    r = client.get("/api/v1/health/ready")
    j = r.json()
    assert set(j["checks"]) == {"database", "storage", "model"}
    assert j["checks"]["database"] == "ok" and set(j["checks"].values()) <= {"ok", "failed"}
    assert (r.status_code == 200) == (j["status"] == "ready")


def test_readiness_is_503_when_the_model_is_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "model_dir", str(tmp_path))
    r = client.get("/api/v1/health/ready")
    assert r.status_code == 503 and r.json()["checks"]["model"] == "failed" and r.json()["status"] == "degraded"


# ---------------------------------------------------------------- key rotation
def test_identifier_key_rotation(monkeypatch):
    s = get_settings()
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(s, "encryption_key", old)
    token = encrypt_secret("Jane Doe")
    monkeypatch.setattr(s, "encryption_key", new)
    monkeypatch.setattr(s, "encryption_key_previous", old)
    assert decrypt_secret(token) == "Jane Doe"  # old data stays readable while the old key is listed
    assert not is_under_current_key(token)
    rotated = rotate_secret(token)
    assert is_under_current_key(rotated) and decrypt_secret(rotated) == "Jane Doe"
    monkeypatch.setattr(s, "encryption_key_previous", "")
    assert decrypt_secret(rotated) == "Jane Doe"  # after rotation the old key can be dropped
    with pytest.raises(Exception):  # noqa: B017  (cryptography raises InvalidToken)
        decrypt_secret(token)


def test_rotate_keys_script_reencrypts_identifiers_and_files(monkeypatch, tmp_path):
    s = get_settings()
    db = get_db()
    monkeypatch.setattr(s, "storage_dir", str(tmp_path))
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(s, "encryption_key", old)
    pid = uuid.uuid4()
    db.patient_identifiers.insert_one({"_id": pid, "full_name_enc": encrypt_secret("Jane Doe"), "mrn_enc": None})
    src = tmp_path / "plain.bin"
    payload = b"EEG" * 2_000_000  # larger than one block boundary is not needed; several MB exercises chunking
    src.write_bytes(payload)
    enc = storage.raw_path(uuid.uuid4(), uuid.uuid4())
    storage.encrypt_file(src, enc)

    monkeypatch.setattr(s, "encryption_key", new)
    monkeypatch.setattr(s, "encryption_key_previous", old)
    assert rotate_mod.rotate_identifiers(db, apply=False) == (1, 1) and rotate_mod.rotate_files(apply=False) == (1, 1)  # dry run counts only
    assert not is_under_current_key(bytes(db.patient_identifiers.find_one({"_id": pid})["full_name_enc"]))
    assert rotate_mod.rotate_identifiers(db, apply=True) == (1, 1) and rotate_mod.rotate_files(apply=True) == (1, 1)
    assert rotate_mod.rotate_identifiers(db, apply=False) == (0, 1) and rotate_mod.rotate_files(apply=False) == (0, 1)

    monkeypatch.setattr(s, "encryption_key_previous", "")  # drop the old key: everything must still be readable
    assert decrypt_secret(bytes(db.patient_identifiers.find_one({"_id": pid})["full_name_enc"])) == "Jane Doe"
    assert b"".join(storage.decrypt_stream(enc)) == payload


def test_file_with_no_matching_key_is_refused(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "storage_dir", str(tmp_path))
    monkeypatch.setattr(s, "encryption_key", Fernet.generate_key().decode())
    src = tmp_path / "p.bin"
    src.write_bytes(b"secret")
    enc = tmp_path / "x.nbe"
    storage.encrypt_file(src, enc)
    monkeypatch.setattr(s, "encryption_key", Fernet.generate_key().decode())  # a different key, old one not listed
    with pytest.raises(storage.StorageError):
        b"".join(storage.decrypt_stream(enc))


# ---------------------------------------------------------------- backup and restore
def test_backup_restore_roundtrip_with_checksums(monkeypatch, tmp_path):
    s = get_settings()
    db = get_db()
    store = tmp_path / "storage"
    monkeypatch.setattr(s, "storage_dir", str(store))
    (store / "raw" / "p1").mkdir(parents=True)
    (store / "raw" / "p1" / "r1.nbe").write_bytes(b"encrypted-bytes")
    doc = {"_id": uuid.uuid4(), "when": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "blob": b"\x00\x01\xff", "nested": {"n": [1, 2.5, None, "x"]}}
    db.ops_roundtrip.insert_one(doc)

    dest = backup_mod.backup(tmp_path / "out")
    manifest = json.loads((dest / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["collections"]["ops_roundtrip"] == 1 and "files.tar.gz" in manifest["files"] and "db/ops_roundtrip.jsonl.gz" in manifest["files"]
    assert not any("key" in str(v).lower() for v in manifest["files"])  # no key material in the backup listing

    target = "neurobridge_test_restore"
    get_client().drop_database(target)
    try:
        out = tmp_path / "restored_storage"
        counts = restore_mod.restore(dest, target, files_to=out)
        assert counts["ops_roundtrip"] == 1
        back = get_client()[target].ops_roundtrip.find_one({"_id": doc["_id"]})
        assert back == doc  # UUID, timezone-aware date, binary and nested values all survive
        assert (out / "raw" / "p1" / "r1.nbe").read_bytes() == b"encrypted-bytes"
        with pytest.raises(restore_mod.RestoreError):  # refuses a non-empty target without --force
            restore_mod.restore(dest, target)
    finally:
        get_client().drop_database(target)


def test_restore_refuses_a_tampered_backup(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path / "storage"))
    get_db().ops_roundtrip.insert_one({"_id": 1, "v": "a"})
    dest = backup_mod.backup(tmp_path / "out", include_files=False)
    f = Path(dest / "db" / "ops_roundtrip.jsonl.gz")
    f.write_bytes(gzip.compress(b'{"_id": 1, "v": "tampered"}\n'))
    with pytest.raises(restore_mod.RestoreError, match="checksum mismatch"):
        restore_mod.restore(dest, "neurobridge_test_restore")
    assert "neurobridge_test_restore" not in get_client().list_database_names()  # nothing was written
