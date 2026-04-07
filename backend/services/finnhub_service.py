"""Finnhub integration service — congressional trades, insider transactions, earnings, company news."""
import os
import httpx
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"


class FinnhubService:
    def __init__(self):
        self.api_key = os.environ.get("FINNHUB_API_KEY", "")
        self.headers = {"X-Finnhub-Token": self.api_key}

    def _is_configured(self) -> bool:
        return bool(self.api_key) and self.api_key != "your_finnhub_api_key"

    async def _get(self, endpoint: str, params: dict = None) -> dict | list:
        if not self._is_configured():
            logger.warning("Finnhub API key not configured — returning empty data")
            return {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{FINNHUB_BASE}{endpoint}",
                    headers=self.headers,
                    params=params or {},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Finnhub API error {e.response.status_code} on {endpoint}: {e.response.text[:200]}")
            return {}
        except Exception as e:
            logger.error(f"Finnhub request error on {endpoint}: {e}")
            return {}

    # ── Congressional Trades ─────────────────────────────────────────────

    async def get_congressional_trades(self, symbol: Optional[str] = None) -> List[Dict]:
        """Fetch recent congressional stock trades from Finnhub.
        Note: This endpoint requires a premium Finnhub plan. Returns empty on 403."""
        params = {}
        if symbol:
            params["symbol"] = symbol.upper()

        data = await self._get("/stock/congressional-trading", params)
        if not data:
            # 403 or other error — free tier doesn't include this endpoint
            return []
        trades = data.get("data", []) if isinstance(data, dict) else []

        results = []
        for t in trades[:30]:
            results.append({
                "representative": t.get("name", "Unknown"),
                "symbol": t.get("symbol", "N/A"),
                "transaction_type": t.get("transactionType", "N/A"),
                "amount": t.get("amountFrom", "N/A"),
                "amount_to": t.get("amountTo", "N/A"),
                "transaction_date": t.get("transactionDate", "N/A"),
                "disclosure_date": t.get("disclosureDate", "N/A"),
                "description": f"{t.get('name', 'Unknown')} {t.get('transactionType', '')} {t.get('symbol', 'N/A')} (${t.get('amountFrom', '?')} - ${t.get('amountTo', '?')})",
            })
        return results

    # ── Insider Transactions ─────────────────────────────────────────────

    async def get_insider_transactions(self, symbol: str) -> List[Dict]:
        """Fetch insider transactions (SEC Form 4) for a given symbol."""
        data = await self._get("/stock/insider-transactions", {"symbol": symbol.upper()})
        txns = data.get("data", []) if isinstance(data, dict) else []

        results = []
        for t in txns[:20]:
            tx_type = t.get("transactionType", "")
            action = "Purchase" if tx_type in ("P", "A", "M") else "Sale" if tx_type in ("S", "D") else tx_type
            results.append({
                "name": t.get("name", "Unknown"),
                "symbol": symbol.upper(),
                "action": action,
                "shares": t.get("share", 0),
                "price": t.get("transactionPrice", 0),
                "value": round((t.get("share", 0) or 0) * (t.get("transactionPrice", 0) or 0), 2),
                "filing_date": t.get("filingDate", "N/A"),
                "description": f"{t.get('name', 'Unknown')} {action} {t.get('share', 0)} shares of {symbol.upper()} at ${t.get('transactionPrice', 'N/A')}",
            })
        return results

    # ── Earnings Calendar ────────────────────────────────────────────────

    async def get_earnings_calendar(self, days_ahead: int = 14) -> List[Dict]:
        """Fetch upcoming earnings announcements."""
        today = datetime.now().strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        data = await self._get("/calendar/earnings", {"from": today, "to": future})
        events = data.get("earningsCalendar", []) if isinstance(data, dict) else []

        results = []
        for e in events[:30]:
            results.append({
                "symbol": e.get("symbol", "N/A"),
                "date": e.get("date", "N/A"),
                "quarter": e.get("quarter", "N/A"),
                "eps_estimate": e.get("epsEstimate"),
                "eps_actual": e.get("epsActual"),
                "revenue_estimate": e.get("revenueEstimate"),
                "revenue_actual": e.get("revenueActual"),
                "hour": e.get("hour", "N/A"),
            })
        return results

    # ── Company News ─────────────────────────────────────────────────────

    async def get_company_news(self, symbol: str) -> List[Dict]:
        """Fetch recent news for a specific company."""
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        data = await self._get("/company-news", {"symbol": symbol.upper(), "from": week_ago, "to": today})
        articles = data if isinstance(data, list) else []

        results = []
        for a in articles[:15]:
            results.append({
                "headline": a.get("headline", "N/A"),
                "source": a.get("source", "N/A"),
                "url": a.get("url", ""),
                "summary": a.get("summary", ""),
                "datetime": a.get("datetime", 0),
                "category": a.get("category", "general"),
                "image": a.get("image", ""),
            })
        return results

    # ── Aggregate for Predictions ────────────────────────────────────────

    async def get_all_data_for_predictions(self, symbol: Optional[str] = None) -> Dict:
        """Fetch all Finnhub data sources in one call for the prediction engine."""
        congressional = await self.get_congressional_trades(symbol)
        earnings = await self.get_earnings_calendar()

        # Get insider data for the symbol, or for top tickers if no symbol
        if symbol:
            insider = await self.get_insider_transactions(symbol)
            news = await self.get_company_news(symbol)
        else:
            # Fetch insider data for a few major tickers to give the AI context
            insider = []
            for tk in ["AAPL", "MSFT", "NVDA"]:
                insider.extend(await self.get_insider_transactions(tk))
            insider = insider[:20]
            news = []

        return {
            "congressional_trades": congressional,
            "congressional_count": len(congressional),
            "insider_transactions": insider,
            "insider_count": len(insider),
            "upcoming_earnings": earnings,
            "earnings_count": len(earnings),
            "company_news": news,
            "news_count": len(news),
            "source": "Finnhub",
        }
