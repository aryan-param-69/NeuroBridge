"""Password hashing, JWT tokens and secret encryption."""

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings

_hasher = PasswordHasher()  # Argon2id by default

# A fixed hash used to keep login timing similar when the e-mail does not exist.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and bool(password_hash)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def validate_password_strength(password: str) -> list[str]:
    s = get_settings()
    problems = []
    if len(password) < s.min_password_length:
        problems.append(f"Password must be at least {s.min_password_length} characters")
    if password.lower() == password or password.upper() == password:
        problems.append("Password must mix upper- and lower-case letters")
    if not any(c.isdigit() for c in password):
        problems.append("Password must contain a digit")
    return problems


# ---- JWT -----------------------------------------------------------------

def create_token(subject: str, token_type: str, minutes: int, extra: dict | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
        "jti": uuid.uuid4().hex,
        **(extra or {}),
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def decode_token(token: str, expected_type: str) -> dict | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != expected_type:
        return None
    return payload


def new_refresh_token() -> tuple[str, str]:
    """Return (opaque token for the cookie, sha256 hash to store)."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---- Secret encryption (patient identifiers, original EEG files) ---------

def _keys() -> list[bytes]:
    s = get_settings()
    if s.encryption_key:
        return [s.encryption_key.encode(), *[k.strip().encode() for k in s.encryption_key_previous.split(",") if k.strip()]]
    # Development/test only: derive a key from SECRET_KEY. Production refuses this.
    return [base64.urlsafe_b64encode(hashlib.sha256(s.secret_key.encode()).digest())]


def _fernet() -> MultiFernet:
    """The first key encrypts; any listed key (current, then ENCRYPTION_KEY_PREVIOUS) can decrypt: this enables key rotation."""
    return MultiFernet([Fernet(k) for k in _keys()])


def is_under_current_key(token: bytes) -> bool:
    """True if the token decrypts with the current (first) key, i.e. it needs no rotation."""
    try:
        Fernet(_keys()[0]).decrypt(token)
        return True
    except InvalidToken:
        return False


def encrypt_secret(plain: str) -> bytes:
    return _fernet().encrypt(plain.encode())


def decrypt_secret(token: bytes) -> str:
    return _fernet().decrypt(token).decode()


def rotate_secret(token: bytes) -> bytes:
    """Re-encrypt a token under the current key (decrypts with any accepted key)."""
    return _fernet().rotate(token)


# ---- Blind indexes (searchable keyed hashes of identifiers) ---------------

def _hmac_key() -> bytes:
    s = get_settings()
    return (s.hmac_key or s.secret_key).encode()  # a separate HMAC_KEY is required in production (see Settings.validate_for_production)


def blind_index(value: str, purpose: str) -> str:
    """Keyed hash (HMAC-SHA256, hex) of `value`, used to search or de-duplicate identifiers without storing them in clear.

    `purpose` separates uses ("nametok", "identity", "mrn"): the same text hashes differently for each, so one index can
    never be used to probe another. Without the key a hash reveals nothing about the value; the caller normalises first."""
    msg = f"{purpose}\x1f{value}".encode()
    return hmac.new(_hmac_key(), msg, hashlib.sha256).hexdigest()
