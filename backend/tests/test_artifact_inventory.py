"""Tests for the read-only /api/admin/ml/artifacts endpoint and
its underlying artifact_inventory helper.

Acceptance:
  1. unauth blocked
  2. admin gets 200
  3. empty models dir returns []
  4. strategist artifact recognized
  5. auditor artifact recognized
  6. env-pointed artifact flagged correctly
  7. no joblib load call
  8. no env mutation
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from services.ml import artifact_inventory


# ── Helpers ──────────────────────────────────────────────────────


def _touch(path: Path, *, content: bytes = b"\x00") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _make_artifacts(root: Path):
    """Build a representative set:
      * strategist_abc123_20260508T120000Z.joblib + sibling manifest
      * auditor_def456_20260508T120000Z.joblib    (no manifest)
      * legacy_old.joblib                          (unknown type)
      * not_a_model.txt                            (skipped)
    """
    s = _touch(root / "strategist_abc123_20260508T120000Z.joblib")
    _touch(root / "manifest_abc123_20260508T120000Z.json", content=b"{}")
    a = _touch(root / "auditor_def456_20260508T120000Z.joblib")
    _touch(root / "legacy_old.joblib")
    _touch(root / "not_a_model.txt")
    return s, a


# ── (3) empty / missing dir → [] ─────────────────────────────────


def test_missing_dir_returns_empty(tmp_path):
    out = artifact_inventory.list_artifacts(models_dir=tmp_path / "does_not_exist")
    assert out == []


def test_dir_with_no_joblibs_returns_empty(tmp_path):
    (tmp_path / "readme.txt").write_text("nothing here")
    out = artifact_inventory.list_artifacts(models_dir=tmp_path)
    assert out == []


# ── (4)+(5) recognise strategist + auditor; skip non-joblib ────


def test_lists_strategist_and_auditor(tmp_path, monkeypatch):
    # Make sure env vars are NOT pointing at our test files.
    monkeypatch.delenv("STRATEGIST_ARTIFACT", raising=False)
    monkeypatch.delenv("AUDITOR_ARTIFACT", raising=False)
    _make_artifacts(tmp_path)
    out = artifact_inventory.list_artifacts(models_dir=tmp_path)
    by_name = {d["filename"]: d for d in out}
    assert "strategist_abc123_20260508T120000Z.joblib" in by_name
    assert "auditor_def456_20260508T120000Z.joblib" in by_name
    assert "legacy_old.joblib" in by_name
    assert "not_a_model.txt" not in by_name
    assert "manifest_abc123_20260508T120000Z.json" not in by_name

    s = by_name["strategist_abc123_20260508T120000Z.joblib"]
    assert s["model_type"] == "strategist"
    assert s["sha_from_filename"] == "abc123"
    assert s["timestamp_from_filename"] == "20260508T120000Z"
    assert s["manifest_present"] is True
    assert s["manifest_path"] is not None
    assert s["currently_pointed_to_by_env"] is False
    assert s["env_var"] is None
    assert isinstance(s["size_bytes"], int)
    assert s["size_bytes"] >= 0
    assert s["age_hours"] >= 0
    assert "mtime" in s

    a = by_name["auditor_def456_20260508T120000Z.joblib"]
    assert a["model_type"] == "auditor"
    assert a["sha_from_filename"] == "def456"
    assert a["manifest_present"] is False
    assert a["manifest_path"] is None
    assert a["currently_pointed_to_by_env"] is False

    legacy = by_name["legacy_old.joblib"]
    assert legacy["model_type"] == "unknown"
    assert legacy["sha_from_filename"] is None
    assert legacy["currently_pointed_to_by_env"] is False
    assert legacy["env_var"] is None


# ── (6) env-pointed artifact is flagged ──────────────────────────


def test_env_pointed_strategist_is_flagged(tmp_path, monkeypatch):
    s, a = _make_artifacts(tmp_path)
    monkeypatch.setenv("STRATEGIST_ARTIFACT", str(s))
    monkeypatch.delenv("AUDITOR_ARTIFACT", raising=False)
    out = artifact_inventory.list_artifacts(models_dir=tmp_path)
    by_name = {d["filename"]: d for d in out}
    s_meta = by_name[s.name]
    a_meta = by_name[a.name]
    assert s_meta["currently_pointed_to_by_env"] is True
    assert s_meta["env_var"] == "STRATEGIST_ARTIFACT"
    assert a_meta["currently_pointed_to_by_env"] is False
    assert a_meta["env_var"] is None


def test_env_pointed_auditor_is_flagged(tmp_path, monkeypatch):
    s, a = _make_artifacts(tmp_path)
    monkeypatch.delenv("STRATEGIST_ARTIFACT", raising=False)
    monkeypatch.setenv("AUDITOR_ARTIFACT", str(a))
    out = artifact_inventory.list_artifacts(models_dir=tmp_path)
    by_name = {d["filename"]: d for d in out}
    assert by_name[s.name]["currently_pointed_to_by_env"] is False
    assert by_name[a.name]["currently_pointed_to_by_env"] is True
    assert by_name[a.name]["env_var"] == "AUDITOR_ARTIFACT"


def test_env_pointing_at_wrong_type_does_not_match(tmp_path, monkeypatch):
    """If STRATEGIST_ARTIFACT points at an auditor file (operator
    error), don't claim a match — env_var stays None on the
    strategist row, and the auditor row also stays unmatched
    because we only check the matching env var per model_type."""
    _, a = _make_artifacts(tmp_path)
    monkeypatch.setenv("STRATEGIST_ARTIFACT", str(a))  # wrong type
    monkeypatch.delenv("AUDITOR_ARTIFACT", raising=False)
    out = artifact_inventory.list_artifacts(models_dir=tmp_path)
    by_name = {d["filename"]: d for d in out}
    assert by_name[a.name]["currently_pointed_to_by_env"] is False
    assert by_name[a.name]["env_var"] is None


def test_unknown_model_type_never_pointed_by_env(tmp_path, monkeypatch):
    _touch(tmp_path / "legacy_old.joblib")
    monkeypatch.setenv("STRATEGIST_ARTIFACT", str(tmp_path / "legacy_old.joblib"))
    out = artifact_inventory.list_artifacts(models_dir=tmp_path)
    assert out[0]["model_type"] == "unknown"
    assert out[0]["currently_pointed_to_by_env"] is False
    assert out[0]["env_var"] is None


# ── (7) no joblib.load call ──────────────────────────────────────


def test_does_not_call_joblib_load(tmp_path, monkeypatch):
    """Patch joblib.load to raise. If the inventory ever loads a
    file, the call will explode."""
    _make_artifacts(tmp_path)
    import joblib
    sentinel_calls = []

    def _trip(*_a, **_kw):
        sentinel_calls.append("load")
        raise AssertionError("artifact inventory must not call joblib.load")

    monkeypatch.setattr(joblib, "load", _trip)
    out = artifact_inventory.list_artifacts(models_dir=tmp_path)
    assert sentinel_calls == []
    assert len(out) >= 2


def test_module_does_not_import_joblib():
    """Source-level guarantee — read-only metadata module never
    imports joblib at all."""
    src = Path(artifact_inventory.__file__).read_text()
    assert "import joblib" not in src
    assert "from joblib" not in src


# ── (8) no env mutation ──────────────────────────────────────────


def test_no_env_mutation(tmp_path, monkeypatch):
    sentinel_keys = [
        "STRATEGIST_ARTIFACT",
        "AUDITOR_ARTIFACT",
        "PHASE6_ENFORCE_EQUITY",
        "ALPHA_MODELS_DIR",
    ]
    for k in sentinel_keys:
        monkeypatch.setenv(k, f"sentinel-{k}")
    before = {k: os.environ.get(k) for k in sentinel_keys}
    _make_artifacts(tmp_path)
    artifact_inventory.list_artifacts(models_dir=tmp_path)
    after = {k: os.environ.get(k) for k in sentinel_keys}
    assert before == after


# ── (1)+(2) HTTP auth gate ───────────────────────────────────────


@pytest.mark.asyncio
async def test_endpoint_blocks_unauth(monkeypatch):
    """The endpoint MUST call _require_admin before returning data.
    Patch _require_admin to raise the same way it does for unauth
    callers and assert the call short-circuits."""
    from fastapi import HTTPException
    from routes import admin_ml_v2

    async def _raise(_request):
        raise HTTPException(status_code=401, detail="unauthorized")

    monkeypatch.setattr(admin_ml_v2, "_require_admin", _raise)
    with pytest.raises(HTTPException) as ei:
        await admin_ml_v2.list_artifacts_endpoint(request=object())
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_admin_gets_200(monkeypatch, tmp_path):
    """When admin gate is satisfied, endpoint returns the
    inventory dict shape."""
    from routes import admin_ml_v2

    async def _ok(_request):
        return None

    monkeypatch.setattr(admin_ml_v2, "_require_admin", _ok)
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    _make_artifacts(tmp_path)
    out = await admin_ml_v2.list_artifacts_endpoint(request=object())
    assert "models_dir" in out
    assert "items" in out
    assert "count" in out
    assert out["count"] == len(out["items"])
    types = {it["model_type"] for it in out["items"]}
    assert {"strategist", "auditor"}.issubset(types)


@pytest.mark.asyncio
async def test_endpoint_empty_dir_returns_empty(monkeypatch, tmp_path):
    from routes import admin_ml_v2

    async def _ok(_request):
        return None

    monkeypatch.setattr(admin_ml_v2, "_require_admin", _ok)
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path / "missing"))
    out = await admin_ml_v2.list_artifacts_endpoint(request=object())
    assert out["items"] == []
    assert out["count"] == 0


# ── Bonus: no broker / executor / pipeline imports ───────────────


def test_artifact_inventory_does_not_import_broker_or_pipeline():
    src = Path(artifact_inventory.__file__).read_text()
    forbidden = [
        "services.alpaca_client",
        "services.kraken_client",
        "services.broker_registry",
        "services.smart_order_service",
        "services.smart_router",
        "services.ml.broker_wire",
        "services.ml.executors",
        "services.ml.pipeline",
    ]
    leaks = [m for m in forbidden if m in src]
    assert not leaks, f"artifact_inventory imports forbidden module(s): {leaks}"
