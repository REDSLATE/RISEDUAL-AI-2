"""Tests for the ChromaDB auto-warmup logic in server.py.

The warmup is a fire-and-forget task spawned at startup. We verify the
no-op early-exit branches here (which are the safety-critical ones —
we never want to overwrite an already-populated cache or run on an
empty Mongo).
"""
from __future__ import annotations

import os

import pytest

from server import _chromadb_warmup


@pytest.mark.asyncio
async def test_warmup_disabled_via_env(monkeypatch, caplog):
    """CHROMA_AUTO_WARMUP=false must short-circuit immediately."""
    monkeypatch.setenv("CHROMA_AUTO_WARMUP", "false")
    with caplog.at_level("INFO"):
        await _chromadb_warmup()
    assert any("disabled via CHROMA_AUTO_WARMUP=false" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_warmup_skips_when_chroma_already_populated(monkeypatch, caplog):
    """If Chroma already has episodes, warmup must not touch save_regime."""
    monkeypatch.setenv("CHROMA_AUTO_WARMUP", "true")

    save_regime_called = []

    async def fake_get_stats():
        return {"total_episodes": 1015, "mongodb_log_count": 6667}

    async def fake_save_regime(_payload):
        save_regime_called.append(_payload)

    import services.market_memory_service as mms
    monkeypatch.setattr(mms, "get_memory_stats", fake_get_stats)
    monkeypatch.setattr(mms, "save_regime", fake_save_regime)

    with caplog.at_level("INFO"):
        await _chromadb_warmup()

    assert save_regime_called == []
    assert any("skip" in m and "1015" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_warmup_skips_when_mongo_is_empty(monkeypatch, caplog):
    """If Mongo log is empty there's nothing to backfill — must not touch Chroma."""
    monkeypatch.setenv("CHROMA_AUTO_WARMUP", "true")

    save_regime_called = []

    async def fake_get_stats():
        return {"total_episodes": 0, "mongodb_log_count": 0}

    async def fake_save_regime(_payload):
        save_regime_called.append(_payload)

    import services.market_memory_service as mms
    monkeypatch.setattr(mms, "get_memory_stats", fake_get_stats)
    monkeypatch.setattr(mms, "save_regime", fake_save_regime)

    with caplog.at_level("INFO"):
        await _chromadb_warmup()

    assert save_regime_called == []
    assert any("Mongo log is empty" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_warmup_swallows_errors(monkeypatch, caplog):
    """Any failure inside warmup must not crash startup."""
    monkeypatch.setenv("CHROMA_AUTO_WARMUP", "true")

    async def boom():
        raise RuntimeError("chroma exploded")

    import services.market_memory_service as mms
    monkeypatch.setattr(mms, "get_memory_stats", boom)

    with caplog.at_level("WARNING"):
        await _chromadb_warmup()

    # Non-critical: must log a warning, must not raise.
    assert any("warmup" in m.lower() and "failed" in m.lower() for m in caplog.messages)
