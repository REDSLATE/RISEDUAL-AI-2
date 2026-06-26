import os
import logging
import time
import uuid
from typing import Optional, Any
from dataclasses import dataclass
import requests

from services.structured_log import log_error, log_warning

logger = logging.getLogger(__name__)


@dataclass
class OrderParams:
    """Common order parameters across all brokers."""
    symbol: str
    qty: float
    side: str
    order_type: str = "market"
    time_in_force: str = "day"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None


class AlpacaTradingService:
    """Alpaca — commission-free API-first trading. Supports paper + live + OAuth."""

    def __init__(self, api_key: str, api_secret: str, paper: bool = True, oauth: bool = False) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.oauth = oauth
        if oauth:
            self.base_url = "https://api.alpaca.markets"
            self.headers = {"Authorization": f"Bearer {api_key}"}
        else:
            self.base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
            self.headers = {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
            }

    def get_account(self) -> Optional[dict]:
        try:
            r = requests.get(f"{self.base_url}/v2/account", headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_alpaca",
                "method": "get_account",
            })
            return None

    def get_positions(self) -> list[dict]:
        try:
            r = requests.get(f"{self.base_url}/v2/positions", headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_alpaca",
                "method": "get_positions",
            })
            return []

    def place_order(self, symbol: str, qty: float, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None) -> Optional[dict]:
        order = OrderParams(symbol=symbol, qty=qty, side=side, order_type=order_type,
                            time_in_force=time_in_force, limit_price=limit_price, stop_price=stop_price)
        return self._execute_order(order)

    def _execute_order(self, o: OrderParams) -> Optional[dict]:
        try:
            data = {
                "symbol": o.symbol.upper(),
                "qty": str(o.qty),
                "side": o.side.lower(),
                "type": o.order_type.lower(),
                "time_in_force": o.time_in_force.lower(),
            }
            if o.limit_price:
                data["limit_price"] = str(o.limit_price)
            if o.stop_price:
                data["stop_price"] = str(o.stop_price)
            r = requests.post(f"{self.base_url}/v2/orders", headers=self.headers, json=data, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_alpaca",
                "method": "place_order",
            })
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> list[dict]:
        try:
            params: dict[str, Any] = {"status": status, "limit": limit}
            r = requests.get(f"{self.base_url}/v2/orders", headers=self.headers,
                             params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_alpaca",
                "method": "get_orders",
            })
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            r = requests.delete(f"{self.base_url}/v2/orders/{order_id}", headers=self.headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_alpaca",
                "method": "cancel_order",
            })
            return False

    def get_order(self, order_id: str) -> Optional[dict]:
        try:
            r = requests.get(f"{self.base_url}/v2/orders/{order_id}", headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_alpaca",
                "method": "get_order",
            })
            return None


class SchwabTradingService:
    """Charles Schwab (formerly TD Ameritrade) — API key-based trading."""

    def __init__(self, api_key: str, api_secret: str, **kwargs: object) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.schwabapi.com/trader/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def get_account(self) -> Optional[dict]:
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
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_schwab",
                "method": "get_account",
            })
            return None

    def get_positions(self) -> list[dict]:
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
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_schwab",
                "method": "get_positions",
            })
            return []

    def place_order(self, symbol: str, qty: float, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None) -> Optional[dict]:
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
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_schwab",
                "method": "place_order",
            })
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> list[dict]:
        try:
            account = self.get_account()
            acc_num = account.get("account_number", "") if account else ""
            r = requests.get(f"{self.base_url}/accounts/{acc_num}/orders",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_schwab",
                "method": "get_orders",
            })
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
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_schwab",
                "method": "cancel_order",
            })
            return False


class IBKRTradingService:
    """Interactive Brokers — Client Portal API (gateway-based)."""

    def __init__(self, api_key: str, api_secret: str, **kwargs: object) -> None:
        # IBKR uses a local gateway. api_key = gateway URL, api_secret = account ID
        self.gateway_url = api_key if api_key.startswith("http") else "https://localhost:5000"
        self.account_id = api_secret
        self.headers = {"Content-Type": "application/json"}
        # IBKR Client Portal uses self-signed certs; load CA bundle if available
        self._verify = os.environ.get("IBKR_CA_BUNDLE", True)

    def get_account(self) -> Optional[dict]:
        try:
            r = requests.get(f"{self.gateway_url}/v1/api/portfolio/accounts",
                             headers=self.headers, verify=self._verify, timeout=10)
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
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_ibkr",
                "method": "get_account",
            })
            return None

    def get_positions(self) -> list[dict]:
        try:
            r = requests.get(f"{self.gateway_url}/v1/api/portfolio/{self.account_id}/positions/0",
                             headers=self.headers, verify=self._verify, timeout=10)
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
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_ibkr",
                "method": "get_positions",
            })
            return []

    def place_order(self, symbol: str, qty: float, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None) -> Optional[dict]:
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
                              headers=self.headers, json=data, verify=self._verify, timeout=10)
            r.raise_for_status()
            result = r.json()
            return {"id": str(result), "status": "submitted", "symbol": symbol}
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_ibkr",
                "method": "place_order",
            })
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> list[dict]:
        try:
            r = requests.get(f"{self.gateway_url}/v1/api/iserver/account/orders",
                             headers=self.headers, verify=self._verify, timeout=10)
            r.raise_for_status()
            return r.json().get("orders", []) if isinstance(r.json(), dict) else r.json()
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_ibkr",
                "method": "get_orders",
            })
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            r = requests.delete(
                f"{self.gateway_url}/v1/api/iserver/account/{self.account_id}/order/{order_id}",
                headers=self.headers, verify=self._verify, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_ibkr",
                "method": "cancel_order",
            })
            return False


class MooMooTradingService:
    """MooMoo (Futu) — OpenAPI REST trading. Uses App Token from developer portal."""

    def __init__(self, api_key: str, api_secret: str, **kwargs: object) -> None:
        self.app_token = api_key
        self.account_id = api_secret
        self.base_url = "https://openapi.moomoo.com"
        self.headers = {
            "Authorization": f"Bearer {self.app_token}",
            "Content-Type": "application/json",
        }

    def get_account(self) -> Optional[dict]:
        try:
            r = requests.get(f"{self.base_url}/v1/account/list",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            accounts = data.get("data", {}).get("accounts", [])
            if accounts:
                acc = accounts[0]
                return {
                    "account_number": acc.get("accountId", self.account_id),
                    "cash": acc.get("cashBalance", 0),
                    "buying_power": acc.get("buyingPower", 0),
                    "equity": acc.get("netAssets", 0),
                    "portfolio_value": acc.get("totalMarketValue", 0),
                }
            return {"account_number": self.account_id, "cash": 0, "buying_power": 0, "equity": 0, "portfolio_value": 0}
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_moomoo",
                "method": "get_account",
            })
            return None

    def get_positions(self) -> list[dict]:
        try:
            r = requests.get(f"{self.base_url}/v1/account/{self.account_id}/positions",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            positions = []
            for p in r.json().get("data", {}).get("positions", []):
                positions.append({
                    "symbol": p.get("ticker", {}).get("symbol", ""),
                    "qty": abs(float(p.get("quantity", 0))),
                    "side": "long" if float(p.get("quantity", 0)) > 0 else "short",
                    "avg_entry_price": float(p.get("avgCost", 0)),
                    "current_price": float(p.get("lastPrice", 0)),
                    "market_value": float(p.get("marketValue", 0)),
                    "unrealized_pl": float(p.get("unrealizedPnL", 0)),
                    "unrealized_plpc": float(p.get("unrealizedPnLPercent", 0)),
                })
            return positions
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_moomoo",
                "method": "get_positions",
            })
            return []

    def place_order(self, symbol: str, qty: float, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None) -> Optional[dict]:
        try:
            data = {
                "accountId": self.account_id,
                "ticker": {"market": "US", "symbol": symbol.upper()},
                "side": "BUY" if side.lower() == "buy" else "SELL",
                "orderType": "MARKET" if order_type == "market" else "LIMIT",
                "quantity": str(int(qty)),
                "timeInForce": time_in_force.upper(),
            }
            if limit_price and order_type != "market":
                data["limitPrice"] = str(limit_price)
            r = requests.post(f"{self.base_url}/v1/trade/order",
                              headers=self.headers, json=data, timeout=10)
            r.raise_for_status()
            result = r.json().get("data", {})
            return {"id": result.get("orderId", ""), "status": "submitted", "symbol": symbol}
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_moomoo",
                "method": "place_order",
            })
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> list[dict]:
        try:
            r = requests.get(f"{self.base_url}/v1/account/{self.account_id}/orders",
                             headers=self.headers, params={"limit": limit}, timeout=10)
            r.raise_for_status()
            return r.json().get("data", {}).get("orders", [])
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_moomoo",
                "method": "get_orders",
            })
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            r = requests.delete(f"{self.base_url}/v1/trade/order/{order_id}",
                                headers=self.headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_moomoo",
                "method": "cancel_order",
            })
            return False


class WebullTradingService:
    """Webull — REST API trading. Uses Access Token + Device ID."""

    def __init__(self, api_key: str, api_secret: str, **kwargs: object) -> None:
        self.access_token = api_key
        self.device_id = api_secret
        self.trade_url = "https://tradeapi.webullbroker.com/api/trade"
        self.user_url = "https://userapi.webull.com/api"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "did": self.device_id,
            "Content-Type": "application/json",
        }
        self._account_id: str | None = None

    def _get_account_id(self) -> str:
        if self._account_id:
            return self._account_id
        try:
            r = requests.get(f"{self.trade_url}/account/getSecAccountList/v5",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                self._account_id = str(data[0].get("secAccountId", ""))
            elif isinstance(data, dict):
                accounts = data.get("data", data.get("accounts", []))
                if accounts:
                    self._account_id = str(accounts[0].get("secAccountId", ""))
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_webull",
                "method": "get_account_id",
            })
        return self._account_id or ""

    def get_account(self) -> Optional[dict]:
        try:
            acc_id = self._get_account_id()
            r = requests.get(f"{self.trade_url}/v5/home/{acc_id}",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            return {
                "account_number": acc_id,
                "id": acc_id,
                "cash": float(data.get("cashBalance", data.get("usableCash", 0))),
                "buying_power": float(data.get("dayBuyingPower", data.get("usableCash", 0))),
                "equity": float(data.get("netLiquidation", data.get("totalMarketValue", 0))),
                "portfolio_value": float(data.get("totalMarketValue", data.get("accountMembers", {}).get("totalMarketValue", 0))),
            }
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_webull",
                "method": "get_account",
            })
            return None

    def get_positions(self) -> list[dict]:
        try:
            acc_id = self._get_account_id()
            r = requests.get(f"{self.trade_url}/v5/home/{acc_id}",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            positions = []
            for p in r.json().get("positions", []):
                positions.append({
                    "symbol": p.get("ticker", {}).get("symbol", p.get("symbol", "")),
                    "qty": abs(float(p.get("position", p.get("quantity", 0)))),
                    "side": "long" if float(p.get("position", p.get("quantity", 0))) > 0 else "short",
                    "avg_entry_price": float(p.get("costPrice", p.get("avgCost", 0))),
                    "current_price": float(p.get("lastPrice", p.get("marketPrice", 0))),
                    "market_value": float(p.get("marketValue", 0)),
                    "unrealized_pl": float(p.get("unrealizedProfitLoss", 0)),
                    "unrealized_plpc": float(p.get("unrealizedProfitLossRate", 0)),
                })
            return positions
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_webull",
                "method": "get_positions",
            })
            return []

    def place_order(self, symbol: str, qty: float, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None) -> Optional[dict]:
        try:
            acc_id = self._get_account_id()
            data: dict[str, Any] = {
                "action": side.upper(),
                "orderType": "MKT" if order_type == "market" else "LMT",
                "quantity": int(qty),
                "timeInForce": time_in_force.upper(),
                "tickerId": symbol.upper(),
            }
            if limit_price and order_type != "market":
                data["lmtPrice"] = float(limit_price)
            if stop_price:
                data["auxPrice"] = float(stop_price)
            r = requests.post(f"{self.trade_url}/order/{acc_id}/place",
                              headers=self.headers, json=data, timeout=10)
            r.raise_for_status()
            result = r.json()
            return {"id": str(result.get("orderId", "")), "status": "submitted", "symbol": symbol}
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_webull",
                "method": "place_order",
            })
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> list[dict]:
        try:
            acc_id = self._get_account_id()
            params: dict[str, Any] = {"secAccountId": acc_id, "count": limit}
            r = requests.get(f"{self.trade_url}/v2/option/list",
                             headers=self.headers, params=params, timeout=10)
            r.raise_for_status()
            return r.json() if isinstance(r.json(), list) else r.json().get("data", [])
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_webull",
                "method": "get_orders",
            })
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            acc_id = self._get_account_id()
            r = requests.post(f"{self.trade_url}/order/{acc_id}/cancel/{order_id}",
                              headers=self.headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_webull",
                "method": "cancel_order",
            })
            return False


class RobinhoodTradingService:
    """Robinhood — REST API trading. Uses OAuth Bearer Token."""

    def __init__(self, api_key: str, api_secret: str, **kwargs: object) -> None:
        self.access_token = api_key
        self.account_url = api_secret if api_secret.startswith("http") else ""
        self.base_url = "https://api.robinhood.com"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        self._account_id: str | None = None
        self._account_url = self.account_url or None

    def _get_account_url(self) -> str:
        if self._account_url:
            return self._account_url
        try:
            r = requests.get(f"{self.base_url}/accounts/",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            results = r.json().get("results", [])
            if results:
                self._account_url = results[0].get("url", "")
                self._account_id = results[0].get("account_number", "")
            return self._account_url or ""
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_robinhood",
                "method": "get_account_url",
            })
            return ""

    def get_account(self) -> Optional[dict]:
        try:
            r = requests.get(f"{self.base_url}/accounts/",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            results = r.json().get("results", [])
            if results:
                acc = results[0]
                portfolio_r = requests.get(f"{self.base_url}/portfolios/{acc.get('account_number', '')}/",
                                           headers=self.headers, timeout=10)
                portfolio = portfolio_r.json() if portfolio_r.ok else {}
                return {
                    "account_number": acc.get("account_number", "N/A"),
                    "id": acc.get("account_number", ""),
                    "cash": float(acc.get("cash", 0)),
                    "buying_power": float(acc.get("buying_power", 0)),
                    "equity": float(portfolio.get("equity", acc.get("portfolio_cash", 0))),
                    "portfolio_value": float(portfolio.get("market_value", 0)),
                }
            return None
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_robinhood",
                "method": "get_account",
            })
            return None

    def get_positions(self) -> list[dict]:
        try:
            r = requests.get(f"{self.base_url}/positions/?nonzero=true",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            positions = []
            for p in r.json().get("results", []):
                qty = float(p.get("quantity", 0))
                if qty == 0:
                    continue
                # Resolve instrument to get symbol
                symbol = ""
                instrument_url = p.get("instrument", "")
                if instrument_url:
                    try:
                        instr_r = requests.get(instrument_url, headers=self.headers, timeout=5)
                        if instr_r.ok:
                            symbol = instr_r.json().get("symbol", "")
                    except Exception:
                        pass
                positions.append({
                    "symbol": symbol,
                    "qty": qty,
                    "side": "long",
                    "avg_entry_price": float(p.get("average_buy_price", 0)),
                    "current_price": 0,
                    "market_value": 0,
                    "unrealized_pl": 0,
                    "unrealized_plpc": 0,
                })
            return positions
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_robinhood",
                "method": "get_positions",
            })
            return []

    def place_order(self, symbol: str, qty: float, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None) -> Optional[dict]:
        try:
            # Resolve instrument URL
            instr_r = requests.get(f"{self.base_url}/instruments/?symbol={symbol.upper()}",
                                   headers=self.headers, timeout=10)
            instr_r.raise_for_status()
            instruments = instr_r.json().get("results", [])
            if not instruments:
                return None
            instrument_url = instruments[0].get("url", "")
            account_url = self._get_account_url()
            data = {
                "account": account_url,
                "instrument": instrument_url,
                "symbol": symbol.upper(),
                "quantity": int(qty),
                "side": side.lower(),
                "type": order_type.lower(),
                "time_in_force": "gfd" if time_in_force == "day" else time_in_force,
                "trigger": "immediate",
            }
            if limit_price:
                data["price"] = str(limit_price)
            if stop_price:
                data["stop_price"] = str(stop_price)
            r = requests.post(f"{self.base_url}/orders/",
                              headers=self.headers, json=data, timeout=10)
            r.raise_for_status()
            result = r.json()
            return {"id": result.get("id", ""), "status": result.get("state", "submitted"), "symbol": symbol}
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_robinhood",
                "method": "place_order",
            })
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> list[dict]:
        try:
            r = requests.get(f"{self.base_url}/orders/",
                             headers=self.headers, params={"page_size": limit}, timeout=10)
            r.raise_for_status()
            return r.json().get("results", [])
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_robinhood",
                "method": "get_orders",
            })
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            r = requests.post(f"{self.base_url}/orders/{order_id}/cancel/",
                              headers=self.headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_robinhood",
                "method": "cancel_order",
            })
            return False


class PublicTradingService:
    """Public.com — REST API trading.

    Auth flow (per https://public.com/api/docs/quickstart):
        1. User generates a long-lived **secret key** in Public's
           developer portal — this is what the operator pastes in
           the "API Token" field of the broker-connect panel.
        2. We exchange that secret for a short-lived (≤24h)
           **access token** via:
               POST /userapiauthservice/personal/access-tokens
               body {"validityInMinutes": N, "secret": <secret>}
           Response carries ``accessToken`` (a JWT).
        3. EVERY downstream call uses the access token as
           ``Authorization: Bearer <accessToken>``.

    The 2026-06-03 RCA caught us using the secret directly as a
    Bearer — Public.com rejects this with 401 (which the broker-
    connect route wraps as a 400 "Could not authenticate").

    The exchange is cached in-process: the first call hits the
    auth service, subsequent calls reuse the JWT until ~60s before
    its declared expiry, then re-exchange transparently.
    """

    AUTH_URL = "https://api.public.com/userapiauthservice/personal/access-tokens"
    _DEFAULT_VALIDITY_MINUTES = 60
    _REFRESH_SLACK_SECONDS = 60

    def __init__(self, api_key: str, api_secret: str, **kwargs: object) -> None:
        # ``api_key`` here is Public's long-lived **secret key** (what
        # their portal labels "API Token"). ``api_secret`` is the
        # **Account ID**. The naming preserves the broker-connect
        # form contract; we just rebind locally for clarity.
        self._secret_key = api_key
        self.account_id = api_secret
        self.base_url = "https://api.public.com/userapigateway"
        self._access_token: Optional[str] = None
        self._access_token_expires_at: float = 0.0

    def _exchange_secret_for_access_token(self) -> Optional[str]:
        """POST the secret to Public's auth service and cache the
        returned access token. Returns the token on success, ``None``
        on auth failure (so the caller can surface a clean error
        instead of leaking a stack trace to the UI)."""
        try:
            resp = requests.post(
                self.AUTH_URL,
                json={
                    "validityInMinutes": self._DEFAULT_VALIDITY_MINUTES,
                    "secret": self._secret_key,
                },
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "[broker_public] access-token exchange returned %d: %s",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json() if resp.content else {}
            token = data.get("accessToken") or data.get("access_token")
            if not token:
                logger.warning(
                    "[broker_public] access-token response missing token: %s",
                    str(data)[:200],
                )
                return None
            # Cache for (validity - slack) seconds. Public doesn't echo
            # the validity back in the response, so we re-use what we
            # asked for. ``time.time()`` keeps this monotonic enough
            # for the ~60min cadence.
            self._access_token = token
            self._access_token_expires_at = (
                time.time()
                + (self._DEFAULT_VALIDITY_MINUTES * 60)
                - self._REFRESH_SLACK_SECONDS
            )
            return token
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_public",
                "method": "_exchange_secret_for_access_token",
            })
            return None

    def _auth_headers(self) -> Optional[dict]:
        """Return the Bearer header for downstream calls, exchanging
        a fresh token if cache is empty/expired. ``None`` means the
        secret is bad and the caller should surface auth failure."""
        if (
            not self._access_token
            or time.time() >= self._access_token_expires_at
        ):
            if not self._exchange_secret_for_access_token():
                return None
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def get_account(self) -> Optional[dict]:
        """Fetch the trading account's balance + buying power.

        2026-06-18 fix: was hitting ``/trading/account`` which actually
        returns a LIST of accounts (not balances). The correct endpoint
        is ``/userapigateway/trading/{accountId}/portfolio/v2`` which
        returns ``buyingPower`` + ``equity`` (cash + stock breakdown).
        """
        try:
            headers = self._auth_headers()
            if headers is None:
                return None
            r = requests.get(
                f"{self.base_url}/trading/{self.account_id}/portfolio/v2",
                headers=headers, timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            # ``buyingPower`` is a nested dict in Public's response
            bp = data.get("buyingPower") or {}
            cash_bp = float(bp.get("cashOnlyBuyingPower") or 0.0)
            full_bp = float(bp.get("buyingPower") or cash_bp)
            # ``equity`` is a list of {type, value, percentageOfPortfolio}
            equity_list = data.get("equity") or []
            cash_val = 0.0
            stock_val = 0.0
            for entry in equity_list:
                etype = (entry.get("type") or "").upper()
                try:
                    val = float(entry.get("value") or 0.0)
                except (TypeError, ValueError):
                    val = 0.0
                if etype == "CASH":
                    cash_val = val
                elif etype == "STOCK":
                    stock_val = val
            total_equity = cash_val + stock_val
            return {
                "account_number": data.get("accountId", self.account_id),
                "id": data.get("accountId", self.account_id),
                "cash": cash_val,
                "buying_power": full_bp,
                "equity": total_equity,
                "portfolio_value": total_equity,
                # Pass-through for callers that want the raw shape.
                "_raw_buying_power": bp,
                "_positions_count": len(data.get("positions") or []),
            }
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_public",
                "method": "get_account",
            })
            return None

    def get_positions(self) -> list[dict]:
        try:
            headers = self._auth_headers()
            if headers is None:
                return []
            r = requests.get(
                f"{self.base_url}/trading/{self.account_id}/portfolio/v2",
                headers=headers, timeout=10,
            )
            r.raise_for_status()
            positions = []
            for p in r.json().get("positions", []):
                inst = p.get("instrument") or {}
                last_price_dict = p.get("lastPrice") or {}
                gain = p.get("instrumentGain") or {}
                try:
                    qty = float(p.get("quantity") or 0.0)
                except (TypeError, ValueError):
                    qty = 0.0
                try:
                    last_price = float(last_price_dict.get("lastPrice") or 0.0)
                except (TypeError, ValueError):
                    last_price = 0.0
                try:
                    market_value = float(p.get("currentValue") or 0.0)
                except (TypeError, ValueError):
                    market_value = 0.0
                try:
                    unrealized_pl = float(gain.get("gainValue") or 0.0)
                except (TypeError, ValueError):
                    unrealized_pl = 0.0
                try:
                    unrealized_plpc = float(gain.get("gainPercentage") or 0.0)
                except (TypeError, ValueError):
                    unrealized_plpc = 0.0
                positions.append({
                    "symbol": inst.get("symbol", ""),
                    "qty": abs(qty),
                    "side": "long" if qty > 0 else "short",
                    "avg_entry_price": 0.0,  # not on portfolio/v2; needs order history
                    "current_price": last_price,
                    "market_value": market_value,
                    "unrealized_pl": unrealized_pl,
                    "unrealized_plpc": unrealized_plpc,
                })
            return positions
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_public",
                "method": "get_positions",
            })
            return []

    def place_order(self, symbol: str, qty: float, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None) -> Optional[dict]:
        """Place a Public.com equity order.

        Discovered via live API probing (2026-06-26 — Friday open).
        The earlier flat-symbol/snake-case shape returned 400; this
        is the actual accepted request body shape:

            {
              "instrument": {"symbol": "...", "type": "EQUITY"},
              "orderSide": "BUY" | "SELL",
              "orderType": "MARKET" | "LIMIT" | ...,
              "quantity": "<string>",                 # fractional ok
              "expiration": {"timeInForce": "DAY" | "GTC" | "GTD"},
              "orderId": "<client UUID>",             # idempotency key
              "limitPrice": "<string>",              # only for LIMIT
              "stopPrice": "<string>",               # only for STOP variants
            }

        Minimum notional: **$1.00**. Caller must size accordingly —
        Public rejects sub-dollar orders with HTTP 400 code 128.

        Returns ``{"id": orderId, "status": "submitted", "symbol": ...}``
        on success, ``None`` on any failure (auth, HTTP, broker
        rejection). Errors are logged with the broker's response body
        so operators can see exactly why a fill was rejected.
        """
        try:
            headers = self._auth_headers()
            if headers is None:
                return None
            headers["User-Agent"] = "public-dev-docs"
            acc_id = self.account_id
            client_order_id = str(uuid.uuid4())
            # Map TIF aliases → Public.com enum.
            tif_raw = (time_in_force or "day").upper()
            tif_map = {
                "DAY": "DAY",
                "GTC": "GTC",
                "GOOD_TILL_CANCEL": "GTC",
                "GTD": "GTD",
                "GOOD_TILL_DATE": "GTD",
            }
            tif = tif_map.get(tif_raw, "DAY")
            data: dict[str, Any] = {
                "instrument": {
                    "symbol": symbol.upper(),
                    "type": "EQUITY",
                },
                "orderSide": (side or "").upper(),
                "orderType": (order_type or "MARKET").upper(),
                "quantity": str(qty),
                "expiration": {"timeInForce": tif},
                "orderId": client_order_id,
            }
            if limit_price and (order_type or "").upper() != "MARKET":
                data["limitPrice"] = str(limit_price)
            if stop_price:
                data["stopPrice"] = str(stop_price)
            r = requests.post(
                f"{self.base_url}/trading/{acc_id}/order",
                headers=headers, json=data, timeout=10,
            )
            if r.status_code >= 400:
                # Surface the broker's exact reason so operators can
                # see WHY a fill was rejected (sub-minimum notional,
                # market closed, instrument not tradable, etc.).
                body = (r.text or "")[:400]
                logger.warning(
                    "[broker_public] place_order %s %s qty=%s rejected "
                    "%d: %s", symbol, side, qty, r.status_code, body,
                )
                return None
            result = r.json() if r.content else {}
            # Response shape: ``orderId`` is the broker-side id, but
            # we also fall back to our client id so the caller always
            # has something to track.
            return {
                "id": result.get("orderId") or client_order_id,
                "status": "submitted",
                "symbol": symbol.upper(),
                "broker_response": result,
            }
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_public",
                "method": "place_order",
                "symbol": symbol,
            })
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> list[dict]:
        try:
            headers = self._auth_headers()
            if headers is None:
                return []
            r = requests.get(f"{self.base_url}/trading/account",
                             headers=headers, timeout=10)
            r.raise_for_status()
            return r.json().get("orders", [])[:limit]
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_public",
                "method": "get_orders",
            })
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            headers = self._auth_headers()
            if headers is None:
                return False
            r = requests.delete(f"{self.base_url}/trading/{self.account_id}/order/{order_id}",
                                headers=headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_public",
                "method": "cancel_order",
            })
            return False

    # ── Market data ──────────────────────────────────────────────────
    # 2026-06-26 — Public.com offers real-time quotes + historical bars
    # under the same JWT auth as trading. Surfacing them here lets the
    # ``market_data_pool`` use Public as the operator's primary price
    # source (consistent with what Alpha actually trades on), with
    # AlphaVantage / Finnhub / etc. as deeper failover.

    def get_quote(self, symbol: str) -> Optional[dict]:
        """Real-time quote for a single equity symbol.

        Returns a dict shaped like the rest of the market_data_pool
        (``symbol``, ``price``, ``bid``, ``ask``, ``source``) or
        ``None`` on auth/network/empty failure.
        """
        try:
            headers = self._auth_headers()
            if headers is None:
                return None
            headers["User-Agent"] = "public-dev-docs"
            r = requests.post(
                f"{self.base_url}/marketdata/{self.account_id}/quotes",
                headers=headers,
                json={"instruments": [{"symbol": symbol.upper(), "type": "EQUITY"}]},
                timeout=10,
            )
            if r.status_code >= 400:
                logger.warning(
                    "[broker_public] get_quote %s returned %d: %s",
                    symbol, r.status_code, r.text[:200],
                )
                return None
            data = r.json() if r.content else {}
            quotes = data.get("quotes") or []
            if not quotes:
                return None
            q = quotes[0]
            try:
                last = float(q.get("last") or 0.0)
            except (TypeError, ValueError):
                last = 0.0
            if last <= 0.0:
                return None
            try:
                bid = float(q.get("bid") or 0.0)
            except (TypeError, ValueError):
                bid = 0.0
            try:
                ask = float(q.get("ask") or 0.0)
            except (TypeError, ValueError):
                ask = 0.0
            return {
                "symbol": symbol.upper(),
                "price": last,
                "bid": bid,
                "ask": ask,
                "last": last,
                "timestamp": q.get("lastTimestamp"),
                "source": "public",
            }
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_public",
                "method": "get_quote",
                "symbol": symbol,
            })
            return None

    def get_daily_bars(
        self, symbol: str, *, days: int = 90,
    ) -> Optional[list[dict]]:
        """OHLCV daily bars for a single equity symbol, newest-first.

        Uses Public.com's ``historicdata`` endpoint discovered via the
        operator's portfolio page (2026-06-26). Returns a list shaped
        to match AlphaVantage's daily output (``date``, ``open``,
        ``high``, ``low``, ``close``, ``volume``) so downstream
        technical analysis works without branching on source.

        Endpoint shape:
            GET /userapigateway/historicdata/{type}/{symbol}/{period}/{aggregation}

        Note: this path is account-agnostic (no ``{accountId}`` in
        the URL), unlike trading endpoints. ``type=EQUITY`` is the
        instrument type; ``aggregation=ONE_DAY`` for daily bars;
        ``period`` is mapped from the requested day count.
        """
        try:
            headers = self._auth_headers()
            if headers is None:
                return None
            # 2026-06-26 — Public.com's API requires this UA when
            # hitting historicdata; without it the endpoint silently
            # responds with an empty body / 404. Discovered by
            # inspecting their portfolio page's network calls.
            headers["User-Agent"] = "public-dev-docs"
            # Map requested days → Public.com ``period`` enum.
            if days <= 7:
                period = "WEEK"
            elif days <= 30:
                period = "MONTH"
            elif days <= 90:
                period = "QUARTER"
            elif days <= 180:
                period = "HALF_YEAR"
            elif days <= 365:
                period = "YEAR"
            elif days <= 365 * 5:
                period = "FIVE_YEARS"
            else:
                period = "TEN_YEARS"
            url = (
                f"{self.base_url}/historicdata/EQUITY/{symbol.upper()}/"
                f"{period}/ONE_DAY"
            )
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code >= 400:
                logger.warning(
                    "[broker_public] get_daily_bars %s returned %d: %s",
                    symbol, r.status_code, r.text[:200],
                )
                return None
            data = r.json() if r.content else {}
            # Public.com nests bars under three session buckets:
            # ``preMarket.bars`` / ``regularMarket.bars`` /
            # ``afterMarket.bars``. For daily OHLCV we want the
            # regular-session bars. Values come back as strings; cast
            # to float here so downstream consumers (RSI/MACD/EMA)
            # don't have to.
            reg = data.get("regularMarket") or {}
            bars = reg.get("bars") or []
            if not bars:
                # Fallback: try a flat ``bars`` key, in case the
                # response shape diverges in the future.
                bars = data.get("bars") or data.get("data") or []
            if not bars:
                return None
            rows = []
            for b in bars:
                try:
                    ts = b.get("timestamp") or b.get("date") or ""
                    # Truncate to YYYY-MM-DD for compat with the rest
                    # of the pool (AlphaVantage et al return dates).
                    date = ts[:10] if isinstance(ts, str) else str(ts)[:10]
                    rows.append({
                        "date": date,
                        "open": float(b.get("open") or 0.0),
                        "high": float(b.get("high") or 0.0),
                        "low": float(b.get("low") or 0.0),
                        "close": float(b.get("close") or 0.0),
                        "volume": int(float(b.get("volume") or 0)),
                    })
                except (TypeError, ValueError):
                    continue
            # Newest-first to match the other providers.
            rows.sort(key=lambda r: r.get("date") or "", reverse=True)
            return rows[:days] if rows else None
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_public",
                "method": "get_daily_bars",
                "symbol": symbol,
            })
            return None


class KrakenTradingService:
    """Kraken — Crypto exchange REST API. Uses API-Key + API-Sign (HMAC-SHA512)."""

    def __init__(self, api_key: str, api_secret: str, **kwargs: object) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.kraken.com"

    def _sign(self, url_path: str, data: dict) -> dict:
        import hashlib
        import hmac
        import base64
        import urllib.parse
        import time
        data["nonce"] = str(int(time.time() * 1000))
        post_data = urllib.parse.urlencode(data)
        encoded = (data["nonce"] + post_data).encode()
        message = url_path.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        return {
            "API-Key": self.api_key,
            "API-Sign": base64.b64encode(mac.digest()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _private(self, endpoint: str, data: Optional[dict] = None) -> dict:
        data = data or {}
        path = f"/0/private/{endpoint}"
        headers = self._sign(path, data)
        r = requests.post(f"{self.base_url}{path}", headers=headers, data=data, timeout=10)
        r.raise_for_status()
        result = r.json()
        if result.get("error"):
            log_warning(logger, {
                "error": str(result["error"]),
                "type": "KrakenAPIError",
                "context": "broker_kraken",
                "endpoint": endpoint,
            })
        return result.get("result", {})

    def get_account(self) -> Optional[dict]:
        try:
            balance = self._private("Balance")
            trade_balance = self._private("TradeBalance")
            # Filter out zero balances
            holdings = {k: float(v) for k, v in balance.items() if float(v) > 0.0001}
            total = sum(holdings.values()) if holdings else 0
            return {
                "account_number": "kraken",
                "id": "kraken",
                "cash": float(trade_balance.get("c", balance.get("ZUSD", 0))),
                "buying_power": float(trade_balance.get("mf", trade_balance.get("c", 0))),
                "equity": float(trade_balance.get("e", total)),
                "portfolio_value": float(trade_balance.get("v", total)),
                "holdings": holdings,
                "holdings_count": len(holdings),
            }
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_kraken",
                "method": "get_account",
            })
            return None

    def get_balances(self) -> dict:
        """Get all non-zero crypto balances."""
        try:
            balance = self._private("Balance")
            return {k: float(v) for k, v in balance.items() if float(v) > 0.0001}
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_kraken",
                "method": "get_balances",
            })
            return {}

    def get_trade_history(self, limit: int = 50) -> list[dict]:
        """Get recent closed trades."""
        try:
            result = self._private("TradesHistory")
            trades = []
            for tid, t in list(result.get("trades", {}).items())[:limit]:
                trades.append({
                    "id": tid,
                    "pair": t.get("pair", ""),
                    "type": t.get("type", ""),
                    "price": float(t.get("price", 0)),
                    "volume": float(t.get("vol", 0)),
                    "cost": float(t.get("cost", 0)),
                    "fee": float(t.get("fee", 0)),
                    "time": t.get("time", 0),
                })
            return trades
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_kraken",
                "method": "get_trade_history",
            })
            return []

    def get_positions(self) -> list[dict]:
        try:
            result = self._private("OpenPositions")
            positions = []
            for pid, p in result.items():
                positions.append({
                    "symbol": p.get("pair", ""),
                    "qty": abs(float(p.get("vol", 0))),
                    "side": p.get("type", "long"),
                    "avg_entry_price": float(p.get("cost", 0)) / max(float(p.get("vol", 1)), 0.001),
                    "current_price": float(p.get("value", 0)) / max(float(p.get("vol", 1)), 0.001),
                    "market_value": float(p.get("value", 0)),
                    "unrealized_pl": float(p.get("net", 0)),
                    "unrealized_plpc": 0,
                })
            return positions
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_kraken",
                "method": "get_positions",
            })
            return []

    def place_order(self, symbol: str, qty: float, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None) -> Optional[dict]:
        try:
            data = {
                "pair": symbol.upper(),
                "type": side.lower(),
                "ordertype": order_type.lower(),
                "volume": str(qty),
            }
            if limit_price and order_type != "market":
                data["price"] = str(limit_price)
            if stop_price:
                data["price2"] = str(stop_price)
            result = self._private("AddOrder", data)
            txid = result.get("txid", [""])[0] if isinstance(result.get("txid"), list) else result.get("txid", "")
            return {"id": txid, "status": "submitted", "symbol": symbol}
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_kraken",
                "method": "place_order",
            })
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> list[dict]:
        try:
            result = self._private("OpenOrders")
            orders = []
            for oid, o in result.get("open", {}).items():
                orders.append({
                    "id": oid,
                    "symbol": o.get("descr", {}).get("pair", ""),
                    "side": o.get("descr", {}).get("type", ""),
                    "status": o.get("status", "open"),
                    "qty": float(o.get("vol", 0)),
                    "price": float(o.get("descr", {}).get("price", 0)),
                })
            return orders[:limit]
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_kraken",
                "method": "get_orders",
            })
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._private("CancelOrder", {"txid": order_id})
            return True
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "broker_kraken",
                "method": "cancel_order",
            })
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
        "moomoo": {
            "name": "MooMoo",
            "class": "MooMooTradingService",
            "key_label": "App Token",
            "secret_label": "Account ID",
            "docs_url": "https://openapi.moomoo.com/docs",
            "has_paper": True,
            "signup_url": "https://www.moomoo.com/us/openapi",
        },
        "webull": {
            "name": "Webull",
            "class": "WebullTradingService",
            "key_label": "Access Token",
            "secret_label": "Device ID",
            "docs_url": "https://www.webull.com/trading-api",
            "has_paper": True,
            "signup_url": "https://www.webull.com",
        },
        "robinhood": {
            "name": "Robinhood",
            "class": "RobinhoodTradingService",
            "key_label": "OAuth Token",
            "secret_label": "Account URL (optional)",
            "docs_url": "https://robinhood.com/us/en/about/api",
            "has_paper": False,
            "signup_url": "https://robinhood.com",
        },
        "public": {
            "name": "Public.com",
            "class": "PublicTradingService",
            "key_label": "API Token",
            "secret_label": "Account ID",
            "docs_url": "https://public.com/api/docs",
            "has_paper": False,
            "signup_url": "https://public.com/api",
        },
        "kraken": {
            "name": "Kraken",
            "class": "KrakenTradingService",
            "key_label": "API Key",
            "secret_label": "Private Key (Base64)",
            "docs_url": "https://docs.kraken.com/api",
            "has_paper": False,
            "signup_url": "https://www.kraken.com/features/trading-api",
        },
    }

    @staticmethod
    def get_broker_client(broker_id: str, credentials: dict) -> "AlpacaTradingService | SchwabTradingService | IBKRTradingService | MooMooTradingService | WebullTradingService | RobinhoodTradingService | PublicTradingService | KrakenTradingService":
        if broker_id == "alpaca":
            return AlpacaTradingService(
                api_key=credentials.get("api_key", ""),
                api_secret=credentials.get("api_secret", ""),
                paper=credentials.get("paper", True),
                oauth=credentials.get("oauth", False),
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
        elif broker_id == "moomoo":
            return MooMooTradingService(
                api_key=credentials.get("api_key", ""),
                api_secret=credentials.get("api_secret", ""),
            )
        elif broker_id == "webull":
            return WebullTradingService(
                api_key=credentials.get("api_key", ""),
                api_secret=credentials.get("api_secret", ""),
            )
        elif broker_id == "robinhood":
            return RobinhoodTradingService(
                api_key=credentials.get("api_key", ""),
                api_secret=credentials.get("api_secret", ""),
            )
        elif broker_id == "public":
            return PublicTradingService(
                api_key=credentials.get("api_key", ""),
                api_secret=credentials.get("api_secret", ""),
            )
        elif broker_id == "kraken":
            return KrakenTradingService(
                api_key=credentials.get("api_key", ""),
                api_secret=credentials.get("api_secret", ""),
            )
        else:
            raise ValueError(f"Unsupported broker: {broker_id}. Supported: {list(BrokerService.SUPPORTED_BROKERS.keys())}")

    @staticmethod
    def get_supported_brokers() -> dict:
        return BrokerService.SUPPORTED_BROKERS