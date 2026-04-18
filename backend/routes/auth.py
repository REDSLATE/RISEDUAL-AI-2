import os
import logging
import bcrypt
import jwt
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
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
    try:
        record = await db.login_attempts.find_one({"identifier": identifier})
        if record and record.get("attempts", 0) >= 5:
            locked_until = record.get("locked_until")
            if locked_until:
                # Normalize to timezone-aware UTC (MongoDB may return naive datetimes)
                if hasattr(locked_until, 'tzinfo') and locked_until.tzinfo is None:
                    locked_until = locked_until.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if locked_until > now:
                    raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")
                else:
                    await db.login_attempts.delete_one({"identifier": identifier})
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"check_brute_force error for {identifier}: {e}")
        # Clear potentially corrupt record so user isn't permanently locked
        try:
            await db.login_attempts.delete_one({"identifier": identifier})
        except Exception:
            pass

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
        "founding_member": user.get("founding_member", False),
        "beta_access": user.get("beta_access", False),
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
    try:
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

        # Grant signup bonus credits
        try:
            from services.credit_service import grant_signup_bonus
            await grant_signup_bonus(str(user_doc["_id"]))
        except Exception as e:
            logging.warning(f"Signup credit grant error: {e}")

        # Process referral code if provided
        if req.ref_code and req.ref_code.strip():
            try:
                from routes.referral import process_referral_signup
                await process_referral_signup(str(user_doc["_id"]), email, req.ref_code.strip())
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

    except HTTPException:
        raise
    except Exception:
        logging.exception("Register route failed")
        raise HTTPException(status_code=500, detail="Registration failed")

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
    try:
        email = req.email.strip().lower()
        ip = request.client.host if request.client else "unknown"
        identifier = f"{ip}:{email}"
        await check_brute_force(identifier)
        user = await db.users.find_one({"email": email})
        if not user:
            await record_failed_attempt(identifier)
            raise HTTPException(status_code=401, detail="Invalid email or password")
        pw_hash = user.get("password_hash")
        if not pw_hash:
            logging.error(f"Login failed: user {email} has no password_hash field")
            raise HTTPException(status_code=401, detail="Invalid email or password")
        try:
            pw_match = verify_password(req.password, pw_hash)
        except Exception as e:
            logging.error(f"Password verify error for {email}: {e}")
            pw_match = False
        if not pw_match:
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

    except HTTPException:
        raise
    except Exception:
        logging.exception("Login route failed")
        raise HTTPException(status_code=500, detail="Login failed")

@auth_router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"message": "Logged out"}

@auth_router.get("/me")
async def me(request: Request):
    try:
        user = await get_current_user(request)
        return user_response({"_id": user["_id"], **user})
    except HTTPException:
        raise
    except Exception:
        logging.exception("Me route failed")
        raise HTTPException(status_code=500, detail="Failed to get user info")


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
    try:
        token = await _extract_refresh_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="No refresh token")
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
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    except Exception:
        logging.exception("Refresh route failed")
        raise HTTPException(status_code=500, detail="Token refresh failed")

@auth_router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    try:
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
        try:
            from services.email_service import send_password_reset_email
            await send_password_reset_email(email, token, origin_url=req.origin_url)
        except Exception as e:
            logging.error(f"Failed to send password reset email: {e}")
        return {"message": "If that email exists, a reset link has been sent."}

    except HTTPException:
        raise
    except Exception:
        logging.exception("Forgot password route failed")
        raise HTTPException(status_code=500, detail="Password reset request failed")

@auth_router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    try:
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

    except HTTPException:
        raise
    except Exception:
        logging.exception("Reset password route failed")
        raise HTTPException(status_code=500, detail="Password reset failed")

# --- Admin Seeding ---
# Hard-deny list: emails that must NEVER be seeded as owner regardless of env.
# The Red Slate Holdings account was the historical owner — user directive in
# Feb 2026 permanently removed it. If a stale env var still points here,
# override it.
_BANNED_OWNER_EMAILS = {"managingdirector@redslateholdings.com"}
_CANONICAL_OWNER_EMAIL = "admin@risedual.ai"


def _resolve_owner_email() -> str:
    """Return the owner email, forcing the canonical one if env is stale."""
    env_val = os.environ.get("OWNER_EMAIL", _CANONICAL_OWNER_EMAIL).strip().lower()
    if env_val in _BANNED_OWNER_EMAILS or not env_val:
        logging.warning(
            f"OWNER_EMAIL env var points at banned/empty value ({env_val!r}); "
            f"overriding to canonical {_CANONICAL_OWNER_EMAIL}."
        )
        return _CANONICAL_OWNER_EMAIL
    return env_val


OWNER_EMAIL = _resolve_owner_email()
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD")

async def seed_admin():
    """Seed the single RISEDUAL owner account.

    Historical note: there used to be two separate seed blocks — `admin` and
    `owner` (Red Slate Holdings). The Red Slate account was deactivated by
    user directive in Feb 2026, and we consolidated to a single owner at
    `admin@risedual.ai`. The cleanup below also deletes any lingering
    `role: merged` Red Slate row on production so the bug where the only
    `role: owner` user was deactivated (blocking broker live trades) cannot
    recur.
    """
    # One-shot cleanup: remove every banned-owner row (e.g. Red Slate Holdings).
    # Intentionally unconditional on role — these accounts must never exist
    # going forward. Safe on every startup: no-op once gone.
    for banned in _BANNED_OWNER_EMAILS:
        try:
            res = await db.users.delete_one({"email": banned})
            if res.deleted_count:
                logging.info(f"Seed cleanup: removed banned owner row {banned}.")
        except Exception as e:
            logging.warning(f"Seed cleanup for {banned} failed (non-critical): {e}")

    # Seed RISEDUAL owner
    if not OWNER_PASSWORD:
        logging.warning("OWNER_PASSWORD not set in .env, skipping owner seed")
        return
    existing_owner = await db.users.find_one({"email": OWNER_EMAIL})
    if not existing_owner:
        await db.users.insert_one({
            "email": OWNER_EMAIL,
            "password_hash": hash_password(OWNER_PASSWORD),
            "name": "RISEDUAL",
            "role": "owner",
            "subscription_status": "pro",
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
        })
    else:
        # Always promote to owner — this is the canonical single-admin account.
        updates = {
            "role": "owner",
            "subscription_status": "pro",
            "name": "RISEDUAL",
            "is_active": True,
        }
        existing_hash = existing_owner.get("password_hash")
        needs_rehash = True
        if existing_hash:
            try:
                needs_rehash = not verify_password(OWNER_PASSWORD, existing_hash)
            except Exception as e:
                logging.warning(f"Owner password verify failed during seed: {e}")
        if needs_rehash:
            updates["password_hash"] = hash_password(OWNER_PASSWORD)
        await db.users.update_one({"email": OWNER_EMAIL}, {"$set": updates})

    # Ensure owner has a credit wallet (Pro Max allocation)
    user = await db.users.find_one({"email": OWNER_EMAIL}, {"_id": 1})
    if user:
        uid = str(user["_id"])
        existing_credits = await db.user_credits.find_one({"user_id": uid})
        if not existing_credits:
            await db.user_credits.insert_one({
                "user_id": uid,
                "credits": 50000,
                "total_earned": 50000,
                "total_spent": 0,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

async def create_indexes():
    await db.users.create_index("email", unique=True)
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.login_attempts.create_index("identifier")
    await db.waitlist.create_index("email", unique=True, name="unique_email")
    await db.waitlist.create_index("referral_code", unique=True, name="unique_referral_code")
    await db.headlines.create_index("content_hash", unique=True)
    await db.headlines.create_index("expires_at", expireAfterSeconds=0)
    await db.headlines.create_index([("scraped_at", -1)])

# --- Owner-only guard ---
async def require_owner(request: Request):
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user

async def require_admin(request: Request):
    """Require owner OR admin role. 'owner' is the super-admin (the founder)."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# --- Admin Routes (Owner + Admin) ---
@auth_router.get("/admin/users")
async def list_users(request: Request):
    await require_admin(request)
    cursor = db.users.find({}, {"password_hash": 0}).limit(200)
    users = []
    async for u in cursor:
        u["_id"] = str(u["_id"])
        users.append(u)
    return {"users": users, "total": len(users)}

@auth_router.post("/admin/users/{user_id}/activate")
async def activate_user(user_id: str, request: Request):
    await require_admin(request)
    result = await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_active": True}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User activated"}

@auth_router.post("/admin/users/{user_id}/deactivate")
async def deactivate_user(user_id: str, request: Request):
    await require_admin(request)
    target = await db.users.find_one({"_id": ObjectId(user_id)})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == "owner":
        raise HTTPException(status_code=403, detail="Cannot deactivate owner account")
    await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_active": False}})
    return {"message": "User deactivated"}

@auth_router.post("/admin/users/{user_id}/grant-pro")
async def grant_pro(user_id: str, request: Request):
    await require_admin(request)
    result = await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"subscription_status": "pro"}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "Pro access granted"}

@auth_router.post("/admin/users/{user_id}/revoke-pro")
async def revoke_pro(user_id: str, request: Request):
    await require_admin(request)
    target = await db.users.find_one({"_id": ObjectId(user_id)})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == "owner":
        raise HTTPException(status_code=403, detail="Cannot revoke owner's Pro access")
    await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"subscription_status": "free"}})
    return {"message": "Pro access revoked"}
