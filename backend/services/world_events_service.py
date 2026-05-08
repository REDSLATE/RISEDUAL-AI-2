import logging
import asyncio
import requests

from bs4 import BeautifulSoup
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SECTOR_KEYWORDS = {
    'Energy': ['oil', 'opec', 'petroleum', 'natural gas', 'pipeline', 'drilling', 'crude', 'energy', 'refinery', 'lng'],
    'Technology': ['chip', 'semiconductor', 'ai ', 'artificial intelligence', 'tech', 'software', 'cyber', 'data', 'cloud', 'apple', 'google', 'microsoft', 'nvidia', 'tsmc'],
    'Defense': ['military', 'defense', 'weapon', 'nato', 'war', 'conflict', 'missile', 'army', 'pentagon', 'lockheed', 'raytheon'],
    'Finance': ['bank', 'fed ', 'federal reserve', 'interest rate', 'inflation', 'tariff', 'trade deal', 'imf', 'world bank', 'treasury', 'bonds'],
    'Healthcare': ['pharma', 'vaccine', 'drug', 'fda', 'health', 'pandemic', 'disease', 'hospital', 'biotech', 'who '],
    'Agriculture': ['crop', 'grain', 'wheat', 'corn', 'farm', 'agriculture', 'food', 'drought', 'fertilizer'],
    'Automotive': ['ev ', 'electric vehicle', 'tesla', 'auto', 'car', 'vehicle', 'battery', 'lithium'],
    'Shipping': ['shipping', 'port', 'cargo', 'supply chain', 'logistics', 'freight', 'container', 'suez', 'panama canal'],
    'Mining': ['gold', 'silver', 'copper', 'mining', 'rare earth', 'lithium', 'cobalt', 'mineral'],
    'Real Estate': ['housing', 'mortgage', 'real estate', 'property', 'construction', 'rent'],
}

SECTOR_TICKERS = {
    'Energy': ['XLE', 'XOM', 'CVX', 'COP', 'SLB', 'OXY'],
    'Technology': ['QQQ', 'AAPL', 'MSFT', 'NVDA', 'TSM', 'GOOGL', 'META'],
    'Defense': ['LMT', 'RTX', 'NOC', 'GD', 'BA', 'LHX'],
    'Finance': ['XLF', 'JPM', 'BAC', 'GS', 'MS', 'C'],
    'Healthcare': ['XLV', 'JNJ', 'PFE', 'UNH', 'MRNA', 'ABT'],
    'Agriculture': ['DBA', 'ADM', 'DE', 'MOS', 'CF'],
    'Automotive': ['TSLA', 'GM', 'F', 'RIVN', 'NIO', 'LI'],
    'Shipping': ['ZIM', 'MATX', 'FDX', 'UPS', 'XPO'],
    'Mining': ['GLD', 'SLV', 'NEM', 'FCX', 'GOLD', 'ALB'],
    'Real Estate': ['XLRE', 'AMT', 'PLD', 'SPG', 'O'],
}


class WorldEventsService:
    def __init__(self) -> None:
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    def _detect_sectors(self, text: str) -> list[dict]:
        text_lower = text.lower()
        affected = []
        for sector, keywords in SECTOR_KEYWORDS.items():
            matches = [kw for kw in keywords if kw in text_lower]
            if matches:
                impact_score = min(len(matches) * 25, 100)
                affected.append({
                    'sector': sector,
                    'impact_score': impact_score,
                    'keywords_matched': matches,
                    'tickers': SECTOR_TICKERS.get(sector, []),
                })
        return sorted(affected, key=lambda x: x['impact_score'], reverse=True)

    def _scrape_rss_sync(self, url: str, source_name: str) -> list[dict]:
        articles = []
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(resp.content, 'xml')
            items = soup.find_all('item')[:10]
            for item in items:
                title = item.find('title')
                desc = item.find('description')
                link = item.find('link')
                pub_date = item.find('pubDate')
                title_text = title.get_text(strip=True) if title else ''
                desc_text = desc.get_text(strip=True) if desc else ''
                combined = f"{title_text} {desc_text}"
                sectors = self._detect_sectors(combined)
                if title_text:
                    articles.append({
                        'title': title_text,
                        'description': desc_text[:300],
                        'source': source_name,
                        'url': link.get_text(strip=True) if link else '',
                        'published': pub_date.get_text(strip=True) if pub_date else '',
                        'affected_sectors': sectors,
                        'is_high_impact': len(sectors) >= 2 or any(s['impact_score'] >= 50 for s in sectors),
                    })
        except Exception as e:
            logger.error(f"Error scraping {source_name} RSS: {str(e)}")
        return articles

    async def scrape_world_events(self) -> dict:
        # Reuters RSS feeds are DNS-unresolvable in this environment and
        # add no unique coverage vs. BBC / NYT / AP / CNBC — removed per
        # operator directive (May 2026).
        # AP News RSS is also DNS-blocked — kept here for parity; a
        # future replacement (e.g., Axios RSS) can slot in.
        rss_sources = [
            ('https://feeds.bbci.co.uk/news/world/rss.xml', 'BBC World'),
            ('https://rss.nytimes.com/services/xml/rss/nyt/World.xml', 'NY Times World'),
            ('https://www.cnbc.com/id/100727362/device/rss/rss.html', 'CNBC World'),
        ]

        all_events = []
        for url, name in rss_sources:
            events = await asyncio.to_thread(self._scrape_rss_sync, url, name)
            all_events.extend(events)

        # Deduplicate by title similarity
        seen_titles = set()
        unique_events = []
        for event in all_events:
            title_key = event['title'][:50].lower()
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique_events.append(event)

        # Separate high-impact events
        high_impact = [e for e in unique_events if e.get('is_high_impact')]
        all_affected_sectors = {}
        for event in unique_events:
            for sector in event.get('affected_sectors', []):
                name = sector['sector']
                if name not in all_affected_sectors:
                    all_affected_sectors[name] = {
                        'sector': name,
                        'total_mentions': 0,
                        'avg_impact': 0,
                        'tickers': sector['tickers'],
                        'events': [],
                    }
                all_affected_sectors[name]['total_mentions'] += 1
                all_affected_sectors[name]['events'].append(event['title'][:80])
                all_affected_sectors[name]['avg_impact'] = max(
                    all_affected_sectors[name]['avg_impact'], sector['impact_score']
                )

        sector_summary = sorted(all_affected_sectors.values(), key=lambda x: x['avg_impact'], reverse=True)

        return {
            'total_events': len(unique_events),
            'high_impact_count': len(high_impact),
            'high_impact_events': high_impact[:5],
            'all_events': unique_events[:20],
            'affected_sectors': sector_summary[:8],
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
