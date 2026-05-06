"""Tests for Patent M Phase 2 — persistence + shadow-hook wiring.

Covers:

1. ``learning_core_persistence`` — pure transforms (memory ↔ doc
   round-trip), env-flag gate, best-effort error handling.
2. ``learning_core_shadow_hook`` — attaches under ``learning_core``
   only when env flag is on, never raises, never mutates the
   decision, never alters direction.
3. ``run_adversarial_decision`` — payload still contains the
   original keys after the hook runs (no regression).
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure the canonical engine is enabled for the in-memory hook
# path (Phase-2 tests don't need persistence on by default).
os.environ.setdefault("REGIME_MEMORY_ENABLED", "true")
os.environ.setdefault("REGIME_MEMORY_MODE", "full_context")

import services.regime_memory_retrieval as _rmr  # noqa: E402

_rmr.REGIME_MEMORY_ENABLED = True
_rmr.REGIME_MEMORY_MODE = "full_context"

from services.learning_core_persistence import (  # noqa: E402
    COLLECTION_NAME,
    doc_to_memory,
    memory_to_doc,
    persist_resolved_memory,
    rehydrate_resolved_memories,
)
from services.learning_core_shadow_hook import (  # noqa: E402
    attach_learning_core_context,
    reset_singleton_for_tests,
)
from services.regime_clustering_layer import (  # noqa: E402
    RegimeFingerprint,
    RegimeLabeledMemory,
)


def _fp() -> RegimeFingerprint:
    return RegimeFingerprint(
        vix_level="normal", yield_curve="flat",
        dxy_trend="strong", credit_spreads="tight",
        liquidity="normal", macro_phase="expansion",
    )


def _mem(mid: str = "M-1") -> RegimeLabeledMemory:
    return RegimeLabeledMemory(
        memory_id=mid,
        timestamp="2026-02-01T00:00:00Z",
        ticker="AAPL",
        direction="LONG",
        entry_price=100.0,
        exit_price=103.5,
        pnl_pct=3.5,
        holding_days=4,
        regime_at_entry=_fp(),
    )


# ─── persistence: pure transforms ──────────────────────────────


def test_collection_name_is_stable():
    """Public contract — admin tooling and migration scripts will
    reference this exact string."""
    assert COLLECTION_NAME == "learning_core_resolved_memories"


def test_memory_to_doc_round_trip_preserves_all_fields():
    original = _mem()
    doc = memory_to_doc(original)
    # ``persisted_at`` is the only added field.
    assert "persisted_at" in doc

    restored = doc_to_memory(doc)
    assert restored.memory_id == original.memory_id
    assert restored.ticker == original.ticker
    assert restored.direction == original.direction
    assert restored.pnl_pct == original.pnl_pct
    assert restored.regime_at_entry == original.regime_at_entry


def test_doc_to_memory_strips_mongo_id():
    doc = memory_to_doc(_mem())
    doc["_id"] = "some-mongo-objectid"
    restored = doc_to_memory(doc)
    # No attribute, no crash, no leakage into the dataclass.
    assert restored.memory_id == "M-1"


# ─── persistence: env gate + error handling ────────────────────


@pytest.mark.asyncio
async def test_persist_skipped_when_env_flag_off(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_PERSISTENCE_ENABLED", "false")
    fake_db = MagicMock()
    out = await persist_resolved_memory(fake_db, _mem())
    assert out["persisted"] is False
    assert out["reason"] == "env_flag_off"


@pytest.mark.asyncio
async def test_persist_skipped_when_db_none(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_PERSISTENCE_ENABLED", "true")
    out = await persist_resolved_memory(None, _mem())
    assert out["persisted"] is False
    assert out["reason"] == "db_handle_none"


@pytest.mark.asyncio
async def test_persist_writes_when_enabled(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_PERSISTENCE_ENABLED", "true")
    fake_collection = MagicMock()
    fake_collection.update_one = AsyncMock()
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    out = await persist_resolved_memory(fake_db, _mem())
    assert out["persisted"] is True
    assert out["memory_id"] == "M-1"
    fake_collection.update_one.assert_called_once()
    args, kwargs = fake_collection.update_one.call_args
    assert args[0] == {"memory_id": "M-1"}
    assert "$set" in args[1]
    assert kwargs["upsert"] is True


@pytest.mark.asyncio
async def test_persist_swallows_mongo_failure(monkeypatch):
    """Persistence is opportunistic — Mongo errors must NEVER
    propagate. Operator sees a warning; in-memory state survives."""
    monkeypatch.setenv("LEARNING_CORE_PERSISTENCE_ENABLED", "true")
    fake_collection = MagicMock()
    fake_collection.update_one = AsyncMock(
        side_effect=RuntimeError("network down"),
    )
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    out = await persist_resolved_memory(fake_db, _mem())
    assert out["persisted"] is False
    assert "network down" in out["reason"]


@pytest.mark.asyncio
async def test_rehydrate_returns_empty_on_failure():
    """Caller decides whether to crash on a missing collection;
    default is run-cold so a fresh pod boots cleanly."""
    fake_db = MagicMock()
    fake_db.__getitem__.side_effect = RuntimeError("no collection")
    out = await rehydrate_resolved_memories(fake_db)
    assert out == []


@pytest.mark.asyncio
async def test_rehydrate_returns_empty_when_db_none():
    out = await rehydrate_resolved_memories(None)
    assert out == []


# ─── shadow hook: env gate + side-effect contract ──────────────


def test_shadow_hook_noop_when_flag_off(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_SHADOW_ENABLED", "false")
    payload: dict[str, Any] = {"decision": "LONG", "confidence": 0.7}
    signal: dict[str, Any] = {"symbol": "AAPL"}
    out = attach_learning_core_context(payload, signal)
    assert "learning_core" not in out
    assert out is payload  # mutated in place


def test_shadow_hook_attaches_when_flag_on(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_SHADOW_ENABLED", "true")
    reset_singleton_for_tests()
    payload: dict[str, Any] = {
        "decision": "LONG",
        "confidence": 0.7,
        "symbol": "AAPL",
    }
    signal: dict[str, Any] = {
        "symbol": "AAPL",
        "confidence": 0.7,
        "expected_r": 0.02,
        "regime": "trending",
        "macro": {
            "vix": 18.0,
            "ten_year": 4.6,
            "two_year": 4.5,
            "dxy": 104.0,
        },
    }
    out = attach_learning_core_context(payload, signal)
    assert "learning_core" in out
    lc = out["learning_core"]
    assert lc["direction_canonical"] == "LONG"
    assert 0.0 <= lc["adjusted_confidence"] <= 1.0
    assert lc["shadow_only"] is True


def test_shadow_hook_does_not_mutate_decision(monkeypatch):
    """Most important contract — the hook is observation-only."""
    monkeypatch.setenv("LEARNING_CORE_SHADOW_ENABLED", "true")
    reset_singleton_for_tests()
    payload: dict[str, Any] = {
        "decision": "LONG",
        "confidence": 0.7,
        "risk_multiplier": 1.0,
    }
    signal: dict[str, Any] = {"symbol": "AAPL", "confidence": 0.7}
    attach_learning_core_context(payload, signal)
    assert payload["decision"] == "LONG"
    assert payload["confidence"] == 0.7
    assert payload["risk_multiplier"] == 1.0


def test_shadow_hook_never_raises_on_bad_input(monkeypatch):
    """Defensive — adversarial signals can be mid-refactor and miss
    fields. The hook must degrade gracefully, not crash."""
    monkeypatch.setenv("LEARNING_CORE_SHADOW_ENABLED", "true")
    reset_singleton_for_tests()
    out = attach_learning_core_context({}, {})
    # Either attached with UNKNOWN or skipped — neither crashes.
    assert out == {} or "learning_core" in out


def test_shadow_hook_unknown_direction_canonicalises_to_unknown(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_SHADOW_ENABLED", "true")
    reset_singleton_for_tests()
    payload: dict[str, Any] = {"decision": "BANANA", "confidence": 0.5}
    signal: dict[str, Any] = {"symbol": "AAPL"}
    out = attach_learning_core_context(payload, signal)
    if "learning_core" in out:  # hook ran
        assert out["learning_core"]["direction_canonical"] == "UNKNOWN"


# ─── end-to-end: run_adversarial_decision still works ──────────


@pytest.mark.asyncio
async def test_run_adversarial_decision_payload_unchanged_when_shadow_off(
    monkeypatch,
):
    """The hook must not break the existing adversarial flow when
    the env flag is off — same payload keys, same shape."""
    monkeypatch.setenv("LEARNING_CORE_SHADOW_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    monkeypatch.setenv("ADVERSARIAL_PHASE", "shadow")

    from services.adversarial_core import run_adversarial_decision

    fake_db = MagicMock()
    fake_db.catalyst_snapshots.find_one = AsyncMock(return_value=None)

    signal = {
        "symbol": "AAPL",
        "confidence": 0.65,
        "expected_r": 0.02,
        "regime": "trending",
        "macro": {"vix": 18, "ten_year": 4.6, "two_year": 4.5},
        "strategist": {"indicators": {"rsi": 50, "momentum_5b": 0.1}},
        "rsi": 50,
        "macd_hist": 0.1,
        "atr_pct": 0.02,
    }
    out = await run_adversarial_decision(fake_db, signal)
    assert out is not None
    assert "decision" in out
    assert "bull_case" in out
    assert "bear_case" in out
    assert "learning_core" not in out  # off
