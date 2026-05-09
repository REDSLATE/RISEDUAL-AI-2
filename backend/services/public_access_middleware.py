"""Public-access middleware — implements the "technical difficulties"
lockout for non-admin traffic.

When ``is_public_access_enabled()`` returns False, this middleware
returns a 503 JSON response for every request EXCEPT:

  * ``/api/health`` and ``/api/health/*``     — operator monitoring
  * ``/api/auth/*``                           — admins must sign in
  * ``/api/system/access`` (GET + POST)       — query / flip state
  * Non-``/api/`` paths (frontend assets)     — let the frontend
                                                serve the lockout
                                                page itself
  * Owner/admin requests                      — bypass via JWT

The 503 body shape is stable so the frontend can pattern-match:

    {
      "error": "service_unavailable",
      "reason": "scheduled_maintenance",
      "message": "RISEDUAL is temporarily offline ..."
    }
"""
from __future__ import annotations

import logging
import os

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


# Path prefixes that MUST stay reachable while the lockout is on.
_BYPASS_EXACT = frozenset({
    "/api/health",
    "/api/system/access",
})
_BYPASS_PREFIXES = (
    "/api/health/",
    "/api/auth/",
    "/api/system/access",  # tolerate trailing slash forms
)


def _path_is_bypassed(path: str) -> bool:
    if path in _BYPASS_EXACT:
        return True
    return any(path.startswith(p) for p in _BYPASS_PREFIXES)


def _decode_jwt_silently(token: str) -> dict | None:
    """Decode an access JWT without raising. Used for fast email
    inspection — no DB lookup, just the signed payload."""
    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            return None
        return payload
    except Exception:  # noqa: BLE001
        return None


def _admin_email_allowlist() -> set[str]:
    """``ADMIN_EMAILS`` env var (comma-separated, lowercased).

    Defaults to the canonical owner email so the lockout never
    accidentally locks out the founder during a deploy where the
    env var hasn't been set yet.
    """
    raw = os.environ.get("ADMIN_EMAILS", "admin@risedual.ai")
    return {
        e.strip().lower()
        for e in raw.split(",") if e.strip()
    }


def _caller_is_admin(request: Request) -> bool:
    """Inspect the JWT for an admin-allowlisted email.

    Falls back to False on any decode error — the middleware errs
    on the side of locking out, which is the safe default during a
    "technical difficulties" window.
    """
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "") or ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        return False
    payload = _decode_jwt_silently(token)
    if not payload:
        return False
    email = (payload.get("email") or "").strip().lower()
    if not email:
        return False
    return email in _admin_email_allowlist()


class PublicAccessMiddleware(BaseHTTPMiddleware):
    """Returns 503 for non-admin API traffic when the lockout is on.

    Frontend assets (``/`` and any non-``/api/`` path) are passed
    through so the SPA can render its own "technical difficulties"
    page and surface the login form for admins.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Frontend assets — let the SPA decide what to render.
        if not path.startswith("/api/"):
            return await call_next(request)

        # Bypass paths — always reachable.
        if _path_is_bypassed(path):
            return await call_next(request)

        # Resolve lockout state. Done lazily so we don't pay the
        # DB cost on every health check / asset request.
        from routes.system_access import is_public_access_enabled
        try:
            enabled = await is_public_access_enabled()
        except Exception:  # noqa: BLE001
            # Fail OPEN — never let a DB hiccup hard-lock the site.
            return await call_next(request)

        if enabled:
            return await call_next(request)

        # Lockout is ON. Admins bypass; everyone else gets 503.
        if _caller_is_admin(request):
            return await call_next(request)

        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "reason": "scheduled_maintenance",
                "message": (
                    "RISEDUAL is temporarily offline while the "
                    "ML stack collects training data. We'll be "
                    "back shortly."
                ),
            },
        )
