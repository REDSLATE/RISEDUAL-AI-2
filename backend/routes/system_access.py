"""Public-access lockout (a.k.a. "Technical Difficulties / Coming Soon" mode).

Operator decree: while the ML stack is running scenarios and
collecting ADL data (May 8 → ~May 22, 2026), the public site shows a
"technical difficulties" page. The owner / admin must still be able
to log in and reach the dashboard so they can monitor the stack.

This module exposes:

  * ``GET  /api/system/access``     — public, returns lockout state
                                      and whether the caller is an
                                      admin (so the frontend can
                                      gate the UI).
  * ``POST /api/system/access``     — owner/admin only, flips the
                                      lockout flag at runtime
                                      without a redeploy.

Lockout state precedence
------------------------
    runtime override (Mongo ``system_settings.public_access``)
        ↓ falls through if missing
    env flag ``PUBLIC_ACCESS_ENABLED`` (default true)

The middleware in ``services.public_access_middleware`` reads the
same precedence chain on every request.

Hard rules (pinned by tests)
----------------------------
  * Default state is ``public_access=true`` (no lockout) so dev /
    preview environments are not surprised by a hard 503.
  * The middleware NEVER blocks ``/api/auth/*``, ``/api/health``,
    ``/api/system/access`` — those must always be reachable so
    admins can sign in and the lockout state can be inspected.
  * Toggling the flag is owner/admin-only.
  * The middleware emits 503 with a stable JSON shape so the
    frontend can pattern-match.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request

from routes.auth import get_optional_user, require_owner

router = APIRouter()


def _get_db():
    """Lazy import of the Mongo handle to avoid a circular import
    with ``server.py``."""
    from server import db
    return db


# Mongo collection that stores the runtime override.
_SETTINGS_COLLECTION = "system_settings"
_LOCKOUT_DOC_ID = "public_access"


def _env_default() -> bool:
    """``PUBLIC_ACCESS_ENABLED`` — default ``true`` so missing config
    fails OPEN (dev/preview keep working).

    Truthy tokens (case-insensitive): ``true``, ``1``, ``yes``, ``on``.
    Anything else → false.
    """
    raw = (os.environ.get("PUBLIC_ACCESS_ENABLED") or "true").strip().lower()
    return raw in {"true", "1", "yes", "on"}


async def is_public_access_enabled() -> bool:
    """Resolve the effective lockout state.

    Runtime override wins; env flag is the fallback.
    """
    db = _get_db()
    if db is None:
        return _env_default()
    try:
        doc = await db[_SETTINGS_COLLECTION].find_one(
            {"_id": _LOCKOUT_DOC_ID},
            {"_id": 0, "enabled": 1},
        )
    except Exception:  # noqa: BLE001 — never let a DB hiccup hard-lock the site
        return _env_default()
    if doc is None or "enabled" not in doc:
        return _env_default()
    return bool(doc["enabled"])


def _is_admin(user: dict | None) -> bool:
    """Owner or admin role bypasses the lockout."""
    if not user:
        return False
    return user.get("role") in ("owner", "admin")


@router.get("/system/access")
async def get_system_access(request: Request) -> dict:
    """Public endpoint — frontend calls this on app load.

    Returns ``public_access`` (boolean — is the public site open?)
    and ``is_admin`` (boolean — is the current cookie holder an
    owner/admin who can bypass any lockout?).
    """
    enabled = await is_public_access_enabled()
    user = await get_optional_user(request)
    return {
        "public_access": bool(enabled),
        "is_admin": _is_admin(user),
        "message": (
            None if enabled else
            "RISEDUAL is temporarily offline while the ML stack "
            "collects training data. We'll be back shortly."
        ),
    }


@router.post("/system/access")
async def set_system_access(
    request: Request, payload: dict,
) -> dict:
    """Owner/admin-only. Flip the lockout at runtime.

    Body: ``{"enabled": true|false}``.
    """
    await require_owner(request)
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(
            status_code=400,
            detail="'enabled' must be a boolean",
        )
    db = _get_db()
    if db is None:
        raise HTTPException(
            status_code=503, detail="Database unavailable",
        )
    await db[_SETTINGS_COLLECTION].update_one(
        {"_id": _LOCKOUT_DOC_ID},
        {"$set": {"enabled": enabled}},
        upsert=True,
    )
    return {"public_access": enabled}
