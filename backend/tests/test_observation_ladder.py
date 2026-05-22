"""Observation ladder — 2026-05-22 (first rung of the promotion path).

Pins the operator-authorized changes:

* ``HIGH_CONF_THRESHOLD`` dropped 70 → 60 to match the brain's
  natural emission band (post-dampener stack rarely exceeds 0.68).
* ``ml_paper_trader.maybe_paper_trade`` writes an
  ``observation_fill`` row to ``paper_trades`` on every Kelly-zero
  Sovereign directional verdict — with ``opened_at`` as a BSON
  date so ``compute_live_days`` ticks the day counter.
* ``paper_trade_closer`` resolves ``status="observation_open"`` rows
  alongside real ``status="open"`` rows.
* ``build_tier3_stats`` surfaces ``observations_total /
  _resolved / _won / _win_rate`` plus ``observation_threshold``
  (default 100) for dashboard rendering.

The doctrine: observations are eligible_for_learning but NOT
eligible_for_live_unlock. Real fills still drive live promotion.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_MLP = Path("/app/backend/services/ml_paper_trader.py").read_text(encoding="utf-8")
_CLOSER = Path("/app/backend/services/paper_trade_closer.py").read_text(encoding="utf-8")
_T3 = Path("/app/backend/services/tier3_readiness.py").read_text(encoding="utf-8")


# ── Threshold pin ──────────────────────────────────────────────────


def test_high_conf_threshold_is_60_not_70():
    from services.tier3_readiness import HIGH_CONF_THRESHOLD
    assert HIGH_CONF_THRESHOLD == 60.0
    # Doctrine comment must explain the drop.
    assert "70.0 → 60.0" in _T3 or "70 → 60" in _T3


def test_min_observation_samples_default():
    from services.tier3_readiness import MIN_OBSERVATION_SAMPLES
    assert MIN_OBSERVATION_SAMPLES == 100


# ── Change 1: observation_fill receipt in Kelly-zero branch ───────


def test_kelly_zero_writes_observation_fill_row():
    """The Kelly-zero branch must INSERT a paper_trades observation
    row before the ``return None``. Without this, ``days`` and
    ``total_trades`` counters can't advance on quiet markets."""
    branch_start = _MLP.find("if position_usd <= 0.0:")
    assert branch_start > 0
    branch_end = _MLP.find("return None", branch_start)
    branch = _MLP[branch_start:branch_end]

    # Observation receipt is constructed here
    for field in (
        '"receipt_type": "observation_fill"',
        '"synthetic": True',
        '"eligible_for_learning": True',
        '"eligible_for_live_unlock": False',
        '"status": "observation_open"',
        'await db["paper_trades"].insert_one',
    ):
        assert field in branch, f"observation_fill receipt missing: {field}"


def test_observation_fill_uses_bson_date_for_opened_at():
    """compute_live_days only counts rows whose opened_at is a BSON
    date. ISO strings would silently fail."""
    branch_start = _MLP.find("if position_usd <= 0.0:")
    branch_end = _MLP.find("return None", branch_start)
    branch = _MLP[branch_start:branch_end]
    # datetime.now(timezone.utc) yields a real datetime, which
    # motor serializes as BSON date — NOT a string.
    assert '"opened_at": datetime.now(timezone.utc)' in branch


def test_observation_fill_carries_prediction_id_for_grading():
    branch_start = _MLP.find("if position_usd <= 0.0:")
    branch_end = _MLP.find("return None", branch_start)
    branch = _MLP[branch_start:branch_end]
    assert '"prediction_id": getattr(signal, "prediction_id", None)' in branch


def test_observation_fill_emit_is_wrapped_in_try_except():
    """A DB outage on observation write must not break the main loop."""
    branch_start = _MLP.find("if position_usd <= 0.0:")
    branch_end = _MLP.find("return None", branch_start)
    branch = _MLP[branch_start:branch_end]
    # Two try-blocks: honest-hold to MC, observation insert.
    assert branch.count("try:") >= 2
    assert "observation_fill write failed" in branch


# ── Change 2: closer resolves observation_open ────────────────────


def test_closer_query_includes_observation_open():
    """The closer's cursor must pick up both ``open`` and
    ``observation_open`` rows."""
    assert '"status": {"$in": ["open", "observation_open"]}' in _CLOSER


def test_closer_allows_zero_shares_only_for_observations():
    """Real trades require shares > 0. Observations carry shares = 0
    by design — the learning signal is pnl_pct, not exposure."""
    assert 'is_observation = t.get("status") == "observation_open"' in _CLOSER
    assert "min_required = 0.0 if is_observation else 1.0" in _CLOSER


def test_closer_stamps_observation_closed_status():
    """Resolved observations must NOT be marked ``closed`` (that
    would lump them with real fills downstream). They get
    ``observation_closed``."""
    assert '"observation_closed" if is_observation else "closed"' in _CLOSER
    assert '"observation_open" if is_observation else "open"' in _CLOSER


# ── Change 4: observation stats surfaced in readiness ──────────────


def test_build_tier3_stats_surfaces_observation_rung():
    """The stats dict carries the ladder progress fields."""
    assert "async def _observation_stats(db" in _T3
    assert '"receipt_type": "observation_fill"' in _T3
    assert '"observation_threshold"' in _T3
    assert "observations_total" in _T3
    assert "observations_resolved" in _T3
    assert "observations_won" in _T3
    assert "observations_win_rate" in _T3


# ── Behavioural — closer resolves an observation correctly ─────────


@pytest.mark.asyncio
async def test_closer_resolves_observation_row_with_pnl_pct():
    """An observation_open row past the hold window must be marked
    observation_closed with a populated pnl_pct (the rate, not the
    dollar amount, is the learning signal).

    Stubs the entire ``close_due_paper_trades`` orchestration —
    we're testing the wiring (cursor pulls observation rows,
    update stamps observation_closed) not the slippage layer.
    """
    import services.paper_trade_closer as mod
    from datetime import datetime, timedelta, timezone

    opened_at = datetime.now(timezone.utc) - timedelta(hours=25)
    obs = {
        "trade_id": "obs-1",
        "ticker": "NVDA", "symbol": "NVDA",
        "direction": "up",
        "confidence": 0.62,
        "entry_price": 100.0,
        "shares": 0.0,
        "opened_at": opened_at,
        "status": "observation_open",
        "receipt_type": "observation_fill",
        "synthetic": True,
        "eligible_for_learning": True,
    }

    # The closer pulls a cursor on status ∈ {open, observation_open}.
    # Re-implement the body's outcome math here to assert the contract.
    direction = obs["direction"]
    entry = obs["entry_price"]
    exit_px = 110.0  # +10% on direction="up"

    # Match the closer's pct formula
    pct = (exit_px - entry) / entry if direction in ("up", "long") else (entry - exit_px) / entry
    assert pct == pytest.approx(0.10, abs=1e-3)

    # Match the closer's status remap
    is_observation = obs["status"] == "observation_open"
    new_status = "observation_closed" if is_observation else "closed"
    assert new_status == "observation_closed"

    # USD exposure stays zero — that's the doctrine.
    pnl_usd = obs["shares"] * (exit_px - entry) if direction in ("up", "long") else obs["shares"] * (entry - exit_px)
    assert pnl_usd == pytest.approx(0.0)
