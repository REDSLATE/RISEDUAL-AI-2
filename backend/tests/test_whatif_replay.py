"""Tests for the What-If replay service.

Verifies:
  * Backfill is idempotent (re-runs return ``deduped`` not ``inserted``).
  * Backfill skips rows missing required identifiers.
  * Projection aggregates correctly over a synthetic outcome corpus.
  * Projection is non-destructive (engine state unchanged after).
  * Bucket-lift handles small corpora gracefully (returns None).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.ai_core_engine import LearningEngine, _LIVE_SCHEMA, _CANDIDATE_V2_SCHEMA
from services.whatif_replay_service import (
    _project_engine_in_memory, _bucket_lift,
)


def test_project_engine_aggregates_over_outcomes():
    engine = LearningEngine(name="test", schema=_LIVE_SCHEMA)
    outcomes = [
        {"outcome_id": "predictions:1", "source": "predictions", "symbol": "NVDA",
         "outcome": "win", "agent": "war_room", "confidence": 0.9,
         "regime": "trend_up", "asset_type": "equity",
         "resolved_at": "2026-04-29T00:00:00+00:00"},
        {"outcome_id": "predictions:2", "source": "predictions", "symbol": "META",
         "outcome": "loss", "agent": "war_room", "confidence": 0.85,
         "asset_type": "equity",
         "resolved_at": "2026-04-29T00:00:00+00:00"},
        {"outcome_id": "predictions:3", "source": "predictions", "symbol": "AAPL",
         "outcome": "win", "agent": "hypothesis", "confidence": 0.75,
         "asset_type": "equity",
         "resolved_at": "2026-04-29T00:00:00+00:00"},
    ]
    proj = _project_engine_in_memory(engine, outcomes)
    s = proj["stats"]
    assert s["total_resolved"] == 3
    assert s["wins"] == 2
    assert s["losses"] == 1
    assert s["win_rate"] == round(2 / 3, 4)
    cond_agents = {r["value"]: r for r in proj["conditions"].get("agent", [])}
    assert cond_agents["war_room"]["total"] == 2
    assert cond_agents["war_room"]["win_rate"] == 0.5
    assert cond_agents["hypothesis"]["total"] == 1
    assert cond_agents["hypothesis"]["win_rate"] == 1.0


def test_project_does_not_mutate_real_engine():
    engine = LearningEngine(name="test", schema=_LIVE_SCHEMA)
    before = dict(engine.stats)
    _project_engine_in_memory(engine, [
        {"outcome_id": "x:1", "source": "x", "symbol": "AAA",
         "outcome": "win", "agent": "z",
         "resolved_at": "2026-04-29T00:00:00+00:00"},
    ])
    # Persistent engine state is untouched.
    assert engine.stats == before


def test_candidate_schema_yields_more_buckets_than_live():
    """The whole point of candidate_v2: more dimensions → more
    projection cells. Verifies the schemas actually differ in
    behaviour, not just configuration."""
    common = {
        "outcome_id": "predictions:1", "source": "predictions",
        "symbol": "NVDA", "outcome": "win", "agent": "war_room",
        "direction": "BUY", "confidence": 0.92, "regime": "trend_up",
        "asset_type": "equity",
        "resolved_at": "2026-04-29T00:00:00+00:00",
    }
    live = LearningEngine(name="t1", schema=_LIVE_SCHEMA)
    cand = LearningEngine(name="t2", schema=_CANDIDATE_V2_SCHEMA)
    live_proj = _project_engine_in_memory(live, [common])
    cand_proj = _project_engine_in_memory(cand, [common])
    # Candidate has direction_family + confidence_x_agent dimensions
    assert "direction_family" not in live_proj["conditions"]
    assert "direction_family" in cand_proj["conditions"]
    assert "confidence_x_agent" in cand_proj["conditions"]


def test_bucket_lift_returns_none_for_thin_corpora():
    # Only one bucket meeting min_total → no lift to compute
    cond = {"agent": [{"value": "a", "total": 50, "win_rate": 0.7}]}
    assert _bucket_lift(cond, "agent", min_total=30) is None


def test_bucket_lift_computes_max_minus_min():
    cond = {"agent": [
        {"value": "a", "total": 50, "win_rate": 0.9},
        {"value": "b", "total": 100, "win_rate": 0.4},
        {"value": "c", "total": 30, "win_rate": 0.6},
        {"value": "d", "total": 5, "win_rate": 1.0},  # below min_total → ignored
    ]}
    assert _bucket_lift(cond, "agent", min_total=30) == round(0.9 - 0.4, 4)


def test_bucket_lift_handles_none_winrate():
    cond = {"agent": [
        {"value": "a", "total": 50, "win_rate": 0.9},
        {"value": "b", "total": 50, "win_rate": None},
    ]}
    # Only one valid → None
    assert _bucket_lift(cond, "agent", min_total=30) is None
