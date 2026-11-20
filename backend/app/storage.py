"""File storage for recordings.

  raw/<patient>/<recording>.nbe     ORIGINAL upload, encrypted at rest (AES-256-GCM, chunked). Restricted.
  deid/<patient>/<recording>.edf    de-identified copy (patient/recording header fields blanked). Used by the viewer.
  derived/<patient>/<run>/processed_raw.fif   output of a pre-processing run (not encrypted by the app)
  tmp/<upload_id>/                  chunk staging while an upload is in progress

The de-identified copy is NOT encrypted by the application (MNE needs a seekable file for fast windowed
reads); protect it with disk/volume encryption in deployment. Swap this module for MinIO/S3 + SSE later.

Encrypted file format:  b"NBE1" | salt(16) | { u32 length | AES-GCM ciphertext+tag }*
Each chunk uses nonce = chunk index and associated data = index || last-chunk flag, so chunks cannot be
reordered, dropped or truncated without detection. The key is derived per file with HKDF.
"""

import hashlib
import shutil
import struct
import uuid
from collections.abc import Iterator
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings

MAGIC = b"NBE1"
CHUNK = 4 * 1024 * 1024


class StorageError(Exception):
    pass


def root() -> Path:
    p = Path(get_settings().storage_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def upload_dir(upload_id: uuid.UUID) -> Path:
    return root() / "tmp" / str(upload_id)


def raw_path(patient_id: uuid.UUID, recording_id: uuid.UUID) -> Path:
    return root() / "raw" / str(patient_id) / f"{recording_id}.nbe"


def deid_path(patient_id: uuid.UUID, recording_id: uuid.UUID) -> Path:
    return root() / "deid" / str(patient_id) / f"{recording_id}.edf"


def processed_path(patient_id: uuid.UUID, run_id: uuid.UUID) -> Path:
    """Continuous processed EEG of one pre-processing run (MNE FIF, float32)."""
    return root() / "derived" / str(patient_id) / str(run_id) / "processed_raw.fif"


def _key(salt: bytes) -> bytes:
    s = get_settings()
    master = (s.encryption_key or s.secret_key).encode()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"neurobridge-file-v1").derive(master)


def _nonce(index: int) -> bytes:
    return index.to_bytes(12, "big")


def _aad(index: int, last: bool) -> bytes:
    return struct.pack(">QB", index, 1 if last else 0)


def _read_blocks(path: Path, size: int) -> Iterator[tuple[bytes, bool]]:
    """Yield (block, is_last), reading one block ahead so the last block is known."""
    with open(path, "rb") as f:
        cur = f.read(size)
        while True:
            nxt = f.read(size)
            yield cur, not nxt
            if not nxt:
                return
            cur = nxt


def encrypt_file(src: Path, dst: Path) -> None:
    import os

    dst.parent.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(16)
    aes = AESGCM(_key(salt))
    tmp = dst.with_suffix(".part")
    with open(tmp, "wb") as out:
        out.write(MAGIC + salt)
        for i, (block, last) in enumerate(_read_blocks(src, CHUNK)):
            ct = aes.encrypt(_nonce(i), block, _aad(i, last))
            out.write(struct.pack(">I", len(ct)) + ct)
    tmp.replace(dst)


def decrypt_stream(src: Path) -> Iterator[bytes]:
    """Yield plaintext chunks. Raises StorageError if the file was modified or truncated."""
    with open(src, "rb") as f:
        head = f.read(20)
        if len(head) != 20 or head[:4] != MAGIC:
            raise StorageError("not an encrypted NeuroBridge file")
        aes = AESGCM(_key(head[4:]))
        index = 0
        hdr = f.read(4)
        while hdr:
            if len(hdr) != 4:
                raise StorageError("truncated file")
            (n,) = struct.unpack(">I", hdr)
            ct = f.read(n)
            nxt = f.read(4)
            last = not nxt
            try:
                yield aes.decrypt(_nonce(index), ct, _aad(index, last))
            except InvalidTag:
                raise StorageError("file failed integrity check (modified, reordered or truncated)") from None
            index += 1
            hdr = nxt


def sha256_of_stream(chunks: Iterator[bytes]) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(c)
    return h.hexdigest()


def remove_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
