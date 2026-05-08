"""Tests for the read-only ML promotion checklist.

Acceptance:
  * Missing artifact → RED/YELLOW, not 500
  * Stale active artifact → RED on active_age
  * Frozen heartbeat → RED on heartbeat_not_frozen
  * Missing manifest → YELLOW on manifest_present
  * Clean state → overall GREEN, ready_for_review=True
  * No write endpoints called (no env/state mutation)
  * Hard-rule: response never contains the phrase "Promote now"
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch, AsyncMock

import pytest

from services.ml import (
    artifact_inventory,
    executor_heartbeat,
    promotion_checklist,
)
from services.ml.promotion_checklist import (
    STATUS_GREEN, STATUS_RED, STATUS_YELLOW, build_checklist,
)


# ── Helpers ──────────────────────────────────────────────────────


def _seed_artifact(root: Path, name: str, *, with_manifest=True,
                   age_hours: float = 0.0) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / name
    p.write_bytes(b"\x00")
    if with_manifest and ("strategist_" in name or "auditor_" in name):
        # Sibling manifest to satisfy artifact_inventory.
        manifest = name.replace("strategist_", "manifest_") \
                       .replace("auditor_", "manifest_") \
                       .replace(".joblib", ".json")
        (root / manifest).write_text("{}")
    if age_hours > 0:
        import time as _t
        new_mtime = _t.time() - age_hours * 3600.0
        os.utime(p, (new_mtime, new_mtime))
    return p


def _heartbeat_snap(*, frozen=False, flood_count=0, flood_lane="equity"):
    """Match executor_heartbeat.get_snapshot() shape."""
    base = {
        "lane": "equity", "frozen": False,
        "last_pipeline_run_at": "2026-05-08T20:00:00+00:00",
        "last_signal_at": None,
        "signals_1h": 4, "buy_sell_1h": 1, "holds_1h": 3,
        "feature_health_avg": 0.8, "model_age_hours": 1.5,
        "model_stale": False,
        "top_clamp_block_reason": None, "top_clamp_block_count": 0,
    }
    eq = {**base, "lane": "equity"}
    cr = {**base, "lane": "crypto"}
    if frozen:
        eq["frozen"] = True
    if flood_count > 0:
        target = eq if flood_lane == "equity" else cr
        target["top_clamp_block_reason"] = "STRATEGIST_FEATURE_HEALTH_LOW"
        target["top_clamp_block_count"] = flood_count
    return {
        "as_of": "2026-05-08T20:00:00+00:00",
        "lanes": {"equity": eq, "crypto": cr},
        "any_frozen": frozen,
    }


class _FakeColl:
    def __init__(self, count_value: int = 0, raises: bool = False):
        self._count = count_value
        self._raises = raises

    async def count_documents(self, _query):
        if self._raises:
            raise RuntimeError("simulated db failure")
        return self._count


class _FakeDB:
    def __init__(self, *, decision_log_count: int = 5,
                 decision_log_raises: bool = False):
        self._coll = _FakeColl(decision_log_count, decision_log_raises)

    def __getitem__(self, name):
        return self._coll


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("STRATEGIST_ARTIFACT", raising=False)
    monkeypatch.delenv("AUDITOR_ARTIFACT", raising=False)
    monkeypatch.delenv("PROMOTION_SHADOW_MIN_ROWS", raising=False)
    yield


# ── Helper fns (pure) ────────────────────────────────────────────


def test_overall_red_wins():
    from services.ml.promotion_checklist import _overall
    checks = [
        {"id": "a", "status": STATUS_GREEN, "label": "", "detail": ""},
        {"id": "b", "status": STATUS_YELLOW, "label": "", "detail": ""},
        {"id": "c", "status": STATUS_RED, "label": "", "detail": ""},
    ]
    overall, ready, msg = _overall(checks)
    assert overall == STATUS_RED
    assert ready is False
    assert "Promote now" not in msg


def test_overall_green_when_all_pass():
    from services.ml.promotion_checklist import _overall
    overall, ready, msg = _overall([
        {"id": "a", "status": STATUS_GREEN, "label": "", "detail": ""}
    ])
    assert overall == STATUS_GREEN
    assert ready is True
    assert msg == "Ready for review."


# ── (a) missing artifact → not 500 ───────────────────────────────


@pytest.mark.asyncio
async def test_missing_artifact_dir_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path / "missing"))
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap()):
        report = await build_checklist(db=_FakeDB())
    by_id = {c["id"]: c for c in report["checks"]}
    assert len(report["checks"]) == 8
    assert by_id["manifest_present"]["status"] == STATUS_YELLOW
    assert by_id["latest_newer_than_active"]["status"] == STATUS_YELLOW
    # No active env vars -> active_on_disk is YELLOW (not RED).
    assert by_id["active_on_disk"]["status"] == STATUS_YELLOW
    assert report["overall_status"] in (STATUS_YELLOW, STATUS_RED)
    assert report["ready_for_review"] is False


# ── (b) stale active artifact → RED ──────────────────────────────


@pytest.mark.asyncio
async def test_stale_active_artifact_returns_red(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    s = _seed_artifact(tmp_path,
                       "strategist_abc_20260501T120000Z.joblib",
                       age_hours=96.0)  # > 72h threshold
    monkeypatch.setenv("STRATEGIST_ARTIFACT", str(s))
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap()):
        report = await build_checklist(db=_FakeDB())
    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["active_age"]["status"] == STATUS_RED
    assert "stale" in by_id["active_age"]["detail"].lower()
    assert report["overall_status"] == STATUS_RED
    assert report["ready_for_review"] is False


# ── (c) frozen heartbeat → RED ───────────────────────────────────


@pytest.mark.asyncio
async def test_frozen_heartbeat_returns_red(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap(frozen=True)):
        report = await build_checklist(db=_FakeDB())
    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["heartbeat_not_frozen"]["status"] == STATUS_RED
    assert "equity" in by_id["heartbeat_not_frozen"]["detail"]
    assert report["overall_status"] == STATUS_RED


@pytest.mark.asyncio
async def test_flood_returns_red(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    snap = _heartbeat_snap(flood_count=120)
    with patch.object(executor_heartbeat, "get_snapshot", return_value=snap):
        report = await build_checklist(db=_FakeDB())
    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["no_flood_1h"]["status"] == STATUS_RED
    assert "STRATEGIST_FEATURE_HEALTH_LOW" in by_id["no_flood_1h"]["detail"]
    assert "120" in by_id["no_flood_1h"]["detail"]


# ── (d) missing manifest → YELLOW ────────────────────────────────


@pytest.mark.asyncio
async def test_missing_manifest_returns_yellow(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    _seed_artifact(tmp_path,
                   "strategist_abc_20260508T120000Z.joblib",
                   with_manifest=False)
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap()):
        report = await build_checklist(db=_FakeDB())
    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["manifest_present"]["status"] == STATUS_YELLOW
    # YELLOW alone shouldn't push overall to RED — it's "needs review".
    assert report["overall_status"] in (STATUS_YELLOW, STATUS_RED)
    if report["overall_status"] == STATUS_YELLOW:
        assert report["ready_for_review"] is False


# ── (e) clean state → GREEN / ready_for_review ───────────────────


@pytest.mark.asyncio
async def test_clean_state_returns_green(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    s = _seed_artifact(tmp_path,
                       "strategist_abc_20260508T120000Z.joblib",
                       age_hours=2.0)
    a = _seed_artifact(tmp_path,
                       "auditor_def_20260508T120000Z.joblib",
                       age_hours=3.0)
    monkeypatch.setenv("STRATEGIST_ARTIFACT", str(s))
    monkeypatch.setenv("AUDITOR_ARTIFACT", str(a))
    monkeypatch.setenv("PROMOTION_SHADOW_MIN_ROWS", "1")
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap()):
        report = await build_checklist(db=_FakeDB(decision_log_count=42))
    by_id = {c["id"]: c for c in report["checks"]}
    assert all(c["status"] == STATUS_GREEN for c in report["checks"]), \
        f"non-green checks: {[c for c in report['checks'] if c['status'] != STATUS_GREEN]}"
    assert report["overall_status"] == STATUS_GREEN
    assert report["ready_for_review"] is True
    assert report["message"] == "Ready for review."
    # Active artifacts surfaced.
    assert report["active_artifacts"]["strategist"] == s.name
    assert report["active_artifacts"]["auditor"] == a.name


# ── env-type mismatch → RED ──────────────────────────────────────


@pytest.mark.asyncio
async def test_env_type_mismatch_returns_red(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    a = _seed_artifact(tmp_path, "auditor_def_20260508T120000Z.joblib")
    # Operator misconfiguration: STRATEGIST_ARTIFACT pointing at an auditor file.
    monkeypatch.setenv("STRATEGIST_ARTIFACT", str(a))
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap()):
        report = await build_checklist(db=_FakeDB())
    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["env_type_matches"]["status"] == STATUS_RED
    assert "STRATEGIST_ARTIFACT" in by_id["env_type_matches"]["detail"]
    assert report["overall_status"] == STATUS_RED


# ── env path missing on disk → RED ───────────────────────────────


@pytest.mark.asyncio
async def test_env_path_missing_on_disk_returns_red(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("STRATEGIST_ARTIFACT",
                       str(tmp_path / "strategist_doesnotexist.joblib"))
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap()):
        report = await build_checklist(db=_FakeDB())
    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["active_on_disk"]["status"] == STATUS_RED
    assert "STRATEGIST_ARTIFACT" in by_id["active_on_disk"]["detail"]


# ── shadow receipts thin → YELLOW ────────────────────────────────


@pytest.mark.asyncio
async def test_thin_shadow_receipts_returns_yellow(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("PROMOTION_SHADOW_MIN_ROWS", "100")
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap()):
        report = await build_checklist(db=_FakeDB(decision_log_count=3))
    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["shadow_receipts"]["status"] == STATUS_YELLOW
    assert "Only 3" in by_id["shadow_receipts"]["detail"]


@pytest.mark.asyncio
async def test_db_unavailable_for_shadow_check(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap()):
        report = await build_checklist(db=None)
    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["shadow_receipts"]["status"] == STATUS_YELLOW
    assert "DB unavailable" in by_id["shadow_receipts"]["detail"]


# ── (f) no write endpoints / hard-rule "Promote now" absent ────


@pytest.mark.asyncio
async def test_no_promote_now_anywhere_in_response(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    s = _seed_artifact(tmp_path,
                       "strategist_abc_20260508T120000Z.joblib",
                       age_hours=2.0)
    monkeypatch.setenv("STRATEGIST_ARTIFACT", str(s))
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap()):
        report = await build_checklist(db=_FakeDB())
    blob = json.dumps(report).lower()
    assert "promote now" not in blob
    assert "promote_now" not in blob
    # Aslo no promotion-action keys leaking in.
    assert "promote_action" not in blob
    assert "set_active" not in blob


@pytest.mark.asyncio
async def test_no_env_mutation_during_build(tmp_path, monkeypatch):
    sentinel_keys = [
        "STRATEGIST_ARTIFACT", "AUDITOR_ARTIFACT",
        "PHASE6_ENFORCE_EQUITY", "ALPACA_API_KEY",
    ]
    for k in sentinel_keys:
        monkeypatch.setenv(k, f"sentinel-{k}")
    before = {k: os.environ.get(k) for k in sentinel_keys}
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap()):
        await build_checklist(db=_FakeDB())
    after = {k: os.environ.get(k) for k in sentinel_keys}
    assert before == after


def test_module_does_not_import_broker_or_pipeline_or_joblib():
    src = Path(promotion_checklist.__file__).read_text()
    assert "import joblib" not in src
    assert "from joblib" not in src
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
    assert not leaks


# ── HTTP gate ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_endpoint_blocks_unauth(monkeypatch):
    from fastapi import HTTPException
    from routes import admin_ml_v2

    async def _raise(_request):
        raise HTTPException(status_code=401, detail="unauthorized")

    monkeypatch.setattr(admin_ml_v2, "_require_admin", _raise)
    with pytest.raises(HTTPException) as ei:
        await admin_ml_v2.promotion_checklist(request=object())
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_admin_returns_full_shape(tmp_path, monkeypatch):
    from routes import admin_ml_v2

    async def _ok(_request):
        return None

    monkeypatch.setattr(admin_ml_v2, "_require_admin", _ok)
    monkeypatch.setenv("ALPHA_MODELS_DIR", str(tmp_path))
    with patch.object(executor_heartbeat, "get_snapshot",
                      return_value=_heartbeat_snap()):
        out = await admin_ml_v2.promotion_checklist(request=object())
    required_keys = {
        "as_of", "overall_status", "ready_for_review", "message",
        "checks", "active_artifacts", "latest_artifacts",
        "any_frozen", "thresholds",
    }
    assert required_keys.issubset(set(out.keys()))
    assert isinstance(out["checks"], list) and len(out["checks"]) == 8
    for c in out["checks"]:
        assert {"id", "label", "status", "detail"}.issubset(c.keys())
        assert c["status"] in {STATUS_GREEN, STATUS_YELLOW, STATUS_RED}
