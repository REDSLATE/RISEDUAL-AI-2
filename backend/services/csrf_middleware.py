"""csrf_middleware — same-origin CSRF defense for cookie-authenticated APIs.

Rationale
---------
The RiseDual API accepts either

* an ``access_token`` httpOnly cookie set by ``POST /api/auth/login``, or
* an ``Authorization: Bearer <jwt>`` header,

as the primary authentication carrier. Bearer-token clients (curl,
CLIs, mobile) are immune to CSRF by construction — the attacker can't
read the token from another origin. Cookie-authenticated browser
sessions are not: SameSite=None cookies used to enable the
preview↔production cross-domain flow means a browser will happily
attach the cookie to a state-mutating request initiated by another
origin.

Two-layer defense
-----------------
1. **SameSite=Lax by default** (see ``routes/auth.set_auth_cookies``).
   Blocks the browser from attaching cookies to cross-site POST /
   PUT / PATCH / DELETE. Existing SameSite=None cookies keep working
   until they refresh, so operators aren't logged out by the change.
2. **Custom header enforcement (this middleware).** On every
   state-mutating request under ``/api`` that carries an
   ``access_token`` cookie AND no ``Authorization`` header, require an
   ``X-Requested-With: XMLHttpRequest`` header. The browser refuses
   to send this header on a cross-site fetch unless CORS preflight
   succeeds, and our CORS allowlist does not admit attacker origins,
   so an attacker cannot forge such a request.

Skip list
---------
* ``OPTIONS`` (preflight)
* Non-``/api`` paths (frontend static assets)
* ``/api/auth/login``, ``/api/auth/register``, ``/api/auth/forgot-password``,
  ``/api/auth/reset-password``  — pre-auth flows; no cookie yet
* Any path containing ``/webhook`` or ``/oauth`` — inbound calls from
  external providers with their own signature-based auth

Configuration
-------------
* ``CSRF_ENFORCE=1`` (default): reject non-conforming requests with 403.
* ``CSRF_ENFORCE=0``: log only, allow through — one-tick migration
  window while the frontend rolls out the fetch-wrapper change.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Path *substrings* that skip CSRF enforcement. Substring match keeps
# the list compact — no need to enumerate every webhook variant.
_SKIP_SUBSTRINGS: tuple[str, ...] = (
    "/webhook",
    "/oauth",
)

# Exact paths that skip CSRF enforcement (pre-auth flows).
_SKIP_EXACT: frozenset[str] = frozenset({
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
})


def _enforce() -> bool:
    raw = (os.environ.get("CSRF_ENFORCE") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _path_skipped(path: str) -> bool:
    if not path.startswith("/api"):
        return True
    if path in _SKIP_EXACT:
        return True
    for needle in _SKIP_SUBSTRINGS:
        if needle in path:
            return True
    return False


class CSRFHeaderMiddleware(BaseHTTPMiddleware):
    """Require ``X-Requested-With: XMLHttpRequest`` on cookie-auth
    state-mutating requests. Bearer-auth and non-auth requests fall
    through unchanged.
    """

    async def dispatch(self, request: StarletteRequest, call_next):
        method = request.method.upper()
        if method not in _MUTATING_METHODS:
            return await call_next(request)

        path = request.url.path or ""
        if _path_skipped(path):
            return await call_next(request)

        # Only enforce on cookie-authenticated requests. If the caller
        # is using Bearer auth (API clients, integration tests), CSRF
        # is not an issue.
        has_cookie_auth = bool(request.cookies.get("access_token"))
        has_bearer = (request.headers.get("authorization") or "").lower().startswith("bearer ")
        if not has_cookie_auth or has_bearer:
            return await call_next(request)

        xrw = (request.headers.get("x-requested-with") or "").strip()
        if xrw.lower() == "xmlhttprequest":
            return await call_next(request)

        # Header missing → likely a cross-site attacker (or a browser
        # form POST that doesn't set the header).
        if not _enforce():
            logger.warning(
                "[csrf] SHADOW-BLOCK path=%s method=%s origin=%s "
                "(enforcement disabled)",
                path, method, request.headers.get("origin", "-"),
            )
            return await call_next(request)

        logger.warning(
            "[csrf] BLOCK path=%s method=%s origin=%s "
            "(missing X-Requested-With)",
            path, method, request.headers.get("origin", "-"),
        )
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "CSRF protection: state-mutating requests must "
                    "include the 'X-Requested-With: XMLHttpRequest' "
                    "header. Bearer-token clients are exempt."
                ),
                "code": "csrf_header_missing",
            },
        )


__all__ = ["CSRFHeaderMiddleware", "_path_skipped", "_enforce"]
