"""Registry + candidate-engine tests.

Verifies:
  * Two engines registered at module-import time (live + candidate_v2).
  * Candidate has a wider schema (6-bin confidence + direction_family +
    confidence_x_agent dimensions).
  * Broadcast fan-out: a single trade lands on every engine.
  * Promotion: live tag flips; previous live becomes candidate.
  * Engine isolation: each engine writes to its own collection name.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.ai_core_engine import (
    LearningEngine, LearningEngineRegistry,
    SchemaConfig, registry, _LIVE_SCHEMA, _CANDIDATE_V2_SCHEMA,
    _direction_family,
)


# ── Module-level registry shape ──────────────────────────────────────


def test_default_registry_has_live_and_candidate():
    names = registry.names()
    assert "live" in names
    assert "candidate_v2" in names
    assert registry.live_name == "live"


def test_live_and_candidate_have_distinct_schemas():
    live = registry.get("live")
    cand = registry.get("candidate_v2")
    assert live.schema.name == "live_v1"
    assert cand.schema.name == "candidate_v2"
    # Candidate is finer at the high-confidence end
    live_labels = [b[0] for b in live.schema.confidence_buckets]
    cand_labels = [b[0] for b in cand.schema.confidence_buckets]
    assert "80-90" in live_labels
    assert "80-85" in cand_labels and "85-90" in cand_labels
    # Candidate adds new dimensions
    assert "direction_family" not in live.schema.dimensions
    assert "direction_family" in cand.schema.dimensions
    assert "confidence_x_agent" in cand.schema.dimensions


def test_engine_collection_names():
    live = registry.get("live")
    cand = registry.get("candidate_v2")
    assert live.trades_collection == "ai_core_trades"
    assert cand.trades_collection == "ai_core_engine_candidate_v2_trades"


# ── Direction-family extraction ──────────────────────────────────────


def test_direction_family():
    assert _direction_family("BUY") == "BULLISH"
    assert _direction_family("STRONG_BUY") == "BULLISH"
    assert _direction_family("SELL") == "BEARISH"
    assert _direction_family("WEAK_SELL") == "BEARISH"
    assert _direction_family("HOLD") == "NEUTRAL"
    assert _direction_family("garbage") is None
    assert _direction_family(None) is None


# ── Candidate schema extracts new dimensions ─────────────────────────


def test_candidate_schema_extracts_cross_dimension():
    pairs = list(_CANDIDATE_V2_SCHEMA.extract_conditions({
        "agent": "war_room", "direction": "BUY", "confidence": 0.92,
        "regime": "trend_up", "asset_type": "equity",
    }))
    keys = {k for k, _ in pairs}
    assert "regime" in keys
    assert "agent" in keys
    assert "asset_type" in keys
    assert "confidence_bucket" in keys
    assert "direction_family" in keys
    assert "confidence_x_agent" in keys
    # 6-bin: 92 falls into 90-100, but the cross dim is "war_room@90-100"
    cross = [v for k, v in pairs if k == "confidence_x_agent"]
    assert cross == ["war_room@90-100"]


def test_live_schema_does_not_extract_candidate_dimensions():
    pairs = list(_LIVE_SCHEMA.extract_conditions({
        "agent": "war_room", "direction": "BUY", "confidence": 0.92,
    }))
    keys = {k for k, _ in pairs}
    assert "direction_family" not in keys
    assert "confidence_x_agent" not in keys


# ── Broadcast fan-out ────────────────────────────────────────────────


@pytest.fixture
def isolated_registry():
    """Standalone registry — doesn't touch the module-level one,
    so test ordering can't pollute global state."""
    r = LearningEngineRegistry()
    r.register(LearningEngine(name="live", schema=_LIVE_SCHEMA), set_live=True)
    r.register(LearningEngine(name="candidate_v2", schema=_CANDIDATE_V2_SCHEMA))
    return r


@pytest.mark.asyncio
async def test_broadcast_records_on_all_engines(isolated_registry):
    raw = {
        "source": "test", "source_id": "t1", "symbol": "NVDA",
        "direction": "BUY", "outcome": "win", "confidence": 0.92,
        "agent": "war_room", "regime": "trend_up", "asset_type": "equity",
    }
    res = await isolated_registry.broadcast_trade(raw)
    assert res["live_result"]["ok"]
    assert "live" in res["engines"]
    assert "candidate_v2" in res["engines"]
    assert res["engines"]["live"]["ok"]
    assert res["engines"]["candidate_v2"]["ok"]
    # Each engine ticked its own counters
    assert isolated_registry.get("live").stats["wins"] == 1
    assert isolated_registry.get("candidate_v2").stats["wins"] == 1
    # Candidate has *more* condition keys than live (extra dims)
    live_keys = isolated_registry.get("live").condition_stats.keys()
    cand_keys = isolated_registry.get("candidate_v2").condition_stats.keys()
    assert len(cand_keys) > len(live_keys)


# ── Promotion ────────────────────────────────────────────────────────


def test_promote_flips_live_tag(isolated_registry):
    assert isolated_registry.live_name == "live"
    res = isolated_registry.promote("candidate_v2")
    assert res["ok"] is True
    assert res["live"] == "candidate_v2"
    assert res["demoted"] == "live"
    assert isolated_registry.live_name == "candidate_v2"
    # Both engines still in registry — previous live is now a candidate
    assert "live" in isolated_registry.names()
    assert "candidate_v2" in isolated_registry.names()


def test_promote_unknown_engine_fails(isolated_registry):
    res = isolated_registry.promote("nonexistent")
    assert res["ok"] is False
    assert res["reason"] == "unknown_engine"


def test_promote_idempotent(isolated_registry):
    res = isolated_registry.promote("live")
    assert res["ok"] is True
    assert res.get("noop") is True


@pytest.mark.asyncio
async def test_reset_all_clears_every_engine(isolated_registry):
    raw = {
        "source": "t", "source_id": "x", "symbol": "AAA",
        "direction": "BUY", "outcome": "win", "agent": "a",
    }
    await isolated_registry.broadcast_trade(raw)
    assert isolated_registry.get("live").stats["wins"] == 1
    assert isolated_registry.get("candidate_v2").stats["wins"] == 1
    res = await isolated_registry.reset_all()
    assert res["ok"]
    assert isolated_registry.get("live").stats["wins"] == 0
    assert isolated_registry.get("candidate_v2").stats["wins"] == 0
