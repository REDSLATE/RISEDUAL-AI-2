import os
import logging
import bcrypt
import jwt
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr
from bson import ObjectId

JWT_ALGORITHM = "HS256"

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

# Will be set by server.py
db = None

def set_db(database):
    global db
    db = database

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

def get_jwt_secret():
    return os.environ["JWT_SECRET"]

def create_access_token(user_id: str, email: str) -> str:
    payload = {"sub": user_id, "email": email, "exp": datetime.now(timezone.utc) + timedelta(minutes=15), "type": "access"}
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    payload = {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(days=7), "type": "refresh"}
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

def set_auth_cookies(response: Response, access_token: str, refresh_token: str):
    is_secure = os.environ.get("FRONTEND_URL", "").startswith("https")
    # SameSite=none allows cookies on cross-origin requests (deployed domain ≠ preview domain).
    # Requires Secure=True (HTTPS). HttpOnly prevents JS access; CSRF mitigated by POST-only mutations.
    samesite_val = "none" if is_secure else "lax"
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=is_secure, samesite=samesite_val, max_age=900, path="/")
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True, secure=is_secure, samesite=samesite_val, max_age=604800, path="/")

async def get_current_user(request: Request) -> dict:
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

async def get_optional_user(request: Request):
    """Returns user dict or None (no error if not logged in)"""
    try:
        return await get_current_user(request)
    except HTTPException:
        return None

# Brute force check
async def check_brute_force(identifier: str):
    record = await db.login_attempts.find_one({"identifier": identifier})
    if record and record.get("attempts", 0) >= 5:
        locked_until = record.get("locked_until")
        if locked_until and locked_until > datetime.now(timezone.utc):
            raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")
        elif locked_until and locked_until <= datetime.now(timezone.utc):
            await db.login_attempts.delete_one({"identifier": identifier})

async def record_failed_attempt(identifier: str):
    record = await db.login_attempts.find_one({"identifier": identifier})
    attempts = (record.get("attempts", 0) if record else 0) + 1
    update = {"$set": {"attempts": attempts, "last_attempt": datetime.now(timezone.utc)}}
    if attempts >= 5:
        update["$set"]["locked_until"] = datetime.now(timezone.utc) + timedelta(minutes=15)
    await db.login_attempts.update_one({"identifier": identifier}, update, upsert=True)

def user_response(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "email": user["email"],
        "name": user.get("name", ""),
        "role": user.get("role", "user"),
        "subscription_status": user.get("subscription_status", "free"),
        "is_active": user.get("is_active", True),
        "trial_ends_at": user.get("trial_ends_at"),
    }

# --- Models ---
class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""
    ref_code: str = ""

class LoginRequest(BaseModel):
    email: str
    password: str

class ForgotPasswordRequest(BaseModel):
    email: str
    origin_url: Optional[str] = None

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

class RedeemBetaKeyRequest(BaseModel):
    beta_key: str
    email: str
    password: str
    name: str = ""

# --- Routes ---
@auth_router.post("/register")
async def register(req: RegisterRequest, response: Response):
    email = req.email.strip().lower()
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user_doc = {
        "email": email,
        "password_hash": hash_password(req.password),
        "name": req.name.strip() or email.split("@")[0],
        "role": "user",
        "subscription_status": "free",
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.users.insert_one(user_doc)
    user_doc["_id"] = result.inserted_id

    # Process referral code if provided
    if req.ref_code and req.ref_code.strip():
        try:
            from routes.referral import process_referral_signup
            await process_referral_signup(str(user_doc["_id"]), email, req.ref_code.strip())
            # Reload user doc to reflect trial status
            updated = await db.users.find_one({"_id": user_doc["_id"]})
            if updated:
                user_doc["subscription_status"] = updated.get("subscription_status", "free")
        except Exception as e:
            logging.warning(f"Referral processing error: {e}")

    access = create_access_token(str(user_doc["_id"]), email)
    refresh = create_refresh_token(str(user_doc["_id"]))
    set_auth_cookies(response, access, refresh)
    resp = user_response(user_doc)
    resp["access_token"] = access
    resp["refresh_token"] = refresh
    return resp

async def _validate_beta_key(beta_key: str, email: str) -> dict:
    """Validate a beta key and email for redemption. Returns waitlist entry or raises."""
    waitlist_entry = await db.waitlist.find_one({"beta_key": beta_key, "status": "invited"}, {"_id": 0})
    if not waitlist_entry:
        raise HTTPException(status_code=400, detail="Invalid or already used beta key")

    expires = waitlist_entry.get("beta_key_expires", "")
    if expires:
        from dateutil.parser import parse as parse_date
        try:
            if parse_date(expires) < datetime.now(timezone.utc):
                raise HTTPException(status_code=400, detail="Beta key has expired. Contact support for a new one.")
        except (ValueError, TypeError):
            pass

    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered. Log in instead.")

    return waitlist_entry


@auth_router.post("/redeem-beta-key")
async def redeem_beta_key(req: RedeemBetaKeyRequest, response: Response):
    """Redeem a beta access key to create an account with Pro trial access."""
    email = req.email.strip().lower()
    beta_key = req.beta_key.strip().upper()

    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    waitlist_entry = await _validate_beta_key(beta_key, email)

    user_doc = {
        "email": email,
        "password_hash": hash_password(req.password),
        "name": req.name.strip() or email.split("@")[0],
        "role": "user",
        "subscription_status": "pro",
        "beta_access": True,
        "beta_key": beta_key,
        "founding_member": waitlist_entry.get("founding_member", False),
        "trial_ends_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.users.insert_one(user_doc)
    user_doc["_id"] = result.inserted_id

    await db.waitlist.update_one(
        {"beta_key": beta_key},
        {"$set": {"status": "active", "activated_at": datetime.now(timezone.utc).isoformat(), "activated_email": email}},
    )

    access = create_access_token(str(user_doc["_id"]), email)
    refresh = create_refresh_token(str(user_doc["_id"]))
    set_auth_cookies(response, access, refresh)
    resp = user_response(user_doc)
    resp["access_token"] = access
    resp["refresh_token"] = refresh
    resp["beta_activated"] = True
    resp["founding_member"] = waitlist_entry.get("founding_member", False)
    return resp

@auth_router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    email = req.email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    identifier = f"{ip}:{email}"
    await check_brute_force(identifier)
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(req.password, user["password_hash"]):
        await record_failed_attempt(identifier)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Your account has been deactivated. Contact support.")
    await db.login_attempts.delete_one({"identifier": identifier})
    access = create_access_token(str(user["_id"]), email)
    refresh = create_refresh_token(str(user["_id"]))
    set_auth_cookies(response, access, refresh)
    resp = user_response(user)
    resp["access_token"] = access
    resp["refresh_token"] = refresh
    return resp

@auth_router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"message": "Logged out"}

@auth_router.get("/me")
async def me(request: Request):
    user = await get_current_user(request)
    return user_response({"_id": user["_id"], **user})


async def _extract_refresh_token(request: Request) -> str:
    """Extract refresh token from cookie, body, or Authorization header."""
    token = request.cookies.get("refresh_token")
    if token:
        return token
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    token = body.get("refresh_token") or ""
    if token:
        return token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return ""


@auth_router.post("/refresh")
async def refresh_token(request: Request, response: Response):
    token = await _extract_refresh_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        access = create_access_token(str(user["_id"]), user["email"])
        is_secure = os.environ.get("FRONTEND_URL", "").startswith("https")
        samesite_val = "none" if is_secure else "lax"
        response.set_cookie(key="access_token", value=access, httponly=True, secure=is_secure, samesite=samesite_val, max_age=900, path="/")
        return {"access_token": access}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

@auth_router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    email = req.email.strip().lower()
    user = await db.users.find_one({"email": email})
    if not user:
        return {"message": "If that email exists, a reset link has been sent."}
    token = secrets.token_urlsafe(32)
    await db.password_reset_tokens.insert_one({
        "token": token,
        "user_id": user["_id"],
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "used": False,
    })
    # Send the reset email via Resend
    try:
        from services.email_service import send_password_reset_email
        await send_password_reset_email(email, token, origin_url=req.origin_url)
    except Exception as e:
        logging.error(f"Failed to send password reset email: {e}")
    return {"message": "If that email exists, a reset link has been sent."}

@auth_router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    record = await db.password_reset_tokens.find_one({"token": req.token, "used": False})
    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    expires_at = record["expires_at"].replace(tzinfo=timezone.utc) if record["expires_at"].tzinfo is None else record["expires_at"]
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    await db.users.update_one({"_id": record["user_id"]}, {"$set": {"password_hash": hash_password(req.new_password)}})
    await db.password_reset_tokens.update_one({"_id": record["_id"]}, {"$set": {"used": True}})
    return {"message": "Password reset successful"}

# --- Admin Seeding ---
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "managingdirector@redslateholdings.com")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD")

async def seed_admin():
    # Seed original admin
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@risedual.ai")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if not admin_password:
        logging.warning("ADMIN_PASSWORD not set in .env, skipping admin seed")
        return
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": "Admin",
            "role": "admin",
            "subscription_status": "pro",
            "created_at": datetime.now(timezone.utc),
        })
    elif not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one({"email": admin_email}, {"$set": {"password_hash": hash_password(admin_password)}})

    # Seed REDSLATE owner
    if not OWNER_PASSWORD:
        logging.warning("OWNER_PASSWORD not set in .env, skipping owner seed")
        return
    existing_owner = await db.users.find_one({"email": OWNER_EMAIL})
    if not existing_owner:
        await db.users.insert_one({
            "email": OWNER_EMAIL,
            "password_hash": hash_password(OWNER_PASSWORD),
            "name": "REDSLATE",
            "role": "owner",
            "subscription_status": "pro",
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
        })
    else:
        updates = {"role": "owner", "subscription_status": "pro", "name": "REDSLATE"}
        if not verify_password(OWNER_PASSWORD, existing_owner["password_hash"]):
            updates["password_hash"] = hash_password(OWNER_PASSWORD)
        await db.users.update_one({"email": OWNER_EMAIL}, {"$set": updates})

async def create_indexes():
    await db.users.create_index("email", unique=True)
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.login_attempts.create_index("identifier")
    await db.waitlist.create_index("email", unique=True, name="unique_email")
    await db.waitlist.create_index("referral_code", unique=True, name="unique_referral_code")

# --- Owner-only guard ---
async def require_owner(request: Request):
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user

# --- Admin Routes (Owner Only) ---
@auth_router.get("/admin/users")
async def list_users(request: Request):
    await require_owner(request)
    cursor = db.users.find({}, {"password_hash": 0}).limit(200)
    users = []
    async for u in cursor:
        u["_id"] = str(u["_id"])
        users.append(u)
    return {"users": users, "total": len(users)}

@auth_router.post("/admin/users/{user_id}/activate")
async def activate_user(user_id: str, request: Request):
    await require_owner(request)
    result = await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_active": True}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User activated"}

@auth_router.post("/admin/users/{user_id}/deactivate")
async def deactivate_user(user_id: str, request: Request):
    await require_owner(request)
    target = await db.users.find_one({"_id": ObjectId(user_id)})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == "owner":
        raise HTTPException(status_code=403, detail="Cannot deactivate owner account")
    await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_active": False}})
    return {"message": "User deactivated"}

@auth_router.post("/admin/users/{user_id}/grant-pro")
async def grant_pro(user_id: str, request: Request):
    await require_owner(request)
    result = await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"subscription_status": "pro"}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "Pro access granted"}

@auth_router.post("/admin/users/{user_id}/revoke-pro")
async def revoke_pro(user_id: str, request: Request):
    await require_owner(request)
    target = await db.users.find_one({"_id": ObjectId(user_id)})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == "owner":
        raise HTTPException(status_code=403, detail="Cannot revoke owner's Pro access")
    await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"subscription_status": "free"}})
    return {"message": "Pro access revoked"}
