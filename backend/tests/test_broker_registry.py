"""Tests for the broker adapter registry + stub adapter semantics.

Covers:
  * Registry resolves known providers to concrete classes.
  * Unknown providers raise ValueError (route layer maps → 400).
  * Fresh instance per call (no shared state between callers).
  * Stub adapter surfaces a stable `BrokerNotImplementedError` on
    every mutation method but returns a clean `enabled=False`
    status without raising (so UI can list the stub broker as
    "coming soon" without a 500).
"""
from __future__ import annotations

import pytest

from services.brokers.alpaca_options import AlpacaOptionsAdapter
from services.brokers.options_adapter import (
    BrokerNotImplementedError,
    OptionLeg,
    OrderSide,
    StubOptionsAdapter,
)
from services.brokers.registry import (
    SUPPORTED_PROVIDERS,
    get_options_adapter,
)


# ── Registry ────────────────────────────────────────────────────────

def test_registry_resolves_alpaca():
    adapter = get_options_adapter("alpaca")
    assert isinstance(adapter, AlpacaOptionsAdapter)
    assert adapter.provider == "alpaca"


def test_registry_resolves_tradier_adapter():
    """Tradier is now a concrete (quote-only) adapter — not a stub.
    Phase 2: quote source for smart router; order methods still
    raise BrokerNotImplementedError."""
    from services.brokers.tradier_options import TradierOptionsAdapter
    adapter = get_options_adapter("tradier")
    assert isinstance(adapter, TradierOptionsAdapter)
    assert adapter.provider == "tradier"


def test_registry_resolves_stub_providers():
    for p in ("tastytrade", "ibkr"):
        adapter = get_options_adapter(p)
        assert isinstance(adapter, StubOptionsAdapter)
        assert adapter.provider == p


def test_registry_is_case_insensitive():
    adapter = get_options_adapter("ALPACA")
    assert adapter.provider == "alpaca"


def test_registry_unknown_provider_raises():
    with pytest.raises(ValueError, match="unknown broker provider"):
        get_options_adapter("robinhood")


def test_registry_returns_fresh_instances():
    """Each call must return a new instance so per-user state
    (e.g. http clients) doesn't leak across requests."""
    a1 = get_options_adapter("alpaca")
    a2 = get_options_adapter("alpaca")
    assert a1 is not a2


def test_registry_declares_supported_providers():
    # Phase 1 invariant — prevents accidental list drift.
    # ``public`` was added when Public.com became the primary equity
    # broker; its options adapter is still a stub pending upstream API.
    assert set(SUPPORTED_PROVIDERS) == {"alpaca", "tradier", "tastytrade", "ibkr", "public"}


# ── Stub adapter ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stub_is_options_enabled_returns_false_no_raise():
    """UI needs to render the stub broker as 'coming soon' without
    a 500. Status query MUST return cleanly."""
    adapter = StubOptionsAdapter("tradier")
    status = await adapter.is_options_enabled()
    assert status.enabled is False
    assert status.provider == "tradier"
    assert "coming soon" in status.details.lower()


@pytest.mark.asyncio
async def test_stub_mutations_raise_not_implemented():
    adapter = StubOptionsAdapter("tradier")

    with pytest.raises(BrokerNotImplementedError):
        await adapter.get_options_buying_power()

    with pytest.raises(BrokerNotImplementedError):
        await adapter.get_option_positions()

    with pytest.raises(BrokerNotImplementedError):
        await adapter.place_option_order(
            [OptionLeg("AAPL  261218C00200000", 1, OrderSide.BUY_TO_OPEN)],
        )

    with pytest.raises(BrokerNotImplementedError):
        await adapter.cancel_option_order("any-id")
