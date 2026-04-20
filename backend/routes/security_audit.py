"""Security Audit Dashboard — routes for viewing OAuth token rotations, failed login attempts, and API usage stats."""
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/security", tags=["security-audit"])

db = None

def set_db(database):
    global db
    db = database


async def _require_admin(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/overview")
async def security_overview(request: Request):
    """High-level security stats for the dashboard."""
    await _require_admin(request)

    now = datetime.now(timezone.utc)
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)

    # Failed login attempts (last 24h and 7d)
    failed_24h = await db.login_attempts.count_documents(
        {"last_attempt": {"$gte": last_24h}}
    )
    failed_7d = await db.login_attempts.count_documents(
        {"last_attempt": {"$gte": last_7d}}
    )

    # Currently locked accounts
    locked = await db.login_attempts.count_documents(
        {"locked_until": {"$gte": now}}
    )

    # OAuth token rotations (last 24h and 7d)
    rotations_24h = await db.oauth_token_audit.count_documents(
        {"timestamp": {"$gte": last_24h}}
    )
    rotations_7d = await db.oauth_token_audit.count_documents(
        {"timestamp": {"$gte": last_7d}}
    )

    # Active broker connections
    active_connections = await db.broker_connections.count_documents(
        {"is_active": True}
    )

    # Total users
    total_users = await db.users.count_documents({})
    pro_users = await db.users.count_documents({"subscription_status": "pro"})

    return {
        "failed_logins_24h": failed_24h,
        "failed_logins_7d": failed_7d,
        "locked_accounts": locked,
        "oauth_rotations_24h": rotations_24h,
        "oauth_rotations_7d": rotations_7d,
        "active_broker_connections": active_connections,
        "total_users": total_users,
        "pro_users": pro_users,
        "checked_at": now.isoformat(),
    }


@router.get("/failed-logins")
async def failed_logins(request: Request, limit: int = 50):
    """Recent failed login attempts with brute-force lockout info."""
    await _require_admin(request)

    cursor = db.login_attempts.find(
        {}, {"_id": 0}
    ).sort("last_attempt", -1).limit(limit)

    attempts = []
    now = datetime.now(timezone.utc)
    async for doc in cursor:
        locked_until = doc.get("locked_until")
        # MongoDB returns naive datetimes — normalize to UTC-aware so we can
        # compare against `now` (which is tz-aware) without TypeError.
        if isinstance(locked_until, datetime) and locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        is_locked = bool(locked_until) and isinstance(locked_until, datetime) and locked_until > now
        attempts.append({
            "identifier": doc.get("identifier", "unknown"),
            "attempts": doc.get("attempts", 0),
            "last_attempt": doc.get("last_attempt", "").isoformat() if hasattr(doc.get("last_attempt", ""), "isoformat") else str(doc.get("last_attempt", "")),
            "locked_until": locked_until.isoformat() if locked_until and hasattr(locked_until, "isoformat") else None,
            "is_locked": is_locked,
        })

    return {"failed_logins": attempts, "count": len(attempts)}


@router.get("/oauth-rotations")
async def oauth_rotations(request: Request, limit: int = 50):
    """Recent OAuth token rotation audit log."""
    await _require_admin(request)

    cursor = db.oauth_token_audit.find(
        {}, {"_id": 0}
    ).sort("timestamp", -1).limit(limit)

    rotations = []
    async for doc in cursor:
        rotations.append({
            "user_id": doc.get("user_id", ""),
            "broker_id": doc.get("broker_id", ""),
            "event": doc.get("event", ""),
            "old_refresh_hash": doc.get("old_refresh_hash", ""),
            "new_refresh_hash": doc.get("new_refresh_hash", ""),
            "expires_in": doc.get("expires_in", 0),
            "timestamp": doc.get("timestamp", "").isoformat() if hasattr(doc.get("timestamp", ""), "isoformat") else str(doc.get("timestamp", "")),
        })

    return {"rotations": rotations, "count": len(rotations)}


@router.get("/broker-connections")
async def broker_connections(request: Request):
    """Active broker connections with auth method and last usage info."""
    await _require_admin(request)

    cursor = db.broker_connections.find(
        {"is_active": True},
        {"_id": 0, "api_key_enc": 0, "api_secret_enc": 0, "oauth_refresh_token_enc": 0}
    )

    connections = []
    async for doc in cursor:
        connections.append({
            "user_id": doc.get("user_id", ""),
            "broker_id": doc.get("broker_id", ""),
            "auth_method": doc.get("auth_method", "api_key"),
            "paper": doc.get("paper", True),
            "account_id": doc.get("account_id", "N/A"),
            "oauth_pkce_used": doc.get("oauth_pkce_used", False),
            "connected_at": doc.get("connected_at", "").isoformat() if hasattr(doc.get("connected_at", ""), "isoformat") else str(doc.get("connected_at", "")),
            "last_used": doc.get("last_used", "").isoformat() if hasattr(doc.get("last_used", ""), "isoformat") else str(doc.get("last_used", "")),
        })

    return {"connections": connections, "count": len(connections)}


@router.post("/unlock/{identifier}")
async def unlock_account(identifier: str, request: Request):
    """Manually unlock a brute-force locked account."""
    await _require_admin(request)

    result = await db.login_attempts.delete_one({"identifier": identifier})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No lock record found for this identifier")

    logger.info(f"Admin manually unlocked: {identifier}")
    return {"status": "unlocked", "identifier": identifier}
