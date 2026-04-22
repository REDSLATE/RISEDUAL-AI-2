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


async def get_enabled_adapters() -> dict[str, BrokerOptionsAdapter]:
    """Return only adapters that are usable RIGHT NOW — the broker
    reports options are enabled AND the enabled-probe didn't raise.

    Used by the smart router to narrow the candidate set before
    spread estimation. Stub adapters (tradier/tastytrade/ibkr)
    self-report `enabled=false` so they drop out here automatically
    — once each gets a concrete adapter, no router-side changes are
    needed.

    The probe is fanned out in parallel because 4-broker account-
    info calls add up. Timeout is handled by each adapter's httpx
    client; this function swallows per-broker failures so one
    flaky broker doesn't starve the others.
    """
    import asyncio
    from services.structured_log import log_warning

    enabled: dict[str, BrokerOptionsAdapter] = {}
    adapters = {name: _PROVIDERS[name]() for name in _PROVIDERS}

    async def _probe(name: str, adapter: BrokerOptionsAdapter):
        try:
            status = await adapter.is_options_enabled()
            return name, adapter, status.enabled
        except Exception as exc:
            # log_warning is tagged so /api/admin/gather-error-rate
            # surfaces a broker-probe flake-rate out of the box.
            import logging
            log_warning(logging.getLogger(__name__), {
                "context": "smart_router_probe",
                "type": type(exc).__name__,
                "error": str(exc),
                "note": f"{name} is_options_enabled probe failed",
            })
            return name, adapter, False

    results = await asyncio.gather(*[
        _probe(name, adapter) for name, adapter in adapters.items()
    ], return_exceptions=False)

    for name, adapter, ok in results:
        if ok:
            enabled[name] = adapter
    return enabled
