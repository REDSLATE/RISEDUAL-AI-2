"""Toxic spike seal — tripwires (2026-06-16).

Operator caught a Toxic Spike Alert email showing 31 high-confidence
failures, 7 of them at EXACTLY 100.0% — a classic sigmoid saturation
smell. The seal has three layers:

  1. ``normalize_confidence`` clamps every input to a max of 95.0.
     No honest model outputs 1.0; the cap makes saturation visible
     instead of letting it poison Chroma's toxic_lesson pool.

  2. ``purge_toxic_lessons`` deletes (not re-tags) toxic episodes
     so they stop surfacing as kNN neighbours during perception.

  3. ``/api/admin/toxic-purge/all`` exposes the wipe to the
     operator (owner-only).

Regression pins below.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.prediction_tracker import normalize_confidence


# ── 1. normalize_confidence cap ──────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    (None, 0.0),
    (-0.5, 0.0),     # negative coerced to 0
    (0.0, 0.0),
    (0.5, 50.0),     # fractional scale
    (0.95, 95.0),    # cap hit on fractional input
    (0.99, 95.0),    # cap clamps
    (1.0, 95.0),     # the most common "saturation" value
    (50.0, 50.0),    # already on 0-100
    (94.9, 94.9),    # just under cap
    (95.0, 95.0),    # exactly at cap
    (99.9, 95.0),    # clamped
    (100.0, 95.0),   # the toxic value from the alert
    (150.0, 95.0),   # impossible value still clamped
])
def test_normalize_confidence_caps_at_95(raw, expected):
    assert normalize_confidence(raw) == expected


def test_cap_hit_emits_log(caplog):
    caplog.set_level("INFO")
    normalize_confidence(1.0)
    matches = [r for r in caplog.records if "cap-hit" in r.message]
    assert len(matches) >= 1


def test_normalize_confidence_no_cap_below_threshold(caplog):
    caplog.set_level("INFO")
    normalize_confidence(0.5)
    matches = [r for r in caplog.records if "cap-hit" in r.message]
    assert matches == []


# ── 2. purge_toxic_lessons targets toxic_lesson + high-conf miss ─────


@pytest.mark.asyncio
async def test_purge_toxic_lessons_targets_correct_rows(monkeypatch):
    """The purge deletes:
      * any outcome=='toxic_lesson' (already-retagged)
      * any outcome=='miss' with confidence > floor

    It must leave outcome=='hit' and outcome=='miss' with low conf
    alone — those carry useful learning signal."""
    from services import market_memory_service as mms

    fake_get = MagicMock(return_value={
        "ids": ["toxic-1", "miss-hi-1", "miss-lo-1", "hit-1", "neutral-1"],
        "metadatas": [
            {"outcome": "toxic_lesson", "confidence": 92.0},
            {"outcome": "miss",        "confidence": 88.0},
            {"outcome": "miss",        "confidence": 55.0},
            {"outcome": "hit",         "confidence": 92.0},
            {"outcome": "neutral",     "confidence": 70.0},
        ],
    })
    deleted: list[list[str]] = []

    def fake_delete(ids):
        deleted.append(list(ids))

    fake_collection = MagicMock()
    fake_collection.get = fake_get
    fake_collection.delete = fake_delete

    monkeypatch.setattr(mms, "_collection", fake_collection)

    out = await mms.purge_toxic_lessons(confidence_floor=80.0)
    assert out["ok"] is True
    assert out["deleted"] == 2
    assert out["scanned"] == 5

    # Verify which ids were actually targeted.
    all_deleted = [i for batch in deleted for i in batch]
    assert set(all_deleted) == {"toxic-1", "miss-hi-1"}


@pytest.mark.asyncio
async def test_purge_toxic_lessons_no_collection():
    """When Chroma isn't initialized, the purge returns a clean
    error envelope (never raises)."""
    from services import market_memory_service as mms
    mms._collection = None
    out = await mms.purge_toxic_lessons()
    assert out["ok"] is False
    assert "deleted" in out


@pytest.mark.asyncio
async def test_purge_toxic_lessons_empty_collection(monkeypatch):
    from services import market_memory_service as mms
    fake_collection = MagicMock()
    fake_collection.get.return_value = {"ids": [], "metadatas": []}
    monkeypatch.setattr(mms, "_collection", fake_collection)
    out = await mms.purge_toxic_lessons()
    assert out["ok"] is True
    assert out["deleted"] == 0
    assert out["scanned"] == 0


# ── 3. Source-level pins on saturation sources ───────────────────────


def test_sovereign_ai_core_caps_confidence_at_95():
    """Pin: sovereign_ai_core aggregation must clamp every step at
    0.95, not 1.0. Regression to 1.0 would reintroduce the saturation
    pattern that minted the operator's toxic spikes."""
    import inspect
    from services import sovereign_ai_core
    src = inspect.getsource(sovereign_ai_core)
    # The bug pattern. There must be NO remaining min(1.0, confidence...)
    # in the catalyst/options aggregation block.
    coordinator = inspect.getsource(sovereign_ai_core.SovereignCoordinator.aggregate) if hasattr(sovereign_ai_core, "SovereignCoordinator") and hasattr(sovereign_ai_core.SovereignCoordinator, "aggregate") else src
    assert "min(_CONF_CAP" in coordinator or "0.95" in coordinator, (
        "sovereign_ai_core must use _CONF_CAP=0.95 (or literal 0.95) "
        "for confidence clamping (regression of 2026-06-18 toxic-spike "
        "saturation RCA)."
    )


def test_sovereign_promotion_gate_caps_at_95():
    """Symmetric pin for the promotion gate."""
    import inspect
    from services import sovereign_promotion_gate
    src = inspect.getsource(sovereign_promotion_gate.maybe_apply_contribution) if hasattr(sovereign_promotion_gate, "maybe_apply_contribution") else inspect.getsource(sovereign_promotion_gate)
    assert "min(0.95" in src, (
        "sovereign_promotion_gate must clamp production_confidence at "
        "0.95 (regression of 2026-06-18 toxic-spike saturation RCA)."
    )
