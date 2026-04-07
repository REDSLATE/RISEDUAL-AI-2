import logging
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

    async def get_sec_filings(self, ticker: str = None) -> List[Dict]:
        """Fetch recent SEC EDGAR filings via full-text search API"""
        filings = []
        try:
            # EDGAR EFTS search API for recent Form 4 filings
            url = 'https://efts.sec.gov/LATEST/search-index?q=*&forms=4'
            resp = requests.get(url, headers={
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
            filings = self._scrape_openinsider()

        return filings[:15]

    def _scrape_openinsider(self) -> List[Dict]:
        """Scrape insider trading data from OpenInsider"""
        trades = []
        try:
            url = 'http://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh=&fd=7&fdr=&td=0&tdr=&feession=&teession=&xp=1&vl=&vh=&ocl=&och=&sic1l=&sic1h=&iession1l=&iession1h=&cnt=15'
            resp = requests.get(url, headers=self.headers, timeout=10)
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
            resp = requests.get(url, headers=self.headers, timeout=10)
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
        """Scrape congressional stock trades from Capitol Trades"""
        trades = self._scrape_capitol_trades()
        if not trades:
            trades = self._scrape_quiverquant_congress()
        return trades[:20]

    def _scrape_capitol_trades(self) -> List[Dict]:
        """Primary: scrape Capitol Trades for congressional trades."""
        trades = []
        try:
            url = 'https://www.capitoltrades.com/trades'
            resp = requests.get(url, headers=self.headers, timeout=15)
            if resp.status_code != 200:
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
    def _parse_capitol_row(row) -> Optional[Dict]:
        """Parse a single row from Capitol Trades table."""
        cols = row.find_all('td')
        if len(cols) < 8:
            return None

        politician_text = cols[0].get_text(strip=True)
        issuer_text = cols[1].get_text(strip=True)
        trade_type = cols[6].get_text(strip=True)
        size = cols[7].get_text(strip=True)

        name, party, chamber = GovFilingsService._parse_politician(politician_text)
        ticker, company = GovFilingsService._parse_issuer(issuer_text)

        return {
            'representative': name,
            'ticker': ticker,
            'company': company,
            'transaction_date': cols[3].get_text(strip=True) if len(cols) > 3 else '',
            'disclosure_date': cols[2].get_text(strip=True) if len(cols) > 2 else '',
            'type': trade_type,
            'amount': size,
            'party': party,
            'chamber': chamber,
            'description': f"{trade_type.upper()} by {name} ({party}-{chamber}) - {ticker} {size}",
        }

    @staticmethod
    def _parse_politician(text: str):
        """Extract name, party, chamber from politician text."""
        name = text
        party = ''
        chamber = ''
        if 'Republican' in text:
            party = 'R'
            name = text.split('Republican')[0].strip()
        elif 'Democrat' in text:
            party = 'D'
            name = text.split('Democrat')[0].strip()
        if 'House' in text:
            chamber = 'House'
        elif 'Senate' in text:
            chamber = 'Senate'
        return name, party, chamber

    @staticmethod
    def _parse_issuer(text: str):
        """Extract ticker and company name from issuer text."""
        ticker_match = re.search(r'([A-Z]{1,5}):[A-Z]{2}', text)
        ticker = ticker_match.group(1) if ticker_match else ''
        company = text.split(ticker)[0].strip() if ticker else text
        return ticker, company

    def _scrape_quiverquant_congress(self) -> List[Dict]:
        """Fallback: scrape QuiverQuant congressional trading page"""
        trades = []
        try:
            url = 'https://www.quiverquant.com/congresstrading/'
            resp = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(resp.text, 'html.parser')
            table = soup.find('table')
            if table:
                rows = table.find_all('tr')[1:16]
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) >= 5:
                        trades.append({
                            'representative': cols[0].get_text(strip=True),
                            'ticker': cols[1].get_text(strip=True),
                            'type': cols[2].get_text(strip=True) if len(cols) > 2 else '',
                            'amount': cols[3].get_text(strip=True) if len(cols) > 3 else '',
                            'transaction_date': cols[4].get_text(strip=True) if len(cols) > 4 else '',
                            'description': f"Congressional trade: {cols[0].get_text(strip=True)} - {cols[1].get_text(strip=True)}",
                        })
        except Exception as e:
            logger.error(f"Error scraping QuiverQuant: {str(e)}")
        return trades

    async def get_all_gov_data(self) -> Dict:
        # Try Finnhub first (reliable API), fallback to scraping
        finnhub_data = {}
        try:
            from services.finnhub_service import FinnhubService
            fh = FinnhubService()
            if fh._is_configured():
                finnhub_data = await fh.get_all_data_for_predictions()
        except Exception as e:
            logger.warning(f"Finnhub fetch failed, falling back to scrapers: {e}")

        finnhub_has_insider = finnhub_data.get("insider_count", 0) > 0
        finnhub_has_earnings = finnhub_data.get("earnings_count", 0) > 0
        finnhub_has_congressional = finnhub_data.get("congressional_count", 0) > 0

        # Always fetch Fed announcements (Finnhub doesn't cover this)
        fed_announcements = await self.get_fed_announcements()

        # Build result using best available source per category
        insider_trades = (
            [{"description": t["description"]} for t in finnhub_data.get("insider_transactions", [])]
            if finnhub_has_insider
            else await self.get_sec_filings()
        )

        # Congressional: Finnhub free tier returns 403, so always try scraping fallback
        congressional_trades = (
            finnhub_data.get("congressional_trades", [])
            if finnhub_has_congressional
            else await self.get_congressional_trades()
        )

        upcoming_earnings = finnhub_data.get("upcoming_earnings", []) if finnhub_has_earnings else []

        source_parts = []
        if finnhub_has_insider or finnhub_has_earnings:
            source_parts.append("finnhub")
        if not finnhub_has_congressional or not finnhub_has_insider:
            source_parts.append("scraping")

        return {
            'insider_trades': insider_trades,
            'insider_count': len(insider_trades),
            'fed_announcements': fed_announcements,
            'fed_count': len(fed_announcements),
            'congressional_trades': congressional_trades,
            'congressional_count': len(congressional_trades),
            'upcoming_earnings': upcoming_earnings,
            'earnings_count': len(upcoming_earnings),
            'company_news_finnhub': finnhub_data.get("company_news", []),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'source': '+'.join(source_parts) if source_parts else 'none',
        }
