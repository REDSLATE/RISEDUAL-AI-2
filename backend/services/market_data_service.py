import os
import requests
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class MarketDataService:
    def __init__(self):
        self.api_key = os.environ.get('ALPHA_VANTAGE_API_KEY')
        self.base_url = 'https://www.alphavantage.co/query'
        self.cache = {}  # Simple in-memory cache
        
    def _get_cache_key(self, symbol: str, data_type: str) -> str:
        return f"{symbol}_{data_type}"
    
    def _is_cache_valid(self, cache_entry: Dict) -> bool:
        """Check if cache entry is still valid (< 30 seconds old)"""
        if not cache_entry:
            return False
        cache_time = cache_entry.get('timestamp')
        if not cache_time:
            return False
        return (datetime.now() - cache_time).seconds < 30
    
    async def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get real-time quote for a symbol"""
        cache_key = self._get_cache_key(symbol, 'quote')
        
        # Check cache first
        if cache_key in self.cache and self._is_cache_valid(self.cache[cache_key]):
            logger.info(f"Returning cached quote for {symbol}")
            return self.cache[cache_key]['data']
        
        try:
            params = {
                'function': 'GLOBAL_QUOTE',
                'symbol': symbol,
                'apikey': self.api_key
            }
            
            response = requests.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if 'Global Quote' in data and data['Global Quote']:
                quote = data['Global Quote']
                result = {
                    'symbol': quote.get('01. symbol', symbol),
                    'price': float(quote.get('05. price', 0)),
                    'change': float(quote.get('09. change', 0)),
                    'changePercent': float(quote.get('10. change percent', '0').replace('%', '')),
                    'volume': int(quote.get('06. volume', 0)),
                    'high': float(quote.get('03. high', 0)),
                    'low': float(quote.get('04. low', 0))
                }
                
                # Cache the result
                self.cache[cache_key] = {
                    'data': result,
                    'timestamp': datetime.now()
                }
                
                return result
            else:
                logger.warning(f"No quote data for {symbol}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching quote for {symbol}: {str(e)}")
            return None
    
    async def get_ticker_data(self) -> List[Dict]:
        """Get ticker data for top stocks"""
        symbols = ['SPY', 'VOO', 'QQQ', 'IVV', 'VTI', 'VUG', 'VEA']
        ticker_data = []
        
        for symbol in symbols:
            quote = await self.get_quote(symbol)
            if quote:
                ticker_data.append({
                    'symbol': quote['symbol'],
                    'price': quote['price'],
                    'change': quote['change'],
                    'changePercent': quote['changePercent']
                })
        
        return ticker_data
    
    async def get_crypto_quote(self, symbol: str, market: str = 'USD') -> Optional[Dict]:
        """Get crypto quote from Alpha Vantage"""
        cache_key = self._get_cache_key(f"crypto_{symbol}", 'quote')
        
        # Check cache first
        if cache_key in self.cache and self._is_cache_valid(self.cache[cache_key]):
            logger.info(f"Returning cached crypto quote for {symbol}")
            return self.cache[cache_key]['data']
        
        try:
            params = {
                'function': 'CURRENCY_EXCHANGE_RATE',
                'from_currency': symbol,
                'to_currency': market,
                'apikey': self.api_key
            }
            
            response = requests.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if 'Realtime Currency Exchange Rate' in data:
                rate = data['Realtime Currency Exchange Rate']
                current_price = float(rate.get('5. Exchange Rate', 0))
                
                # Get previous close to calculate change (simplified)
                result = {
                    'symbol': symbol,
                    'price': current_price,
                    'change': 0,  # Alpha Vantage doesn't provide direct change for crypto
                    'changePercent': 0,
                    'market': market,
                    'lastUpdate': rate.get('6. Last Refreshed', '')
                }
                
                # Cache the result
                self.cache[cache_key] = {
                    'data': result,
                    'timestamp': datetime.now()
                }
                
                return result
            else:
                logger.warning(f"No crypto quote data for {symbol}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching crypto quote for {symbol}: {str(e)}")
            return None
    
    async def get_crypto_data(self) -> List[Dict]:
        """Get top crypto currencies data"""
        cryptos = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE']
        crypto_data = []
        
        for crypto in cryptos:
            quote = await self.get_crypto_quote(crypto)
            if quote:
                crypto_data.append(quote)
        
        return crypto_data
    
    def generate_dark_pool_data(self) -> List[Dict]:
        """Generate dark pool trading data"""
        import random
        
        symbols = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'META', 'GOOGL', 'AMZN', 'PLTR', 'AMD', 'SPY']
        dark_pool_data = []
        
        for symbol in symbols[:8]:
            dark_pool_data.append({
                'symbol': symbol,
                'darkPoolVolume': random.randint(500000, 5000000),
                'totalVolume': random.randint(10000000, 50000000),
                'darkPoolPercent': round(random.uniform(15, 45), 2),
                'averagePrice': round(random.uniform(100, 500), 2),
                'priceChange': round(random.uniform(-5, 5), 2),
                'sentiment': random.choice(['Bullish', 'Bearish', 'Neutral']),
                'timestamp': datetime.now().isoformat()
            })
        
        return dark_pool_data
    
    def generate_mock_options_data(self, data_type: str) -> Dict:
        """Generate realistic mock options data based on type"""
        # This is a sophisticated mock that will be replaced when options API is available
        import random
        
        def generate_contract():
            symbols = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'META', 'GOOGL', 'AMZN', 'PLTR', 'AMD', 'MRVL']
            types = ['Call', 'Put']
            return {
                'contract': random.choice(symbols),
                'price': f"${random.randint(50, 500)} {random.choice(types)}",
                'returns': f"{random.randint(50, 400)}%",
                'volOI': round(random.uniform(0.1, 10), 2),
                'power': random.randint(40, 100),
                'ivRank': random.randint(20, 80),
                'aiScore': random.randint(30, 70)
            }
        
        if data_type == 'radar':
            return {
                'mostActivelyTraded': [generate_contract() for _ in range(4)],
                'volatilityOpportunities': [generate_contract() for _ in range(4)]
            }
        elif data_type == 'flow':
            return {
                'mostActivelyTraded': [generate_contract() for _ in range(5)],
                'dteEdge': [generate_contract() for _ in range(5)],
                'volatilityLow': [generate_contract() for _ in range(3)],
                'volatilityHigh': [generate_contract() for _ in range(3)]
            }
        elif data_type == 'momentum':
            return [generate_contract() for _ in range(5)]
        elif data_type == 'fast_movers':
            return [generate_contract() for _ in range(5)]
        elif data_type == 'unusual_volume':
            contracts = [generate_contract() for _ in range(3)]
            for contract in contracts:
                contract['sentiment'] = random.choice(['Bearish', 'Bullish'])
            return contracts
        
        return {}