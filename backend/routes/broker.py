"""Broker connection management routes — per-user API key storage & trading."""
import os
import logging
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from services.datetime_utils import ensure_utc
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional
from cryptography.fernet import Fernet
import base64
import hashlib
import requests as http_requests
import secrets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/broker", tags=["broker"])

db = None

def set_db(database):
    global db
    db = database


# --- Encryption helpers (derive Fernet key from JWT_SECRET) ---
def _get_fernet():
    secret = os.environ.get("JWT_SECRET", "fallback-secret-key")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)

def encrypt_value(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()

def decrypt_value(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()


# --- OAuth Configuration ---
OAUTH_CONFIGS = {
    "alpaca": {
        "authorize_url": "https://app.alpaca.markets/oauth/authorize",
        "token_url": "https://api.alpaca.markets/oauth/token",
        "scopes": "account:write trading",
        "client_id_env": "ALPACA_OAUTH_CLIENT_ID",
        "client_secret_env": "ALPACA_OAUTH_CLIENT_SECRET",
        "supports_pkce": False,
        "token_expiry_seconds": 900,
    },
    "schwab": {
        "authorize_url": "https://api.schwabapi.com/v1/oauth/authorize",
        "token_url": "https://api.schwabapi.com/v1/oauth/token",
        "scopes": "readonly",
        "client_id_env": "SCHWAB_OAUTH_CLIENT_ID",
        "client_secret_env": "SCHWAB_OAUTH_CLIENT_SECRET",
        "supports_pkce": True,
        "token_expiry_seconds": 1800,
    },
    "ibkr": {
        "authorize_url": "https://www.interactivebrokers.com/authorize",
        "token_url": "https://www.interactivebrokers.com/v1/api/oauth/token",
        "scopes": "trading account",
        "client_id_env": "IBKR_OAUTH_CLIENT_ID",
        "client_secret_env": "IBKR_OAUTH_CLIENT_SECRET",
        "supports_pkce": True,
        "token_expiry_seconds": 86400,
    },
}


# --- PKCE (Proof Key for Code Exchange) ---
import hashlib as _hl

def _generate_pkce():
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        _hl.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


# --- Request Models ---
class ConnectBrokerRequest(BaseModel):
    broker_id: str  # 'alpaca', 'schwab', 'ibkr', etc.
    api_key: str
    api_secret: str
    paper: bool = True  # Paper trading mode (Alpaca)

class PlaceOrderRequest(BaseModel):
    symbol: str
    quantity: float
    side: str  # 'buy' or 'sell'
    order_type: str = "market"  # 'market', 'limit', 'stop', 'stop_limit'
    time_in_force: str = "day"  # 'day', 'gtc', 'ioc'
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None


# --- Auth helper (inline to avoid circular imports) ---
async def _get_user(request: Request) -> dict:
    from routes.auth import get_current_user
    return await get_current_user(request)


async def _is_execution_allowed(request: Request) -> bool:
    """Only the owner account can execute live trades. All others are read-only."""
    user = await _get_user(request)
    return user.get("role") == "owner"


# --- Helper: get user's broker credentials from DB ---
async def _get_user_broker(user_id: str, broker_id: str) -> dict:
    conn = await db.broker_connections.find_one(
        {"user_id": user_id, "broker_id": broker_id, "is_active": True},
        {"_id": 0}
    )
    if not conn:
        raise HTTPException(status_code=404, detail=f"No active {broker_id} connection found. Please connect your broker first.")
    return conn


def _build_client(conn: dict):
    """Build a broker client from stored connection data."""
    from services.broker_service import BrokerService
    api_key = decrypt_value(conn["api_key_enc"])
    api_secret = decrypt_value(conn["api_secret_enc"])
    broker_id = conn["broker_id"]
    credentials = {
        "api_key": api_key,
        "api_secret": api_secret,
        "paper": conn.get("paper", True),
        "oauth": conn.get("auth_method") == "oauth",
    }
    return BrokerService.get_broker_client(broker_id, credentials)


async def _request_token_refresh(cfg: dict, refresh_token: str, client_id: str, client_secret: str, broker_id: str, user_id: str) -> dict:
    """Make the HTTP request to refresh an OAuth token. Returns token_data or raises."""
    token_payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        resp = await asyncio.to_thread(http_requests.post, cfg["token_url"], data=token_payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"OAuth token refresh failed for {broker_id} (user {user_id}): {e}")
        raise HTTPException(status_code=401, detail=f"Token refresh failed. Please reconnect {broker_id} via OAuth.")


async def _log_token_rotation(user_id: str, broker_id: str, old_refresh: str, new_refresh: str, expiry_seconds: int):
    """Log a token rotation event for security audit."""
    await db.oauth_token_audit.insert_one({
        "user_id": user_id,
        "broker_id": broker_id,
        "event": "token_rotated",
        "old_refresh_hash": hashlib.sha256(old_refresh.encode()).hexdigest()[:16],
        "new_refresh_hash": hashlib.sha256(new_refresh.encode()).hexdigest()[:16],
        "expires_in": expiry_seconds,
        "timestamp": datetime.now(timezone.utc),
    })


async def _refresh_oauth_token(user_id: str, broker_id: str, conn: dict) -> dict:
    """Refresh an OAuth access token using the stored refresh token.
    Implements refresh token rotation — new refresh token replaces the old one.
    """
    if broker_id not in OAUTH_CONFIGS:
        raise HTTPException(status_code=400, detail=f"OAuth not configured for {broker_id}")

    refresh_token_enc = conn.get("oauth_refresh_token_enc")
    if not refresh_token_enc:
        raise HTTPException(status_code=401, detail=f"No refresh token stored for {broker_id}. Please reconnect via OAuth.")

    refresh_token = decrypt_value(refresh_token_enc)
    cfg = OAUTH_CONFIGS[broker_id]
    client_id, client_secret = await _get_oauth_credentials(broker_id)
    if not client_id:
        raise HTTPException(status_code=500, detail=f"OAuth credentials not configured for {broker_id}")

    token_data = await _request_token_refresh(cfg, refresh_token, client_id, client_secret, broker_id, user_id)

    new_access = token_data.get("access_token", "")
    if not new_access:
        raise HTTPException(status_code=401, detail="Token refresh returned no access token.")

    new_refresh = token_data.get("refresh_token", refresh_token)
    expiry_seconds = token_data.get("expires_in", cfg.get("token_expiry_seconds", 3600))

    update = {
        "api_key_enc": encrypt_value(new_access),
        "oauth_refresh_token_enc": encrypt_value(new_refresh),
        "oauth_expires_at": datetime.now(timezone.utc),
        "oauth_token_expiry_seconds": expiry_seconds,
        "oauth_last_refreshed": datetime.now(timezone.utc),
        "last_used": datetime.now(timezone.utc),
    }

    await _log_token_rotation(user_id, broker_id, refresh_token, new_refresh, expiry_seconds)
    await db.broker_connections.update_one({"user_id": user_id, "broker_id": broker_id}, {"$set": update})
    logger.info(f"OAuth token rotated for user {user_id} -> {broker_id} (expires in {expiry_seconds}s)")

    conn.update(update)
    return conn


async def _get_or_refresh_client(user_id: str, broker_id: str, conn: dict):
    """Get broker client, auto-refreshing OAuth tokens if expired."""
    if conn.get("auth_method") == "oauth":
        expires_at = conn.get("oauth_expires_at")
        expiry_secs = conn.get("oauth_token_expiry_seconds", OAUTH_CONFIGS.get(broker_id, {}).get("token_expiry_seconds", 3600))

        if expires_at:
            # Mongo strips tzinfo on round-trip; ensure_utc() re-tags
            # the value so the subtraction below doesn't raise
            # `TypeError: can't subtract offset-naive and offset-aware
            # datetimes` and silently disable token refresh.
            expires_at = ensure_utc(expires_at)
        if expires_at:
            token_age = (datetime.now(timezone.utc) - expires_at).total_seconds()
            if token_age > expiry_secs * 0.8:  # Refresh at 80% of expiry
                logger.info(f"OAuth token nearing expiry for {broker_id}, refreshing...")
                conn = await _refresh_oauth_token(user_id, broker_id, conn)

    return _build_client(conn)


# ============================================================
# BROKER CONNECTION CRUD
# ============================================================

@router.get("/execution-status")
async def execution_status(request: Request):
    """Check if the current user has live trade execution privileges."""
    allowed = await _is_execution_allowed(request)
    return {"execution_allowed": allowed, "mode": "live" if allowed else "read_only"}


@router.post("/connect")
async def connect_broker(req: ConnectBrokerRequest, request: Request):
    """Connect a broker by saving encrypted API keys and validating the connection."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    # Admin-only brokers
    ADMIN_ONLY_BROKERS = {"kraken"}
    if req.broker_id in ADMIN_ONLY_BROKERS and user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail=f"{req.broker_id} is currently available to admin accounts only.")

    # Build a temporary client to validate credentials
    from services.broker_service import BrokerService
    credentials = {"api_key": req.api_key, "api_secret": req.api_secret, "paper": req.paper}
    try:
        client = BrokerService.get_broker_client(req.broker_id, credentials)
        account = await asyncio.to_thread(client.get_account)
        if not account:
            raise HTTPException(status_code=400, detail="Could not authenticate with broker. Please check your API keys.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Broker connect validation error: {e}")
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")

    # Encrypt and store
    doc = {
        "user_id": user_id,
        "broker_id": req.broker_id,
        "api_key_enc": encrypt_value(req.api_key),
        "api_secret_enc": encrypt_value(req.api_secret),
        "paper": req.paper,
        "is_active": True,
        "account_id": account.get("account_number", account.get("id", "N/A")),
        "connected_at": datetime.now(timezone.utc),
        "last_used": datetime.now(timezone.utc),
    }

    # Upsert (replace existing connection for same broker)
    await db.broker_connections.update_one(
        {"user_id": user_id, "broker_id": req.broker_id},
        {"$set": doc},
        upsert=True,
    )

    return {
        "status": "connected",
        "broker_id": req.broker_id,
        "account_id": doc["account_id"],
        "paper": req.paper,
        "message": f"Successfully connected to {req.broker_id}",
    }


@router.get("/connections")
async def list_connections(request: Request):
    """List all broker connections for the authenticated user, with optional account data."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    is_admin = user.get("role") in ("owner", "admin")
    cursor = db.broker_connections.find(
        {"user_id": user_id, "is_active": True},
        {"_id": 0, "api_key_enc": 0, "api_secret_enc": 0}
    )
    connections = []
    async for c in cursor:
        conn_data = dict(c)
        # For admin, try to fetch live account info
        if is_admin:
            try:
                full_conn = await db.broker_connections.find_one(
                    {"user_id": user_id, "broker_id": c["broker_id"], "is_active": True},
                    {"_id": 0}
                )
                if full_conn:
                    client = _build_client(full_conn)
                    account = await asyncio.to_thread(client.get_account)
                    if account:
                        conn_data["account"] = {
                            "equity": account.get("equity", 0),
                            "buying_power": account.get("buying_power", 0),
                            "portfolio_value": account.get("portfolio_value", 0),
                        }
            except Exception:
                pass
        connections.append(conn_data)
    return {"connections": connections}


@router.delete("/disconnect/{broker_id}")
async def disconnect_broker(broker_id: str, request: Request):
    """Disconnect (soft-delete) a broker connection."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    result = await db.broker_connections.update_one(
        {"user_id": user_id, "broker_id": broker_id},
        {"$set": {"is_active": False}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "disconnected", "broker_id": broker_id}



# ============================================================
# KRAKEN-SPECIFIC ENDPOINTS (Admin Only)
# ============================================================

@router.get("/kraken/balances")
async def kraken_balances(request: Request):
    """Get Kraken crypto balances. Admin only."""
    user = await _get_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    user_id = str(user["_id"]) if not isinstance(user["_id"], str) else user["_id"]
    conn = await _get_user_broker(user_id, "kraken")
    client = _build_client(conn)
    balances = await asyncio.to_thread(client.get_balances)
    return {"balances": balances, "count": len(balances)}


@router.get("/kraken/trades")
async def kraken_trade_history(request: Request, limit: int = 50):
    """Get Kraken trade history. Admin only."""
    user = await _get_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    user_id = str(user["_id"]) if not isinstance(user["_id"], str) else user["_id"]
    conn = await _get_user_broker(user_id, "kraken")
    client = _build_client(conn)
    trades = await asyncio.to_thread(client.get_trade_history, limit)
    return {"trades": trades, "count": len(trades)}


# ============================================================
# OAUTH 2.0 FLOW
# ============================================================

async def _get_oauth_credentials(broker_id: str) -> tuple:
    """Get OAuth client ID and secret — checks DB first, then env vars."""
    cfg = OAUTH_CONFIGS.get(broker_id)
    if not cfg:
        return "", ""

    # 1. Check DB for admin-configured credentials
    if db is not None:
        try:
            doc = await db.broker_oauth_config.find_one({"broker_id": broker_id})
            if doc and doc.get("client_id") and doc.get("client_secret_enc"):
                return doc["client_id"], decrypt_value(doc["client_secret_enc"])
        except Exception as e:
            logger.warning(f"DB OAuth config lookup failed for {broker_id}: {e}")

    # 2. Fall back to environment variables
    client_id = os.environ.get(cfg["client_id_env"], "")
    client_secret = os.environ.get(cfg["client_secret_env"], "")
    return client_id, client_secret


@router.get("/oauth/{broker_id}/status")
async def oauth_status(broker_id: str):
    """Check if OAuth is configured for a broker."""
    if broker_id not in OAUTH_CONFIGS:
        return {"available": False, "reason": "OAuth not supported for this broker"}
    cfg = OAUTH_CONFIGS[broker_id]
    client_id, _ = await _get_oauth_credentials(broker_id)
    return {
        "available": bool(client_id),
        "broker_id": broker_id,
        "configured": bool(client_id),
        "supports_pkce": cfg.get("supports_pkce", False),
    }


@router.get("/oauth/capabilities")
async def oauth_capabilities():
    """Public endpoint: OAuth capability report for broker compliance verification."""
    return {
        "three_legged_oauth": True,
        "authorization_code_grant": True,
        "refresh_token_rotation": True,
        "pkce_support": True,
        "csrf_state_validation": True,
        "token_encryption": "AES-256 (Fernet)",
        "supported_brokers": [
            {
                "broker_id": bid,
                "authorize_url": cfg["authorize_url"],
                "supports_pkce": cfg.get("supports_pkce", False),
                "token_expiry_seconds": cfg.get("token_expiry_seconds", 3600),
            }
            for bid, cfg in OAUTH_CONFIGS.items()
        ],
        "security_features": [
            "CSRF state tokens (one-time use)",
            "PKCE S256 code challenge for supported brokers",
            "Refresh token rotation on every refresh",
            "Token audit trail (oauth_token_audit collection)",
            "Encrypted credential storage (AES-256)",
            "Automatic token refresh at 80% expiry",
            "httpOnly secure cookies for session auth",
        ],
    }


def _resolve_origin(request: Request) -> str:
    """Resolve the origin from request headers for OAuth redirect URIs."""
    origin = request.headers.get("origin", "")
    if not origin:
        referer = request.headers.get("referer", "")
        if referer:
            from urllib.parse import urlparse
            parsed = urlparse(referer)
            origin = f"{parsed.scheme}://{parsed.netloc}"
        else:
            origin = str(request.base_url).rstrip("/")
    return origin


async def _exchange_oauth_code(cfg: dict, code: str, client_id: str, client_secret: str,
                                redirect_uri: str, pkce_verifier: str = None) -> dict:
    """Exchange an authorization code for OAuth tokens. Returns token_data dict or raises."""
    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }
    if pkce_verifier:
        token_payload["code_verifier"] = pkce_verifier

    resp = await asyncio.to_thread(
        http_requests.post, cfg["token_url"], data=token_payload, timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


async def _validate_oauth_account(broker_id: str, access_token: str) -> dict:
    """Validate an OAuth token by fetching the broker account. Returns account dict."""
    from services.broker_service import BrokerService
    credentials = {"api_key": access_token, "api_secret": "oauth", "paper": False, "oauth": True}
    client = BrokerService.get_broker_client(broker_id, credentials)
    account = await asyncio.to_thread(client.get_account)
    if not account:
        raise ValueError("Account fetch returned empty")
    return account


async def _store_oauth_connection(user_id: str, broker_id: str, token_data: dict,
                                   account: dict, pkce_used: bool) -> None:
    """Persist the OAuth connection with encrypted tokens to MongoDB."""
    cfg = OAUTH_CONFIGS[broker_id]
    expiry_seconds = token_data.get("expires_in", cfg.get("token_expiry_seconds", 3600))
    doc = {
        "user_id": user_id,
        "broker_id": broker_id,
        "api_key_enc": encrypt_value(token_data["access_token"]),
        "api_secret_enc": encrypt_value("oauth"),
        "paper": False,
        "is_active": True,
        "auth_method": "oauth",
        "oauth_refresh_token_enc": encrypt_value(token_data.get("refresh_token", "")),
        "oauth_expires_at": datetime.now(timezone.utc),
        "oauth_token_expiry_seconds": expiry_seconds,
        "oauth_last_refreshed": datetime.now(timezone.utc),
        "oauth_pkce_used": pkce_used,
        "account_id": account.get("account_number", account.get("id", "N/A")),
        "connected_at": datetime.now(timezone.utc),
        "last_used": datetime.now(timezone.utc),
    }
    await db.broker_connections.update_one(
        {"user_id": user_id, "broker_id": broker_id}, {"$set": doc}, upsert=True,
    )


@router.get("/oauth/{broker_id}/authorize")
async def oauth_authorize(broker_id: str, request: Request):
    """Start OAuth flow — returns the authorization URL for the frontend to redirect to."""
    if broker_id not in OAUTH_CONFIGS:
        raise HTTPException(status_code=400, detail=f"OAuth not supported for {broker_id}")

    cfg = OAUTH_CONFIGS[broker_id]
    client_id, _ = await _get_oauth_credentials(broker_id)
    if not client_id:
        raise HTTPException(status_code=500, detail=f"OAuth not configured for {broker_id}. Admin must set credentials in Settings.")

    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    # Generate a CSRF state token
    state = secrets.token_urlsafe(32)

    # PKCE support
    pkce_verifier = None
    pkce_params = ""
    if cfg.get("supports_pkce"):
        pkce_verifier, pkce_challenge = _generate_pkce()
        pkce_params = f"&code_challenge={pkce_challenge}&code_challenge_method=S256"

    await db.oauth_states.insert_one({
        "state": state,
        "user_id": user_id,
        "broker_id": broker_id,
        "pkce_verifier": pkce_verifier,
        "created_at": datetime.now(timezone.utc),
    })

    origin = _resolve_origin(request)
    redirect_uri = f"{origin}/api/broker/oauth/{broker_id}/callback"

    authorize_url = (
        f"{cfg['authorize_url']}?"
        f"response_type=code&"
        f"client_id={client_id}&"
        f"redirect_uri={redirect_uri}&"
        f"state={state}&"
        f"scope={cfg['scopes']}"
        f"{pkce_params}"
    )

    return {"authorize_url": authorize_url, "state": state}


@router.get("/oauth/{broker_id}/callback")
async def oauth_callback(broker_id: str, request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle OAuth callback — exchange code for tokens and store the connection."""
    if error:
        return RedirectResponse(url=f"/?broker_error={error}")
    if broker_id not in OAUTH_CONFIGS:
        return RedirectResponse(url="/?broker_error=unsupported_broker")
    if not code or not state:
        return RedirectResponse(url="/?broker_error=missing_code")

    state_doc = await db.oauth_states.find_one_and_delete({"state": state})
    if not state_doc:
        return RedirectResponse(url="/?broker_error=invalid_state")

    user_id = state_doc["user_id"]
    pkce_verifier = state_doc.get("pkce_verifier")
    cfg = OAUTH_CONFIGS[broker_id]
    client_id, client_secret = await _get_oauth_credentials(broker_id)

    redirect_uri = f"{_resolve_origin(request)}/api/broker/oauth/{broker_id}/callback"

    try:
        token_data = await _exchange_oauth_code(cfg, code, client_id, client_secret, redirect_uri, pkce_verifier)
    except Exception as e:
        logger.error(f"OAuth token exchange failed for {broker_id}: {e}")
        return RedirectResponse(url="/?broker_error=token_exchange_failed")

    access_token = token_data.get("access_token", "")
    if not access_token:
        return RedirectResponse(url="/?broker_error=no_access_token")

    try:
        account = await _validate_oauth_account(broker_id, access_token)
    except Exception as e:
        logger.error(f"OAuth account validation failed: {e}")
        return RedirectResponse(url="/?broker_error=validation_failed")

    await _store_oauth_connection(user_id, broker_id, token_data, account, bool(pkce_verifier))
    logger.info(f"OAuth connection established for user {user_id} -> {broker_id}")
    return RedirectResponse(url=f"/?broker_connected={broker_id}")


# ============================================================
# ACCOUNT & POSITIONS
# ============================================================

@router.get("/account/{broker_id}")
async def get_account(broker_id: str, request: Request):
    """Get account info for a connected broker."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = await _get_or_refresh_client(user_id, broker_id, conn)
    account = await asyncio.to_thread(client.get_account)
    if not account:
        raise HTTPException(status_code=502, detail="Failed to fetch account from broker")

    # Update last_used
    await db.broker_connections.update_one(
        {"user_id": user_id, "broker_id": broker_id},
        {"$set": {"last_used": datetime.now(timezone.utc)}}
    )

    return {
        "broker": broker_id,
        "account_id": account.get("account_number", account.get("id", "N/A")),
        "cash": float(account.get("cash", 0)),
        "buying_power": float(account.get("buying_power", 0)),
        "portfolio_value": float(account.get("portfolio_value", account.get("equity", 0))),
        "equity": float(account.get("equity", 0)),
        "status": account.get("status", "active"),
        "paper": conn.get("paper", True),
        "auth_method": conn.get("auth_method", "api_key"),
    }


@router.get("/positions/{broker_id}")
async def get_positions(broker_id: str, request: Request):
    """Get all positions for a connected broker."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = await _get_or_refresh_client(user_id, broker_id, conn)
    positions = await asyncio.to_thread(client.get_positions)
    formatted = []
    for p in (positions or []):
        formatted.append({
            "symbol": p.get("symbol", ""),
            "qty": float(p.get("qty", 0)),
            "side": p.get("side", "long"),
            "avg_entry_price": float(p.get("avg_entry_price", 0)),
            "current_price": float(p.get("current_price", 0)),
            "market_value": float(p.get("market_value", 0)),
            "unrealized_pl": float(p.get("unrealized_pl", 0)),
            "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
        })
    return {"positions": formatted, "total": len(formatted)}


# ============================================================
# ORDER MANAGEMENT
# ============================================================

async def _log_order(user_id: str, broker_id: str, req: PlaceOrderRequest, result: dict, *, proof_chain_entity_id: Optional[str] = None) -> None:
    """Log order to MongoDB and send notifications.

    ``proof_chain_entity_id`` is persisted alongside the order so the
    nightly position-reconciler (``services/position_reconciler.py``)
    can append OUTCOME_VERIFIED to the same proof chain when the
    broker reports the position has closed externally. Without this
    field the chain dead-ends at fill and Step-10 of the IP lifecycle
    can never close.
    """
    await db.trade_orders.insert_one({
        "user_id": user_id,
        "broker_id": broker_id,
        "broker_order_id": result.get("id", ""),
        "symbol": req.symbol.upper(),
        "side": req.side,
        "qty": req.quantity,
        "order_type": req.order_type,
        "status": result.get("status", "submitted"),
        "limit_price": req.limit_price,
        "stop_price": req.stop_price,
        "created_at": datetime.now(timezone.utc),
        "proof_chain_entity_id": proof_chain_entity_id,
        "outcome_appended": False,
    })
    try:
        from services.push_service import notify_trade_execution
        await notify_trade_execution(
            db, user_id, req.symbol.upper(), req.side,
            req.quantity, result.get("id", ""), broker_id,
            result.get("status", "submitted"),
        )
    except Exception as e:
        logger.warning(f"Trade notification failed (non-critical): {e}")


@router.post("/order/{broker_id}")
async def place_order(broker_id: str, req: PlaceOrderRequest, request: Request):
    """Place a trade order through a connected broker. Owner-only.

    Mode guard: caller must be in LIVE trading mode. PAPER users are
    rejected with 403 + a structured `detail.code = "wrong_mode"` so
    the frontend can render a "Switch to LIVE" CTA.
    """
    from services.trading_mode_guards import require_live_mode
    await require_live_mode(request)
    if not await _is_execution_allowed(request):
        raise HTTPException(status_code=403, detail="Live trade execution is restricted to authorized accounts. Your connection is read-only.")
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = await _get_or_refresh_client(user_id, broker_id, conn)

    result = await asyncio.to_thread(
        client.place_order,
        symbol=req.symbol, qty=req.quantity, side=req.side,
        order_type=req.order_type, time_in_force=req.time_in_force,
        limit_price=req.limit_price, stop_price=req.stop_price,
    )
    if not result:
        raise HTTPException(status_code=400, detail="Order rejected by broker")

    # ── Patent J/K/M/I — post-fill guard audit ───────────────────────
    # Run the manual-order guard *before* we persist the order row so
    # the entity_id can be written into ``trade_orders`` atomically.
    # The position reconciler later reads it back to append
    # OUTCOME_VERIFIED on close. Failure here is non-fatal — we log
    # and proceed with status=None so the order still records.
    proof_chain_entity_id = None
    try:
        _est_notional = float(req.quantity) * float(req.limit_price or 0.0)
        if _est_notional > 0:
            from services.manual_order_guard import run_manual_order_guard
            from server import db as _server_db
            _g = await run_manual_order_guard(
                db=_server_db,
                user=user,
                asset_class="equity",
                symbol=str(req.symbol),
                side=str(req.side),
                base_notional=_est_notional,
                context={
                    "route": "broker.place_order",
                    "broker_id": broker_id,
                    "order_id": result.get("id", ""),
                    "phase": "post_fill_audit",
                },
            )
            proof_chain_entity_id = (_g or {}).get("proof_chain_entity_id")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[manual_guard] post-fill audit failed (non-critical): {e}")

    await _log_order(user_id, broker_id, req, result, proof_chain_entity_id=proof_chain_entity_id)

    return {
        "status": result.get("status", "submitted"),
        "order_id": result.get("id", ""),
        "symbol": req.symbol.upper(),
        "side": req.side,
        "qty": req.quantity,
        "type": req.order_type,
        # Surfaces the IP entity_id so external reconcilers can later
        # link the broker fill back to its proof chain.
        "proof_chain_entity_id": proof_chain_entity_id,
    }


@router.get("/orders/{broker_id}")
async def get_orders(broker_id: str, request: Request, status: str = "all"):
    """Get order history from a connected broker."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = await _get_or_refresh_client(user_id, broker_id, conn)
    orders = await asyncio.to_thread(client.get_orders, status=status)
    formatted = []
    for o in (orders or []):
        formatted.append({
            "id": o.get("id", ""),
            "symbol": o.get("symbol", ""),
            "side": o.get("side", ""),
            "qty": o.get("qty", 0),
            "type": o.get("type", ""),
            "status": o.get("status", ""),
            "filled_qty": o.get("filled_qty", 0),
            "filled_avg_price": o.get("filled_avg_price"),
            "submitted_at": o.get("submitted_at", ""),
            "created_at": o.get("created_at", ""),
        })
    return {"orders": formatted, "total": len(formatted)}


@router.delete("/order/{broker_id}/{order_id}")
async def cancel_order(broker_id: str, order_id: str, request: Request):
    """Cancel a pending order. Owner-only.

    Mode guard: cancelling LIVE orders requires the caller to be in
    LIVE mode (mirrors the gate on /order/{broker_id} POST).
    """
    from services.trading_mode_guards import require_live_mode
    await require_live_mode(request)
    if not await _is_execution_allowed(request):
        raise HTTPException(status_code=403, detail="Live trade execution is restricted to authorized accounts.")
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = await _get_or_refresh_client(user_id, broker_id, conn)
    success = await asyncio.to_thread(client.cancel_order, order_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to cancel order")
    return {"status": "cancelled", "order_id": order_id}


# ============================================================
# PORTFOLIO SYNC HELPERS
# ============================================================

async def _sync_watchlist(user_id: str, symbols: list[str]) -> None:
    """Sync position symbols into the user's watchlist."""
    if not symbols:
        return
    existing = await db.watchlists.find_one({"user_id": user_id})
    if existing:
        new_symbols = list(set(existing.get("symbols", [])) | set(symbols))
        await db.watchlists.update_one(
            {"user_id": user_id},
            {"$set": {"symbols": new_symbols, "updated_at": datetime.now(timezone.utc)}}
        )
    else:
        await db.watchlists.insert_one({
            "user_id": user_id,
            "symbols": symbols,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        })


async def _store_portfolio_snapshot(
    user_id: str, broker_id: str, positions: list, total_value: float, total_pl: float
) -> None:
    """Persist a portfolio snapshot to the database."""
    await db.portfolio_snapshots.update_one(
        {"user_id": user_id, "broker_id": broker_id},
        {"$set": {
            "user_id": user_id,
            "broker_id": broker_id,
            "positions": [{
                "symbol": p.get("symbol", ""),
                "qty": float(p.get("qty", 0)),
                "avg_entry": float(p.get("avg_entry_price", 0)),
                "current_price": float(p.get("current_price", 0)),
                "market_value": float(p.get("market_value", 0)),
                "unrealized_pl": float(p.get("unrealized_pl", 0)),
            } for p in (positions or [])],
            "total_value": total_value,
            "total_unrealized_pl": total_pl,
            "synced_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


# ============================================================
# PORTFOLIO SYNC
# ============================================================

@router.get("/portfolio-sync/{broker_id}")
async def portfolio_sync(broker_id: str, request: Request):
    """Sync broker positions with RISEDUAL AI's strategy and watchlist system."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    conn = await _get_user_broker(user_id, broker_id)
    client = _build_client(conn)

    account, positions, orders = await asyncio.gather(
        asyncio.to_thread(client.get_account),
        asyncio.to_thread(client.get_positions),
        asyncio.to_thread(client.get_orders, status="all"),
    )

    position_symbols = [p.get("symbol", "") for p in (positions or [])]
    total_value = float(account.get("portfolio_value", account.get("equity", 0))) if account else 0
    total_pl = sum(float(p.get("unrealized_pl", 0)) for p in (positions or []))

    await asyncio.gather(
        _sync_watchlist(user_id, position_symbols),
        _store_portfolio_snapshot(user_id, broker_id, positions, total_value, total_pl),
    )

    return {
        "synced": True,
        "broker_id": broker_id,
        "positions_count": len(positions or []),
        "symbols_synced": position_symbols,
        "total_portfolio_value": total_value,
        "total_unrealized_pl": total_pl,
        "open_orders": len([o for o in (orders or []) if o.get("status") in ("new", "accepted", "pending_new")]),
    }


# ============================================================
# REAL-TIME P&L TRACKER
# ============================================================

@router.get("/pnl-summary")
async def get_pnl_summary(request: Request):
    """Aggregate real-time P&L across all connected brokers."""
    user = await _get_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    cursor = db.broker_connections.find(
        {"user_id": user_id, "is_active": True},
        {"_id": 0}
    )
    connections = []
    async for c in cursor:
        connections.append(c)

    if not connections:
        return {
            "total_value": 0, "total_pl": 0, "total_pl_pct": 0,
            "day_pl": 0, "brokers": [], "positions": [], "sector_allocation": [],
        }

    brokers, all_positions, total_value, total_pl, total_cost = await _fetch_all_broker_data(connections)

    sector_map = _classify_sectors(all_positions)
    total_pl_pct = round((total_pl / total_cost) * 100, 2) if total_cost > 0 else 0
    all_positions.sort(key=lambda p: abs(p["unrealized_pl"]), reverse=True)

    return {
        "total_value": round(total_value, 2),
        "total_pl": round(total_pl, 2),
        "total_pl_pct": total_pl_pct,
        "total_cost_basis": round(total_cost, 2),
        "brokers": brokers,
        "positions": all_positions,
        "sector_allocation": sector_map,
        "positions_count": len(all_positions),
    }


async def _fetch_all_broker_data(connections: list) -> tuple:
    """Fetch positions from all broker connections and aggregate data."""
    brokers = []
    all_positions = []
    total_value = 0
    total_pl = 0
    total_cost = 0

    for conn in connections:
        try:
            client = _build_client(conn)
            account = await asyncio.to_thread(client.get_account)
            positions = await asyncio.to_thread(client.get_positions)

            broker_value = float(account.get("portfolio_value", account.get("equity", 0))) if account else 0
            broker_cash = float(account.get("cash", 0)) if account else 0
            broker_pl, pos_cost, pos_list = _process_positions(positions, conn["broker_id"])

            total_cost += pos_cost
            total_value += broker_value
            total_pl += broker_pl
            all_positions.extend(pos_list)

            brokers.append({
                "broker_id": conn["broker_id"],
                "account_id": conn.get("account_id", ""),
                "paper": conn.get("paper", True),
                "portfolio_value": round(broker_value, 2),
                "cash": round(broker_cash, 2),
                "unrealized_pl": round(broker_pl, 2),
                "positions_count": len(positions or []),
            })
        except Exception as e:
            logger.warning(f"P&L fetch error for {conn['broker_id']}: {e}")
            brokers.append({"broker_id": conn["broker_id"], "error": str(e)})

    return brokers, all_positions, total_value, total_pl, total_cost


def _process_positions(positions: list, broker_id: str) -> tuple:
    """Extract and format position data, returning (broker_pl, cost, position_list)."""
    broker_pl = 0
    cost = 0
    result = []
    for p in (positions or []):
        qty = float(p.get("qty", 0))
        avg_entry = float(p.get("avg_entry_price", 0))
        current = float(p.get("current_price", 0))
        mkt_val = float(p.get("market_value", 0))
        unrealized = float(p.get("unrealized_pl", 0))
        unrealized_pct = float(p.get("unrealized_plpc", 0))
        cost_basis = qty * avg_entry

        broker_pl += unrealized
        cost += cost_basis

        result.append({
            "symbol": p.get("symbol", ""),
            "broker": broker_id,
            "qty": qty,
            "avg_entry": round(avg_entry, 2),
            "current_price": round(current, 2),
            "market_value": round(mkt_val, 2),
            "unrealized_pl": round(unrealized, 2),
            "unrealized_pl_pct": round(unrealized_pct * 100, 2),
            "cost_basis": round(cost_basis, 2),
            "side": p.get("side", "long"),
        })
    return broker_pl, cost, result


def _classify_sectors(positions: list) -> list:
    """Simple sector classification based on well-known tickers."""
    sector_lookup = {
        "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology", "GOOG": "Technology",
        "AMZN": "Consumer Disc.", "TSLA": "Consumer Disc.", "NKE": "Consumer Disc.",
        "META": "Communication", "NFLX": "Communication", "DIS": "Communication",
        "JPM": "Financials", "BAC": "Financials", "GS": "Financials", "V": "Financials",
        "JNJ": "Healthcare", "UNH": "Healthcare", "PFE": "Healthcare", "ABBV": "Healthcare",
        "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
        "PG": "Consumer Staples", "KO": "Consumer Staples", "PEP": "Consumer Staples",
        "CAT": "Industrials", "BA": "Industrials", "HON": "Industrials",
        "NVDA": "Technology", "AMD": "Technology", "INTC": "Technology", "CRM": "Technology",
        "SPY": "Index", "QQQ": "Index", "IWM": "Index", "VOO": "Index",
    }

    sectors = {}
    for p in positions:
        sector = sector_lookup.get(p["symbol"], "Other")
        if sector not in sectors:
            sectors[sector] = {"name": sector, "value": 0, "pl": 0, "count": 0}
        sectors[sector]["value"] += p["market_value"]
        sectors[sector]["pl"] += p["unrealized_pl"]
        sectors[sector]["count"] += 1

    total = sum(s["value"] for s in sectors.values())
    result = []
    for s in sectors.values():
        s["pct"] = round((s["value"] / total) * 100, 1) if total > 0 else 0
        s["value"] = round(s["value"], 2)
        s["pl"] = round(s["pl"], 2)
        result.append(s)

    result.sort(key=lambda s: s["value"], reverse=True)
    return result
