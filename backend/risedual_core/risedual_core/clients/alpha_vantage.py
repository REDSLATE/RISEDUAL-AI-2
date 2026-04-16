"""Alpha Vantage REST API client.

Provides async methods for company overviews, daily time series, and
technical indicators (SMA, RSI, MACD) via the Alpha Vantage API.
"""
from __future__ import annotations

from typing import Any

import httpx

from risedual_core.clients.base import BaseMarketClient


class AlphaVantageClient(BaseMarketClient):
    """Async client for the Alpha Vantage REST API.

    All requests target the single Alpha Vantage query endpoint.  The API key
    is appended automatically.  Errors are caught gracefully; empty dicts are
    returned on failure.

    Parameters
    ----------
    api_key:
        Alpha Vantage API key.
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


    # Rate limit: Alpha Vantage free tier: 5 req/min (25/day)
    _default_rate: float = 0.083
    _default_capacity: float = 2.0

    @property
    def base_url(self) -> str:
        """Alpha Vantage query endpoint (all functions use this URL)."""
        return "https://www.alphavantage.co/query"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return base query params (apikey) merged with any *extra* pairs."""
        params: dict[str, Any] = {"apikey": self._api_key}
        if extra:
            params.update(extra)
        return params

    # ── Public API methods ────────────────────────────────────────────────────

    async def get_overview(self, symbol: str) -> dict[str, Any]:
        """Fetch company overview and fundamental data for *symbol*.

        Calls ``function=OVERVIEW``.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``).

        Returns
        -------
        dict
            Raw Alpha Vantage OVERVIEW payload containing fields such as
            ``Symbol``, ``Name``, ``Sector``, ``PERatio``, ``EPS``, etc.
            Empty dict on failure or API error.
        """
        data = await self._get(
            self.base_url,
            params=self._params({"function": "OVERVIEW", "symbol": symbol}),
            timeout=30.0,
        )
        if not isinstance(data, dict) or "Symbol" not in data:
            return {}
        return data

    async def get_daily(
        self,
        symbol: str,
        outputsize: str = "compact",
    ) -> dict[str, Any]:
        """Fetch daily OHLCV time series for *symbol*.

        Calls ``function=TIME_SERIES_DAILY``.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``).
        outputsize:
            ``"compact"`` (last 100 data points) or ``"full"`` (full history).

        Returns
        -------
        dict
            Top-level metadata and ``"Time Series (Daily)"`` key containing
            date-keyed OHLCV sub-dicts.  Empty dict on failure.
        """
        data = await self._get(
            self.base_url,
            params=self._params({
                "function": "TIME_SERIES_DAILY",
                "symbol": symbol,
                "outputsize": outputsize,
            }),
            timeout=30.0,
        )
        if not isinstance(data, dict) or "Time Series (Daily)" not in data:
            return {}
        return data

    async def get_sma(
        self,
        symbol: str,
        interval: str = "daily",
        time_period: int = 20,
        series_type: str = "close",
    ) -> dict[str, Any]:
        """Fetch Simple Moving Average (SMA) values for *symbol*.

        Calls ``function=SMA``.

        Parameters
        ----------
        symbol:
            Ticker symbol.
        interval:
            Time interval between data points.  One of ``"daily"``,
            ``"weekly"``, ``"monthly"``, ``"1min"``, ``"5min"``,
            ``"15min"``, ``"30min"``, ``"60min"``.
        time_period:
            Number of data points used to calculate each SMA value.
        series_type:
            Price type: ``"close"``, ``"open"``, ``"high"``, or ``"low"``.

        Returns
        -------
        dict
            Metadata and ``"Technical Analysis: SMA"`` key.
            Empty dict on failure.
        """
        data = await self._get(
            self.base_url,
            params=self._params({
                "function": "SMA",
                "symbol": symbol,
                "interval": interval,
                "time_period": time_period,
                "series_type": series_type,
            }),
            timeout=30.0,
        )
        if not isinstance(data, dict) or "Technical Analysis: SMA" not in data:
            return {}
        return data

    async def get_rsi(
        self,
        symbol: str,
        interval: str = "daily",
        time_period: int = 14,
        series_type: str = "close",
    ) -> dict[str, Any]:
        """Fetch Relative Strength Index (RSI) values for *symbol*.

        Calls ``function=RSI``.

        Parameters
        ----------
        symbol:
            Ticker symbol.
        interval:
            Time interval between data points.
        time_period:
            Number of data points used to calculate each RSI value.
        series_type:
            Price type: ``"close"``, ``"open"``, ``"high"``, or ``"low"``.

        Returns
        -------
        dict
            Metadata and ``"Technical Analysis: RSI"`` key.
            Empty dict on failure.
        """
        data = await self._get(
            self.base_url,
            params=self._params({
                "function": "RSI",
                "symbol": symbol,
                "interval": interval,
                "time_period": time_period,
                "series_type": series_type,
            }),
            timeout=30.0,
        )
        if not isinstance(data, dict) or "Technical Analysis: RSI" not in data:
            return {}
        return data

    async def get_macd(
        self,
        symbol: str,
        interval: str = "daily",
        series_type: str = "close",
    ) -> dict[str, Any]:
        """Fetch MACD (Moving Average Convergence/Divergence) values for *symbol*.

        Calls ``function=MACD`` with default parameters:
        fastperiod=12, slowperiod=26, signalperiod=9.

        Parameters
        ----------
        symbol:
            Ticker symbol.
        interval:
            Time interval between data points.
        series_type:
            Price type: ``"close"``, ``"open"``, ``"high"``, or ``"low"``.

        Returns
        -------
        dict
            Metadata and ``"Technical Analysis: MACD"`` key where each date
            maps to ``{"MACD": ..., "MACD_Signal": ..., "MACD_Hist": ...}``.
            Empty dict on failure.
        """
        data = await self._get(
            self.base_url,
            params=self._params({
                "function": "MACD",
                "symbol": symbol,
                "interval": interval,
                "series_type": series_type,
            }),
            timeout=30.0,
        )
        if not isinstance(data, dict) or "Technical Analysis: MACD" not in data:
            return {}
        return data
