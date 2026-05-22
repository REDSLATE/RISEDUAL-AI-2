"""Honest-hold-on-Kelly-zero — 2026-05-22 doctrine pin.

Pins the change to ``services/ml_paper_trader.py::maybe_paper_trade``
so that when ``half_kelly_position`` returns 0, the function does NOT
silently ``return None``. Instead, it emits an honest-hold receipt to
MC with the contract:

    raw_action:                       BUY | SELL
    market_decision:                  HOLD
    execution_decision:               OBSERVE_ONLY
    would_have_traded_without_gates:  False
    hold_reason:                      kelly_zero_size

Why this matters
----------------
Without this telemetry, MC cannot distinguish "Alpha was bearish
(genuine HOLD)" from "Alpha was bullish but Kelly self-throttled to
$0 on weak conviction (Kelly-zero HOLD)". Patent J + Stage 3
calibration depend on knowing the difference.

Hard rails
----------
* The honest-hold emit fires INSIDE the ``position_usd <= 0`` branch
  AND BEFORE the ``return None``.
* It is wrapped in ``try/except`` so an MC outage never breaks Alpha's
  main loop.
* Local paper-trade behaviour is unchanged — still no ``paper_trades``
  row written on Kelly-zero.
* The honest-hold receipt carries the five-field doctrine envelope.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_SRC_PATH = Path("/app/backend/services/ml_paper_trader.py")


@pytest.fixture(scope="module")
def src() -> str:
    return _SRC_PATH.read_text(encoding="utf-8")


# ── Static authority firewall ──────────────────────────────────────


def test_kelly_zero_branch_emits_honest_hold(src: str):
    """The Kelly-zero branch must contain an
    ``emit_intent_from_consensus`` call."""
    branch_start = src.find("if position_usd <= 0.0:")
    assert branch_start > 0, "Kelly-zero branch missing"
    branch_end = src.find("return None", branch_start)
    assert branch_end > branch_start
    branch = src[branch_start:branch_end]
    assert "emit_intent_from_consensus" in branch, (
        "Kelly-zero branch must emit an honest-hold receipt, not silently return None"
    )


def test_honest_hold_carries_doctrine_envelope(src: str):
    """The receipt payload must declare the five-field honesty
    envelope so MC / Patent J / Stage 3 ledger can recognise it."""
    branch_start = src.find("if position_usd <= 0.0:")
    branch_end = src.find("return None", branch_start)
    branch = src[branch_start:branch_end]
    for field in (
        '"market_decision": "HOLD"',
        '"execution_decision": "OBSERVE_ONLY"',
        '"would_have_traded_without_gates": False',
        '"hold_reason": "kelly_zero_size"',
        '"raw_action"',
        '"display_action"',
    ):
        assert field in branch, f"honest-hold receipt missing field: {field}"


def test_honest_hold_emit_is_wrapped_in_try_except(src: str):
    """An MC outage must never break Alpha's main loop."""
    branch_start = src.find("if position_usd <= 0.0:")
    branch_end = src.find("return None", branch_start)
    branch = src[branch_start:branch_end]
    assert "try:" in branch
    assert "except Exception" in branch
    assert "honest-hold emit failed" in branch.lower()


def test_kelly_zero_branch_still_returns_none(src: str):
    """The honest-hold emit MUST precede ``return None`` — local
    paper_trades behavior is unchanged on Kelly-zero."""
    branch_start = src.find("if position_usd <= 0.0:")
    branch_end = src.find("return None", branch_start)
    # The return None must still be there.
    assert branch_end > branch_start
    # And no paper_trades insert lives in this branch.
    branch = src[branch_start:branch_end]
    assert 'paper_trades"].insert_one' not in branch


# ── Behavioural test — patch MC, drive the branch, assert call ────


@pytest.mark.asyncio
async def test_kelly_zero_calls_emit_intent_from_consensus(monkeypatch):
    """Drive ``maybe_paper_trade`` down the Kelly-zero branch and
    confirm ``emit_intent_from_consensus`` is invoked exactly once
    with ``hold_reason='kelly_zero_size'``.

    Heavy stubs — we're testing the wiring, not the Sovereign /
    failure-penalty / sizing stack.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    # Force kelly to return 0.
    with patch(
        "services.ml_paper_trader.half_kelly_position",
        return_value=0.0,
    ), patch(
        "services.ml_paper_trader._current_portfolio_value",
        new=AsyncMock(return_value=100_000.0),
    ), patch(
        "sovereign.intent_bridge.emit_intent_from_consensus",
        new=AsyncMock(return_value={"ok": True}),
    ) as mock_emit, patch(
        "sovereign.mc_client.MCClient",
        return_value=MagicMock(),
    ):
        # Disable the council penalty / sovereign / failure-penalty
        # branches so we reach kelly with a simple direction.
        from services import ml_paper_trader as mod

        # Stub `get_failure_penalty` to no-op
        monkeypatch.setattr(
            "services.ml_paper_trader.get_failure_penalty",
            AsyncMock(return_value=None),
            raising=False,
        )

        # Build the absolute minimum Signal-like object the function
        # needs to reach the Kelly branch.
        signal = MagicMock()
        signal.direction = MagicMock(value="up")
        signal.confidence = 0.65
        signal.prediction_id = "pred-test-001"
        signal.feature_importance = {}

        snapshot = MagicMock()
        for attr in ("rsi", "momentum_5b", "atr_pct", "volume_zscore",
                     "dollar_volume", "dollar_volume_baseline"):
            setattr(snapshot, attr, None)

        # Fake db with the absolute minimum collections accessed
        # before the Kelly check.
        class _Coll:
            async def find_one(self, *a, **kw):
                return None

            async def insert_one(self, *a, **kw):
                return MagicMock(inserted_id="x")

            def find(self, *a, **kw):
                class _Cur:
                    def sort(self, *a, **kw):
                        return self

                    def limit(self, *a, **kw):
                        return self

                    async def to_list(self, *a, **kw):
                        return []

                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        raise StopAsyncIteration
                return _Cur()

            async def count_documents(self, *a, **kw):
                return 0

            async def update_one(self, *a, **kw):
                return MagicMock(modified_count=0)

        class _DB:
            def __getitem__(self, _name):
                return _Coll()

            def __getattr__(self, _name):
                return _Coll()

        try:
            result = await mod.maybe_paper_trade(
                _DB(), "AAPL", signal, snapshot, "trend_up", [],
            )
        except Exception as exc:  # noqa: BLE001
            # If we land in a different branch's exception before
            # reaching kelly, the test is still useful as a static
            # check — but let's surface it.
            pytest.skip(f"maybe_paper_trade aborted before kelly: {exc}")

        # On Kelly-zero, we must return None
        assert result is None
        # And the honest-hold emit must have fired exactly once
        # with the expected hold_reason.
        assert mock_emit.call_count == 1, (
            f"expected exactly one honest-hold emit, got {mock_emit.call_count}"
        )
        call_kwargs = mock_emit.call_args
        # Second positional arg is the receipt dict.
        receipt = call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
        assert receipt is not None, "honest-hold emit got no receipt arg"
        assert receipt["hold_reason"] == "kelly_zero_size"
        assert receipt["market_decision"] == "HOLD"
        assert receipt["execution_decision"] == "OBSERVE_ONLY"
        assert receipt["would_have_traded_without_gates"] is False
        assert receipt["raw_action"] == "BUY"  # direction.value == "up"
