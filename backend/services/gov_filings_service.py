import logging
import asyncio
import requests
import re
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class GovFilingsService:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }

    async def _get(self, url, **kwargs):
        kwargs.setdefault('timeout', 10)
        return await asyncio.to_thread(requests.get, url, **kwargs)

    async def get_sec_filings(self, ticker: str = None) -> List[Dict]:
        """Fetch recent SEC EDGAR filings via full-text search API"""
        filings = []
        try:
            # EDGAR EFTS search API for recent Form 4 filings
            url = 'https://efts.sec.gov/LATEST/search-index?q=*&forms=4'
            resp = await self._get(url, headers={
                'User-Agent': 'RISEDUALAI admin@risedual.ai',
                'Accept': 'application/json',
            }, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                hits = data.get('hits', {}).get('hits', [])[:15]
                for hit in hits:
                    src = hit.get('_source', {})
                    filings.append({
                        'form_type': src.get('forms', 'Form 4'),
                        'company': src.get('display_names', ['Unknown'])[0] if src.get('display_names') else 'Unknown',
                        'filed_date': src.get('file_date', ''),
                        'description': 'Insider Transaction',
                    })
        except Exception as e:
            logger.error(f"Error fetching SEC filings: {str(e)}")

        # Fallback: scrape OpenInsider
        if not filings:
            filings = await self._scrape_openinsider()

        return filings[:15]

    async def _scrape_openinsider(self) -> List[Dict]:
        """Scrape insider trading data from OpenInsider"""
        trades = []
        try:
            url = 'http://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh=&fd=7&fdr=&td=0&tdr=&feession=&teession=&xp=1&vl=&vh=&ocl=&och=&sic1l=&sic1h=&iession1l=&iession1h=&cnt=15'
            resp = await self._get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(resp.text, 'html.parser')
            table = soup.find('table', class_='tinytable')
            if table:
                rows = table.find_all('tr')[1:16]
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) >= 12:
                        trades.append({
                            'form_type': 'Form 4',
                            'filed_date': cols[1].get_text(strip=True),
                            'ticker': cols[3].get_text(strip=True),
                            'company': cols[4].get_text(strip=True),
                            'insider_name': cols[5].get_text(strip=True),
                            'insider_title': cols[6].get_text(strip=True),
                            'trade_type': cols[7].get_text(strip=True),
                            'price': cols[8].get_text(strip=True),
                            'qty': cols[9].get_text(strip=True),
                            'value': cols[11].get_text(strip=True),
                            'description': f"Insider {cols[7].get_text(strip=True)} by {cols[5].get_text(strip=True)}",
                        })
        except Exception as e:
            logger.error(f"Error scraping OpenInsider: {str(e)}")
        return trades

    async def get_fed_announcements(self) -> List[Dict]:
        """Scrape Federal Reserve announcements from RSS"""
        announcements = []
        try:
            url = 'https://www.federalreserve.gov/feeds/press_all.xml'
            resp = await self._get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(resp.content, 'xml')
            items = soup.find_all('item')[:10]
            for item in items:
                title = item.find('title')
                link = item.find('link')
                pub_date = item.find('pubDate')
                desc = item.find('description')
                announcements.append({
                    'title': title.get_text(strip=True) if title else '',
                    'url': link.get_text(strip=True) if link else '',
                    'date': pub_date.get_text(strip=True) if pub_date else '',
                    'description': desc.get_text(strip=True)[:300] if desc else '',
                    'source': 'Federal Reserve',
                    'type': 'fed_announcement',
                })
        except Exception as e:
            logger.error(f"Error fetching Fed announcements: {str(e)}")
        return announcements

    async def get_congressional_trades(self) -> List[Dict]:
        """Scrape congressional stock trades from Capitol Trades (stock-only filter)"""
        trades = await self._scrape_capitol_trades()
        return trades[:20]

    async def _scrape_capitol_trades(self) -> List[Dict]:
        """Primary: scrape Capitol Trades for congressional stock trades."""
        trades = []
        try:
            url = 'https://www.capitoltrades.com/trades?assetType=stock'
            resp = await self._get(url, headers=self.headers, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"Capitol Trades returned {resp.status_code}")
                return trades
            soup = BeautifulSoup(resp.text, 'html.parser')
            table = soup.find('table')
            if not table:
                return trades
            rows = table.find_all('tr')[1:21]
            for row in rows:
                trade = self._parse_capitol_row(row)
                if trade:
                    trades.append(trade)
        except Exception as e:
            logger.error(f"Error scraping Capitol Trades: {str(e)}")
        return trades

    @staticmethod
    def _fix_date(raw: str) -> str:
        """Fix concatenated dates like '6 Mar2026' → '6 Mar 2026'."""
        return re.sub(r'(\d{4})$', r' \1', raw.strip()) if raw else ''

    @staticmethod
    def _parse_capitol_row(row) -> Optional[Dict]:
        """Parse a single row from Capitol Trades table using CSS selectors."""
        cols = row.find_all('td')
        if len(cols) < 8:
            return None

        # Politician: extract from <a> tag and <span> elements
        name_a = cols[0].find('a', class_='text-txt-interactive')
        name = name_a.get_text(strip=True) if name_a else cols[0].get_text(strip=True).split('Democrat')[0].split('Republican')[0].strip()
        pol_cell = cols[0].find(class_='cell--politician')
        party_spans = pol_cell.find_all('span') if pol_cell else []
        party_raw = party_spans[0].get_text(strip=True) if party_spans else ''
        party = 'D' if 'Democrat' in party_raw else 'R' if 'Republican' in party_raw else ''
        chamber = party_spans[1].get_text(strip=True) if len(party_spans) > 1 else ''

        # Issuer: extract from <h3 class="issuer-name"> and <span> for ticker
        iss_h3 = cols[1].find('h3', class_='issuer-name')
        company = iss_h3.get_text(strip=True) if iss_h3 else ''
        iss_spans = cols[1].find_all('span')
        ticker_raw = iss_spans[0].get_text(strip=True) if iss_spans else ''
        ticker = ticker_raw.split(':')[0] if ':' in ticker_raw else ticker_raw
        if ticker == 'N/A':
            ticker = ''

        trade_type = cols[6].get_text(strip=True)
        size = cols[7].get_text(strip=True)
        tx_date = GovFilingsService._fix_date(cols[3].get_text(strip=True)) if len(cols) > 3 else ''
        disc_date = GovFilingsService._fix_date(cols[2].get_text(strip=True)) if len(cols) > 2 else ''

        return {
            'representative': name,
            'ticker': ticker,
            'company': company,
            'transaction_date': tx_date,
            'disclosure_date': disc_date,
            'type': trade_type,
            'amount': size,
            'party': party,
            'chamber': chamber,
            'description': f"{trade_type.upper()} by {name} ({party}-{chamber}) — {ticker or company} {size}",
        }

    async def _fetch_finnhub_data(self) -> Dict:
        """Try Finnhub API first (reliable structured data)."""
        try:
            from services.finnhub_service import FinnhubService
            fh = FinnhubService()
            if fh._is_configured():
                return await fh.get_all_data_for_predictions()
        except Exception as e:
            logger.warning(f"Finnhub fetch failed, falling back to scrapers: {e}")
        return {}

    async def _resolve_insider_trades(self, finnhub_data: Dict) -> List[Dict]:
        """Get insider trades: QuiverQuant → Finnhub → SEC scraping."""
        # Try QuiverQuant first
        from services.quiver_service import get_insider_trades, is_configured
        if is_configured():
            quiver_data = await get_insider_trades(limit=20)
            if quiver_data:
                return quiver_data

        # Fallback: Finnhub
        if finnhub_data.get("insider_count", 0) > 0:
            return [{"description": t["description"]} for t in finnhub_data.get("insider_transactions", [])]
        # Fallback: SEC scraping
        return await self.get_sec_filings()

    async def _resolve_congressional_trades(self, finnhub_data: Dict) -> List[Dict]:
        """Get congressional trades: QuiverQuant → Finnhub → Capitol Trades scraping."""
        # Try QuiverQuant first
        from services.quiver_service import get_congressional_trades, is_configured
        if is_configured():
            quiver_data = await get_congressional_trades(limit=20)
            if quiver_data:
                return quiver_data

        # Fallback: Finnhub
        if finnhub_data.get("congressional_count", 0) > 0:
            return finnhub_data.get("congressional_trades", [])
        # Fallback: Capitol Trades scraping
        return await self.get_congressional_trades()

    @staticmethod
    def _determine_source(finnhub_data: Dict) -> str:
        """Determine which data sources contributed to the result."""
        parts: List[str] = []
        if finnhub_data.get("insider_count", 0) > 0 or finnhub_data.get("earnings_count", 0) > 0:
            parts.append("finnhub")
        if finnhub_data.get("congressional_count", 0) == 0 or finnhub_data.get("insider_count", 0) == 0:
            parts.append("scraping")
        return '+'.join(parts) if parts else 'none'

    async def get_all_gov_data(self) -> Dict:
        """Aggregate government/institutional data from all available sources.
        
        Priority: QuiverQuant API → Finnhub → Web scrapers → MongoDB fallback.
        """
        from services.lobbying_service import LobbyingService
        from services.quiver_service import get_lobbying, get_gov_contracts, is_configured as quiver_ok

        finnhub_data = await self._fetch_finnhub_data()

        # Core data — QuiverQuant tried first inside resolve methods
        insider_trades, congressional_trades, fed_announcements, lobbying_db = await asyncio.gather(
            self._resolve_insider_trades(finnhub_data),
            self._resolve_congressional_trades(finnhub_data),
            self.get_fed_announcements(),
            LobbyingService().get_top_spenders(10),
        )

        # QuiverQuant extras (lobbying API + gov contracts) — as supplementary
        quiver_lobbying = []
        gov_contracts = []
        if quiver_ok():
            quiver_lobbying, gov_contracts = await asyncio.gather(
                get_lobbying(limit=15),
                get_gov_contracts(limit=15),
            )

        upcoming_earnings = (
            finnhub_data.get("upcoming_earnings", [])
            if finnhub_data.get("earnings_count", 0) > 0
            else []
        )

        source = self._determine_source(finnhub_data)
        if quiver_ok():
            source = "quiverquant+" + source if source else "quiverquant"

        return {
            'insider_trades': insider_trades,
            'insider_count': len(insider_trades),
            'fed_announcements': fed_announcements,
            'fed_count': len(fed_announcements),
            'congressional_trades': congressional_trades,
            'congressional_count': len(congressional_trades),
            'upcoming_earnings': upcoming_earnings,
            'earnings_count': len(upcoming_earnings),
            'lobbying_top_spenders': lobbying_db,
            'lobbying_count': len(lobbying_db),
            'lobbying_live': quiver_lobbying,
            'lobbying_live_count': len(quiver_lobbying),
            'gov_contracts': gov_contracts,
            'gov_contracts_count': len(gov_contracts),
            'company_news_finnhub': finnhub_data.get("company_news", []),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'source': source,
        }
