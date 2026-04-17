import os
import logging
from typing import Optional, List, Dict
from dataclasses import dataclass
import requests
from datetime import datetime, timedelta

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

    def __init__(self, api_key: str, api_secret: str, paper: bool = True, oauth: bool = False):
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
        order = OrderParams(symbol=symbol, qty=qty, side=side, order_type=order_type,
                            time_in_force=time_in_force, limit_price=limit_price, stop_price=stop_price)
        return self._execute_order(order)

    def _execute_order(self, o: OrderParams) -> Optional[Dict]:
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
        # IBKR Client Portal uses self-signed certs; load CA bundle if available
        self._verify = os.environ.get("IBKR_CA_BUNDLE", True)

    def get_account(self) -> Optional[Dict]:
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
            logger.error(f"IBKR get_account error: {e}")
            return None

    def get_positions(self) -> List[Dict]:
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
                              headers=self.headers, json=data, verify=self._verify, timeout=10)
            r.raise_for_status()
            result = r.json()
            return {"id": str(result), "status": "submitted", "symbol": symbol}
        except Exception as e:
            logger.error(f"IBKR place_order error: {e}")
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> List[Dict]:
        try:
            r = requests.get(f"{self.gateway_url}/v1/api/iserver/account/orders",
                             headers=self.headers, verify=self._verify, timeout=10)
            r.raise_for_status()
            return r.json().get("orders", []) if isinstance(r.json(), dict) else r.json()
        except Exception as e:
            logger.error(f"IBKR get_orders error: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            r = requests.delete(
                f"{self.gateway_url}/v1/api/iserver/account/{self.account_id}/order/{order_id}",
                headers=self.headers, verify=self._verify, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"IBKR cancel_order error: {e}")
            return False


class MooMooTradingService:
    """MooMoo (Futu) — OpenAPI REST trading. Uses App Token from developer portal."""

    def __init__(self, api_key: str, api_secret: str, **kwargs):
        self.app_token = api_key
        self.account_id = api_secret
        self.base_url = "https://openapi.moomoo.com"
        self.headers = {
            "Authorization": f"Bearer {self.app_token}",
            "Content-Type": "application/json",
        }

    def get_account(self) -> Optional[Dict]:
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
            logger.error(f"MooMoo get_account error: {e}")
            return None

    def get_positions(self) -> List[Dict]:
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
            logger.error(f"MooMoo get_positions error: {e}")
            return []

    def place_order(self, symbol: str, qty, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price=None, stop_price=None) -> Optional[Dict]:
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
            logger.error(f"MooMoo place_order error: {e}")
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> List[Dict]:
        try:
            r = requests.get(f"{self.base_url}/v1/account/{self.account_id}/orders",
                             headers=self.headers, params={"limit": limit}, timeout=10)
            r.raise_for_status()
            return r.json().get("data", {}).get("orders", [])
        except Exception as e:
            logger.error(f"MooMoo get_orders error: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            r = requests.delete(f"{self.base_url}/v1/trade/order/{order_id}",
                                headers=self.headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"MooMoo cancel_order error: {e}")
            return False


class WebullTradingService:
    """Webull — REST API trading. Uses Access Token + Device ID."""

    def __init__(self, api_key: str, api_secret: str, **kwargs):
        self.access_token = api_key
        self.device_id = api_secret
        self.trade_url = "https://tradeapi.webullbroker.com/api/trade"
        self.user_url = "https://userapi.webull.com/api"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "did": self.device_id,
            "Content-Type": "application/json",
        }
        self._account_id = None

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
            logger.error(f"Webull get_account_id error: {e}")
        return self._account_id or ""

    def get_account(self) -> Optional[Dict]:
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
            logger.error(f"Webull get_account error: {e}")
            return None

    def get_positions(self) -> List[Dict]:
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
            logger.error(f"Webull get_positions error: {e}")
            return []

    def place_order(self, symbol: str, qty, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price=None, stop_price=None) -> Optional[Dict]:
        try:
            acc_id = self._get_account_id()
            data = {
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
            logger.error(f"Webull place_order error: {e}")
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> List[Dict]:
        try:
            acc_id = self._get_account_id()
            r = requests.get(f"{self.trade_url}/v2/option/list",
                             headers=self.headers, params={"secAccountId": acc_id, "count": limit}, timeout=10)
            r.raise_for_status()
            return r.json() if isinstance(r.json(), list) else r.json().get("data", [])
        except Exception as e:
            logger.error(f"Webull get_orders error: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            acc_id = self._get_account_id()
            r = requests.post(f"{self.trade_url}/order/{acc_id}/cancel/{order_id}",
                              headers=self.headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Webull cancel_order error: {e}")
            return False


class RobinhoodTradingService:
    """Robinhood — REST API trading. Uses OAuth Bearer Token."""

    def __init__(self, api_key: str, api_secret: str, **kwargs):
        self.access_token = api_key
        self.account_url = api_secret if api_secret.startswith("http") else ""
        self.base_url = "https://api.robinhood.com"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        self._account_id = None
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
            logger.error(f"Robinhood get_account_url error: {e}")
            return ""

    def get_account(self) -> Optional[Dict]:
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
            logger.error(f"Robinhood get_account error: {e}")
            return None

    def get_positions(self) -> List[Dict]:
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
            logger.error(f"Robinhood get_positions error: {e}")
            return []

    def place_order(self, symbol: str, qty, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price=None, stop_price=None) -> Optional[Dict]:
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
            logger.error(f"Robinhood place_order error: {e}")
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> List[Dict]:
        try:
            r = requests.get(f"{self.base_url}/orders/",
                             headers=self.headers, params={"page_size": limit}, timeout=10)
            r.raise_for_status()
            return r.json().get("results", [])
        except Exception as e:
            logger.error(f"Robinhood get_orders error: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            r = requests.post(f"{self.base_url}/orders/{order_id}/cancel/",
                              headers=self.headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Robinhood cancel_order error: {e}")
            return False


class PublicTradingService:
    """Public.com — REST API trading. Uses Bearer Token from developer portal."""

    def __init__(self, api_key: str, api_secret: str, **kwargs):
        self.access_token = api_key
        self.account_id = api_secret
        self.base_url = "https://api.public.com/userapigateway"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def get_account(self) -> Optional[Dict]:
        try:
            r = requests.get(f"{self.base_url}/trading/account",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            return {
                "account_number": data.get("accountId", self.account_id),
                "id": data.get("accountId", self.account_id),
                "cash": float(data.get("cashAvailable", data.get("cash", 0))),
                "buying_power": float(data.get("buyingPower", data.get("cashAvailable", 0))),
                "equity": float(data.get("equity", data.get("totalValue", 0))),
                "portfolio_value": float(data.get("portfolioValue", data.get("equity", 0))),
            }
        except Exception as e:
            logger.error(f"Public get_account error: {e}")
            return None

    def get_positions(self) -> List[Dict]:
        try:
            r = requests.get(f"{self.base_url}/trading/account",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            positions = []
            for p in r.json().get("positions", []):
                positions.append({
                    "symbol": p.get("symbol", p.get("instrument", {}).get("symbol", "")),
                    "qty": abs(float(p.get("quantity", 0))),
                    "side": "long" if float(p.get("quantity", 0)) > 0 else "short",
                    "avg_entry_price": float(p.get("averageCost", p.get("avgPrice", 0))),
                    "current_price": float(p.get("currentPrice", p.get("lastPrice", 0))),
                    "market_value": float(p.get("marketValue", 0)),
                    "unrealized_pl": float(p.get("unrealizedPnl", 0)),
                    "unrealized_plpc": float(p.get("unrealizedPnlPercent", 0)),
                })
            return positions
        except Exception as e:
            logger.error(f"Public get_positions error: {e}")
            return []

    def place_order(self, symbol: str, qty, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price=None, stop_price=None) -> Optional[Dict]:
        try:
            acc_id = self.account_id
            data = {
                "symbol": symbol.upper(),
                "side": side.upper(),
                "type": order_type.upper(),
                "quantity": str(qty),
                "timeInForce": time_in_force.upper(),
            }
            if limit_price and order_type != "market":
                data["limitPrice"] = str(limit_price)
            if stop_price:
                data["stopPrice"] = str(stop_price)
            r = requests.post(f"{self.base_url}/trading/{acc_id}/order",
                              headers=self.headers, json=data, timeout=10)
            r.raise_for_status()
            result = r.json()
            return {"id": result.get("orderId", ""), "status": "submitted", "symbol": symbol}
        except Exception as e:
            logger.error(f"Public place_order error: {e}")
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> List[Dict]:
        try:
            r = requests.get(f"{self.base_url}/trading/account",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json().get("orders", [])[:limit]
        except Exception as e:
            logger.error(f"Public get_orders error: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            r = requests.delete(f"{self.base_url}/trading/{self.account_id}/order/{order_id}",
                                headers=self.headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Public cancel_order error: {e}")
            return False


class KrakenTradingService:
    """Kraken — Crypto exchange REST API. Uses API-Key + API-Sign (HMAC-SHA512)."""

    def __init__(self, api_key: str, api_secret: str, **kwargs):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.kraken.com"

    def _sign(self, url_path: str, data: Dict) -> Dict:
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

    def _private(self, endpoint: str, data: Optional[Dict] = None) -> Dict:
        data = data or {}
        path = f"/0/private/{endpoint}"
        headers = self._sign(path, data)
        r = requests.post(f"{self.base_url}{path}", headers=headers, data=data, timeout=10)
        r.raise_for_status()
        result = r.json()
        if result.get("error"):
            logger.warning(f"Kraken API error on {endpoint}: {result['error']}")
        return result.get("result", {})

    def get_account(self) -> Optional[Dict]:
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
            logger.error(f"Kraken get_account error: {e}")
            return None

    def get_balances(self) -> Dict:
        """Get all non-zero crypto balances."""
        try:
            balance = self._private("Balance")
            return {k: float(v) for k, v in balance.items() if float(v) > 0.0001}
        except Exception as e:
            logger.error(f"Kraken get_balances error: {e}")
            return {}

    def get_trade_history(self, limit: int = 50) -> List[Dict]:
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
            logger.error(f"Kraken get_trade_history error: {e}")
            return []

    def get_positions(self) -> List[Dict]:
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
            logger.error(f"Kraken get_positions error: {e}")
            return []

    def place_order(self, symbol: str, qty, side: str, order_type: str = "market",
                    time_in_force: str = "day", limit_price=None, stop_price=None) -> Optional[Dict]:
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
            logger.error(f"Kraken place_order error: {e}")
            return None

    def get_orders(self, status: str = "all", limit: int = 50) -> List[Dict]:
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
            logger.error(f"Kraken get_orders error: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._private("CancelOrder", {"txid": order_id})
            return True
        except Exception as e:
            logger.error(f"Kraken cancel_order error: {e}")
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
    def get_broker_client(broker_id: str, credentials: Dict):
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
    def get_supported_brokers():
        return BrokerService.SUPPORTED_BROKERS