import os
import logging
from typing import Optional, List, Dict
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class AlpacaTradingService:
    """Alpaca — commission-free API-first trading. Supports paper + live."""

    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }

    def get_account(self) -> Optional[Dict]:
        try:
            r = requests.get(f"{self.base_url}/v2/account", headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Alpaca get_account error: {e}")
            return None

    def get_positions(self) -> List[Dict]:
        try:
            r = requests.get(f"{self.base_url}/v2/positions", headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Alpaca get_positions error: {e}")
            return []

    def place_order(self, symbol: str, qty, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None) -> Optional[Dict]:
        try:
            data = {
                "symbol": symbol.upper(),
                "qty": str(qty),
                "side": side.lower(),
                "type": order_type.lower(),
                "time_in_force": time_in_force.lower(),
            }
            if limit_price:
                data["limit_price"] = str(limit_price)
            if stop_price:
                data["stop_price"] = str(stop_price)
            r = requests.post(f"{self.base_url}/v2/orders", headers=self.headers, json=data, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Alpaca place_order error: {e}")
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> List[Dict]:
        try:
            r = requests.get(f"{self.base_url}/v2/orders", headers=self.headers,
                             params={"status": status, "limit": limit}, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Alpaca get_orders error: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            r = requests.delete(f"{self.base_url}/v2/orders/{order_id}", headers=self.headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Alpaca cancel_order error: {e}")
            return False

    def get_order(self, order_id: str) -> Optional[Dict]:
        try:
            r = requests.get(f"{self.base_url}/v2/orders/{order_id}", headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Alpaca get_order error: {e}")
            return None


class SchwabTradingService:
    """Charles Schwab (formerly TD Ameritrade) — API key-based trading."""

    def __init__(self, api_key: str, api_secret: str, **kwargs):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.schwabapi.com/trader/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def get_account(self) -> Optional[Dict]:
        try:
            r = requests.get(f"{self.base_url}/accounts", headers=self.headers, timeout=10)
            r.raise_for_status()
            accounts = r.json()
            if isinstance(accounts, list) and accounts:
                acc = accounts[0].get("securitiesAccount", accounts[0])
                return {
                    "account_number": acc.get("accountNumber", acc.get("accountId", "N/A")),
                    "cash": acc.get("currentBalances", {}).get("cashBalance", 0),
                    "buying_power": acc.get("currentBalances", {}).get("buyingPower", 0),
                    "equity": acc.get("currentBalances", {}).get("equity", 0),
                    "portfolio_value": acc.get("currentBalances", {}).get("liquidationValue", 0),
                }
            return accounts
        except Exception as e:
            logger.error(f"Schwab get_account error: {e}")
            return None

    def get_positions(self) -> List[Dict]:
        try:
            r = requests.get(f"{self.base_url}/accounts?fields=positions", headers=self.headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            positions = []
            if isinstance(data, list) and data:
                for pos in data[0].get("securitiesAccount", {}).get("positions", []):
                    instrument = pos.get("instrument", {})
                    positions.append({
                        "symbol": instrument.get("symbol", ""),
                        "qty": pos.get("longQuantity", 0) or pos.get("shortQuantity", 0),
                        "side": "long" if pos.get("longQuantity", 0) > 0 else "short",
                        "avg_entry_price": pos.get("averagePrice", 0),
                        "current_price": pos.get("currentDayProfitLossPercentage", 0),
                        "market_value": pos.get("marketValue", 0),
                        "unrealized_pl": pos.get("currentDayProfitLoss", 0),
                        "unrealized_plpc": pos.get("currentDayProfitLossPercentage", 0),
                    })
            return positions
        except Exception as e:
            logger.error(f"Schwab get_positions error: {e}")
            return []

    def place_order(self, symbol: str, qty, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price=None, stop_price=None) -> Optional[Dict]:
        try:
            account = self.get_account()
            acc_num = account.get("account_number", "") if account else ""
            data = {
                "orderType": order_type.upper(),
                "session": "NORMAL",
                "duration": time_in_force.upper() if time_in_force != "day" else "DAY",
                "orderStrategyType": "SINGLE",
                "orderLegCollection": [{
                    "instruction": "BUY" if side.lower() == "buy" else "SELL",
                    "quantity": int(qty),
                    "instrument": {"symbol": symbol.upper(), "assetType": "EQUITY"},
                }],
            }
            if limit_price:
                data["price"] = str(limit_price)
            r = requests.post(f"{self.base_url}/accounts/{acc_num}/orders",
                              headers=self.headers, json=data, timeout=10)
            r.raise_for_status()
            return {"id": r.headers.get("Location", ""), "status": "submitted", "symbol": symbol}
        except Exception as e:
            logger.error(f"Schwab place_order error: {e}")
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> List[Dict]:
        try:
            account = self.get_account()
            acc_num = account.get("account_number", "") if account else ""
            r = requests.get(f"{self.base_url}/accounts/{acc_num}/orders",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            logger.error(f"Schwab get_orders error: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            account = self.get_account()
            acc_num = account.get("account_number", "") if account else ""
            r = requests.delete(f"{self.base_url}/accounts/{acc_num}/orders/{order_id}",
                                headers=self.headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Schwab cancel_order error: {e}")
            return False


class IBKRTradingService:
    """Interactive Brokers — Client Portal API (gateway-based)."""

    def __init__(self, api_key: str, api_secret: str, **kwargs):
        # IBKR uses a local gateway. api_key = gateway URL, api_secret = account ID
        self.gateway_url = api_key if api_key.startswith("http") else "https://localhost:5000"
        self.account_id = api_secret
        self.headers = {"Content-Type": "application/json"}

    def get_account(self) -> Optional[Dict]:
        try:
            r = requests.get(f"{self.gateway_url}/v1/api/portfolio/accounts",
                             headers=self.headers, verify=False, timeout=10)
            r.raise_for_status()
            accounts = r.json()
            if isinstance(accounts, list) and accounts:
                acc = accounts[0]
                return {
                    "account_number": acc.get("accountId", "N/A"),
                    "cash": acc.get("availableFunds", {}).get("amount", 0),
                    "buying_power": acc.get("buyingPower", {}).get("amount", 0),
                    "equity": acc.get("netLiquidation", {}).get("amount", 0),
                    "portfolio_value": acc.get("netLiquidation", {}).get("amount", 0),
                }
            return None
        except Exception as e:
            logger.error(f"IBKR get_account error: {e}")
            return None

    def get_positions(self) -> List[Dict]:
        try:
            r = requests.get(f"{self.gateway_url}/v1/api/portfolio/{self.account_id}/positions/0",
                             headers=self.headers, verify=False, timeout=10)
            r.raise_for_status()
            positions = []
            for p in r.json():
                positions.append({
                    "symbol": p.get("ticker", p.get("contractDesc", "")),
                    "qty": abs(p.get("position", 0)),
                    "side": "long" if p.get("position", 0) > 0 else "short",
                    "avg_entry_price": p.get("avgCost", 0),
                    "current_price": p.get("mktPrice", 0),
                    "market_value": p.get("mktValue", 0),
                    "unrealized_pl": p.get("unrealizedPnl", 0),
                    "unrealized_plpc": 0,
                })
            return positions
        except Exception as e:
            logger.error(f"IBKR get_positions error: {e}")
            return []

    def place_order(self, symbol: str, qty, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price=None, stop_price=None) -> Optional[Dict]:
        try:
            data = {
                "orders": [{
                    "acctId": self.account_id,
                    "conid": 0,  # Would need contract lookup
                    "orderType": "MKT" if order_type == "market" else "LMT",
                    "side": side.upper(),
                    "quantity": int(qty),
                    "tif": time_in_force.upper(),
                }]
            }
            if limit_price:
                data["orders"][0]["price"] = limit_price
            r = requests.post(f"{self.gateway_url}/v1/api/iserver/account/{self.account_id}/orders",
                              headers=self.headers, json=data, verify=False, timeout=10)
            r.raise_for_status()
            result = r.json()
            return {"id": str(result), "status": "submitted", "symbol": symbol}
        except Exception as e:
            logger.error(f"IBKR place_order error: {e}")
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> List[Dict]:
        try:
            r = requests.get(f"{self.gateway_url}/v1/api/iserver/account/orders",
                             headers=self.headers, verify=False, timeout=10)
            r.raise_for_status()
            return r.json().get("orders", []) if isinstance(r.json(), dict) else r.json()
        except Exception as e:
            logger.error(f"IBKR get_orders error: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            r = requests.delete(
                f"{self.gateway_url}/v1/api/iserver/account/{self.account_id}/order/{order_id}",
                headers=self.headers, verify=False, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"IBKR cancel_order error: {e}")
            return False


class BrokerService:
    """Factory for broker clients."""

    SUPPORTED_BROKERS = {
        "alpaca": {
            "name": "Alpaca",
            "class": "AlpacaTradingService",
            "key_label": "API Key ID",
            "secret_label": "API Secret Key",
            "docs_url": "https://alpaca.markets/docs/trading",
            "has_paper": True,
            "signup_url": "https://app.alpaca.markets/signup",
        },
        "schwab": {
            "name": "Charles Schwab",
            "class": "SchwabTradingService",
            "key_label": "Access Token",
            "secret_label": "App Secret",
            "docs_url": "https://developer.schwab.com",
            "has_paper": False,
            "signup_url": "https://developer.schwab.com",
        },
        "ibkr": {
            "name": "Interactive Brokers",
            "class": "IBKRTradingService",
            "key_label": "Gateway URL",
            "secret_label": "Account ID",
            "docs_url": "https://www.interactivebrokers.com/api",
            "has_paper": False,
            "signup_url": "https://www.interactivebrokers.com",
        },
    }

    @staticmethod
    def get_broker_client(broker_id: str, credentials: Dict):
        if broker_id == "alpaca":
            return AlpacaTradingService(
                api_key=credentials.get("api_key", ""),
                api_secret=credentials.get("api_secret", ""),
                paper=credentials.get("paper", True),
            )
        elif broker_id == "schwab":
            return SchwabTradingService(
                api_key=credentials.get("api_key", ""),
                api_secret=credentials.get("api_secret", ""),
            )
        elif broker_id == "ibkr":
            return IBKRTradingService(
                api_key=credentials.get("api_key", ""),
                api_secret=credentials.get("api_secret", ""),
            )
        else:
            raise ValueError(f"Unsupported broker: {broker_id}. Supported: {list(BrokerService.SUPPORTED_BROKERS.keys())}")

    @staticmethod
    def get_supported_brokers():
        return BrokerService.SUPPORTED_BROKERS