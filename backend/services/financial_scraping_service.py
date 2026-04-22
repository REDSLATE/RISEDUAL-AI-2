import logging
import asyncio
import requests
from typing import Any
from bs4 import BeautifulSoup

from datetime import datetime, timezone

from services.structured_log import log_error, log_warning

logger = logging.getLogger(__name__)

class FinancialScrapingService:
    """Scrape financial news and data from multiple sources"""
    
    def __init__(self) -> None:
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    async def _get(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault('headers', self.headers)
        kwargs.setdefault('timeout', 10)
        return await asyncio.to_thread(requests.get, url, **kwargs)
    
    async def scrape_financial_news(self) -> list[dict]:
        """Scrape latest financial news from multiple sources"""
        news = []
        
        # Scrape from various sources
        news.extend(await self._scrape_cnbc())
        news.extend(await self._scrape_reuters())
        news.extend(await self._scrape_marketwatch())
        news.extend(await self._scrape_fox_business())
        news.extend(await self._scrape_wsj())
        news.extend(await self._scrape_bloomberg())
        news.extend(await self._scrape_oan())
        news.extend(await self._scrape_epoch_times())
        
        return news
    
    async def _scrape_cnbc(self) -> list[dict]:
        """Scrape CNBC market news"""
        try:
            url = 'https://www.cnbc.com/markets/'
            response = await self._get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles = []
            for article in soup.find_all('div', class_='Card-titleContainer')[:10]:
                title = article.find('a')
                if title:
                    articles.append({
                        'source': 'CNBC',
                        'title': title.get_text(strip=True),
                        'url': title.get('href'),
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'sentiment': None  # Will be analyzed by AI
                    })
            return articles
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "financial_scrape",
                "note": "CNBC scraping error",
            })
            return []
    
    async def _scrape_reuters(self) -> list[dict]:
        """Scrape Reuters market news"""
        try:
            url = 'https://www.reuters.com/markets/'
            response = await self._get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles = []
            for article in soup.find_all('a', {'data-testid': 'Heading'})[:10]:
                href = article.get('href', '')
                if not isinstance(href, str):
                    continue
                articles.append({
                    'source': 'Reuters',
                    'title': article.get_text(strip=True),
                    'url': 'https://www.reuters.com' + href,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'sentiment': None
                })
            return articles
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "financial_scrape",
                "note": "Reuters scraping error",
            })
            return []
    
    async def _scrape_marketwatch(self) -> list[dict]:
        """Scrape MarketWatch headlines"""
        try:
            url = 'https://www.marketwatch.com/latest-news'
            response = await self._get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles = []
            for article in soup.find_all('h3', class_='article__headline')[:10]:
                link = article.find('a')
                if link:
                    articles.append({
                        'source': 'MarketWatch',
                        'title': link.get_text(strip=True),
                        'url': link.get('href'),
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "financial_scrape",
                "note": "MarketWatch scraping error",
            })
            return []
    
    async def _scrape_fox_business(self) -> list[dict]:
        """Scrape Fox Business news"""
        try:
            url = 'https://www.foxbusiness.com/markets'
            response = await self._get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles = []
            for article in soup.find_all('h2', class_='title')[:10]:
                link = article.find('a')
                if link:
                    href = link.get('href', '')
                    if not isinstance(href, str):
                        continue
                    articles.append({
                        'source': 'Fox Business',
                        'title': link.get_text(strip=True),
                        'url': 'https://www.foxbusiness.com' + href,
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "financial_scrape",
                "note": "Fox Business scraping error",
            })
            return []
    
    async def _scrape_wsj(self) -> list[dict]:
        """Scrape Wall Street Journal market news"""
        try:
            url = 'https://www.wsj.com/news/markets'
            response = await self._get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles = []
            for article in soup.find_all('h3', class_='WSJTheme--headline')[:10]:
                link = article.find('a')
                if link:
                    href = link.get('href', '')
                    # BeautifulSoup can return a list for multi-value
                    # attributes (e.g. `class`). `href` is single-valued in
                    # HTML, but scraping is fragile — skip malformed links
                    # defensively so a one-off markup change from WSJ can't
                    # crash the whole news pipeline with `AttributeError`.
                    if not isinstance(href, str):
                        continue
                    full_url = href if href.startswith('http') else 'https://www.wsj.com' + href
                    articles.append({
                        'source': 'Wall Street Journal',
                        'title': link.get_text(strip=True),
                        'url': full_url,
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "financial_scrape",
                "note": "WSJ scraping error",
            })
            return []
    
    async def _scrape_bloomberg(self) -> list[dict]:
        """Scrape Bloomberg market news"""
        try:
            url = 'https://www.bloomberg.com/markets'
            response = await self._get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles = []
            for article in soup.find_all('article')[:10]:
                headline = article.find('a')
                if headline:
                    href = headline.get('href', '')
                    # See `_scrape_wsj` — defensive isinstance guard.
                    if not isinstance(href, str):
                        continue
                    full_url = href if href.startswith('http') else 'https://www.bloomberg.com' + href
                    articles.append({
                        'source': 'Bloomberg',
                        'title': headline.get_text(strip=True),
                        'url': full_url,
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "financial_scrape",
                "note": "Bloomberg scraping error",
            })
            return []
    
    async def _scrape_oan(self) -> list[dict]:
        """Scrape One America News (OAN) business news"""
        try:
            url = 'https://www.oann.com/category/business/'
            response = await self._get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles = []
            for article in soup.find_all('h2', class_='entry-title')[:10]:
                link = article.find('a')
                if link:
                    articles.append({
                        'source': 'OAN',
                        'title': link.get_text(strip=True),
                        'url': link.get('href'),
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "financial_scrape",
                "note": "OAN scraping error",
            })
            return []
    
    async def _scrape_epoch_times(self) -> list[dict]:
        """Scrape Epoch Times business news"""
        try:
            url = 'https://www.theepochtimes.com/business'
            response = await self._get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles = []
            for article in soup.find_all('h3')[:10]:
                link = article.find('a')
                if link:
                    href = link.get('href', '')
                    # See `_scrape_wsj` — defensive isinstance guard.
                    if not isinstance(href, str):
                        continue
                    full_url = href if href.startswith('http') else 'https://www.theepochtimes.com' + href
                    articles.append({
                        'source': 'Epoch Times',
                        'title': link.get_text(strip=True),
                        'url': full_url,
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "financial_scrape",
                "note": "Epoch Times scraping error",
            })
            return []

    
    async def scrape_reddit_sentiment(self) -> list[dict]:
        """Scrape Reddit WallStreetBets for sentiment"""
        try:
            # Using Reddit JSON API (no auth needed for public posts)
            url = 'https://www.reddit.com/r/wallstreetbets/hot.json?limit=25'
            response = await self._get(url)
            data = response.json()
            
            posts = []
            for post in data['data']['children']:
                post_data = post['data']
                posts.append({
                    'source': 'Reddit-WSB',
                    'title': post_data['title'],
                    'score': post_data['score'],
                    'num_comments': post_data['num_comments'],
                    'url': 'https://reddit.com' + post_data['permalink'],
                    'timestamp': datetime.fromtimestamp(post_data['created_utc']).isoformat(),
                    'sentiment': None
                })
            return posts
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "financial_scrape",
                "note": "Reddit scraping error",
            })
            return []
    
    async def scrape_insider_trades(self) -> list[dict]:
        """Scrape recent insider trading activity from OpenInsider.

        The previous implementation hit the `/screener?...` path which
        returns a shell page with the filter form only — no results
        table. The canonical landing URL below serves the fully-
        rendered results table in one HTTP round-trip.

        Column indices (verified against live HTML 2026-04-22):
            [0] flag  [1] filing_date  [2] trade_date  [3] ticker
            [4] company_name  [5] insider_name  [6] title
            [7] trade_type  [8] price  [9] qty  [10] owned
            [11] delta_own  [12] value
        """
        try:
            url = "http://openinsider.com/latest-insider-sales-of-1m"
            response = await self._get(url)
            soup = BeautifulSoup(response.content, 'html.parser')

            table = soup.find(
                'table',
                class_=lambda c: c is not None and 'tinytable' in c,
            )
            if not table:
                return []

            trades = []
            # Skip header row. Cap at 25 rows so a single API response
            # doesn't balloon when the landing page bumps its default
            # page size.
            for row in table.find_all('tr')[1:26]:
                cols = row.find_all('td')
                if len(cols) < 13:
                    continue
                trades.append({
                    'ticker':      cols[3].get_text(strip=True),
                    'company':     cols[4].get_text(strip=True),
                    'insider':     cols[5].get_text(strip=True),
                    'title':       cols[6].get_text(strip=True),
                    'trade_type':  cols[7].get_text(strip=True),
                    'price':       cols[8].get_text(strip=True),
                    'qty':         cols[9].get_text(strip=True),
                    'value':       cols[12].get_text(strip=True),
                    'filing_date': cols[1].get_text(strip=True),
                    'trade_date':  cols[2].get_text(strip=True),
                    'timestamp':   datetime.now(timezone.utc).isoformat(),
                })
            return trades
        except Exception as e:
            log_error(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "financial_scrape",
                "note": "Insider trades scraping error",
            })
            return []