"""Broker adapter registry.

One call site (`get_options_adapter(provider)`) → one concrete
adapter. Keeps route code branch-free on broker identity.

Phase 1 providers:
  * `alpaca`      — live implementation (BTO / STC single-leg)
  * `tradier`     — stub (501)
  * `tastytrade`  — stub (501)
  * `ibkr`        — stub (501)

Adding a new broker: implement `BrokerOptionsAdapter`, register the
class in `_PROVIDERS`, done. No route-layer changes required.
"""
from __future__ import annotations

from typing import Callable

from services.brokers.alpaca_options import AlpacaOptionsAdapter
from services.brokers.options_adapter import (
    BrokerOptionsAdapter,
    StubOptionsAdapter,
)

# Factory dict — each value returns a FRESH adapter instance per
# call so we don't leak httpx client state across users (adapters
# read env-vars, not a shared client pool).
_PROVIDERS: dict[str, Callable[[], BrokerOptionsAdapter]] = {
    "alpaca":     AlpacaOptionsAdapter,
    "tradier":    lambda: StubOptionsAdapter("tradier"),
    "tastytrade": lambda: StubOptionsAdapter("tastytrade"),
    "ibkr":       lambda: StubOptionsAdapter("ibkr"),
}

SUPPORTED_PROVIDERS: list[str] = list(_PROVIDERS.keys())


def get_options_adapter(provider: str) -> BrokerOptionsAdapter:
    """Return a concrete adapter for the requested provider.

    Unknown providers raise `ValueError` — the route layer should
    map this to HTTP 400 so the caller sees which provider string
    was wrong instead of a vague 500.
    """
    key = (provider or "").strip().lower()
    if key not in _PROVIDERS:
        raise ValueError(
            f"unknown broker provider '{provider}' "
            f"(supported: {SUPPORTED_PROVIDERS})"
        )
    return _PROVIDERS[key]()
