import os

os.environ["ENVIRONMENT"] = "test"
os.environ["RATE_LIMIT_ENABLED"] = "false"  # the suite logs in hundreds of times from one address; test_ops_security.py turns it on explicitly
# Tests run against a REAL MongoDB, in a database that is dropped before every test.
os.environ["MONGO_DB"] = "neurobridge_test"
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.mongo import ensure_indexes, get_client, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ROLE_NAMES  # noqa: E402
from app.services import users as users_repo  # noqa: E402

PASSWORD = "Correct-Horse-9-Battery"
TEST_DB = "neurobridge_test"


@pytest.fixture(scope="session", autouse=True)
def _require_mongodb():
    assert get_settings().mongo_db == TEST_DB
    try:
        get_client().admin.command("ping")
    except PyMongoError as e:
        pytest.exit(f"MongoDB is not reachable at {get_settings().mongo_url}: {e}", returncode=2)


@pytest.fixture(autouse=True)
def fresh_db():
    db = get_db()
    assert db.name == TEST_DB  # never drop anything else on the server
    get_client().drop_database(TEST_DB)
    ensure_indexes(db)
    yield db


@pytest.fixture
def client():
    return TestClient(app)


def make_user(role: str, email: str | None = None) -> dict:
    email = email or f"{role}@example.com"
    db = get_db()
    u = users_repo.create(db, email=email, full_name=role.title(), password_hash=hash_password(PASSWORD),
                          roles=[role])
    return {"id": u.id, "email": email}


def login(client: TestClient, user: dict) -> str:
    r = client.post("/api/v1/auth/login", json={"email": user["email"], "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tokens(client):
    """One logged-in user per role: {role: token}."""
    return {role: login(client, make_user(role)) for role in ROLE_NAMES}
