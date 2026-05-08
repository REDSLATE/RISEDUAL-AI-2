"""Finnhub REST API client.

Provides async methods for real-time quotes, company profiles, news, and
analyst recommendation trends via the Finnhub.io API.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from risedual_core.clients.base import BaseMarketClient


class FinnhubClient(BaseMarketClient):
    """Async client for the Finnhub REST API.

    All methods append the API token automatically and return empty dicts or
    lists on failure — the caller never needs to handle raw HTTP exceptions.

    Parameters
    ----------
    api_key:
        Finnhub API token.
    http_client:
        Optional shared :class:`httpx.AsyncClient`.  When supplied the caller
        is responsible for its lifecycle.  When ``None`` (default) a new
        transient client is created per request.
    """

    def __init__(
        self,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key=api_key, http_client=http_client)

    # ── BaseMarketClient interface ────────────────────────────────────────────


    # Rate limit: Finnhub free tier: 60 req/min
    _default_rate: float = 1.0
    _default_capacity: float = 5.0

    @property
    def base_url(self) -> str:
        """Finnhub v1 API base URL."""
        return "https://finnhub.io/api/v1"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return base query params (token) merged with any *extra* pairs."""
        params: dict[str, Any] = {"token": self._api_key}
        if extra:
            params.update(extra)
        return params

    # ── Public API methods ────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """Fetch a real-time quote for *symbol*.

        Calls ``GET /quote`` and normalises the Finnhub single-letter field
        names into descriptive keys.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``).

        Returns
        -------
        dict
            Keys: ``current_price``, ``change``, ``percent_change``, ``high``,
            ``low``, ``open``, ``previous_close``, ``timestamp``.
            Empty dict on failure.
        """
        data = await self._get(
            f"{self.base_url}/quote",
            params=self._params({"symbol": symbol}),
        )
        if not isinstance(data, dict):
            return {}
        return {
            "current_price": data.get("c"),
            "change": data.get("d"),
            "percent_change": data.get("dp"),
            "high": data.get("h"),
            "low": data.get("l"),
            "open": data.get("o"),
            "previous_close": data.get("pc"),
            "timestamp": data.get("t"),
        }

    async def get_company_profile(self, symbol: str) -> dict[str, Any]:
        """Fetch company profile metadata for *symbol*.

        Calls ``GET /stock/profile2``.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``).

        Returns
        -------
        dict
            Keys: ``name``, ``ticker``, ``exchange``, ``industry``,
            ``market_cap``, ``ipo_date``, ``currency``, ``country``,
            ``phone``, ``logo``, ``weburl``, ``shares_outstanding``.
            Empty dict on failure or missing data.
        """
        data = await self._get(
            f"{self.base_url}/stock/profile2",
            params=self._params({"symbol": symbol}),
        )
        if not isinstance(data, dict) or not data:
            return {}
        return {
            "name": data.get("name"),
            "ticker": data.get("ticker"),
            "exchange": data.get("exchange"),
            "industry": data.get("finnhubIndustry"),
            "market_cap": data.get("marketCapitalization"),
            "ipo_date": data.get("ipo"),
            "currency": data.get("currency"),
            "country": data.get("country"),
            "phone": data.get("phone"),
            "logo": data.get("logo"),
            "weburl": data.get("weburl"),
            "shares_outstanding": data.get("shareOutstanding"),
        }

    async def get_news(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """Fetch recent company news for *symbol*.

        Calls ``GET /company-news``.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``).
        from_date:
            Start date in ``YYYY-MM-DD`` format.
        to_date:
            End date in ``YYYY-MM-DD`` format.

        Returns
        -------
        list[dict]
            Each dict has keys: ``headline``, ``summary``, ``url``,
            ``datetime`` (ISO-8601 string), ``source``, ``image``,
            ``category``.  Empty list on failure.
        """
        data = await self._get(
            f"{self.base_url}/company-news",
            params=self._params({"symbol": symbol, "from": from_date, "to": to_date}),
        )
        if not isinstance(data, list):
            return []
        return [
            {
                "headline": item.get("headline"),
                "summary": item.get("summary"),
                "url": item.get("url"),
                "datetime": (
                    datetime.fromtimestamp(item["datetime"], tz=timezone.utc).isoformat()
                    if item.get("datetime")
                    else None
                ),
                "source": item.get("source"),
                "image": item.get("image"),
                "category": item.get("category"),
            }
            for item in data
        ]

    async def get_recommendation_trends(self, symbol: str) -> list[dict[str, Any]]:
        """Fetch analyst recommendation trends for *symbol*.

        Calls ``GET /stock/recommendation``.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``).

        Returns
        -------
        list[dict]
            Each dict has keys: ``period``, ``strong_buy``, ``buy``,
            ``hold``, ``sell``, ``strong_sell``.
            Empty list on failure.
        """
        data = await self._get(
            f"{self.base_url}/stock/recommendation",
            params=self._params({"symbol": symbol}),
        )
        if not isinstance(data, list):
            return []
        return [
            {
                "period": item.get("period"),
                "strong_buy": item.get("strongBuy"),
                "buy": item.get("buy"),
                "hold": item.get("hold"),
                "sell": item.get("sell"),
                "strong_sell": item.get("strongSell"),
            }
            for item in data
        ]
