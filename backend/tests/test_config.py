import pytest

from app.core.config import Settings

SAFE = dict(environment="production", secret_key="x" * 40, encryption_key="k", hmac_key="h",
            cookie_secure=True, mongo_url="mongodb://neurobridge:pw@mongo:27017/?authSource=admin")


def test_production_rejects_insecure_defaults():
    with pytest.raises(RuntimeError):
        Settings(environment="production").validate_for_production()


def test_production_accepts_safe_config():
    Settings(**SAFE).validate_for_production()


def test_production_rejects_unauthenticated_mongodb():
    with pytest.raises(RuntimeError, match="credentials"):
        Settings(**{**SAFE, "mongo_url": "mongodb://mongo:27017"}).validate_for_production()


def test_blind_index_is_keyed_deterministic_and_purpose_separated(monkeypatch):
    from app.core.config import get_settings
    from app.core.security import blind_index

    s = get_settings()
    monkeypatch.setattr(s, "hmac_key", "key-one")
    a = blind_index("doe jane|1972-04-10", "identity")
    assert a == blind_index("doe jane|1972-04-10", "identity") and len(a) == 64 and "doe" not in a  # deterministic hex, no plaintext
    assert a != blind_index("doe jane|1972-04-10", "mrn")  # purposes never collide
    assert a != blind_index("doe jane|1972-04-11", "identity")
    monkeypatch.setattr(s, "hmac_key", "key-two")
    assert blind_index("doe jane|1972-04-10", "identity") != a  # a different key gives unrelated hashes
