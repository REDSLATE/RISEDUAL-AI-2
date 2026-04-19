import logging
import asyncio
import requests
from bs4 import BeautifulSoup

from datetime import datetime

logger = logging.getLogger(__name__)

class FinancialScrapingService:
    """Scrape financial news and data from multiple sources"""
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    async def _get(self, url, **kwargs):
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
                        'timestamp': datetime.utcnow().isoformat(),
                        'sentiment': None  # Will be analyzed by AI
                    })
            return articles
        except Exception as e:
            logger.error(f"CNBC scraping error: {str(e)}")
            return []
    
    async def _scrape_reuters(self) -> list[dict]:
        """Scrape Reuters market news"""
        try:
            url = 'https://www.reuters.com/markets/'
            response = await self._get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles = []
            for article in soup.find_all('a', {'data-testid': 'Heading'})[:10]:
                articles.append({
                    'source': 'Reuters',
                    'title': article.get_text(strip=True),
                    'url': 'https://www.reuters.com' + article.get('href', ''),
                    'timestamp': datetime.utcnow().isoformat(),
                    'sentiment': None
                })
            return articles
        except Exception as e:
            logger.error(f"Reuters scraping error: {str(e)}")
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
                        'timestamp': datetime.utcnow().isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            logger.error(f"MarketWatch scraping error: {str(e)}")
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
                    articles.append({
                        'source': 'Fox Business',
                        'title': link.get_text(strip=True),
                        'url': 'https://www.foxbusiness.com' + link.get('href', ''),
                        'timestamp': datetime.utcnow().isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            logger.error(f"Fox Business scraping error: {str(e)}")
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
                    full_url = href if href.startswith('http') else 'https://www.wsj.com' + href
                    articles.append({
                        'source': 'Wall Street Journal',
                        'title': link.get_text(strip=True),
                        'url': full_url,
                        'timestamp': datetime.utcnow().isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            logger.error(f"WSJ scraping error: {str(e)}")
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
                    full_url = href if href.startswith('http') else 'https://www.bloomberg.com' + href
                    articles.append({
                        'source': 'Bloomberg',
                        'title': headline.get_text(strip=True),
                        'url': full_url,
                        'timestamp': datetime.utcnow().isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            logger.error(f"Bloomberg scraping error: {str(e)}")
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
                        'timestamp': datetime.utcnow().isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            logger.error(f"OAN scraping error: {str(e)}")
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
                    full_url = href if href.startswith('http') else 'https://www.theepochtimes.com' + href
                    articles.append({
                        'source': 'Epoch Times',
                        'title': link.get_text(strip=True),
                        'url': full_url,
                        'timestamp': datetime.utcnow().isoformat(),
                        'sentiment': None
                    })
            return articles
        except Exception as e:
            logger.error(f"Epoch Times scraping error: {str(e)}")
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
            logger.error(f"Reddit scraping error: {str(e)}")
            return []
    
    async def scrape_insider_trades(self) -> list[dict]:
        """Scrape recent insider trading activity"""
        try:
            # Using OpenInsider.com
            url = 'http://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh=&fd=730&fdr=&td=0&tdr=&fdlyl=&fdlyh=&daysago=&xp=1&xs=1&vl=&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=100&page=1'
            response = await self._get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            trades = []
            table = soup.find('table', class_='tinytable')
            if table:
                rows = table.find_all('tr')[1:11]  # Get first 10 trades
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) >= 5:
                        trades.append({
                            'ticker': cols[3].get_text(strip=True) if len(cols) > 3 else '',
                            'insider': cols[1].get_text(strip=True) if len(cols) > 1 else '',
                            'trade_type': cols[5].get_text(strip=True) if len(cols) > 5 else '',
                            'value': cols[7].get_text(strip=True) if len(cols) > 7 else '',
                            'timestamp': datetime.utcnow().isoformat()
                        })
            return trades
        except Exception as e:
            logger.error(f"Insider trades scraping error: {str(e)}")
            return []