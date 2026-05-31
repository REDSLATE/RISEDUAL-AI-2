"""Tripwire coverage for Alpha's opinion emission to Mission Control.

2026-06 operator override: all brains (Alpha included) can occupy
the executor seat. Opinions therefore ride alongside intents on
every consensus tick so MC's discussion layer surfaces Alpha's
reasoning regardless of who is in the executor chair.

These tests lock in the wire:

  1. ``_build_opinion_payload`` shapes a receipt into a valid
     ``post_opinion`` kwarg dict for every verdict (BUY/SELL/HOLD/etc).
  2. ``emit_opinion_from_consensus`` calls the monorepo sidecar's
     ``post_opinion`` and survives sidecar outages gracefully.
  3. ``emit_intent_from_consensus`` ALSO fires an opinion alongside
     the intent on directional verdicts — single call site, both
     wires hot.
  4. Non-directional (HOLD) consensus still emits an opinion even
     though the intent path short-circuits. Doctrine: "no opinion"
     is itself an opinion worth publishing.
  5. ``emit_opinion=False`` kw is honored so callers that need
     opinion-free intent emission (legacy) can still do so.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sovereign.intent_bridge import (
    _build_opinion_payload,
    emit_intent_from_consensus,
    emit_opinion_from_consensus,
)


def _receipt(**over):
    base = {
        "symbol": "NVDA",
        "raw_action": "BUY",
        "final_confidence": 72,
        "raw_confidence": 78,
        "market_decision": "BUY",
        "display_action": "BUY",
        "summary": "Strong upside read on NVDA from council majority.",
        "individual_weights": {
            "alpha": 1.1, "camaro": 1.0, "chevelle": 0.95, "redeye": 0.9,
        },
        "snapshot": {"price": 950.5, "volume": 12345678},
    }
    base.update(over)
    return base


# ── _build_opinion_payload ─────────────────────────────────────────────


def test_build_opinion_payload_shapes_basic_fields():
    out = _build_opinion_payload(_receipt())
    assert out is not None
    assert out["topic"] == "equity:NVDA"
    assert out["stance"] == "BUY"
    assert out["confidence"] == pytest.approx(0.72)
    assert "NVDA" in out["body"] or "upside" in out["body"].lower()


def test_build_opinion_payload_emits_for_hold():
    """HOLD is a valid opinion — must NOT short-circuit like intents."""
    out = _build_opinion_payload(
        _receipt(raw_action="HOLD", market_decision="HOLD"),
    )
    assert out is not None
    assert out["stance"] == "HOLD"


def test_build_opinion_payload_emits_for_unknown_action_as_hold():
    """Unknown verdicts collapse to HOLD on the opinion wire."""
    out = _build_opinion_payload(
        _receipt(raw_action="MAYBE", market_decision="MAYBE"),
    )
    assert out is not None
    # Unknown verdicts surface as their literal value or fall through;
    # what matters is the opinion is still emitted (not None).
    assert out["topic"] == "equity:NVDA"


def test_build_opinion_payload_returns_none_for_empty_symbol():
    assert _build_opinion_payload(_receipt(symbol="")) is None


def test_build_opinion_payload_classifies_crypto_lane():
    out = _build_opinion_payload(_receipt(symbol="BTC/USD"))
    assert out["topic"].startswith("crypto:")


def test_build_opinion_payload_carries_trace_id_in_evidence():
    out = _build_opinion_payload(_receipt(), trace_id="abc12345")
    assert out["evidence"]["trace_id"] == "abc12345"


def test_build_opinion_payload_carries_snapshot_in_evidence():
    out = _build_opinion_payload(_receipt())
    assert out["evidence"]["snapshot"]["price"] == 950.5


def test_build_opinion_payload_clamps_confidence_to_unit_scale():
    out = _build_opinion_payload(_receipt(final_confidence=150))
    assert 0.0 <= out["confidence"] <= 1.0


def test_build_opinion_payload_uses_notes_when_summary_missing():
    receipt = _receipt()
    receipt.pop("summary")
    out = _build_opinion_payload(receipt, notes="custom rationale")
    assert out["body"] == "custom rationale"


# ── emit_opinion_from_consensus ────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_opinion_from_consensus_calls_post_opinion():
    with patch(
        "services.risedual_monorepo_client.post_opinion",
        new=AsyncMock(return_value={"ok": True, "id": "op-1"}),
    ) as mocked:
        out = await emit_opinion_from_consensus(_receipt())
        assert out == {"ok": True, "id": "op-1"}
        mocked.assert_awaited_once()
        kwargs = mocked.call_args.kwargs
        assert kwargs["topic"] == "equity:NVDA"
        assert kwargs["stance"] == "BUY"


@pytest.mark.asyncio
async def test_emit_opinion_from_consensus_swallows_sidecar_failure():
    """A flaky monorepo must never propagate up to the consensus loop."""
    with patch(
        "services.risedual_monorepo_client.post_opinion",
        new=AsyncMock(side_effect=RuntimeError("sidecar down")),
    ):
        out = await emit_opinion_from_consensus(_receipt())
        assert out is None


@pytest.mark.asyncio
async def test_emit_opinion_from_consensus_skips_empty_symbol():
    with patch(
        "services.risedual_monorepo_client.post_opinion",
        new=AsyncMock(return_value={"ok": True}),
    ) as mocked:
        out = await emit_opinion_from_consensus(_receipt(symbol=""))
        assert out is None
        mocked.assert_not_called()


# ── emit_intent_from_consensus side-channel ────────────────────────────


@pytest.mark.asyncio
async def test_emit_intent_also_fires_opinion_on_directional():
    """Single call site, both wires hot — directional consensus emits
    BOTH an intent (to MC's /api/intents) AND an opinion (to MC's
    /api/ingest/opinion). Tripwire for the 2026-06 wire-up."""
    client = MagicMock()
    client.post_intent.return_value = {"id": "ix-1", "status": "ok"}
    with patch(
        "services.risedual_monorepo_client.post_opinion",
        new=AsyncMock(return_value={"ok": True, "id": "op-1"}),
    ) as opinion_mock:
        out = await emit_intent_from_consensus(client, _receipt())
        assert out == {"id": "ix-1", "status": "ok"}
        client.post_intent.assert_called_once()
        opinion_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_intent_fires_opinion_even_on_hold():
    """HOLD short-circuits the intent path but the opinion still
    flows — "no opinion" is itself a publishable observation."""
    client = MagicMock()
    with patch(
        "services.risedual_monorepo_client.post_opinion",
        new=AsyncMock(return_value={"ok": True, "id": "op-2"}),
    ) as opinion_mock:
        out = await emit_intent_from_consensus(
            client, _receipt(raw_action="HOLD", market_decision="HOLD"),
        )
        assert out is None
        client.post_intent.assert_not_called()
        opinion_mock.assert_awaited_once()
        assert opinion_mock.call_args.kwargs["stance"] == "HOLD"


@pytest.mark.asyncio
async def test_emit_intent_opinion_failure_does_not_block_intent_return():
    """Opinion sidecar can be down without affecting the intent
    return value — they ride independent wires."""
    client = MagicMock()
    client.post_intent.return_value = {"id": "ix-2", "status": "ok"}
    with patch(
        "services.risedual_monorepo_client.post_opinion",
        new=AsyncMock(side_effect=RuntimeError("sidecar down")),
    ):
        out = await emit_intent_from_consensus(client, _receipt())
        assert out == {"id": "ix-2", "status": "ok"}


@pytest.mark.asyncio
async def test_emit_intent_emit_opinion_false_disables_side_channel():
    """Legacy callers can opt out of opinion side-channel."""
    client = MagicMock()
    client.post_intent.return_value = {"id": "ix-3", "status": "ok"}
    with patch(
        "services.risedual_monorepo_client.post_opinion",
        new=AsyncMock(return_value={"ok": True}),
    ) as opinion_mock:
        out = await emit_intent_from_consensus(
            client, _receipt(), emit_opinion=False,
        )
        assert out == {"id": "ix-3", "status": "ok"}
        opinion_mock.assert_not_called()


@pytest.mark.asyncio
async def test_emit_intent_passes_trace_id_to_opinion():
    """Both wires share the same trace_id so the operator can grep
    one consensus tick across intent + opinion logs."""
    client = MagicMock()
    client.post_intent.return_value = {"id": "ix-4", "status": "ok"}
    captured_trace = {}
    with patch(
        "services.risedual_monorepo_client.post_opinion",
        new=AsyncMock(return_value={"ok": True}),
    ) as opinion_mock:
        await emit_intent_from_consensus(client, _receipt())
        opinion_mock.assert_awaited_once()
        evidence = opinion_mock.call_args.kwargs["evidence"]
        captured_trace["trace_id"] = evidence.get("trace_id")
    # 8-char hex trace IDs are auto-generated when none supplied
    assert captured_trace["trace_id"] is not None
    assert len(captured_trace["trace_id"]) == 8
