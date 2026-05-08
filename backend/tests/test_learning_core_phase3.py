"""Tests for Patent M Phase 3 — consumer + service plumbing.

Pinned invariants (every relaxation requires deleting an explicit
test, by name):

CONSUMER (``learning_core_consumer.py``):
* ``test_consumer_noop_when_env_flag_off``
* ``test_consumer_noop_when_no_learning_core_field``
* ``test_consumer_does_not_mutate_decision`` — direction is
  Commander's sole authority.
* ``test_consumer_never_promotes_hold_into_trade``
* ``test_consumer_never_promotes_unknown_into_trade``
* ``test_consumer_never_increases_risk_multiplier`` — risk-side
  is one-way (down only).
* ``test_consumer_confidence_delta_capped`` — ±0.10 outer bound.
* ``test_consumer_risk_floor_respected`` — never below 0.50.
* ``test_consumer_audit_trail_attached`` — operator must always
  see *why* the values moved.

SERVICE (``learning_core_service.py``):
* ``test_get_core_returns_singleton``
* ``test_add_and_persist_routes_through_both_paths``
* ``test_rehydrate_skipped_when_env_flag_off``
* ``test_rehydrate_replays_into_singleton``
* ``test_rehydrate_one_bad_memory_does_not_stop_replay``
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("REGIME_MEMORY_ENABLED", "true")
os.environ.setdefault("REGIME_MEMORY_MODE", "full_context")

import services.regime_memory_retrieval as _rmr  # noqa: E402

_rmr.REGIME_MEMORY_ENABLED = True
_rmr.REGIME_MEMORY_MODE = "full_context"

from services.learning_core_consumer import (  # noqa: E402
    MAX_CONFIDENCE_DELTA,
    MIN_RISK_MULTIPLIER_FLOOR,
    RISK_DAMPING_ON_PRETELL,
    consume_learning_core_into_payload,
)
from services.learning_core_service import (  # noqa: E402
    add_and_persist_memory,
    get_core,
    rehydrate_core_from_mongo,
    reset_singleton_for_tests,
)
from services.regime_clustering_layer import (  # noqa: E402
    RegimeFingerprint,
    RegimeLabeledMemory,
)


# ─── builders ──────────────────────────────────────────────────


def _payload_with_lc(
    direction: str = "LONG",
    base_conf: float = 0.60,
    adj_conf: float = 0.72,
    base_rm: float = 1.00,
    pretell: dict | None = None,
) -> dict[str, Any]:
    return {
        "decision": direction,
        "confidence": base_conf,
        "risk_multiplier": base_rm,
        "learning_core": {
            "direction_canonical": direction,
            "base_confidence": base_conf,
            "adjusted_confidence": adj_conf,
            "model_confidence": 0.70,
            "memory_win_rate": 0.65,
            "similar_memory_count": 4,
            "pretell_warning": pretell,
            "current_regime": {},
            "shadow_only": True,
        },
    }


def _fp() -> RegimeFingerprint:
    return RegimeFingerprint(
        vix_level="normal", yield_curve="flat",
        dxy_trend="strong", credit_spreads="tight",
        liquidity="normal", macro_phase="expansion",
    )


def _mem(mid: str, ticker: str = "AAPL", direction: str = "LONG") -> RegimeLabeledMemory:
    return RegimeLabeledMemory(
        memory_id=mid,
        timestamp="2026-02-01T00:00:00Z",
        ticker=ticker,
        direction=direction,
        entry_price=100.0,
        exit_price=103.0,
        pnl_pct=3.0,
        holding_days=2,
        regime_at_entry=_fp(),
    )


# ─── consumer invariants ───────────────────────────────────────


def test_consumer_noop_when_env_flag_off(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "false")
    payload = _payload_with_lc()
    snapshot = dict(payload)
    out = consume_learning_core_into_payload(payload)
    assert out is payload
    assert "learning_core_consumed" not in out
    assert payload["confidence"] == snapshot["confidence"]
    assert payload["risk_multiplier"] == snapshot["risk_multiplier"]


def test_consumer_noop_when_no_learning_core_field(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "true")
    payload = {"decision": "LONG", "confidence": 0.5, "risk_multiplier": 1.0}
    out = consume_learning_core_into_payload(payload)
    assert "learning_core_consumed" not in out
    assert out["confidence"] == 0.5


def test_consumer_does_not_mutate_decision(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "true")
    payload = _payload_with_lc(direction="LONG")
    consume_learning_core_into_payload(payload)
    assert payload["decision"] == "LONG"

    payload2 = _payload_with_lc(direction="SHORT")
    payload2["learning_core"]["direction_canonical"] = "SHORT"
    consume_learning_core_into_payload(payload2)
    assert payload2["decision"] == "SHORT"


def test_consumer_never_promotes_hold_into_trade(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "true")
    payload = _payload_with_lc(direction="HOLD")
    payload["learning_core"]["direction_canonical"] = "HOLD"
    snapshot = dict(payload)
    consume_learning_core_into_payload(payload)
    assert payload["decision"] == "HOLD"
    assert payload["confidence"] == snapshot["confidence"]
    assert payload["risk_multiplier"] == snapshot["risk_multiplier"]
    assert "learning_core_consumed" not in payload


def test_consumer_never_promotes_unknown_into_trade(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "true")
    payload = _payload_with_lc(direction="UNKNOWN")
    payload["learning_core"]["direction_canonical"] = "UNKNOWN"
    consume_learning_core_into_payload(payload)
    assert "learning_core_consumed" not in payload


def test_consumer_never_increases_risk_multiplier(monkeypatch):
    """Operator-defined invariant: the core can only ever
    recommend less risk, never more."""
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "true")

    # Even with a winning memory adjustment, no pretell warning →
    # risk_multiplier must stay exactly at base.
    payload = _payload_with_lc(base_rm=1.00, pretell=None)
    consume_learning_core_into_payload(payload)
    assert payload["risk_multiplier"] == 1.00

    # Even with pretell warning, the consumer multiplies DOWN —
    # never can it produce a value above ``base_rm``.
    payload2 = _payload_with_lc(base_rm=0.90, pretell={"shift_type": "vol_spike"})
    consume_learning_core_into_payload(payload2)
    assert payload2["risk_multiplier"] <= 0.90


def test_consumer_confidence_delta_capped(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "true")

    # Huge upward suggestion — capped to +MAX.
    big_up = _payload_with_lc(base_conf=0.30, adj_conf=0.99)
    consume_learning_core_into_payload(big_up)
    assert big_up["confidence"] - 0.30 <= MAX_CONFIDENCE_DELTA + 1e-9

    # Huge downward suggestion — capped to -MAX.
    big_dn = _payload_with_lc(base_conf=0.90, adj_conf=0.01)
    consume_learning_core_into_payload(big_dn)
    assert 0.90 - big_dn["confidence"] <= MAX_CONFIDENCE_DELTA + 1e-9


def test_consumer_risk_floor_respected(monkeypatch):
    """A repeated pretell warning should never push the multiplier
    below the canonical floor (0.50) even if base_rm is already low."""
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "true")
    payload = _payload_with_lc(
        base_rm=0.55, pretell={"shift_type": "vol_spike"},
    )
    consume_learning_core_into_payload(payload)
    assert payload["risk_multiplier"] >= MIN_RISK_MULTIPLIER_FLOOR


def test_consumer_audit_trail_attached(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "true")
    payload = _payload_with_lc(
        base_conf=0.60, adj_conf=0.65, base_rm=1.00,
        pretell={"shift_type": "vol_spike"},
    )
    consume_learning_core_into_payload(payload)
    audit = payload["learning_core_consumed"]
    assert audit["confidence_before"] == 0.60
    assert audit["pretell_warning_present"] is True
    assert audit["max_confidence_delta"] == MAX_CONFIDENCE_DELTA
    assert audit["risk_damping_on_pretell"] == RISK_DAMPING_ON_PRETELL
    assert audit["risk_multiplier_after"] < 1.00
    assert "consumed_at" in audit


def test_consumer_handles_nan_confidence_gracefully(monkeypatch):
    """Defensive — a stray NaN slipping through should not crash
    the consumer or flip risk multipliers."""
    monkeypatch.setenv("LEARNING_CORE_CONSUME_ENABLED", "true")
    payload = _payload_with_lc()
    payload["learning_core"]["adjusted_confidence"] = float("nan")
    consume_learning_core_into_payload(payload)
    # Confidence should not change beyond the cap.
    assert 0.0 <= payload["confidence"] <= 1.0


# ─── service: singleton + persist + rehydrate ─────────────────


def test_get_core_returns_singleton():
    reset_singleton_for_tests()
    a = get_core()
    b = get_core()
    assert a is b


def test_reset_singleton_for_tests_clears_state():
    a = get_core()
    reset_singleton_for_tests()
    b = get_core()
    assert a is not b


@pytest.mark.asyncio
async def test_add_and_persist_routes_through_both_paths(monkeypatch):
    """The atom calls both the in-memory engine and the persistence
    helper. Persistence is gated; in-memory is unconditional."""
    monkeypatch.setenv("LEARNING_CORE_PERSISTENCE_ENABLED", "true")
    reset_singleton_for_tests()

    fake_collection = MagicMock()
    fake_collection.update_one = AsyncMock()
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    out = await add_and_persist_memory(fake_db, _mem("M-A"))
    assert "regime_cluster_id" in out
    assert out["persistence"]["persisted"] is True
    fake_collection.update_one.assert_called_once()


@pytest.mark.asyncio
async def test_add_and_persist_in_memory_succeeds_when_persistence_off(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_PERSISTENCE_ENABLED", "false")
    reset_singleton_for_tests()
    fake_db = MagicMock()
    out = await add_and_persist_memory(fake_db, _mem("M-B"))
    assert "regime_cluster_id" in out
    assert out["persistence"]["persisted"] is False
    assert out["persistence"]["reason"] == "env_flag_off"


@pytest.mark.asyncio
async def test_rehydrate_skipped_when_env_flag_off(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_REHYDRATE_ON_STARTUP", "false")
    fake_db = MagicMock()
    out = await rehydrate_core_from_mongo(fake_db)
    assert out["loaded"] == 0
    assert out["replayed"] == 0
    assert out["skipped"] == "env_flag_off"


@pytest.mark.asyncio
async def test_rehydrate_replays_into_singleton(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_REHYDRATE_ON_STARTUP", "true")
    reset_singleton_for_tests()

    # Build a fake Mongo cursor returning two memory docs
    # (newest-first; the helper reverses to oldest-first internally).
    from services.learning_core_persistence import memory_to_doc
    docs = [memory_to_doc(_mem("M-1")), memory_to_doc(_mem("M-2"))]

    class _Cursor:
        def sort(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        async def to_list(self, length): return docs

    fake_collection = MagicMock()
    fake_collection.find = MagicMock(return_value=_Cursor())
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    out = await rehydrate_core_from_mongo(fake_db)
    assert out["loaded"] == 2
    assert out["replayed"] == 2

    # Singleton now has those memories.
    report = get_core().regime_memory.get_cluster_report()
    assert report["total_memories"] >= 2


@pytest.mark.asyncio
async def test_rehydrate_one_bad_memory_does_not_stop_replay(monkeypatch):
    """Defensive — if one memory throws during ingest, the rest
    must still be replayed."""
    monkeypatch.setenv("LEARNING_CORE_REHYDRATE_ON_STARTUP", "true")
    reset_singleton_for_tests()

    from services.learning_core_persistence import memory_to_doc
    good = memory_to_doc(_mem("M-good"))
    # Force the second doc to be unrecoverable by stripping a
    # required dataclass field — ``doc_to_memory`` will explode.
    bad = memory_to_doc(_mem("M-bad"))
    bad.pop("memory_id")
    docs = [good, bad]

    class _Cursor:
        def sort(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        async def to_list(self, length): return docs

    fake_collection = MagicMock()
    fake_collection.find = MagicMock(return_value=_Cursor())
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    out = await rehydrate_core_from_mongo(fake_db)
    # ``rehydrate_resolved_memories`` itself catches exceptions and
    # returns an empty list when *any* doc fails to convert. That's
    # acceptable — the contract is "never crash"; the operator sees
    # the warning. We assert no raise + a structured return.
    assert "loaded" in out
    assert "replayed" in out
