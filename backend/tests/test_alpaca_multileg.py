"""Tests for multi-leg body construction in
`services.brokers.alpaca_options.AlpacaOptionsAdapter`.

Mocks the `_request` method at the adapter boundary. We're
pinning the outgoing JSON body shape for Alpaca's `/v2/orders`
multi-leg envelope — the field names matter for 422 resilience
(`order_class`, `legs`, `ratio_qty`, `position_intent`).

Covers:
  * 2-leg vertical spread → correct mleg envelope, ratio 1:1
  * 4-leg iron condor → 4 legs, all ratio 1
  * 2:1 ratio spread — larger leg gets ratio_qty=2
  * empty legs → ValueError
  * >4 legs → ValueError
  * mismatched qty ratios → ValueError
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.brokers.alpaca_options import AlpacaOptionsAdapter
from services.brokers.options_adapter import OptionLeg, OrderSide


def _mock_order_response():
    return {
        "id": "order-123",
        "status": "accepted",
        "qty": "1",
        "filled_qty": "0",
        "created_at": "2026-04-22T14:00:00Z",
    }


def _adapter_with_mock():
    a = AlpacaOptionsAdapter(
        api_key="test", secret_key="test", base_url="https://example",
    )
    a._request = AsyncMock(return_value=_mock_order_response())
    return a


# Canonical 21-char OCC for tests — AAPL $190 call & $195 call 2026-12-18.
OCC_LOW = "AAPL  261218C00190000"
OCC_HIGH = "AAPL  261218C00195000"
OCC_HIGHER = "AAPL  261218C00200000"
OCC_HIGHEST = "AAPL  261218C00210000"


@pytest.mark.asyncio
async def test_two_leg_vertical_spread():
    a = _adapter_with_mock()
    legs = [
        OptionLeg(OCC_LOW, 1, OrderSide.BUY_TO_OPEN),
        OptionLeg(OCC_HIGH, 1, OrderSide.SELL_TO_OPEN),
    ]
    await a.place_option_order(legs, order_type="limit", limit_price=1.50)

    call_args = a._request.call_args
    body = call_args.kwargs["json_body"]
    assert body["order_class"] == "mleg"
    assert body["qty"] == "1"
    assert body["type"] == "limit"
    assert body["limit_price"] == "1.5"
    assert len(body["legs"]) == 2
    # Compact OCC translation happens at the boundary.
    assert body["legs"][0]["symbol"] == "AAPL261218C00190000"
    assert body["legs"][0]["side"] == "buy"
    assert body["legs"][0]["ratio_qty"] == "1"
    assert "position_intent" not in body["legs"][0]
    assert body["legs"][1]["side"] == "sell"


@pytest.mark.asyncio
async def test_four_leg_iron_condor():
    a = _adapter_with_mock()
    legs = [
        OptionLeg(OCC_LOW, 1, OrderSide.BUY_TO_OPEN),     # protective
        OptionLeg(OCC_HIGH, 1, OrderSide.SELL_TO_OPEN),   # short put
        OptionLeg(OCC_HIGHER, 1, OrderSide.SELL_TO_OPEN), # short call
        OptionLeg(OCC_HIGHEST, 1, OrderSide.BUY_TO_OPEN), # protective
    ]
    await a.place_option_order(legs, order_type="limit", limit_price=0.75)
    body = a._request.call_args.kwargs["json_body"]
    assert body["order_class"] == "mleg"
    assert len(body["legs"]) == 4


@pytest.mark.asyncio
async def test_ratio_spread_math():
    """1:2 vertical — one long low-strike, two short high-strike."""
    a = _adapter_with_mock()
    legs = [
        OptionLeg(OCC_LOW, 1, OrderSide.BUY_TO_OPEN),
        OptionLeg(OCC_HIGH, 2, OrderSide.SELL_TO_OPEN),
    ]
    await a.place_option_order(legs)
    body = a._request.call_args.kwargs["json_body"]
    assert body["qty"] == "1"  # base multiplier = min(1, 2) = 1
    assert body["legs"][0]["ratio_qty"] == "1"
    assert body["legs"][1]["ratio_qty"] == "2"


@pytest.mark.asyncio
async def test_empty_legs_raises():
    a = _adapter_with_mock()
    with pytest.raises(ValueError, match="empty"):
        await a.place_option_order([])


@pytest.mark.asyncio
async def test_five_plus_legs_rejected():
    a = _adapter_with_mock()
    legs = [
        OptionLeg(OCC_LOW, 1, OrderSide.BUY_TO_OPEN),
        OptionLeg(OCC_HIGH, 1, OrderSide.SELL_TO_OPEN),
        OptionLeg(OCC_HIGHER, 1, OrderSide.SELL_TO_OPEN),
        OptionLeg(OCC_HIGHEST, 1, OrderSide.BUY_TO_OPEN),
        OptionLeg(OCC_LOW, 1, OrderSide.BUY_TO_OPEN),
    ]
    with pytest.raises(ValueError, match="up to 4"):
        await a.place_option_order(legs)


@pytest.mark.asyncio
async def test_mismatched_ratio_raises():
    """1 and 3 don't share a common clean multiplier (gcd=1 but 3 isn't
    an integer multiple of 1… actually this IS valid. Test a real
    mismatch: 2 and 3. base=2; 3 % 2 != 0."""
    a = _adapter_with_mock()
    legs = [
        OptionLeg(OCC_LOW, 2, OrderSide.BUY_TO_OPEN),
        OptionLeg(OCC_HIGH, 3, OrderSide.SELL_TO_OPEN),
    ]
    with pytest.raises(ValueError, match="common multiplier"):
        await a.place_option_order(legs)


@pytest.mark.asyncio
async def test_single_leg_still_uses_single_leg_body():
    """Regression: single-leg calls must NOT trigger the mleg path."""
    a = _adapter_with_mock()
    legs = [OptionLeg(OCC_LOW, 1, OrderSide.BUY_TO_OPEN)]
    await a.place_option_order(legs)
    body = a._request.call_args.kwargs["json_body"]
    assert "order_class" not in body
    assert "legs" not in body
    assert body["symbol"] == "AAPL261218C00190000"
    assert body["side"] == "buy"
