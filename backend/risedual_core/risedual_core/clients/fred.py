"""St. Louis Fed FRED REST API client.

Provides async methods for fetching economic data series observations and
searching available FRED series.

Common FRED series IDs for reference:
    GDP       – Gross Domestic Product (quarterly, billions USD)
    UNRATE    – Civilian Unemployment Rate (monthly, %)
    CPIAUCSL  – Consumer Price Index, All Urban Consumers (monthly)
    DFF       – Federal Funds Effective Rate (daily, %)
    T10Y2Y    – 10-Year minus 2-Year Treasury Yield Spread (daily, %)
    VIXCLS    – CBOE Volatility Index (daily)
"""
from __future__ import annotations

from typing import Any

import httpx

from risedual_core.clients.base import BaseMarketClient


class FredClient(BaseMarketClient):
    """Async client for the St. Louis Fed FRED REST API.

    All methods require a valid FRED API key passed at construction time.
    Errors are caught gracefully; empty lists are returned on failure so the
    caller never needs to handle raw HTTP exceptions.

    Parameters
    ----------
    api_key:
        FRED API key (obtain at https://fred.stlouisfed.org/docs/api/api_key.html).
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


    # Rate limit: FRED: generous limits, using conservative 30/min
    _default_rate: float = 0.5
    _default_capacity: float = 3.0

    @property
    def base_url(self) -> str:
        """FRED API base URL."""
        return "https://api.stlouisfed.org/fred"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return base query params (api_key + file_type=json) merged with *extra*."""
        params: dict[str, Any] = {
            "api_key": self._api_key,
            "file_type": "json",
        }
        if extra:
            params.update(extra)
        return params

    # ── Public API methods ────────────────────────────────────────────────────

    async def get_series(
        self,
        series_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Fetch recent observations for a FRED data series.

        Calls ``GET /series/observations`` sorted descending so the most
        recent *limit* observations are returned.

        Parameters
        ----------
        series_id:
            FRED series identifier (e.g. ``"GDP"``, ``"UNRATE"``).
        limit:
            Maximum number of observations to return.

        Returns
        -------
        list[dict]
            Each dict has keys: ``date`` (``YYYY-MM-DD``), ``value`` (string —
            ``"."`` indicates a missing observation), ``series_id``.
            Empty list on failure.
        """
        data = await self._get(
            f"{self.base_url}/series/observations",
            params=self._params({
                "series_id": series_id,
                "sort_order": "desc",
                "limit": limit,
            }),
        )
        if not isinstance(data, dict) or "observations" not in data:
            return []
        return [
            {
                "date": obs.get("date"),
                "value": obs.get("value"),
                "series_id": series_id,
            }
            for obs in data["observations"]
        ]

    async def search_series(
        self,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search for FRED data series matching *query*.

        Calls ``GET /series/search`` ordered by popularity descending.

        Parameters
        ----------
        query:
            Free-text search string (e.g. ``"unemployment"``).
        limit:
            Maximum number of series results to return.

        Returns
        -------
        list[dict]
            Each dict has keys: ``id``, ``title``, ``observation_start``,
            ``observation_end``, ``frequency``, ``units``,
            ``seasonal_adjustment``, ``notes``.
            Empty list on failure.
        """
        data = await self._get(
            f"{self.base_url}/series/search",
            params=self._params({
                "search_text": query,
                "limit": limit,
                "order_by": "popularity",
                "sort_order": "desc",
            }),
        )
        if not isinstance(data, dict) or "seriess" not in data:
            return []
        return [
            {
                "id": s.get("id"),
                "title": s.get("title"),
                "observation_start": s.get("observation_start"),
                "observation_end": s.get("observation_end"),
                "frequency": s.get("frequency"),
                "units": s.get("units"),
                "seasonal_adjustment": s.get("seasonal_adjustment"),
                "notes": s.get("notes"),
            }
            for s in data["seriess"]
        ]
