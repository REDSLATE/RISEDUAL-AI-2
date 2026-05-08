"""Compatibility shim — the admin ML routes used to live here as a
single 543-line module. They have been split along authority
boundaries into the ``routes.admin_ml`` package:

  * routes/admin_ml/_routers.py     — shared APIRouter + DB handle
  * routes/admin_ml/_auth.py        — late-bound require_admin()
  * routes/admin_ml/receipts.py     — boot receipts + decision log + Phase 5b + Camaro
  * routes/admin_ml/kanban.py       — RoadGuard pair + calibration kanban
  * routes/admin_ml/v2_pipeline.py  — synthetic pipeline dry-run
  * routes/admin_ml/safety.py       — Phase 5d/6 read-only safety surface

This shim re-exports the same public names the old module exposed
so ``route_registry.py`` and existing tests keep working without
any code changes:

  * ``router``               — APIRouter at /api/admin/ml/v2
  * ``ml_safety_router``     — APIRouter at /api/admin/ml
  * ``set_db(database)``     — wires the Mongo handle into the package
  * Endpoint functions referenced directly by tests
    (``list_artifacts_endpoint``, ``promotion_checklist``).

Tests can still ``monkeypatch.setattr(admin_ml_v2, "_require_admin",
...)`` — the package's :func:`routes.admin_ml._auth.require_admin`
re-resolves the patched callable via this shim at every request.

No new code should be added to this file; extend the package
modules instead.
"""
from __future__ import annotations

# Bind ``_require_admin`` first so the package's late-bound auth
# helper can resolve the canonical implementation (and any
# test-time monkey-patches) via ``sys.modules["routes.admin_ml_v2"]``.
from routes.admin import _require_admin  # noqa: F401

# Now re-export the package's public surface.
from routes.admin_ml import (  # noqa: E402, F401
    list_artifacts_endpoint,
    ml_safety_router,
    pipeline_heartbeat,
    pipeline_receipts,
    promotion_checklist,
    router,
    set_db,
    wedge_alerter_history,
    wedge_alerter_run_now,
    wedge_alerter_status,
)

__all__ = [
    "_require_admin",
    "list_artifacts_endpoint",
    "ml_safety_router",
    "pipeline_heartbeat",
    "pipeline_receipts",
    "promotion_checklist",
    "router",
    "set_db",
    "wedge_alerter_history",
    "wedge_alerter_run_now",
    "wedge_alerter_status",
]
