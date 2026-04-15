"""Key Vault admin routes — secure management of platform API keys."""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import List
from routes.auth import get_current_user

router = APIRouter(prefix="/api/vault")
db = None


def set_db(database):
    global db
    db = database


class StoreKeyRequest(BaseModel):
    name: str = Field(..., description="Environment variable name, e.g. OPENAI_API_KEY")
    value: str = Field(..., description="The API key value")
    category: str = Field("general", description="Category: ai, market_data, email, search, etc.")
    description: str = Field("", description="Optional description")


class BulkStoreRequest(BaseModel):
    keys: List[StoreKeyRequest]


class DeleteKeyRequest(BaseModel):
    name: str


@router.get("/keys")
async def list_vault_keys(request: Request):
    """List all stored platform keys (previews only, no secret values)."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    from services.key_vault import KeyVault
    vault = KeyVault(db)
    keys = await vault.list_keys()
    return {"keys": keys, "count": len(keys)}


@router.post("/keys")
async def store_vault_key(req: StoreKeyRequest, request: Request):
    """Encrypt and store a platform API key. Auto-reloads affected provider pools."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    from services.key_vault import KeyVault
    vault = KeyVault(db)
    await vault.inject_and_reload(req.name, req.value, req.category, req.description, user.get("email", "admin"))
    return {"stored": True, "name": req.name, "message": "Key stored and providers reloaded"}


@router.post("/keys/bulk")
async def bulk_store_keys(req: BulkStoreRequest, request: Request):
    """Store multiple API keys in one call. Admin only."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    from services.key_vault import KeyVault
    vault = KeyVault(db)
    stored = []
    for key in req.keys:
        await vault.inject_and_reload(key.name, key.value, key.category, key.description, user.get("email", "admin"))
        stored.append(key.name)
    return {"stored": stored, "count": len(stored), "message": f"{len(stored)} keys stored and providers reloaded"}


@router.post("/keys/validate")
async def validate_key(req: StoreKeyRequest, request: Request):
    """Test an API key against its provider before storing. Admin only."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    result = await _validate_key(req.name, req.value)
    return result


async def _validate_key(name: str, value: str) -> dict:
    """Probe a key against its provider's cheapest endpoint."""
    import httpx
    validators = {
        "OPENAI_API_KEY": _validate_openai,
        "ANTHROPIC_API_KEY": _validate_anthropic,
        "OPENROUTER_API_KEY": _validate_openrouter,
        "TWELVEDATA_API_KEY": _validate_twelvedata,
        "FRED_API_KEYS": _validate_fred,
        "SENDGRID_API_KEY": _validate_sendgrid,
        "TAVILY_API_KEY": _validate_tavily,
        "ALPHA_VANTAGE_API_KEY": _validate_alphavantage,
        "FINNHUB_API_KEY": _validate_finnhub,
        "RESEND_API_KEY": _validate_resend,
        "STOCKFIT_API_KEY": _validate_stockfit,
        "POLYGON_API_KEY": _validate_polygon,
        "QUIVERQUANT_API_KEY": _validate_quiverquant,
    }
    fn = validators.get(name)
    if not fn:
        return {"valid": None, "name": name, "message": "No validator for this key type — will store as-is"}
    try:
        return await fn(value)
    except Exception as e:
        return {"valid": False, "name": name, "error": str(e)[:200]}


async def _validate_openai(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
        ok = r.status_code == 200
        return {"valid": ok, "name": "OPENAI_API_KEY", "status": r.status_code,
                "message": "Connected to OpenAI" if ok else f"HTTP {r.status_code}"}


async def _validate_anthropic(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.anthropic.com/v1/models",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        ok = r.status_code == 200
        return {"valid": ok, "name": "ANTHROPIC_API_KEY", "status": r.status_code,
                "message": "Connected to Anthropic" if ok else f"HTTP {r.status_code}"}


async def _validate_openrouter(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://openrouter.ai/api/v1/models", headers={"Authorization": f"Bearer {key}"})
        ok = r.status_code == 200
        return {"valid": ok, "name": "OPENROUTER_API_KEY", "status": r.status_code,
                "message": "Connected to OpenRouter" if ok else f"HTTP {r.status_code}"}


async def _validate_twelvedata(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.twelvedata.com/quote", params={"symbol": "AAPL", "apikey": key})
        data = r.json() if r.status_code == 200 else {}
        ok = r.status_code == 200 and "code" not in data
        return {"valid": ok, "name": "TWELVEDATA_API_KEY", "status": r.status_code,
                "message": "Connected to TwelveData" if ok else data.get("message", f"HTTP {r.status_code}")}


async def _validate_fred(key: str) -> dict:
    import httpx
    first_key = key.split(",")[0].strip()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.stlouisfed.org/fred/series",
                        params={"series_id": "GDP", "api_key": first_key, "file_type": "json"})
        ok = r.status_code == 200 and "error" not in r.text.lower()[:200]
        return {"valid": ok, "name": "FRED_API_KEYS", "status": r.status_code,
                "message": "Connected to FRED" if ok else f"HTTP {r.status_code}"}


async def _validate_sendgrid(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.sendgrid.com/v3/scopes", headers={"Authorization": f"Bearer {key}"})
        ok = r.status_code == 200
        return {"valid": ok, "name": "SENDGRID_API_KEY", "status": r.status_code,
                "message": "Connected to SendGrid" if ok else f"HTTP {r.status_code}"}


async def _validate_tavily(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post("https://api.tavily.com/search",
                         json={"api_key": key, "query": "test", "max_results": 1})
        ok = r.status_code == 200
        return {"valid": ok, "name": "TAVILY_API_KEY", "status": r.status_code,
                "message": "Connected to Tavily" if ok else f"HTTP {r.status_code}"}


async def _validate_alphavantage(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://www.alphavantage.co/query",
                        params={"function": "GLOBAL_QUOTE", "symbol": "IBM", "apikey": key})
        data = r.json() if r.status_code == 200 else {}
        ok = r.status_code == 200 and "Global Quote" in data
        return {"valid": ok, "name": "ALPHA_VANTAGE_API_KEY", "status": r.status_code,
                "message": "Connected to Alpha Vantage" if ok else "Rate limited or invalid key"}


async def _validate_finnhub(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://finnhub.io/api/v1/quote",
                        params={"symbol": "AAPL"}, headers={"X-Finnhub-Token": key})
        ok = r.status_code == 200
        return {"valid": ok, "name": "FINNHUB_API_KEY", "status": r.status_code,
                "message": "Connected to Finnhub" if ok else f"HTTP {r.status_code}"}


async def _validate_resend(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.resend.com/domains", headers={"Authorization": f"Bearer {key}"})
        ok = r.status_code == 200
        return {"valid": ok, "name": "RESEND_API_KEY", "status": r.status_code,
                "message": "Connected to Resend" if ok else f"HTTP {r.status_code}"}


async def _validate_stockfit(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.stockfit.io/v1/api/earnings/snapshot",
                        params={"symbol": "AAPL"}, headers={"Authorization": f"Bearer {key}"})
        ok = r.status_code == 200
        return {"valid": ok, "name": "STOCKFIT_API_KEY", "status": r.status_code,
                "message": "Connected to StockFit" if ok else f"HTTP {r.status_code}"}


async def _validate_polygon(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"https://api.polygon.io/v2/aggs/ticker/AAPL/prev?apiKey={key}")
        ok = r.status_code == 200
        return {"valid": ok, "name": "POLYGON_API_KEY", "status": r.status_code,
                "message": "Connected to Polygon" if ok else f"HTTP {r.status_code}"}


async def _validate_quiverquant(key: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.quiverquant.com/beta/live/congresstrading",
                        headers={"Authorization": f"Bearer {key}"})
        ok = r.status_code == 200
        return {"valid": ok, "name": "QUIVERQUANT_API_KEY", "status": r.status_code,
                "message": "Connected to QuiverQuant" if ok else f"HTTP {r.status_code}"}


@router.delete("/keys/{name}")
async def delete_vault_key(name: str, request: Request):
    """Delete a platform key from the vault."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    from services.key_vault import KeyVault
    vault = KeyVault(db)
    deleted = await vault.delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"deleted": True, "name": name}
