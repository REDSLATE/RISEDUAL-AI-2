import asyncio
import logging
import secrets as _secrets
from typing import Dict, List, Optional
from datetime import datetime

import httpx
from services.price_provider import get_quote as pp_get_quote, get_crypto_quote as pp_get_crypto_quote
from services.providerrouter import ProviderRouter
from services.provider_registry import get_market_data_provider_pool

logger = logging.getLogger(__name__)
_rng = _secrets.SystemRandom()

class MarketDataService:
    def __init__(self, db=None):
        self.db = db
        self.router = ProviderRouter("market_data", get_market_data_provider_pool(), db=db)
    
    async def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get real-time quote via smart price provider (AV -> yfinance -> cache)."""
        try:
            quote = await pp_get_quote(symbol)
            if not quote:
                return None
            return {
                'symbol': quote.get('symbol', symbol),
                'price': quote.get('price', 0),
                'change': quote.get('change', 0),
                'changePercent': quote.get('change_pct', 0),
                'volume': quote.get('volume', 0),
                'high': quote.get('high', 0),
                'low': quote.get('low', 0),
            }
        except Exception as e:
            logger.error(f"Error fetching quote for {symbol}: {str(e)}")
            return None
    
    async def get_ticker_data(self) -> List[Dict]:
        """Get ticker data for top stocks (parallel fetch, 150 req/min plan)"""
        symbols = ['SPY', 'VOO', 'QQQ', 'IVV', 'VTI', 'VUG', 'VEA']
        tasks = [self.get_quote(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ticker_data = []
        for quote in results:
            if isinstance(quote, dict) and quote:
                ticker_data.append({
                    'symbol': quote['symbol'],
                    'price': quote['price'],
                    'change': quote['change'],
                    'changePercent': quote['changePercent']
                })
        return ticker_data
    
    async def get_crypto_quote(self, symbol: str, market: str = 'USD') -> Optional[Dict]:
        """Get crypto quote via smart price provider (AV -> yfinance -> cache)."""
        try:
            quote = await pp_get_crypto_quote(symbol)
            if not quote:
                return None
            return quote
        except Exception as e:
            logger.error(f"Error fetching crypto quote for {symbol}: {str(e)}")
            return None
    
    async def get_crypto_data(self) -> List[Dict]:
        """Get top 15 crypto currencies data (parallel fetch)"""
        cryptos = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE',
                    'AVAX', 'DOT', 'MATIC', 'LINK', 'SHIB', 'LTC', 'UNI', 'ATOM']
        tasks = [self.get_crypto_quote(crypto) for crypto in cryptos]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        crypto_data = []
        for quote in results:
            if isinstance(quote, dict) and quote:
                crypto_data.append(quote)
        return crypto_data

    async def _fetch_top_stocks(self, provider: dict):
        p = provider.get("provider")
        key = provider.get("api_key")

        async with httpx.AsyncClient(timeout=20) as client:
            if p == "alphavantage":
                url = "https://www.alphavantage.co/query"
                params = {"function": "TOP_GAINERS_LOSERS", "apikey": key}
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()

            if p == "finnhub":
                url = "https://finnhub.io/api/v1/scan/technical-indicator"
                params = {"symbol": "AAPL", "resolution": "D", "token": key}
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()

            if p == "twelvedata":
                url = "https://api.twelvedata.com/time_series"
                params = {"symbol": "AAPL", "interval": "1day", "apikey": key}
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()

        raise RuntimeError(f"Unsupported market data provider: {p}")

    async def get_top_stocks(self):
        routed = await self.router.run(self._fetch_top_stocks)
        return {
            "data": routed["result"],
            "provider": routed["provider"],
        }

    def generate_dark_pool_data(self) -> List[Dict]:
        """Generate dark pool trading data"""
        symbols = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'META', 'GOOGL', 'AMZN', 'PLTR', 'AMD', 'SPY']
        dark_pool_data = []
        
        for symbol in symbols[:8]:
            dark_pool_data.append({
                'symbol': symbol,
                'darkPoolVolume': _rng.randint(500000, 5000000),
                'totalVolume': _rng.randint(10000000, 50000000),
                'darkPoolPercent': round(_rng.uniform(15, 45), 2),
                'averagePrice': round(_rng.uniform(100, 500), 2),
                'priceChange': round(_rng.uniform(-5, 5), 2),
                'sentiment': _rng.choice(['Bullish', 'Bearish', 'Neutral']),
                'timestamp': datetime.now().isoformat()
            })
        
        return dark_pool_data
    
    def _generate_contract(self) -> Dict:
        """Generate a single mock options contract."""
        symbols = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'META', 'GOOGL', 'AMZN', 'PLTR', 'AMD', 'MRVL']
        return {
            'contract': _rng.choice(symbols),
            'price': f"${_rng.randint(50, 500)} {_rng.choice(['Call', 'Put'])}",
            'returns': f"{_rng.randint(50, 400)}%",
            'volOI': round(_rng.uniform(0.1, 10), 2),
            'power': _rng.randint(40, 100),
            'ivRank': _rng.randint(20, 80),
            'aiScore': _rng.randint(30, 70),
        }

    def _generate_contracts(self, count: int) -> List[Dict]:
        """Generate a list of mock options contracts."""
        return [self._generate_contract() for _ in range(count)]

    def _generate_contracts_with_sentiment(self, count: int) -> List[Dict]:
        """Generate contracts with sentiment annotation."""
        contracts = self._generate_contracts(count)
        for c in contracts:
            c['sentiment'] = _rng.choice(['Bearish', 'Bullish'])
        return contracts

    def generate_mock_options_data(self, data_type: str) -> Dict:
        """Generate realistic mock options data based on type."""
        generators = {
            'radar': lambda: {
                'mostActivelyTraded': self._generate_contracts(4),
                'volatilityOpportunities': self._generate_contracts(4),
            },
            'flow': lambda: {
                'mostActivelyTraded': self._generate_contracts(5),
                'dteEdge': self._generate_contracts(5),
                'volatilityLow': self._generate_contracts(3),
                'volatilityHigh': self._generate_contracts(3),
            },
            'momentum': lambda: self._generate_contracts(5),
            'fast_movers': lambda: self._generate_contracts(5),
            'unusual_volume': lambda: self._generate_contracts_with_sentiment(3),
        }
        gen = generators.get(data_type)
        return gen() if gen else {}