"""Tests for the QuiverQuant live-feed fallback layer.

Covers:
  * `_filter_rows_by_ticker` — case-insensitive match on both `Ticker`
    and `ticker` keys, empty input handling.
  * `_fetch_with_live_fallback` — primary success returns data as-is;
    primary failure routes through the live feed + filter; both-fail
    returns `[]`.
  * `get_congressional_trades(ticker=...)` — end-to-end verifies the
    public getter's fallback path without hitting the real API.
  * `get_gov_contracts(ticker=...)` — verifies the two-tier fallback
    (govcontractsall → govcontracts aggregated) actually runs when
    the primary route is down.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import quiver_service as qs


def test_filter_rows_by_ticker_matches_both_key_casings():
    rows = [
        {"Ticker": "AAPL", "x": 1},
        {"ticker": "aapl", "x": 2},
        {"Ticker": "msft", "x": 3},
        {"Ticker": None, "x": 4},
        {"x": 5},
    ]
    assert qs._filter_rows_by_ticker(rows, "aapl") == [
        {"Ticker": "AAPL", "x": 1},
        {"ticker": "aapl", "x": 2},
    ]


def test_filter_rows_by_ticker_empty_ticker_returns_all():
    rows = [{"Ticker": "A"}, {"Ticker": "B"}]
    assert qs._filter_rows_by_ticker(rows, "") == rows


@pytest.mark.asyncio
async def test_fetch_with_live_fallback_primary_wins():
    """When the primary (historical/{ticker}) route returns data, the
    live-feed fallback MUST NOT be queried — that's the cache-friendly
    happy path."""
    primary_payload = [{"Ticker": "AAPL", "source": "primary"}]

    async def fake_fetch(key, url):
        if "historical/congresstrading/AAPL" in url:
            return primary_payload
        raise AssertionError(f"should not fetch live feed: {url}")

    with patch.object(qs, "_fetch_quiver", side_effect=fake_fetch):
        out = await qs._fetch_with_live_fallback(
            primary_url="https://api.quiverquant.com/beta/historical/congresstrading/AAPL",
            primary_key="congresstrading_historical",
            live_url="https://api.quiverquant.com/beta/live/congresstrading",
            live_key="congresstrading_live",
            ticker="AAPL",
        )
    assert out == primary_payload


@pytest.mark.asyncio
async def test_fetch_with_live_fallback_fails_over_and_filters():
    """Primary returns None (5xx upstream) → fall back to the live
    feed and filter by ticker. Rows for other tickers must be
    stripped — otherwise the caller would see pollution in a
    per-ticker query."""
    async def fake_fetch(key, url):
        if "historical" in url:
            return None  # upstream 500
        return [
            {"Ticker": "AAPL", "id": 1},
            {"Ticker": "MSFT", "id": 2},
            {"Ticker": "AAPL", "id": 3},
        ]

    with patch.object(qs, "_fetch_quiver", side_effect=fake_fetch):
        out = await qs._fetch_with_live_fallback(
            primary_url="https://api.quiverquant.com/beta/historical/congresstrading/AAPL",
            primary_key="congresstrading_historical",
            live_url="https://api.quiverquant.com/beta/live/congresstrading",
            live_key="congresstrading_live",
            ticker="AAPL",
        )
    assert [r["id"] for r in out] == [1, 3]


@pytest.mark.asyncio
async def test_fetch_with_live_fallback_both_down_returns_empty():
    with patch.object(qs, "_fetch_quiver", AsyncMock(return_value=None)):
        out = await qs._fetch_with_live_fallback(
            primary_url="p", primary_key="pk",
            live_url="l", live_key="lk",
            ticker="AAPL",
        )
    assert out == []


@pytest.mark.asyncio
async def test_get_congressional_trades_per_ticker_uses_fallback():
    """End-to-end: when the historical route 500s, the public getter
    still returns structured trade rows by falling through to the
    live feed. Regression lock for the Feb 2026 fix."""
    async def fake_fetch(key, url):
        if "historical" in url:
            return None
        return [
            {"Representative": "Jane Doe", "Ticker": "AAPL",
             "Transaction": "Purchase", "Amount": "1001.0",
             "TransactionDate": "2025-09-04", "Party": "D",
             "House": "Senate"},
            {"Representative": "John Roe", "Ticker": "MSFT",
             "Transaction": "Sale", "Amount": "500.0"},
        ]

    with patch.object(qs, "_fetch_quiver", side_effect=fake_fetch):
        rows = await qs.get_congressional_trades(ticker="AAPL", limit=10)

    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["representative"] == "Jane Doe"
    assert rows[0]["party"] == "D"
    assert rows[0]["chamber"] == "Senate"


@pytest.mark.asyncio
async def test_get_gov_contracts_two_tier_fallback():
    """`govcontractsall` down → fall back to `govcontracts` aggregated
    feed. Confirms the last-resort path from the Feb 2026 fix."""
    async def fake_fetch(key, url):
        if "historical" in url:
            return None
        if "govcontractsall" in url:
            return None  # primary live also down
        if url.endswith("/beta/live/govcontracts"):
            # Aggregated payload shape: no agency, no description.
            return [
                {"Ticker": "AAPL", "Amount": "299.0"},
                {"Ticker": "MSFT", "Amount": "500.0"},
            ]
        raise AssertionError(f"unexpected url: {url}")

    with patch.object(qs, "_fetch_quiver", side_effect=fake_fetch):
        rows = await qs.get_gov_contracts(ticker="AAPL", limit=5)

    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["amount"] == 299.0
    # Aggregated feed has no agency or description — confirm the
    # transform handles the missing fields gracefully.
    assert rows[0]["agency"] == ""
    assert rows[0]["description"] == ""
