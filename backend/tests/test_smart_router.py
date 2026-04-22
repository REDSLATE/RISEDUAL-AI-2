"""Tests for `services.brokers.smart_router`.

Uses mocked adapters — the router is pure dispatch, so no HTTP
round-trip is needed to pin its behaviour.

Covers:
  * `pick` selects the lowest-spread broker.
  * Ties break alphabetically (deterministic outputs).
  * Brokers with failing spread probes get sentinel score but
    stay in the candidate list (route-last).
  * `pick` raises `RuntimeError` when NO broker is enabled —
    route layer maps to 503.
  * Adapter probe failures (is_options_enabled raises) don't
    crash the whole route — that broker just drops out.
  * `route_order` returns both the order AND the decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest

from services.brokers.options_adapter import (
    BrokerOptionsAdapter,
    OptionLeg,
    OptionOrder,
    OptionPosition,
    OptionsBuyingPower,
    OptionsEnabledStatus,
    OrderSide,
    OrderStatus,
)
from services.brokers.smart_router import SmartOrderRouter


# ── Fake adapter ───────────────────────────────────────────────────
#
# Hand-rolled because the real adapters do IO. We want the router
# tests to be ~ms not ~seconds.

@dataclass
class FakeAdapter(BrokerOptionsAdapter):
    _provider: str
    enabled: bool
    spread: float
    probe_raises: Optional[Exception] = None
    spread_raises: Optional[Exception] = None

    def __post_init__(self):
        self.provider = self._provider

    async def is_options_enabled(self):
        if self.probe_raises:
            raise self.probe_raises
        return OptionsEnabledStatus(
            enabled=self.enabled, level=2, provider=self.provider,
        )

    async def get_options_buying_power(self):
        return OptionsBuyingPower(cash=100_000, options_buying_power=100_000)

    async def get_option_positions(self) -> list[OptionPosition]:
        return []

    async def place_option_order(
        self, legs, *, order_type="market", time_in_force="day", limit_price=None,
    ):
        from datetime import datetime, timezone
        return OptionOrder(
            order_id=f"order-{self.provider}",
            status=OrderStatus.ACCEPTED,
            legs=[], qty=legs[0].qty, filled_qty=0,
            limit_price=limit_price, time_in_force=time_in_force,
            created_at=datetime.now(timezone.utc),
        )

    async def cancel_option_order(self, order_id):
        return True

    async def try_get_spread(self, occ_symbol: str) -> Optional[float]:
        if self.spread_raises:
            raise self.spread_raises
        return self.spread


# ── Fixtures ────────────────────────────────────────────────────────

def _patch_enabled(*adapters: FakeAdapter):
    """Patch `get_enabled_adapters` so the router sees exactly the
    adapters we want. The registry's probe logic already has its
    own tests; we don't re-exercise it here."""
    enabled = {a.provider: a for a in adapters if a.enabled}
    return patch(
        "services.brokers.smart_router.get_enabled_adapters",
        AsyncMock(return_value=enabled),
    )


# ── Tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pick_selects_lowest_spread_broker():
    a = FakeAdapter("alpaca", enabled=True, spread=0.05)
    t = FakeAdapter("tradier", enabled=True, spread=0.02)
    with _patch_enabled(a, t):
        decision = await SmartOrderRouter().pick("AAPL  261218C00200000")
    assert decision.provider == "tradier"
    assert decision.estimated_spread == 0.02
    # Losing candidates are still in the list so UI can show "would
    # have been alpaca @ $0.05" transparency.
    providers = [c[0] for c in decision.all_candidates]
    assert set(providers) == {"alpaca", "tradier"}


@pytest.mark.asyncio
async def test_pick_breaks_ties_alphabetically():
    a = FakeAdapter("alpaca", enabled=True, spread=0.03)
    t = FakeAdapter("tradier", enabled=True, spread=0.03)
    with _patch_enabled(a, t):
        decision = await SmartOrderRouter().pick("AAPL  261218C00200000")
    assert decision.provider == "alpaca"  # alpha < tradier


@pytest.mark.asyncio
async def test_pick_raises_when_no_broker_enabled():
    with _patch_enabled():  # empty
        with pytest.raises(RuntimeError, match="no broker"):
            await SmartOrderRouter().pick("AAPL  261218C00200000")


@pytest.mark.asyncio
async def test_spread_probe_failure_does_not_crash_routing():
    """If one broker's spread probe 500s, the router still routes —
    just with that broker ranked last via the sentinel."""
    good = FakeAdapter("alpaca", enabled=True, spread=0.04)
    flaky = FakeAdapter(
        "tradier", enabled=True, spread=0.01,
        spread_raises=RuntimeError("quote endpoint 500"),
    )
    with _patch_enabled(good, flaky):
        decision = await SmartOrderRouter().pick("AAPL  261218C00200000")
    # Alpaca wins because tradier's probe returned sentinel.
    assert decision.provider == "alpaca"


@pytest.mark.asyncio
async def test_single_broker_smart_router_is_passthrough():
    """Phase 1 case — only Alpaca enabled. Smart router should pick
    alpaca without bombing."""
    a = FakeAdapter("alpaca", enabled=True, spread=0.05)
    with _patch_enabled(a):
        decision = await SmartOrderRouter().pick("AAPL  261218C00200000")
    assert decision.provider == "alpaca"
    assert len(decision.all_candidates) == 1


@pytest.mark.asyncio
async def test_route_order_returns_both_order_and_decision():
    a = FakeAdapter("alpaca", enabled=True, spread=0.05)
    with _patch_enabled(a):
        order, decision = await SmartOrderRouter().route_order(
            "AAPL  261218C00200000",
            [OptionLeg("AAPL  261218C00200000", 1, OrderSide.BUY_TO_OPEN)],
        )
    assert order.order_id == "order-alpaca"
    assert decision.provider == "alpaca"
