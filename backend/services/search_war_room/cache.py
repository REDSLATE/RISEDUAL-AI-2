"""Search War Room — In-memory cache with TTL."""
import time
from typing import Optional

_CACHE: dict = {}


def make_key(engine: str, query: str) -> str:
    return f'{engine}:{query.strip().lower()}'


def get_cached(engine: str, query: str, ttl_seconds: int) -> Optional[dict]:
    key = make_key(engine, query)
    item = _CACHE.get(key)
    if not item:
        return None
    if time.time() - item['stored_at'] > ttl_seconds:
        del _CACHE[key]
        return None
    return item['value']


def set_cached(engine: str, query: str, value: dict):
    key = make_key(engine, query)
    _CACHE[key] = {'stored_at': time.time(), 'value': value}
    if len(_CACHE) > 500:
        oldest = min(_CACHE, key=lambda k: _CACHE[k]['stored_at'])
        del _CACHE[oldest]


def cache_status() -> dict:
    return {'entries': len(_CACHE)}
