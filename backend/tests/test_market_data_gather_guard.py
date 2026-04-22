"""Regression test for the asyncio.gather guard in market_data_service.

``MarketDataService.get_ticker_data`` and ``get_crypto_data`` fire one
quote fetch per symbol via ``asyncio.gather(return_exceptions=True)``.
Without the canonical unwrap guard, any ``CancelledError`` surfaced in
the result list (e.g. during FastAPI shutdown) would slip past a naive
``isinstance(x, Exception)`` check and crash downstream when the
handler indexes into ``quote['symbol']``.

This test pins the three-tier guard behaviour:
  1. CancelledError → silently dropped (no log, no crash).
  2. Plain Exception → structured log_error + dropped.
  3. None result → dropped silently.
  4. Valid dict → appears in the returned list.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from services.market_data_service import MarketDataService


# ────────────────────────────────────────────────────────────────────────────
# get_ticker_data
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_ticker_data_survives_cancelled_and_exception(caplog):
    """Guard survives mixed CancelledError / Exception / None / valid
    results without crashing, and only logs the real exception."""
    caplog.set_level(logging.DEBUG, logger="services.market_data_service")
    svc = MarketDataService(db=None)

    # Seven symbols in get_ticker_data → seven results. We feed one of
    # each failure mode plus four valid dicts so the happy path is also
    # exercised.
    fake_results = [
        asyncio.CancelledError("shutdown mid-fetch"),
        RuntimeError("provider 500"),
        None,
        {"symbol": "IVV", "price": 100, "change": 1, "changePercent": 1},
        {"symbol": "VTI", "price": 200, "change": 2, "changePercent": 2},
        {"symbol": "VUG", "price": 300, "change": 3, "changePercent": 3},
        {"symbol": "VEA", "price": 400, "change": 4, "changePercent": 4},
    ]

    async def _fake_gather(*tasks, return_exceptions=False):
        return fake_results

    with patch.object(asyncio, "gather", _fake_gather):
        out = await svc.get_ticker_data()

    # Three failures dropped, four valid entries kept.
    assert len(out) == 4
    assert {row["symbol"] for row in out} == {"IVV", "VTI", "VUG", "VEA"}

    # CancelledError must NOT log. RuntimeError MUST log at ERROR level
    # with the structured `context=market_data.ticker` tag.
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 1, (
        f"expected exactly one ERROR log for the RuntimeError, got "
        f"{[r.message for r in error_records]}"
    )
    msg = error_records[0].message
    assert "context=market_data.ticker" in msg
    assert "type=RuntimeError" in msg


# ────────────────────────────────────────────────────────────────────────────
# get_crypto_data
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_crypto_data_survives_cancelled_and_exception(caplog):
    """Same guard applies to the crypto path — same three tiers."""
    caplog.set_level(logging.DEBUG, logger="services.market_data_service")
    svc = MarketDataService(db=None)

    # 15 cryptos → 15 results; pad with Nones after three markers.
    fake_results: list = [
        asyncio.CancelledError("cancel"),
        ValueError("bad payload"),
        {"symbol": "BNB", "price": 600},
    ]
    fake_results.extend([None] * 12)

    async def _fake_gather(*tasks, return_exceptions=False):
        return fake_results

    with patch.object(asyncio, "gather", _fake_gather):
        out = await svc.get_crypto_data()

    assert out == [{"symbol": "BNB", "price": 600}]
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 1
    assert "context=market_data.crypto" in error_records[0].message
    assert "type=ValueError" in error_records[0].message
