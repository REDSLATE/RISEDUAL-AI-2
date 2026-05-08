"""Financial Modeling Prep (FMP) REST API client.

Provides async methods for income statements, balance sheets, cash flow
statements, key financial ratios, and earnings calendar data.
"""
from __future__ import annotations

from typing import Any

import httpx

from risedual_core.clients.base import BaseMarketClient


class FMPClient(BaseMarketClient):
    """Async client for the Financial Modeling Prep (FMP) REST API.

    Covers endpoints not available on Finnhub or Alpha Vantage: income
    statements, balance sheets, cash flow statements, financial ratios,
    and earnings history.

    All methods return normalised dicts with snake_case keys.  Errors are
    caught gracefully; empty lists are returned on failure.

    Parameters
    ----------
    api_key:
        FMP API key.  The free tier supports most endpoints with rate limits.
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


    # Rate limit: FMP free tier: ~250 req/day, throttled to 10/min
    _default_rate: float = 0.17
    _default_capacity: float = 3.0

    @property
    def base_url(self) -> str:
        """FMP v3 API base URL."""
        return "https://financialmodelingprep.com/api/v3"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return base query params (apikey) merged with any *extra* pairs."""
        params: dict[str, Any] = {"apikey": self._api_key}
        if extra:
            params.update(extra)
        return params

    # ── Public API methods ────────────────────────────────────────────────────

    async def get_income_statement(
        self,
        symbol: str,
        period: str = "annual",
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        """Fetch income statements for *symbol*.

        Calls ``GET /income-statement/{symbol}``.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``).
        period:
            ``"annual"`` or ``"quarter"``.
        limit:
            Number of periods to return (default 4).

        Returns
        -------
        list[dict]
            Each dict contains: ``date``, ``period``, ``revenue``,
            ``gross_profit``, ``gross_margin_pct``, ``operating_income``,
            ``operating_margin_pct``, ``net_income``, ``net_margin_pct``,
            ``eps``, ``ebitda``.  Empty list on failure.
        """
        data = await self._get(
            f"{self.base_url}/income-statement/{symbol.upper()}",
            params=self._params({"period": period, "limit": limit}),
        )
        if not isinstance(data, list):
            return []
        return [
            {
                "date": d.get("date"),
                "period": d.get("period"),
                "revenue": d.get("revenue"),
                "gross_profit": d.get("grossProfit"),
                "gross_margin_pct": round((d.get("grossProfitRatio") or 0) * 100, 2),
                "operating_income": d.get("operatingIncome"),
                "operating_margin_pct": round((d.get("operatingIncomeRatio") or 0) * 100, 2),
                "net_income": d.get("netIncome"),
                "net_margin_pct": round((d.get("netIncomeRatio") or 0) * 100, 2),
                "eps": d.get("eps"),
                "ebitda": d.get("ebitda"),
            }
            for d in data
        ]

    async def get_balance_sheet(
        self,
        symbol: str,
        period: str = "annual",
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        """Fetch balance sheet statements for *symbol*.

        Calls ``GET /balance-sheet-statement/{symbol}``.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``).
        period:
            ``"annual"`` or ``"quarter"``.
        limit:
            Number of periods to return (default 4).

        Returns
        -------
        list[dict]
            Each dict contains: ``date``, ``period``, ``cash_and_equivalents``,
            ``total_assets``, ``total_liabilities``, ``total_equity``,
            ``total_debt``, ``current_ratio``, ``debt_to_equity``,
            ``book_value_per_share``.  Empty list on failure.
        """
        data = await self._get(
            f"{self.base_url}/balance-sheet-statement/{symbol.upper()}",
            params=self._params({"period": period, "limit": limit}),
        )
        if not isinstance(data, list):
            return []
        results: list[dict[str, Any]] = []
        for d in data:
            total_equity = d.get("totalStockholdersEquity") or 1  # avoid div/0
            total_debt = d.get("totalDebt") or 0
            current_assets = d.get("totalCurrentAssets") or 0
            current_liab = d.get("totalCurrentLiabilities") or 1  # avoid div/0
            results.append({
                "date": d.get("date"),
                "period": d.get("period"),
                "cash_and_equivalents": d.get("cashAndCashEquivalents"),
                "total_assets": d.get("totalAssets") or 0,
                "total_liabilities": d.get("totalLiabilities") or 0,
                "total_equity": total_equity,
                "total_debt": total_debt,
                "current_ratio": round(current_assets / current_liab, 2),
                "debt_to_equity": round(total_debt / total_equity, 2),
                "book_value_per_share": d.get("bookValuePerShare"),
            })
        return results

    async def get_cash_flow(
        self,
        symbol: str,
        period: str = "annual",
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        """Fetch cash flow statements for *symbol*.

        Calls ``GET /cash-flow-statement/{symbol}``.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``).
        period:
            ``"annual"`` or ``"quarter"``.
        limit:
            Number of periods to return (default 4).

        Returns
        -------
        list[dict]
            Each dict contains: ``date``, ``period``, ``operating_cash_flow``,
            ``capex``, ``free_cash_flow``, ``dividends_paid``,
            ``net_cash_from_financing``.  Empty list on failure.
        """
        data = await self._get(
            f"{self.base_url}/cash-flow-statement/{symbol.upper()}",
            params=self._params({"period": period, "limit": limit}),
        )
        if not isinstance(data, list):
            return []
        return [
            {
                "date": d.get("date"),
                "period": d.get("period"),
                "operating_cash_flow": d.get("operatingCashFlow"),
                "capex": d.get("capitalExpenditure"),
                "free_cash_flow": d.get("freeCashFlow"),
                "dividends_paid": d.get("dividendsPaid"),
                "net_cash_from_financing": d.get("netCashUsedForFinancingActivites"),
            }
            for d in data
        ]

    async def get_key_ratios(
        self,
        symbol: str,
        period: str = "annual",
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        """Fetch key financial ratios for *symbol*.

        Calls ``GET /ratios/{symbol}``.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``).
        period:
            ``"annual"`` or ``"quarter"``.
        limit:
            Number of periods to return (default 4).

        Returns
        -------
        list[dict]
            Each dict contains: ``date``, ``period``, ``pe_ratio``,
            ``pb_ratio``, ``ps_ratio``, ``peg_ratio``, ``roe`` (%),
            ``roa`` (%), ``current_ratio``, ``quick_ratio``,
            ``debt_to_equity``, ``dividend_yield`` (%).
            Empty list on failure.
        """
        data = await self._get(
            f"{self.base_url}/ratios/{symbol.upper()}",
            params=self._params({"period": period, "limit": limit}),
        )
        if not isinstance(data, list):
            return []
        return [
            {
                "date": d.get("date"),
                "period": d.get("period"),
                "pe_ratio": d.get("priceEarningsRatio"),
                "pb_ratio": d.get("priceToBookRatio"),
                "ps_ratio": d.get("priceToSalesRatio"),
                "peg_ratio": d.get("priceEarningsToGrowthRatio"),
                "roe": round((d.get("returnOnEquity") or 0) * 100, 2),
                "roa": round((d.get("returnOnAssets") or 0) * 100, 2),
                "current_ratio": d.get("currentRatio"),
                "quick_ratio": d.get("quickRatio"),
                "debt_to_equity": d.get("debtEquityRatio"),
                "dividend_yield": round((d.get("dividendYield") or 0) * 100, 2),
            }
            for d in data
        ]

    async def get_earnings_calendar(
        self,
        symbol: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Fetch historical EPS actuals vs. estimates for *symbol*.

        Calls ``GET /historical/earning_calendar/{symbol}``.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``).
        limit:
            Number of earnings events to return (default 8).

        Returns
        -------
        list[dict]
            Each dict contains: ``date``, ``eps_actual``, ``eps_estimated``,
            ``revenue_actual``, ``revenue_estimated``, ``surprise_pct``
            (None when estimates are unavailable).  Empty list on failure.
        """
        data = await self._get(
            f"{self.base_url}/historical/earning_calendar/{symbol.upper()}",
            params=self._params({"limit": limit}),
        )
        if not isinstance(data, list):
            return []
        results: list[dict[str, Any]] = []
        for d in data:
            eps_actual = d.get("eps")
            eps_estimated = d.get("epsEstimated")
            surprise_pct: float | None = None
            if eps_actual is not None and eps_estimated and eps_estimated != 0:
                surprise_pct = round((eps_actual - eps_estimated) / abs(eps_estimated) * 100, 2)
            results.append({
                "date": d.get("date"),
                "eps_actual": eps_actual,
                "eps_estimated": eps_estimated,
                "revenue_actual": d.get("revenue"),
                "revenue_estimated": d.get("revenueEstimated"),
                "surprise_pct": surprise_pct,
            })
        return results
