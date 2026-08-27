"""Tests for the broker/vendor split helpers in the market data pool.

These helpers back ``services.provider_policy.fetch_execution_quote``.
They intentionally bypass the MongoDB price cache used by
``market_quote`` — the freshness rule needs a real wire-time stamp,
and a cached response would defeat that guarantee.
"""

from __future__ import annotations

import time


import services.market_data_pool as pool_mod
from services.provider_pool import ProviderPool


def _run(coro):
    """Drive an async call from a sync test on a private loop.

    Same reason as ``test_provider_policy._run_gate``: we don't
    want to close the module-scoped default loop that older
    ``asyncio.get_event_loop()``-style tests rely on.
    """
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_pool_with(entries: list[dict]) -> ProviderPool:
    """Build a throwaway ``ProviderPool`` in test scope."""
    return ProviderPool(entries, name="TEST_MARKET_POOL")


def _swap_pool(monkeypatch, entries: list[dict]) -> None:
    """Replace the module-level ``market_pool`` for one test."""
    monkeypatch.setattr(pool_mod, "market_pool", _make_pool_with(entries))


def test_fetch_broker_quote_returns_broker_when_healthy(monkeypatch):
    """``fetch_broker_quote`` must dispatch only through providers
    in the ``_BROKER_PROVIDERS`` set (currently ``{"public"}``)."""

    _swap_pool(monkeypatch, [
        {"name": "public-primary", "provider": "public", "api_key": "__from_db__", "priority": 1},
        {"name": "finnhub-backup", "provider": "finnhub", "api_key": "k", "priority": 2},
    ])

    called = []

    async def _fake_dispatch(provider, symbol):
        called.append((provider.provider, symbol))
        return {"symbol": symbol, "price": 150.0, "source": provider.provider}

    monkeypatch.setattr(pool_mod, "_dispatch_quote", _fake_dispatch)

    q = _run(pool_mod.fetch_broker_quote("AAPL"))
    assert q is not None
    assert q["price"] == 150.0
    assert called == [("public", "AAPL")]  # only broker consulted


def test_fetch_broker_quote_returns_none_when_broker_absent(monkeypatch):
    """No broker in the pool → ``None``, never a vendor quote."""

    _swap_pool(monkeypatch, [
        {"name": "finnhub-backup", "provider": "finnhub", "api_key": "k", "priority": 1},
    ])

    async def _fake_dispatch(provider, symbol):  # pragma: no cover — must NOT be called
        raise AssertionError("vendor must not be dispatched by fetch_broker_quote")

    monkeypatch.setattr(pool_mod, "_dispatch_quote", _fake_dispatch)

    q = _run(pool_mod.fetch_broker_quote("AAPL"))
    assert q is None


def test_fetch_vendor_quote_skips_broker_and_walks_vendor_chain(monkeypatch):
    """``fetch_vendor_quote`` must skip broker entries and try
    vendors in pool order until one succeeds."""

    _swap_pool(monkeypatch, [
        {"name": "public-primary", "provider": "public", "api_key": "__from_db__", "priority": 1},
        {"name": "finnhub-backup", "provider": "finnhub", "api_key": "k", "priority": 2},
        {"name": "polygon-backup", "provider": "polygon", "api_key": "k", "priority": 3},
    ])

    seen: list[str] = []

    async def _fake_dispatch(provider, symbol):
        seen.append(provider.provider)
        if provider.provider == "finnhub":
            raise RuntimeError("finnhub throttled")
        return {"symbol": symbol, "price": 151.0, "source": provider.provider}

    monkeypatch.setattr(pool_mod, "_dispatch_quote", _fake_dispatch)

    q = _run(pool_mod.fetch_vendor_quote("AAPL"))
    assert q is not None
    # public never touched, finnhub tried and failed, polygon served.
    assert "public" not in seen
    assert seen == ["finnhub", "polygon"]
    assert q["price"] == 151.0


def test_fetch_vendor_quote_returns_none_when_only_broker_configured(monkeypatch):
    """A pool with only broker entries has no vendor to consult."""

    _swap_pool(monkeypatch, [
        {"name": "public-primary", "provider": "public", "api_key": "__from_db__", "priority": 1},
    ])

    async def _fake_dispatch(*_):  # pragma: no cover — must NOT be called
        raise AssertionError("broker must not be dispatched by fetch_vendor_quote")

    monkeypatch.setattr(pool_mod, "_dispatch_quote", _fake_dispatch)

    q = _run(pool_mod.fetch_vendor_quote("AAPL"))
    assert q is None


def test_dispatch_quote_stamps_fetched_at_when_provider_omits_it(monkeypatch):
    """The freshness gate depends on ``fetched_at`` — ``_dispatch_quote``
    must stamp one when the underlying provider forgets to."""

    _swap_pool(monkeypatch, [
        {"name": "finnhub-backup", "provider": "finnhub", "api_key": "k", "priority": 1},
    ])

    async def _fake_finnhub(_api_key, _symbol):
        # Intentionally omit ``fetched_at`` — this is the field the
        # dispatcher must fill in.
        return {"symbol": "AAPL", "price": 150.0, "source": "finnhub"}

    monkeypatch.setattr(pool_mod, "_finnhub_quote", _fake_finnhub)

    provider = pool_mod.market_pool.providers[0]
    before = time.time()
    result = _run(pool_mod._dispatch_quote(provider, "AAPL"))
    after = time.time()

    assert "fetched_at" in result
    assert before <= result["fetched_at"] <= after
    assert result["provider_name"] == "finnhub-backup"
