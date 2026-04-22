"""Polygon.io REST API client — drop-in alternative to :class:`FinnhubClient`.

Prototype adapter for the Phase-1 data-API consolidation goal: give
callers a single swappable interface so we can flip providers via the
`MARKET_DATA_PROVIDER` env var without touching the call sites.

Field mapping is hand-picked to match `FinnhubClient`'s normalised
output so downstream code doesn't need to care which provider served
the request.

Rate limits
-----------
Polygon's free tier allows 5 req/min — stricter than Finnhub's 60/min.
The real rate-limit win kicks in on the Starter plan ($29/mo) which
is unlimited. We expose :attr:`_default_rate` / :attr:`_default_capacity`
on the class so callers can bump them up once they upgrade the
subscription; the `MARKET_DATA_POLYGON_RPS` env var provides the same
override without code changes.

Endpoint map (Polygon → Finnhub equivalent)
-------------------------------------------
  /v2/last/trade/{symbol}              → get_quote
  /v3/reference/tickers/{symbol}       → get_company_profile
  /v2/reference/news?ticker={symbol}   → get_news
  (no direct equivalent)               → get_recommendation_trends ⇒ []
  /v2/aggs/ticker/{symbol}/range/...   → get_aggregates (bonus, bar history)

The recommendation-trends call returns an empty list with a debug log
so callers fall back gracefully — this field was a Finnhub specialty
and Polygon doesn't publish analyst-consensus in the same form.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from risedual_core.clients.base import BaseMarketClient

logger = logging.getLogger(__name__)


class PolygonClient(BaseMarketClient):
    """Async client for the Polygon.io REST API.

    Mirrors :class:`FinnhubClient`'s public surface so the two can be
    swapped via configuration without touching call sites.
    """

    # Polygon free tier: 5 req/min. Callers on paid plans should set
    # `MARKET_DATA_POLYGON_RPS` (requests per second) to relax this.
    _default_rate: float = float(os.getenv("MARKET_DATA_POLYGON_RPS", "0.08"))
    _default_capacity: float = float(os.getenv("MARKET_DATA_POLYGON_BURST", "5"))

    def __init__(
        self,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key=api_key, http_client=http_client)

    @property
    def base_url(self) -> str:
        """Polygon REST base URL."""
        return "https://api.polygon.io"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return query params with the api key appended."""
        params: dict[str, Any] = {"apiKey": self._api_key}
        if extra:
            params.update(extra)
        return params

    # ── Public API methods ────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """Real-time last-trade quote for *symbol*, normalised to the
        same shape as :meth:`FinnhubClient.get_quote`.

        Polygon's `/v2/last/trade` endpoint is **paid-tier only**
        (free tier returns 403). We try it first, and on 403 fall
        back to the previous day's close from `/v2/aggs/.../prev`
        which is accessible on all tiers. The fallback loses the
        real-time tick but gives callers SOMETHING rather than an
        empty dict — critical for the provider-shadow mode that
        runs on free keys in dev.

        Always enriches with the previous-day bar for
        `high`/`low`/`open`/`previous_close`. Returns empty dict
        on total failure.
        """
        last_trade = await self._get(
            f"{self.base_url}/v2/last/trade/{symbol}",
            params=self._params(),
        )
        prev = await self._get(
            f"{self.base_url}/v2/aggs/ticker/{symbol}/prev",
            params=self._params({"adjusted": "true"}),
        )

        prev_bars = (
            prev.get("results") if isinstance(prev, dict) and prev else None
        )
        prev_bar = prev_bars[0] if prev_bars else {}
        prev_close = prev_bar.get("c")

        # Preferred: real-time last trade (paid tier).
        current: float | None = None
        ts: int | None = None
        if isinstance(last_trade, dict):
            results = last_trade.get("results") or {}
            current = results.get("p")
            ts = results.get("t")

        # Fallback: previous-day close (free tier). Marks the tier
        # via an explicit `source` field so admin UIs can surface it.
        source = "last_trade"
        if current is None and prev_close is not None:
            current = prev_close
            ts = prev_bar.get("t")
            source = "prev_close_fallback"

        if current is None:
            return {}

        change = None
        percent_change = None
        if prev_close and source == "last_trade":
            # Only compute change when we actually have a real-time
            # reference — prev_close vs prev_close is always 0 and
            # would mask stale-data problems.
            change = current - prev_close
            percent_change = (change / prev_close) * 100 if prev_close else None

        return {
            "current_price": current,
            "change": change,
            "percent_change": percent_change,
            "high": prev_bar.get("h"),
            "low": prev_bar.get("l"),
            "open": prev_bar.get("o"),
            "previous_close": prev_close,
            "timestamp": ts,
            "source": source,
        }

    async def get_company_profile(self, symbol: str) -> dict[str, Any]:
        """Company metadata — maps Polygon's `/v3/reference/tickers/{id}`
        response into the same keys as :meth:`FinnhubClient.get_company_profile`.
        """
        data = await self._get(
            f"{self.base_url}/v3/reference/tickers/{symbol}",
            params=self._params(),
        )
        if not isinstance(data, dict):
            return {}
        results = data.get("results") or {}
        if not results:
            return {}

        # Polygon market_cap is a full integer; Finnhub reports in millions.
        # Normalise to Finnhub's scale so downstream callers don't care.
        market_cap_raw = results.get("market_cap")
        market_cap_mm: float | None = (
            market_cap_raw / 1_000_000 if isinstance(market_cap_raw, (int, float)) else None
        )

        return {
            "name": results.get("name"),
            "ticker": results.get("ticker"),
            "exchange": results.get("primary_exchange"),
            "industry": results.get("sic_description"),
            "market_cap": market_cap_mm,
            "ipo_date": results.get("list_date"),
            "currency": results.get("currency_name"),
            "country": results.get("locale"),
            "phone": results.get("phone_number"),
            "logo": (results.get("branding") or {}).get("logo_url"),
            "weburl": results.get("homepage_url"),
            "shares_outstanding": results.get("share_class_shares_outstanding"),
        }

    async def get_news(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """Company news — maps Polygon's `/v2/reference/news` into the
        same item schema as :meth:`FinnhubClient.get_news`.

        Polygon uses ISO-8601 datetimes directly, so no unix-epoch
        conversion is needed here.
        """
        data = await self._get(
            f"{self.base_url}/v2/reference/news",
            params=self._params(
                {
                    "ticker": symbol,
                    "published_utc.gte": from_date,
                    "published_utc.lte": to_date,
                    "order": "desc",
                    "limit": 50,
                }
            ),
        )
        if not isinstance(data, dict):
            return []
        results = data.get("results") or []
        items: list[dict[str, Any]] = []
        for item in results:
            items.append(
                {
                    "headline": item.get("title"),
                    "summary": item.get("description"),
                    "url": item.get("article_url"),
                    "datetime": item.get("published_utc"),
                    "source": (item.get("publisher") or {}).get("name"),
                    "image": item.get("image_url"),
                    # Polygon tags articles with multiple keywords — we surface
                    # the first one as `category` to mirror Finnhub's single-
                    # category shape.
                    "category": (item.get("keywords") or [None])[0],
                }
            )
        return items

    async def get_recommendation_trends(self, symbol: str) -> list[dict[str, Any]]:
        """Analyst recommendation trends — Polygon has no direct
        equivalent to Finnhub's ``/stock/recommendation``. Returns an
        empty list so callers fall back gracefully."""
        logger.debug(
            "[polygon] get_recommendation_trends(%s) — not supported, returning []",
            symbol,
        )
        return []

    # ── Bonus: historical bars (Polygon excels here) ──────────────────────────

    async def get_aggregates(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
        timespan: str = "day",
        multiplier: int = 1,
        adjusted: bool = True,
    ) -> list[dict[str, Any]]:
        """Historical OHLCV aggregates — the feature Polygon is best at.

        Returns a list of bars with keys: ``open``, ``high``, ``low``,
        ``close``, ``volume``, ``vwap``, ``timestamp`` (ISO-8601 UTC),
        ``trades``. Empty list on failure.
        """
        url = (
            f"{self.base_url}/v2/aggs/ticker/{symbol}"
            f"/range/{multiplier}/{timespan}/{from_date}/{to_date}"
        )
        data = await self._get(
            url,
            params=self._params({"adjusted": "true" if adjusted else "false"}),
        )
        if not isinstance(data, dict):
            return []
        rows = data.get("results") or []
        bars: list[dict[str, Any]] = []
        for row in rows:
            ts = row.get("t")
            iso = (
                datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
                if isinstance(ts, (int, float))
                else None
            )
            bars.append(
                {
                    "open": row.get("o"),
                    "high": row.get("h"),
                    "low": row.get("l"),
                    "close": row.get("c"),
                    "volume": row.get("v"),
                    "vwap": row.get("vw"),
                    "timestamp": iso,
                    "trades": row.get("n"),
                }
            )
        return bars
