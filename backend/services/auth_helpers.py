"""Shared authentication helpers — extracted to break circular imports between auth ↔ referral."""
import os
import jwt
from datetime import datetime, timezone
from typing import Any
from fastapi import HTTPException, Request
from bson import ObjectId

JWT_ALGORITHM = "HS256"

# Will be set by server.py
db = None

def set_db(database: Any) -> None:
    global db
    db = database

def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]

async def get_current_user(request: Request) -> dict:
    """Extract and verify JWT, return user dict."""
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user["_id"] = str(user["_id"])
        user.pop("password_hash", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_optional_user(request: Request) -> dict | None:
    """Returns user dict or None (no error if not logged in)."""
    try:
        return await get_current_user(request)
    except HTTPException:
        return None

def is_pro_user(user: dict) -> bool:
    """Check if user has Pro access (pro subscription or active trial)."""
    if not user:
        return False
    status = user.get("subscription_status", "free")
    if status == "pro":
        return True
    if status == "trial":
        expires = user.get("trial_expires_at")
        if expires:
            if isinstance(expires, str):
                try:
                    expires = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                except ValueError:
                    return False
            return expires > datetime.now(timezone.utc)
    return False


async def enforce_credits(user: dict, action: str) -> None:
    """Deduct credits for a given action. Raises HTTPException(402) if insufficient.
    No-op if user is None (unauthenticated). Pro users get certain actions free."""
    if not user:
        return
    from services.credit_service import deduct_credits, get_user_plan
    plan_key = get_user_plan(user)
    cr = await deduct_credits(str(user["_id"]), action, plan_key)
    if not cr["allowed"]:
        raise HTTPException(
            status_code=402,
            detail=cr.get("error", "Not enough credits"),
        )
