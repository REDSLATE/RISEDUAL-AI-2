"""Key Vault admin routes — secure management of platform API keys."""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
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
