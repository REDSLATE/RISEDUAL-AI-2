"""Shared routers + state for the admin_ml route package.

Defines the two APIRouter instances + the late-bound DB handle so
sibling endpoint modules can register their decorators without
re-creating the routers (and without circular imports).

Authority boundary: this module owns ROUTER STATE only — it never
defines an endpoint. Endpoint modules (receipts/kanban/safety/
v2_pipeline) import these routers and register their handlers.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter

logger = logging.getLogger("routes.admin_ml")

router = APIRouter(prefix="/api/admin/ml/v2", tags=["admin", "ml-v2"])
# Phase 5d safety surface — exposed at the simpler /api/admin/ml
# prefix (no /v2) so operators can curl heartbeat/receipts without
# guessing the version path.
ml_safety_router = APIRouter(prefix="/api/admin/ml", tags=["admin", "ml-safety"])

_db: Optional[Any] = None


def set_db(database: Any) -> None:
    global _db
    _db = database


def get_db() -> Optional[Any]:
    return _db
