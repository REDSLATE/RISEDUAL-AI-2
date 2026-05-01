"""
Tests for the PredictionDirection schema enum, the unknown-direction
metric helper, and the writer-side validation guard.

Together these form the "canonicalize at entry" contract from the
2026-05-01 hardening:

  1. Raw AI verdicts hit the system exactly once.
  2. They are validated / canonicalised into the enum at the write
     boundary (log_prediction).
  3. Every downstream service uses the canonical value.
  4. Any token that slips past the enum fires a loud, visible signal
     (logger.warning + in-memory counter + Mongo audit row).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.prediction_tracker import (
    PredictionDirection,
    canonical_ai_dir,
    record_unknown_direction_token,
    get_unknown_direction_counter_snapshot,
    reset_unknown_direction_counter,
)


# ── Enum completeness ─────────────────────────────────────────────


def test_enum_covers_every_strong_and_weak_variant():
    """The enum is the single source of truth. STRONG_* / WEAK_* /
    canonical LONG/SHORT/BUY/SELL MUST all be present."""
    required = {
        "STRONG_BUY", "BUY", "WEAK_BUY", "BULLISH", "LONG", "UP",
        "HOLD", "NEUTRAL", "WAIT",
        "WEAK_SELL", "SELL", "STRONG_SELL", "BEARISH", "SHORT", "DOWN",
    }
    present = {m.value for m in PredictionDirection}
    missing = required - present
    assert not missing, f"PredictionDirection missing tokens: {missing}"


def test_enum_validate_accepts_all_canonical_values():
    for m in PredictionDirection:
        assert PredictionDirection.validate(m.value) is m


def test_enum_validate_normalises_lowercase_and_whitespace():
    assert PredictionDirection.validate("strong_buy") is PredictionDirection.STRONG_BUY
    assert PredictionDirection.validate("  Weak_Sell  ") is PredictionDirection.WEAK_SELL


def test_enum_validate_rejects_garbage():
    with pytest.raises(ValueError) as exc:
        PredictionDirection.validate("MAYBE_KIND_OF_BUY")
    # Error message must actually list the valid vocabulary — the
    # caller who emitted the bad token needs a signpost.
    assert "STRONG_BUY" in str(exc.value)


def test_enum_validate_rejects_none_and_empty():
    with pytest.raises(ValueError):
        PredictionDirection.validate(None)
    with pytest.raises(ValueError):
        PredictionDirection.validate("")


def test_enum_and_direction_sets_agree():
    """Any enum value MUST classify as LONG / SHORT / UNKNOWN
    consistently via canonical_ai_dir. If an enum value ever
    classifies differently from the direction sets, the cleanup
    drifted — bug class incoming."""
    for m in PredictionDirection:
        canon = canonical_ai_dir(m.value)
        if m in {PredictionDirection.HOLD, PredictionDirection.NEUTRAL, PredictionDirection.WAIT}:
            assert canon == "UNKNOWN", f"{m.value} should be UNKNOWN in canonical_ai_dir (it's a non-trade side)"
        elif m.value in {"BUY", "BULLISH", "LONG", "UP", "STRONG_BUY", "WEAK_BUY"}:
            assert canon == "LONG", f"{m.value} MUST classify as LONG"
        else:
            assert canon == "SHORT", f"{m.value} MUST classify as SHORT"


# ── Unknown-direction metric helper ───────────────────────────────


def test_counter_starts_empty_after_reset():
    reset_unknown_direction_counter()
    assert get_unknown_direction_counter_snapshot() == {}


@pytest.mark.asyncio
async def test_record_increments_in_memory_counter_per_context():
    reset_unknown_direction_counter()
    await record_unknown_direction_token("FOO", context="ctx_a")
    await record_unknown_direction_token("BAR", context="ctx_a")
    await record_unknown_direction_token("BAZ", context="ctx_b")
    snap = get_unknown_direction_counter_snapshot()
    assert snap["ctx_a"] == 2
    assert snap["ctx_b"] == 1


@pytest.mark.asyncio
async def test_record_writes_audit_row_when_db_supplied():
    reset_unknown_direction_counter()
    mock_collection = AsyncMock()
    mock_db = MagicMock()
    mock_db.data_integrity_metrics = mock_collection
    await record_unknown_direction_token("GARBAGE", context="unit_test", db=mock_db)
    mock_collection.insert_one.assert_awaited_once()
    inserted = mock_collection.insert_one.await_args.args[0]
    assert inserted["metric"] == "unknown_direction_token"
    assert inserted["token"] == "GARBAGE"
    assert inserted["context"] == "unit_test"
    assert "fired_at" in inserted
    assert "_id" in inserted


@pytest.mark.asyncio
async def test_record_swallows_db_failures_silently():
    """The metric path MUST never break the trading hot path. A
    failing Mongo write should NOT propagate."""
    reset_unknown_direction_counter()
    mock_db = MagicMock()
    mock_db.data_integrity_metrics.insert_one = AsyncMock(
        side_effect=RuntimeError("mongo down")
    )
    # Must not raise.
    await record_unknown_direction_token("X", context="ctx", db=mock_db)
    # And the in-memory counter must still tick, because that's
    # cheap and local.
    assert get_unknown_direction_counter_snapshot()["ctx"] == 1


# ── grade_prediction unknown-token counter integration ────────────


def test_grade_prediction_unknown_bumps_counter():
    """Confirms the sync path in grade_prediction still hits the
    counter even when no event loop / db is available."""
    reset_unknown_direction_counter()
    from services.prediction_tracker import grade_prediction
    grade = grade_prediction(
        direction="MAYBE_KIND_OF_BUY",
        price_at_prediction=100.0,
        price_now=110.0,
    )
    assert grade == "STRONG_MISS"  # safety net still fires
    assert (
        get_unknown_direction_counter_snapshot().get("grade_prediction", 0) >= 1
    ), "grade_prediction must bump the unknown-direction counter on garbage input"
