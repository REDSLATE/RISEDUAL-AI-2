"""Admin route-introspection endpoint.

Self-documenting map of every ``/api/admin/*`` route registered on
the FastAPI app — surfaces source-module, owner-gating level, and
HTTP method in one shot. Doubles as a route-registry health check
(any duplicate registration is visible at a glance, which would
have caught the ``news-shock/ingestion-sparkline`` orphan-stub bug
during the news/benzinga extraction in seconds).

  * ``GET /api/admin/_routes``

Owner-gated. Read-only. ~30 lines of real logic — the rest is
docstring + helpers.
"""
from __future__ import annotations

import inspect
import logging

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/admin", tags=["admin-introspection"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


def _classify_gate(endpoint) -> str:
    """Cheap gate classifier — peeks at the endpoint's source for the
    ``_require_owner`` / ``_require_admin`` calls our extraction
    pattern uses. ``unknown`` covers third-party routes that don't
    follow our pattern (or builtins / lambdas)."""
    try:
        src = inspect.getsource(endpoint)
    except (OSError, TypeError):
        return "unknown"
    if "_require_owner" in src:
        return "owner"
    if "_require_admin" in src:
        return "admin"
    if "get_current_user" in src or "current_user" in src:
        return "auth"
    return "open"


@router.get("/_routes")
async def list_admin_routes(request: Request):
    """Return every registered ``/api/admin/*`` route with its source
    module, owner-gating level, and HTTP method.

    Response shape::

        {
          "total": 165,
          "by_module": {
              "routes.admin": 35,
              "routes.admin_news": 12,
              ...
          },
          "by_gate": {"owner": 158, "admin": 4, "auth": 2, "unknown": 1},
          "duplicates": [],          # any path registered twice
          "routes": [
              {
                "path": "/api/admin/spread-watch",
                "method": "GET",
                "module": "routes.admin_spread_slippage",
                "gate": "owner",
                "name": "spread_watch",
              },
              ...
          ]
        }

    The ``duplicates`` list is the operator's go-to signal that an
    extraction left a stub behind. Empty on a healthy decomposition.
    """
    await _require_owner(request)
    rows: list[dict] = []
    seen: dict[tuple[str, str], int] = {}
    duplicates: list[dict] = []

    for r in request.app.routes:
        path = getattr(r, "path", None)
        if not path or not path.startswith("/api/admin"):
            continue
        endpoint = getattr(r, "endpoint", None)
        module = getattr(endpoint, "__module__", "?") if endpoint else "?"
        name = getattr(endpoint, "__name__", "?") if endpoint else "?"
        methods = sorted(getattr(r, "methods", []) or [])
        for method in methods:
            # FastAPI implicitly registers HEAD for every GET — skip
            # so the count matches operator intuition.
            if method == "HEAD":
                continue
            key = (method, path)
            seen[key] = seen.get(key, 0) + 1
            rows.append({
                "path": path,
                "method": method,
                "module": module,
                "gate": _classify_gate(endpoint) if endpoint else "unknown",
                "name": name,
            })

    for (method, path), count in seen.items():
        if count > 1:
            duplicates.append({"path": path, "method": method, "count": count})

    rows.sort(key=lambda x: (x["module"], x["path"], x["method"]))

    by_module: dict[str, int] = {}
    by_gate: dict[str, int] = {}
    for row in rows:
        by_module[row["module"]] = by_module.get(row["module"], 0) + 1
        by_gate[row["gate"]] = by_gate.get(row["gate"], 0) + 1

    return {
        "total": len(rows),
        "by_module": dict(sorted(by_module.items(), key=lambda kv: -kv[1])),
        "by_gate": by_gate,
        "duplicates": duplicates,
        "routes": rows,
    }
