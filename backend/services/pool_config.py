import json
import os



def _safe_json_list(raw: str) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def get_ai_provider_pool() -> list[dict]:
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

    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        fallback.append({
            "name": "openrouter-gpt52",
            "provider": "openrouter",
            "api_key": openrouter_key,
            "model": "openai/gpt-5.2",
            "priority": 4,
        })

    return fallback


def get_market_data_provider_pool() -> list[dict]:
    pool = _safe_json_list(os.environ.get("MARKET_DATA_PROVIDER_POOL", ""))
    if pool:
        return pool

    fallback = []
    # 2026-06-26 — Public.com is the operator's primary equity broker.
    # When credentials are present, prefer it as the market-data
    # primary too: same JWT, same source-of-truth for prices Alpha
    # actually trades on, no extra rate-limit budget burned. Falls back
    # to AlphaVantage / Finnhub / etc. when Public is down or for
    # symbols it doesn't cover.
    #
    # Credentials live in either env vars (preferred for service-level
    # config) or the ``broker_connections`` row written by the
    # broker-connect UI. The market-data dispatcher resolves the row
    # lazily on first call — see ``_public_quote_async`` in
    # ``market_data_pool.py``.
    public_key = os.environ.get("PUBLIC_API_KEY", "")
    public_account = os.environ.get("PUBLIC_ACCOUNT_ID", "")
    # Sentinel allows the dispatcher to load creds from
    # ``broker_connections`` if env is unset but the broker is
    # connected. ``api_key`` must be truthy for ProviderPool to keep
    # the entry, so we use a sentinel string the dispatcher recognises.
    if public_key or public_account:
        fallback.append({
            "name": "public-primary",
            "provider": "public",
            "api_key": public_key or "__from_db__",
            "account_id": public_account,
            "priority": 1,
        })
    else:
        # No env override — try the broker-connect row. Sentinel value
        # tells the dispatcher to lazy-load from Mongo on first call.
        fallback.append({
            "name": "public-primary",
            "provider": "public",
            "api_key": "__from_db__",
            "account_id": "",
            "priority": 1,
        })

    av = os.environ.get("ALPHAVANTAGEAPIKEY")
    if av:
        fallback.append({
            "name": "alphavantage-backup",
            "provider": "alphavantage",
            "api_key": av,
            "priority": 2,
        })

    finnhub = os.environ.get("FINNHUB_API_KEY")
    if finnhub:
        fallback.append({
            "name": "finnhub-backup",
            "provider": "finnhub",
            "api_key": finnhub,
            "priority": 3,
        })

    ms = os.environ.get("MARKETSTACK_API_KEY")
    if ms:
        fallback.append({
            "name": "marketstack-backup",
            "provider": "marketstack",
            "api_key": ms,
            "priority": 4,
        })

    # Polygon — optional A/B challenger. Default priority puts it last
    # in the chain; operators can override via
    # MARKET_DATA_POLYGON_PRIORITY=1 to make it the primary, but with
    # Public taking primary now Polygon is typically the deepest
    # backup. Disabled when POLYGON_API_KEY is unset.
    polygon = os.environ.get("POLYGON_API_KEY")
    if polygon:
        try:
            poly_priority = int(os.environ.get("MARKET_DATA_POLYGON_PRIORITY", "5"))
        except ValueError:
            poly_priority = 5
        fallback.append({
            "name": "polygon-ab",
            "provider": "polygon",
            "api_key": polygon,
            "priority": poly_priority,
        })

    return fallback


def get_email_provider_pool() -> list[dict]:
    pool = _safe_json_list(os.environ.get("EMAIL_PROVIDER_POOL", ""))
    if pool:
        return pool

    fallback = []
    sendgrid_key = os.environ.get("SENDGRID_API_KEY")
    if sendgrid_key:
        fallback.append({
            "name": "sendgrid-primary",
            "provider": "sendgrid",
            "api_key": sendgrid_key,
            "priority": 1,
        })

    return fallback
