"""Yahoo Finance adapter — scrapes basic stock data, no key required."""
import httpx
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

TTL = 300


async def run(query: str, symbol: str | None = None) -> EngineResult:
    ticker = (symbol or query.strip().split()[0]).upper()
    cached = get_cached('yahoo', ticker, TTL)
    if cached:
        return EngineResult(**cached)
    try:
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d'
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, headers={'User-Agent': 'RISEDUALAI/1.0'})
            if r.status_code != 200:
                return EngineResult(engine='yahoo', status='error', source_type='market', query=query, error=f'http_{r.status_code}')
            data = r.json()
            meta = data.get('chart', {}).get('result', [{}])[0].get('meta', {})
            price = meta.get('regularMarketPrice', 0)
            prev = meta.get('chartPreviousClose', 0)
            change_pct = round(((price - prev) / prev) * 100, 2) if prev else 0
            result = EngineResult(
                engine='yahoo', status='ok', source_type='market', query=query,
                title=f'{ticker} ${price}', summary=f'{ticker}: ${price} ({change_pct:+.2f}%)',
                items=[{'price': price, 'prev_close': prev, 'change_pct': change_pct, 'currency': meta.get('currency', 'USD')}],
                confidence=0.85, authoritative=True, cached=False,
            )
            set_cached('yahoo', ticker, result.model_dump())
            return result
    except Exception as exc:
        return EngineResult(engine='yahoo', status='error', source_type='market', query=query, error=str(exc))
