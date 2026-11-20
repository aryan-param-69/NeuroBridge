"""Loading trained models safely.

A model file is a pickle, and unpickling runs code. It is therefore loaded ONLY from the configured model directory and ONLY after its
SHA-256 matches the value written into its model card at training time. A file that was modified or swapped is refused.
"""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import joblib

from app.core.config import get_settings


class ModelError(RuntimeError):
    pass


def model_root() -> Path:
    return Path(get_settings().model_dir).resolve()


def _card(name_dir: Path) -> dict:
    f = name_dir / "model_card.json"
    if not f.exists():
        raise ModelError("model card missing")
    return json.loads(f.read_text(encoding="utf-8"))


def list_models() -> list[dict]:
    root = model_root()
    out = []
    if root.exists():
        for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "model.joblib").exists()):
            try:
                c = _card(d)
                out.append({"dir": d.name, "name": c["name"], "version": c["version"], "status": c.get("status", "staging"),
                            "intended_use": c["intended_use"], "not_for": c.get("not_for", []), "training_data": c.get("training_data"),
                            "validation": c.get("validation"), "known_limits": c.get("known_limits", []), "model_sha256": c["model_sha256"],
                            "model": c.get("model")})
            except (ModelError, KeyError, ValueError):
                continue
    return out


@lru_cache(maxsize=4)
def load(dir_name: str) -> tuple[dict, dict]:
    """Return (bundle, card) after verifying the file hash. Names with path parts are refused."""
    if dir_name != Path(dir_name).name or dir_name.startswith("."):
        raise ModelError("invalid model name")
    d = model_root() / dir_name
    f = d / "model.joblib"
    if not f.exists():
        raise ModelError("model not found")
    card = _card(d)
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    if digest != card.get("model_sha256"):
        raise ModelError("model file does not match its recorded SHA-256; refusing to load it")
    return joblib.load(f), card
