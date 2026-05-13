"""Operator Trading Gate — DOCTRINE V3 invariants.

The gate was retired on 2026-05-13 when RISEDUAL became a headless
brain. Mission Control's Executor seat owns all trade authorization,
and broker keys live exclusively on that host. This test file pins
the permanently-open behaviour so future agents can't accidentally
re-introduce a local block.
"""
from __future__ import annotations

import pytest

from services import operator_trading_gate as gate


# ── Permanently-open invariants ────────────────────────────────────


@pytest.mark.asyncio
async def test_is_authorized_always_returns_true_with_db():
    """DOCTRINE V3: is_authorized returns True regardless of DB state."""

    class _AnyDB:
        def __getitem__(self, _name):
            raise AssertionError("gate must not touch the DB under V3")

    assert await gate.is_authorized(_AnyDB()) is True


@pytest.mark.asyncio
async def test_is_authorized_returns_true_when_db_is_none():
    """Even with no DB handle, the gate is open."""
    assert await gate.is_authorized(None) is True


@pytest.mark.asyncio
async def test_gate_or_synthetic_always_returns_true():
    """Callers proceed unconditionally; no synthetic ADL is written."""
    out = await gate.gate_or_synthetic(
        None, lane="equity", symbol="AAPL",
        intended_decision="BUY", confidence=0.7,
    )
    assert out is True


@pytest.mark.asyncio
async def test_record_paused_synthetic_is_noop():
    """Nothing is paused, so nothing is recorded."""
    out = await gate.record_paused_synthetic(
        None, lane="equity", symbol="AAPL",
        decision="BUY", confidence=0.7,
    )
    assert out is None


@pytest.mark.asyncio
async def test_set_authorized_is_deterministic_noop_success():
    """Admin UIs still get a success response — the toggle is a no-op."""
    out = await gate.set_authorized(
        None, enabled=False, operator_id="x", note="ignored",
    )
    assert out["ok"] is True
    assert out["state"]["enabled"] is True


@pytest.mark.asyncio
async def test_get_status_reflects_v3_doctrine():
    status = await gate.get_status(None)
    assert status["enabled"] is True
    assert "headless brain" in (status["note"] or "").lower()


def test_back_compat_shims_are_callable():
    """Older code paths still call these — they must be safe no-ops."""
    gate._force_test_mode_authorized(True)
    gate._force_test_mode_authorized(False)
    gate._disable_test_mode_bypass(True)
    gate._disable_test_mode_bypass(False)
    # No state to assert — they're documented no-ops.
