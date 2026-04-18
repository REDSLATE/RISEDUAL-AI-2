import logging
import asyncio
import requests
from typing import Dict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ForeignMarketsService:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    def _scrape_yahoo_quote(self, symbol: str, name: str, region: str) -> Dict:
        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d'
            resp = requests.get(url, headers=self.headers, timeout=8)
            data = resp.json()
            meta = data.get('chart', {}).get('result', [{}])[0].get('meta', {})
            price = meta.get('regularMarketPrice', 0)
            prev_close = meta.get('previousClose', 0) or meta.get('chartPreviousClose', 0)
            change = price - prev_close if prev_close else 0
            change_pct = (change / prev_close * 100) if prev_close else 0
            return {
                'symbol': symbol,
                'name': name,
                'region': region,
                'price': round(price, 2),
                'change': round(change, 2),
                'change_percent': round(change_pct, 2),
                'prev_close': round(prev_close, 2),
                'currency': meta.get('currency', 'USD'),
                'market_state': meta.get('marketState', 'CLOSED'),
            }
        except Exception as e:
            logger.error(f"Error fetching {symbol}: {str(e)}")
            return {
                'symbol': symbol, 'name': name, 'region': region,
                'price': 0, 'change': 0, 'change_percent': 0,
                'prev_close': 0, 'currency': 'N/A', 'market_state': 'ERROR',
            }

    async def get_foreign_markets(self) -> Dict:
        indices = [
            # Asia
            ('^N225', 'Nikkei 225', 'Asia'),
            ('^HSI', 'Hang Seng', 'Asia'),
            ('000001.SS', 'Shanghai Composite', 'Asia'),
            ('^KS11', 'KOSPI', 'Asia'),
            ('^BSESN', 'BSE Sensex', 'Asia'),
            # Europe
            ('^FTSE', 'FTSE 100', 'Europe'),
            ('^GDAXI', 'DAX', 'Europe'),
            ('^FCHI', 'CAC 40', 'Europe'),
            ('^STOXX50E', 'Euro Stoxx 50', 'Europe'),
            # Americas
            ('^GSPC', 'S&P 500', 'Americas'),
            ('^DJI', 'Dow Jones', 'Americas'),
            ('^IXIC', 'NASDAQ', 'Americas'),
            ('^BVSP', 'Bovespa', 'Americas'),
        ]

        commodities = [
            ('CL=F', 'Crude Oil (WTI)', 'Commodity'),
            ('BZ=F', 'Brent Crude', 'Commodity'),
            ('GC=F', 'Gold', 'Commodity'),
            ('SI=F', 'Silver', 'Commodity'),
            ('NG=F', 'Natural Gas', 'Commodity'),
            ('HG=F', 'Copper', 'Commodity'),
        ]

        currencies = [
            ('EURUSD=X', 'EUR/USD', 'Currency'),
            ('GBPUSD=X', 'GBP/USD', 'Currency'),
            ('JPY=X', 'USD/JPY', 'Currency'),
            ('CNY=X', 'USD/CNY', 'Currency'),
        ]

        all_data = []
        for symbol, name, region in indices + commodities + currencies:
            quote = await asyncio.to_thread(self._scrape_yahoo_quote, symbol, name, region)
            all_data.append(quote)

        asia = [d for d in all_data if d['region'] == 'Asia']
        europe = [d for d in all_data if d['region'] == 'Europe']
        americas = [d for d in all_data if d['region'] == 'Americas']
        comms = [d for d in all_data if d['region'] == 'Commodity']
        currs = [d for d in all_data if d['region'] == 'Currency']

        # Pre-market correlation signals
        signals = []
        for d in asia + europe:
            if abs(d['change_percent']) >= 1.5:
                direction = 'up' if d['change_percent'] > 0 else 'down'
                signals.append({
                    'market': d['name'],
                    'change_percent': d['change_percent'],
                    'signal': f"{d['name']} {direction} {abs(d['change_percent']):.1f}% - may impact US markets",
                    'severity': 'high' if abs(d['change_percent']) >= 3 else 'medium',
                })

        return {
            'asia': asia,
            'europe': europe,
            'americas': americas,
            'commodities': comms,
            'currencies': currs,
            'correlation_signals': sorted(signals, key=lambda x: abs(x['change_percent']), reverse=True),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
