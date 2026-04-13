"""QuiverQuant Service — Alternative data from QuiverQuant API.

Provides congressional trading, insider trading, corporate lobbying,
and government contracts data. Used as PRIMARY source with existing
scrapers as fallbacks.
"""
import os
import logging
import asyncio
from typing import List, Dict, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_api_key: Optional[str] = None


def _get_key() -> Optional[str]:
    global _api_key
    if _api_key is None:
        _api_key = os.environ.get("QUIVER_API_KEY", "")
    return _api_key if _api_key else None


def _init_client():
    """Initialize QuiverQuant client. Returns None if no key."""
    key = _get_key()
    if not key:
        return None
    try:
        import quiverquant
        return quiverquant.quiver(key)
    except Exception as e:
        logger.error(f"QuiverQuant client init failed: {e}")
        return None


async def get_congressional_trades(ticker: Optional[str] = None, limit: int = 20) -> List[Dict]:
    """Fetch recent congressional stock trades from QuiverQuant."""
    try:
        client = _init_client()
        if not client:
            return []

        df = await asyncio.to_thread(
            lambda: client.congress_trading(ticker) if ticker else client.congress_trading()
        )

        if df is None or df.empty:
            return []

        trades = []
        for _, row in df.head(limit).iterrows():
            representative = str(row.get("Representative", row.get("representative", "")))
            tx_type = str(row.get("Transaction", row.get("transaction", "")))
            amount = str(row.get("Amount", row.get("amount", "")))
            tkr = str(row.get("Ticker", row.get("ticker", ticker or "")))
            date_val = row.get("TransactionDate", row.get("Date", row.get("date", "")))
            party = str(row.get("Party", row.get("party", "")))
            chamber = str(row.get("House", row.get("house", "")))

            date_str = ""
            if hasattr(date_val, "strftime"):
                date_str = date_val.strftime("%Y-%m-%d")
            elif date_val:
                date_str = str(date_val)[:10]

            party_short = "D" if "dem" in party.lower() else "R" if "rep" in party.lower() else ""

            trades.append({
                "representative": representative,
                "ticker": tkr,
                "transaction_date": date_str,
                "type": tx_type,
                "amount": amount,
                "party": party_short,
                "chamber": chamber,
                "description": f"{tx_type.upper()} by {representative} ({party_short}-{chamber}) — {tkr} {amount}",
                "source": "quiverquant",
            })

        logger.info(f"QuiverQuant congressional: {len(trades)} trades")
        return trades

    except Exception as e:
        logger.warning(f"QuiverQuant congressional trades error: {e}")
        return []


async def get_insider_trades(ticker: Optional[str] = None, limit: int = 20) -> List[Dict]:
    """Fetch recent insider trades (SEC Form 4) from QuiverQuant."""
    try:
        key = _get_key()
        if not key:
            return []

        import requests
        headers = {"Authorization": f"Token {key}", "Accept": "application/json"}
        url = "https://api.quiverquant.com/beta/live/insiders"
        if ticker:
            url = f"https://api.quiverquant.com/beta/historical/insiders/{ticker}"

        resp = await asyncio.to_thread(
            lambda: requests.get(url, headers=headers, timeout=30)
        )
        if resp.status_code != 200:
            logger.warning(f"QuiverQuant insiders: HTTP {resp.status_code}")
            return []

        data = resp.json()
        if not data:
            return []

        trades = []
        for row in data[-limit:] if len(data) > limit else data:
            name = str(row.get("Name", row.get("name", "")))
            title = str(row.get("Title", row.get("title", "")))
            tx_type = str(row.get("Transaction", row.get("transaction", "")))
            shares = str(row.get("Shares", row.get("shares", "")))
            price = str(row.get("Price", row.get("price", "")))
            tkr = str(row.get("Ticker", row.get("ticker", ticker or "")))
            date_str = str(row.get("Date", row.get("date", "")))[:10]

            trades.append({
                "form_type": "Form 4",
                "filed_date": date_str,
                "ticker": tkr,
                "insider_name": name,
                "insider_title": title,
                "trade_type": tx_type,
                "price": price,
                "qty": shares,
                "description": f"Insider {tx_type} by {name} ({title}) — {tkr} {shares} shares @ ${price}",
                "source": "quiverquant",
            })

        logger.info(f"QuiverQuant insiders: {len(trades)} trades")
        return trades

    except Exception as e:
        logger.warning(f"QuiverQuant insider trades error: {e}")
        return []


async def get_lobbying(ticker: Optional[str] = None, limit: int = 20) -> List[Dict]:
    """Fetch recent corporate lobbying data from QuiverQuant."""
    try:
        key = _get_key()
        if not key:
            return []

        # Use direct API call (library has parsing issues with large responses)
        import requests
        headers = {"Authorization": f"Token {key}", "Accept": "application/json"}
        url = f"https://api.quiverquant.com/beta/live/lobbying"
        if ticker:
            url = f"https://api.quiverquant.com/beta/historical/lobbying/{ticker}"

        resp = await asyncio.to_thread(
            lambda: requests.get(url, headers=headers, timeout=30)
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        if not data:
            return []

        # Take most recent entries
        records = []
        for row in data[-limit:] if len(data) > limit else data:
            client_name = str(row.get("Client", row.get("client", "")))
            amount = row.get("Amount", row.get("amount", 0))
            issue = str(row.get("Issue", row.get("issue", "")))
            tkr = str(row.get("Ticker", row.get("ticker", ticker or "")))
            date_str = str(row.get("Date", row.get("date", "")))[:10]

            try:
                amount_float = float(amount)
            except (ValueError, TypeError):
                amount_float = 0

            records.append({
                "ticker": tkr,
                "client": client_name,
                "amount": amount_float,
                "issue": issue,
                "date": date_str,
                "description": f"{client_name} lobbied ${amount_float:,.0f} on {issue}",
                "source": "quiverquant",
            })

        logger.info(f"QuiverQuant lobbying: {len(records)} records")
        return records

    except Exception as e:
        logger.warning(f"QuiverQuant lobbying error: {e}")
        return []


async def get_gov_contracts(ticker: Optional[str] = None, limit: int = 20) -> List[Dict]:
    """Fetch government contract data from QuiverQuant."""
    try:
        key = _get_key()
        if not key:
            return []

        import requests
        headers = {"Authorization": f"Token {key}", "Accept": "application/json"}
        url = "https://api.quiverquant.com/beta/live/govcontracts"
        if ticker:
            url = f"https://api.quiverquant.com/beta/historical/govcontracts/{ticker}"

        resp = await asyncio.to_thread(
            lambda: requests.get(url, headers=headers, timeout=30)
        )
        if resp.status_code != 200:
            logger.warning(f"QuiverQuant gov contracts: HTTP {resp.status_code}")
            return []

        data = resp.json()
        if not data:
            return []

        contracts = []
        for row in data[-limit:] if len(data) > limit else data:
            agency = str(row.get("Agency", row.get("agency", "")))
            amount = row.get("Amount", row.get("amount", 0))
            desc = str(row.get("Description", row.get("description", "")))
            tkr = str(row.get("Ticker", row.get("ticker", ticker or "")))
            date_str = str(row.get("Date", row.get("date", "")))[:10]

            try:
                amount_float = float(amount)
            except (ValueError, TypeError):
                amount_float = 0

            contracts.append({
                "ticker": tkr,
                "agency": agency,
                "amount": amount_float,
                "description": desc[:200] if desc else "",
                "date": date_str,
                "source": "quiverquant",
            })

        logger.info(f"QuiverQuant contracts: {len(contracts)} records")
        return contracts

    except Exception as e:
        logger.warning(f"QuiverQuant gov contracts error: {e}")
        return []


def is_configured() -> bool:
    """Check if QuiverQuant API key is available."""
    return _get_key() is not None
