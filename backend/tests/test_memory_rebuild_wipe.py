"""Tests for the destructive ``wipe=true`` path of
``POST /api/accuracy/memory/rebuild-from-mongo``.

The wipe path is the operator's escape hatch for the toxic-spike
recurrence scenario: if ChromaDB ever ships corrupt rows that
``save_regime``'s upsert can't overwrite (e.g. orphaned ids), the
operator clicks "Wipe + rebuild" and gets back to a deterministic
state.

Three things this suite pins:

1. **Default behavior is unchanged.** ``wipe=false`` (or omitted)
   does NOT touch ``reset_collection`` — backwards compatibility
   contract for any existing automation hitting the endpoint.

2. **Wipe path is admin-gated.** A non-admin user can't accidentally
   destroy the collection.

3. **Wipe response is auditable.** The response body carries the
   pre-wipe count so the operator can see exactly what was deleted,
   and the structured log line includes the user identity.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def fake_user_admin():
    return {"_id": "u-admin", "email": "admin@risedual.ai", "role": "owner"}


@pytest.fixture
def fake_user_member():
    return {"_id": "u-member", "email": "member@example.com", "role": "member"}


def _async_iter(items):
    """Tiny helper — make an async generator from a list so we can
    stand in for ``db.predictions.find().limit()`` cursors."""
    async def _gen():
        for item in items:
            yield item
    return _gen()


def _stub_db_with_predictions(items):
    db = MagicMock()
    cursor = MagicMock()
    cursor.limit = MagicMock(return_value=_async_iter(items))
    db.predictions.find = MagicMock(return_value=cursor)
    return db


@pytest.mark.asyncio
async def test_rebuild_default_does_not_wipe(fake_user_admin):
    """Backwards-compat: omitting ``wipe`` (or passing False) must
    NOT call ``reset_collection``. Existing automation that hits
    this endpoint daily must keep working unchanged."""
    from routes import accuracy as mod
    from fastapi import Request

    saved_db = mod.db
    mod.db = _stub_db_with_predictions([])
    try:
        with patch.object(mod, "get_current_user", new=AsyncMock(return_value=fake_user_admin)), \
             patch("services.market_memory_service.reset_collection") as reset_mock, \
             patch("services.market_memory_service.save_regime", new=AsyncMock()), \
             patch("services.mongo_chroma_sync_metrics.mark_rebuild", new=AsyncMock()):
            req = Request({"type": "http", "headers": []})
            out = await mod.rebuild_memory_from_mongo(req, days=30, limit=10)

        assert out["ok"] is True
        assert out["wipe"] is False
        assert "wiped" not in out, "wipe-only response field must be absent"
        reset_mock.assert_not_called()
    finally:
        mod.db = saved_db


@pytest.mark.asyncio
async def test_rebuild_wipe_calls_reset_collection(fake_user_admin):
    """``wipe=true`` triggers ``reset_collection`` exactly once,
    BEFORE iterating predictions. Order matters — wiping after
    the rebuild loop would discard the freshly-saved rows."""
    from routes import accuracy as mod
    from fastapi import Request

    call_order: list[str] = []

    async def _track_save(*args, **kwargs):
        call_order.append("save")

    def _track_reset():
        call_order.append("reset")
        return {"ok": True, "deleted": 1027}

    saved_db = mod.db
    mod.db = _stub_db_with_predictions([
        {
            "symbol": "BTC",
            "timestamp": "2026-04-30T10:00:00+00:00",
            "price_at_prediction": 67500.0,
            "confidence": 0.7,
            "regime": {},
            "verified_24h": {"correct": True, "verified_at": "2026-04-30T10:00:00+00:00"},
            "prediction_id": "p-1",
        },
    ])
    try:
        with patch.object(mod, "get_current_user", new=AsyncMock(return_value=fake_user_admin)), \
             patch("services.market_memory_service.reset_collection",
                   side_effect=_track_reset) as reset_mock, \
             patch("services.market_memory_service.save_regime",
                   new=AsyncMock(side_effect=_track_save)), \
             patch("services.mongo_chroma_sync_metrics.mark_rebuild", new=AsyncMock()):
            req = Request({"type": "http", "headers": []})
            out = await mod.rebuild_memory_from_mongo(req, days=30, limit=10, wipe=True)

        reset_mock.assert_called_once()
        assert out["ok"] is True
        assert out["wipe"] is True
        assert out["wiped"] == 1027
        assert out["rebuilt"] == 1
        # reset MUST happen before any save_regime call.
        assert call_order == ["reset", "save"], (
            "Reset must run before predictions are replayed — "
            f"got: {call_order}"
        )
    finally:
        mod.db = saved_db


@pytest.mark.asyncio
async def test_rebuild_wipe_blocks_non_admin(fake_user_member):
    """Owner-only — a regular user can't trigger the destructive
    path even if they craft the request directly."""
    from routes import accuracy as mod
    from fastapi import Request, HTTPException

    saved_db = mod.db
    mod.db = MagicMock()
    try:
        with patch.object(mod, "get_current_user", new=AsyncMock(return_value=fake_user_member)), \
             patch("services.market_memory_service.reset_collection") as reset_mock:
            req = Request({"type": "http", "headers": []})
            with pytest.raises(HTTPException) as exc_info:
                await mod.rebuild_memory_from_mongo(req, days=30, wipe=True)

        assert exc_info.value.status_code == 403
        reset_mock.assert_not_called(), "Wipe must NOT run on auth failure"
    finally:
        mod.db = saved_db


@pytest.mark.asyncio
async def test_rebuild_wipe_db_unavailable_does_not_call_reset(fake_user_admin):
    """If Mongo is unavailable, we MUST NOT proceed to wipe Chroma —
    that would leave the system in an unrecoverable state with no
    Mongo source to backfill from."""
    from routes import accuracy as mod
    from fastapi import Request

    saved_db = mod.db
    mod.db = None
    try:
        with patch.object(mod, "get_current_user", new=AsyncMock(return_value=fake_user_admin)), \
             patch("services.market_memory_service.reset_collection") as reset_mock:
            req = Request({"type": "http", "headers": []})
            out = await mod.rebuild_memory_from_mongo(req, days=30, wipe=True)

        assert out == {"ok": False, "reason": "db_unavailable"}
        reset_mock.assert_not_called()
    finally:
        mod.db = saved_db


def test_reset_collection_returns_count_before_wipe():
    """The ``reset_collection`` helper itself must report what it
    deleted so the API response can echo it to the operator."""
    from services import market_memory_service as mod

    fake_collection = MagicMock()
    fake_collection.count.return_value = 1234
    fake_client = MagicMock()
    fake_client.get_or_create_collection.return_value = fake_collection

    saved_client = mod._client
    saved_collection = mod._collection
    mod._client = fake_client
    mod._collection = fake_collection
    try:
        result = mod.reset_collection()
        assert result == {"ok": True, "deleted": 1234}
        fake_client.delete_collection.assert_called_once_with(name="market_regimes")
        fake_client.get_or_create_collection.assert_called_once()
    finally:
        mod._client = saved_client
        mod._collection = saved_collection


def test_reset_collection_uninitialized_is_safe_noop():
    """Calling ``reset_collection`` before init must not crash —
    just report the not-initialized state. Defensive against
    test-runner ordering surprises."""
    from services import market_memory_service as mod

    saved_client = mod._client
    saved_collection = mod._collection
    mod._client = None
    mod._collection = None
    try:
        result = mod.reset_collection()
        assert result["ok"] is False
        assert result["reason"] == "memory_not_initialized"
        assert result["deleted"] == 0
    finally:
        mod._client = saved_client
        mod._collection = saved_collection
