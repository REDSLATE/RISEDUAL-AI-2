import json
import os
from typing import List, Dict


def _safe_json_list(raw: str) -> List[Dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def get_ai_provider_pool() -> List[Dict]:
    pool = _safe_json_list(os.environ.get("AI_PROVIDER_POOL", ""))
    if pool:
        return pool

    fallback = []
    emergent = os.environ.get("EMERGENTLLMKEY")
    if emergent:
        fallback.append({
            "name": "emergent-primary",
            "provider": "openai",
            "api_key": emergent,
            "model": "gpt-5.2",
            "priority": 1,
        })

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        fallback.append({
            "name": "openai-backup",
            "provider": "openai",
            "api_key": openai_key,
            "model": "gpt-4.1",
            "priority": 2,
        })

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        fallback.append({
            "name": "anthropic-backup",
            "provider": "anthropic",
            "api_key": anthropic_key,
            "model": "claude-sonnet-4",
            "priority": 3,
        })

    return fallback


def get_market_data_provider_pool() -> List[Dict]:
    pool = _safe_json_list(os.environ.get("MARKET_DATA_PROVIDER_POOL", ""))
    if pool:
        return pool

    fallback = []
    av = os.environ.get("ALPHAVANTAGEAPIKEY")
    if av:
        fallback.append({
            "name": "alphavantage-primary",
            "provider": "alphavantage",
            "api_key": av,
            "priority": 1,
        })

    finnhub = os.environ.get("FINNHUB_API_KEY")
    if finnhub:
        fallback.append({
            "name": "finnhub-backup",
            "provider": "finnhub",
            "api_key": finnhub,
            "priority": 2,
        })

    twelvedata = os.environ.get("TWELVEDATA_API_KEY")
    if twelvedata:
        fallback.append({
            "name": "twelvedata-backup",
            "provider": "twelvedata",
            "api_key": twelvedata,
            "priority": 3,
        })

    return fallback
