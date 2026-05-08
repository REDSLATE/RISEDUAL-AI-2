"""Artifact discovery — file-stat + env-read only.

Surfaces every ``.joblib`` under the configured models directory
with metadata that an operator needs to decide whether to promote:

  * filename / full_path / size_bytes / mtime / age_hours
  * sha_from_filename (parsed from ``<type>_<sha>_<utc>.joblib``)
  * model_type: ``strategist`` | ``auditor`` | ``unknown``
  * manifest_present (sibling ``manifest_<sha>_<utc>.json``)
  * currently_pointed_to_by_env (matches STRATEGIST_ARTIFACT or
    AUDITOR_ARTIFACT exactly)
  * env_var: ``STRATEGIST_ARTIFACT`` | ``AUDITOR_ARTIFACT`` | None

Strict invariants:
  * NEVER calls ``joblib.load`` — purely file-stat metadata.
  * NEVER mutates ``.env`` or any env variable.
  * NEVER promotes / restarts / calls broker.
  * Missing directory returns an empty list — no crash.
"""
from __future__ import annotations

__domain__ = "PRD"

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_MODELS_DIR = "/app/backend/data/models"
_FILENAME_RE = re.compile(
    r"^(?P<type>strategist|auditor)_(?P<sha>[A-Za-z0-9]+)_"
    r"(?P<ts>\d{8}T\d{6}Z)\.joblib$"
)
_ENV_VAR_BY_TYPE = {
    "strategist": "STRATEGIST_ARTIFACT",
    "auditor":    "AUDITOR_ARTIFACT",
}


def _models_dir() -> Path:
    return Path(os.environ.get("ALPHA_MODELS_DIR") or DEFAULT_MODELS_DIR)


def _parse_filename(name: str) -> Dict[str, Any]:
    """Extract ``model_type`` + ``sha_from_filename`` from a versioned
    artifact name. Falls back to ``unknown`` when the name doesn't
    match the canonical retrain pattern."""
    m = _FILENAME_RE.match(name)
    if not m:
        return {"model_type": "unknown",
                "sha_from_filename": None,
                "timestamp_from_filename": None}
    return {"model_type": m.group("type"),
            "sha_from_filename": m.group("sha"),
            "timestamp_from_filename": m.group("ts")}


def _manifest_path_for(art_path: Path) -> Path:
    """Sibling manifest_<sha>_<ts>.json for a strategist/auditor file."""
    return art_path.with_name(
        art_path.name
        .replace("strategist_", "manifest_")
        .replace("auditor_", "manifest_")
        .replace(".joblib", ".json")
    )


def _env_pointing_at(path: Path, model_type: str) -> Dict[str, Any]:
    """Compare the artifact's full path against the relevant
    artifact env var. NEVER mutates env."""
    env_var = _ENV_VAR_BY_TYPE.get(model_type)
    if env_var is None:
        return {"currently_pointed_to_by_env": False, "env_var": None}
    pointed = (os.environ.get(env_var) or "").strip()
    is_match = bool(pointed) and Path(pointed).resolve() == path.resolve()
    return {"currently_pointed_to_by_env": is_match,
            "env_var": env_var if is_match else None}


def list_artifacts(*, models_dir: Path = None) -> List[Dict[str, Any]]:
    """Return one dict per ``.joblib`` file in the models dir.

    Sorted newest-first by mtime. Returns ``[]`` when the directory
    is missing or unreadable — never raises.
    """
    root = models_dir or _models_dir()
    if not root.exists() or not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    now = time.time()
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    for p in children:
        if not p.is_file() or p.suffix != ".joblib":
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        size_bytes = int(stat.st_size)
        mtime_epoch = float(stat.st_mtime)
        age_hours = max(0.0, (now - mtime_epoch) / 3600.0)
        parsed = _parse_filename(p.name)
        manifest_path = _manifest_path_for(p)
        env_info = _env_pointing_at(p, parsed["model_type"])
        out.append({
            "filename": p.name,
            "full_path": str(p),
            "size_bytes": size_bytes,
            "mtime": _iso(mtime_epoch),
            "age_hours": round(age_hours, 4),
            "sha_from_filename": parsed["sha_from_filename"],
            "timestamp_from_filename": parsed["timestamp_from_filename"],
            "model_type": parsed["model_type"],
            "manifest_present": manifest_path.exists(),
            "manifest_path": str(manifest_path) if manifest_path.exists() else None,
            **env_info,
        })
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
