"""SEC EDGAR adapter — free filings data, no key required."""
import httpx
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

TTL = 900
SEC_UA = 'RISEDUALAI/1.0 support@risedual.ai'


async def _ticker_to_cik(symbol: str) -> str | None:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get('https://www.sec.gov/files/company_tickers.json', headers={'User-Agent': SEC_UA})
        r.raise_for_status()
        for _, row in r.json().items():
            if row.get('ticker', '').upper() == symbol.upper():
                return str(row.get('cik_str')).zfill(10)
    return None


async def run(query: str, symbol: str | None = None):
    ticker = symbol or query.strip().split()[0].upper()
    cached = get_cached('sec', ticker, TTL)
    if cached:
        return EngineResult(**cached)
    try:
        cik = await _ticker_to_cik(ticker)
        if not cik:
            return EngineResult(engine='sec', status='error', source_type='filing', query=query, error='ticker_not_found')
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f'https://data.sec.gov/submissions/CIK{cik}.json', headers={'User-Agent': SEC_UA})
            if r.status_code != 200:
                return EngineResult(engine='sec', status='error', source_type='filing', query=query, error=f'http_{r.status_code}')
            data = r.json()
            recent = data.get('filings', {}).get('recent', {})
            forms = recent.get('form', [])[:5]
            dates = recent.get('filingDate', [])[:5]
            accessions = recent.get('accessionNumber', [])[:5]
            items = [{'form': f, 'date': d, 'accession': a} for f, d, a in zip(forms, dates, accessions)]
            result = EngineResult(
                engine='sec', status='ok', source_type='filing', query=query,
                title=f'{ticker} SEC filings', summary=f'{len(items)} recent filings for {ticker}',
                items=items, confidence=0.95, authoritative=True, cached=False,
            )
            set_cached('sec', ticker, result.model_dump())
            return result
    except Exception as exc:
        return EngineResult(engine='sec', status='error', source_type='filing', query=query, error=str(exc))
