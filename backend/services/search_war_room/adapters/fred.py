"""FRED adapter — free macro data with multi-key rotation.

Keys: Set FRED_API_KEYS (comma-separated) in .env.
Get free keys from https://fred.stlouisfed.org/docs/api/api_key.html
"""
import httpx
from services.key_rotator import KeyRotator
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

TTL = 3600
fred_rotator = KeyRotator("FRED_API_KEYS")

SERIES_MAP = {
    'cpi': 'CPIAUCSL', 'gdp': 'GDP', 'rates': 'FEDFUNDS', 'fed funds': 'FEDFUNDS',
    'unemployment': 'UNRATE', 'inflation': 'CPIAUCSL', 'interest rate': 'FEDFUNDS',
    'treasury': 'DGS10', '10 year': 'DGS10', 'yield': 'DGS10',
    'housing': 'HOUST', 'retail': 'RSXFS', 'pce': 'PCEPI',
    'payroll': 'PAYEMS', 'jobs': 'PAYEMS', 'consumer': 'UMCSENT',
}


async def run(query: str) -> EngineResult:
    if not fred_rotator.available:
        return EngineResult(engine='fred', status='skipped', source_type='macro', query=query, error='missing_fred_api_keys')
    q = query.lower()
    series_id = next((v for k, v in SERIES_MAP.items() if k in q), None)
    if not series_id:
        return EngineResult(engine='fred', status='skipped', source_type='macro', query=query, error='no_matching_series')
    cached = get_cached('fred', series_id, TTL)
    if cached:
        return EngineResult(**cached)

    key = fred_rotator.get()
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get('https://api.stlouisfed.org/fred/series/observations', params={
                'series_id': series_id, 'api_key': key, 'file_type': 'json',
                'sort_order': 'desc', 'limit': 5,
            })
            if r.status_code != 200:
                fred_rotator.mark_failed(key)
                return EngineResult(engine='fred', status='error', source_type='macro', query=query, error=f'http_{r.status_code}')
            fred_rotator.mark_success(key)
            obs = r.json().get('observations', [])[:5]
            items = [{'date': o.get('date'), 'value': o.get('value')} for o in obs]
            result = EngineResult(
                engine='fred', status='ok', source_type='macro', query=query,
                title=f'FRED {series_id}', summary=f'{series_id}: {items[0]["value"] if items else "N/A"} (latest)',
                items=items, confidence=0.9, authoritative=True, cached=False,
            )
            set_cached('fred', series_id, result.model_dump())
            return result
    except Exception as exc:
        fred_rotator.mark_failed(key)
        return EngineResult(engine='fred', status='error', source_type='macro', query=query, error=str(exc))
