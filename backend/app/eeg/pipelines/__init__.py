"""Versioned pipeline definitions (YAML) and the rules for overriding them.

A processing run stores the fully resolved parameters and a hash of them, so identical parameters always
give the same `params_hash` and any change is visible.
"""

import copy
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

import yaml

DIR = Path(__file__).parent
FILE_RE = re.compile(r"^(?P<name>[a-z_]+)_v(?P<version>\d+\.\d+\.\d+)\.yaml$")

# Overrides a user may request. Others require a new pipeline version.
#   key -> (validator, roles allowed)
OVERRIDABLE = {
    "line_freq_hz": (lambda v: v in (50, 60), ("clinician", "eeg_technologist")),
    "epoching.reject_ptp_uv": (lambda v: isinstance(v, (int, float)) and 50 <= v <= 1000, ("clinician",)),
    "bandpass_hz": (lambda v: isinstance(v, list) and len(v) == 2 and 0.1 <= v[0] < v[1] <= 100, ("clinician",)),
}


class PipelineError(ValueError):
    pass


@lru_cache
def _load_all() -> dict[tuple[str, str], dict]:
    out = {}
    for f in sorted(DIR.glob("*.yaml")):
        m = FILE_RE.match(f.name)
        if not m:
            continue
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        if d["name"] != m["name"] or d["version"] != m["version"]:
            raise PipelineError(f"{f.name}: name/version inside the file do not match the file name")
        out[(d["name"], d["version"])] = d
    return out


def list_pipelines() -> list[dict]:
    return [{"name": n, "version": v, "description": " ".join(d["description"].split()), "params": d["params"]}
            for (n, v), d in _load_all().items()]


def default_version(name: str) -> str:
    # A version with `default: false` is available on request but is never picked silently.
    versions = [v for (n, v), d in _load_all().items() if n == name and d.get("default", True)]
    if not versions:
        raise PipelineError(f"Unknown pipeline '{name}'")
    return max(versions, key=lambda s: tuple(int(x) for x in s.split(".")))


def resolve(name: str, version: str | None, line_freq_hz: int, overrides: dict | None, roles: list[str]) -> dict:
    """Return the fully resolved parameter set (defaults + site line frequency + permitted overrides)."""
    version = version or default_version(name)
    base = _load_all().get((name, version))
    if base is None:
        raise PipelineError(f"Unknown pipeline {name} v{version}")
    params = copy.deepcopy(base["params"])
    params["line_freq_hz"] = line_freq_hz
    for key, value in (overrides or {}).items():
        if key not in OVERRIDABLE:
            raise PipelineError(f"'{key}' cannot be overridden; create a new pipeline version instead")
        check, allowed = OVERRIDABLE[key]
        if not set(roles) & set(allowed):
            raise PipelineError(f"Your role may not change '{key}'")
        if not check(value):
            raise PipelineError(f"Invalid value for '{key}'")
        target = params
        *parents, leaf = key.split(".")
        for p in parents:
            target = target[p]
        target[leaf] = value
    return {"name": name, "version": version, "params": params}


def params_hash(name: str, version: str, params: dict) -> str:
    blob = json.dumps({"pipeline": name, "version": version, "params": params}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()
