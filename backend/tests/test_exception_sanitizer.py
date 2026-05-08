"""Tests for the global exception sanitizer in server.py.

We don't want to import server.py directly (huge graph). Instead we
build a minimal FastAPI app that mounts the same two exception handlers
and exercise them in isolation. If the policy here drifts, server.py's
handlers must be kept in lock-step.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException


def _build_app() -> FastAPI:
    """Mirror of server.py's exception handler policy.

    Policy:
      * 4xx — keep detail (intentional client-facing).
      * 500 — replace with generic message; original logged.
      * 5xx other than 500 — keep detail (501/503 are capability/availability).
    """
    app = FastAPI()
    logger = logging.getLogger("test_exception_sanitizer")

    @app.exception_handler(StarletteHTTPException)
    async def sanitize_http_exception(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 500:
            logger.error(
                f"[5xx-sanitized] path={request.url.path} "
                f"status=500 raw_detail={exc.detail!r}"
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"},
                headers=getattr(exc, "headers", None) or {},
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(Exception)
    async def sanitize_unhandled_exception(request: Request, exc: Exception):
        logger.exception(
            f"[unhandled] path={request.url.path} type={type(exc).__name__}"
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    @app.get("/raise-500")
    async def raise_500():
        # Simulate a developer doing `raise HTTPException(500, str(e))`.
        raise HTTPException(
            status_code=500,
            detail="MongoServerError: connection to /var/run/mongo.sock refused"
        )

    @app.get("/raise-501")
    async def raise_501():
        raise HTTPException(status_code=501, detail="Tradier not configured")

    @app.get("/raise-503")
    async def raise_503():
        raise HTTPException(status_code=503, detail="Rate limited")

    @app.get("/raise-400")
    async def raise_400():
        raise HTTPException(status_code=400, detail="bad input: foo")

    @app.get("/raise-403")
    async def raise_403():
        raise HTTPException(status_code=403, detail="Pro subscription required")

    @app.get("/raise-unhandled")
    async def raise_unhandled():
        # Bare exception — should be caught by the catch-all.
        raise RuntimeError("ENOENT: /app/backend/secrets/jwt.key")

    return app


@pytest.fixture
def client():
    return TestClient(_build_app(), raise_server_exceptions=False)


def test_500_detail_is_sanitized(client):
    """The original detail (which leaks paths / Mongo errors) must be replaced."""
    r = client.get("/raise-500")
    assert r.status_code == 500
    body = r.json()
    assert body == {"detail": "Internal server error"}
    # Confirm none of the leaky strings made it out.
    raw = r.text
    assert "MongoServerError" not in raw
    assert "/var/run/mongo.sock" not in raw


def test_501_detail_is_preserved(client):
    """501 is intentional capability messaging — must pass through."""
    r = client.get("/raise-501")
    assert r.status_code == 501
    assert r.json() == {"detail": "Tradier not configured"}


def test_503_detail_is_preserved(client):
    r = client.get("/raise-503")
    assert r.status_code == 503
    assert r.json() == {"detail": "Rate limited"}


def test_400_detail_is_preserved(client):
    r = client.get("/raise-400")
    assert r.status_code == 400
    assert r.json() == {"detail": "bad input: foo"}


def test_403_detail_is_preserved(client):
    """403s must keep detail so Pro-gating messages stay user-facing."""
    r = client.get("/raise-403")
    assert r.status_code == 403
    assert r.json() == {"detail": "Pro subscription required"}


def test_unhandled_exception_is_sanitized(client):
    """Bare `raise SomeError(...)` must never leak the message."""
    r = client.get("/raise-unhandled")
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error"}
    assert "ENOENT" not in r.text
    assert "/app/backend/secrets" not in r.text
