"""Regression tests for the MooMoo-first broker quote path.

Code review 2026-09-03 identified a MEDIUM severity defect: the
initial MooMoo path returned ``fetched_at`` as an ISO string, but
``provider_policy._quote_age_seconds`` uses ``float(ts)`` which
raises on ISO strings — the freshness gate then saw age=None and
conservatively rejected every MooMoo quote (silently neutralising
the primary-broker path). The follow-up fix returns unix-seconds
float, parsed from MooMoo's ``data_time`` when possible so a stale
previous-close price can be caught by the age gate.

This suite locks down:

* Returned dict shape is compatible with ``provider_policy`` — has
  the expected keys and ``fetched_at`` is numeric.
* Fresh MooMoo quotes with a recent ``data_time`` pass the age
  parser.
* Stale MooMoo quotes (e.g. previous-close) return a large age so
  the freshness gate can reject them.
* Missing ``data_time`` falls back to ``time.time()`` without
  raising.
* Zero last-price MooMoo response is not returned (fall through
  to Public.com happens).
"""
from __future__ import annotations

import time as _time
from unittest.mock import patch

import pytest

from services import market_data_pool


class _FakeSnap:
    def __init__(self, *, last, bid=0.0, ask=0.0, volume=0.0, ts=""):
        self.last = last
        self.bid = bid
        self.ask = ask
        self.volume = volume
        self.ts = ts


@pytest.fixture(autouse=True)
def _pool_available_and_breaker_ok():
    """Force the pool to look available (via a non-empty providers
    list) and the breaker CLOSED so the tests hit the MooMoo branch
    instead of short-circuiting."""
    from services import broker_circuit_breaker as _cb
    from services.provider_pool import ProviderEntry
    _cb.reset()
    # A single dummy non-public entry keeps ``available`` True but
    # never wins a public-tier match, so the Public.com fallback
    # naturally returns None when MooMoo doesn't answer.
    original = list(market_data_pool.market_pool.providers)
    dummy = ProviderEntry(name="dummy-vendor", provider="vendor-x", api_key="", priority=1)
    market_data_pool.market_pool.providers = [dummy]
    yield
    market_data_pool.market_pool.providers = original
    _cb.reset()


@pytest.mark.asyncio
async def test_moomoo_quote_fetched_at_is_unix_seconds_float():
    """The critical bug: earlier code returned ISO string; the
    consumer does float(ts) → ValueError → freshness gate rejects."""
    now_market = _time.strftime(
        "%Y-%m-%d %H:%M:%S",
        _time.localtime(_time.time()),
    )
    fake_snap = _FakeSnap(last=100.0, bid=99.99, ask=100.01, ts=now_market)
    with patch(
        "services.moomoo_market_data_adapter.snapshot_quote",
        return_value=fake_snap,
    ):
        result = await market_data_pool.fetch_broker_quote("NFLX")
    assert result is not None
    assert result["provider_name"] == "moomoo-opend"
    assert isinstance(result["fetched_at"], float), (
        "fetched_at must be unix-seconds-float so provider_policy's "
        "float(ts) age calculation works"
    )
    assert result["price"] == 100.0


@pytest.mark.asyncio
async def test_moomoo_stale_data_time_produces_large_age():
    """A very old data_time (e.g. yesterday's close carried in a
    halted-symbol response) must resolve to a large positive age
    so the freshness gate can reject the quote."""
    # A hard-coded date well in the past — timezone-agnostic
    # (2020 is 6+ years before any test run) and avoids the
    # awkwardness of "now minus 2 hours" being reinterpreted
    # through ET and coming out in the future when the CI
    # container runs in UTC.
    fake_snap = _FakeSnap(last=100.0, ts="2020-01-15 09:30:00")
    with patch(
        "services.moomoo_market_data_adapter.snapshot_quote",
        return_value=fake_snap,
    ):
        result = await market_data_pool.fetch_broker_quote("NFLX")
    assert result is not None
    age = _time.time() - float(result["fetched_at"])
    assert age > 24 * 60 * 60, (
        f"a 2020 data_time should register at least 1 day age; got {age:.1f}s"
    )


@pytest.mark.asyncio
async def test_moomoo_missing_data_time_falls_back_to_now():
    """No ``data_time`` on the wire → stamp with now(). The gate
    can't reject on age alone but the drift check still catches
    disagreement with vendor witnesses."""
    fake_snap = _FakeSnap(last=100.0, ts="")
    before = _time.time()
    with patch(
        "services.moomoo_market_data_adapter.snapshot_quote",
        return_value=fake_snap,
    ):
        result = await market_data_pool.fetch_broker_quote("NFLX")
    after = _time.time()
    assert result is not None
    assert before <= result["fetched_at"] <= after + 0.5


@pytest.mark.asyncio
async def test_moomoo_zero_last_price_falls_through_to_public():
    """MooMoo returns ``last=0.0`` on a coverage miss. That must
    not be treated as a valid broker quote — the code falls
    through to Public.com."""
    fake_snap = _FakeSnap(last=0.0, ts="")
    # No public providers configured → whole call returns None
    # (dummy vendor doesn't match ``_BROKER_PROVIDERS``). We only
    # care that MooMoo's zero didn't win.
    with patch(
        "services.moomoo_market_data_adapter.snapshot_quote",
        return_value=fake_snap,
    ):
        result = await market_data_pool.fetch_broker_quote("NFLX")
    assert result is None


@pytest.mark.asyncio
async def test_moomoo_disabled_via_env_skips_primary(monkeypatch):
    """Operator override — the env kill switch must skip MooMoo
    entirely so Public.com becomes the only broker path."""
    monkeypatch.setenv("ALPHA_BROKER_QUOTE_PREFER_MOOMOO", "0")
    called = {"snapshot_quote": 0}

    def _fake_snap(*_a, **_kw):
        called["snapshot_quote"] += 1
        return _FakeSnap(last=100.0)

    with patch(
        "services.moomoo_market_data_adapter.snapshot_quote",
        side_effect=_fake_snap,
    ):
        await market_data_pool.fetch_broker_quote("NFLX")
    assert called["snapshot_quote"] == 0


@pytest.mark.asyncio
async def test_moomoo_exception_falls_through_gracefully():
    """A MooMoo adapter crash must not kill the whole broker
    quote path — Public.com fallback still runs."""
    with patch(
        "services.moomoo_market_data_adapter.snapshot_quote",
        side_effect=RuntimeError("OpenD unreachable"),
    ):
        # Should not raise; returns None because no public providers
        # match ``_BROKER_PROVIDERS`` in the dummy test pool.
        result = await market_data_pool.fetch_broker_quote("NFLX")
    assert result is None
