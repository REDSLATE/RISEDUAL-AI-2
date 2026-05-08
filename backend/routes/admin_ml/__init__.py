"""Admin ML route package.

Authority-boundary split (formerly ``routes/admin_ml_v2.py`` —
now a compatibility shim):

  * ``_routers``    — APIRouter declarations + DB handle
  * ``_auth``       — late-bound admin auth gate
  * ``receipts``    — boot receipts + decision log + Phase 5b + Camaro
  * ``kanban``      — RoadGuard pair + calibration kanban
  * ``v2_pipeline`` — synthetic pipeline dry-run
  * ``safety``      — Phase 5d/6 read-only safety surface

The endpoint modules register their handlers on the routers via
side-effect imports below. Import order is deliberate: ``_routers``
declares the routers, ``_auth`` registers the auth gate helper,
then each endpoint module attaches its decorators.
"""
from __future__ import annotations

from ._routers import (
    get_db,
    logger,
    ml_safety_router,
    router,
    set_db,
)

# ── Side-effect imports — register endpoints on the shared routers.
# Order is alphabetical for readability; behaviour is order-independent
# because each module touches a disjoint URL space.
from . import kanban as _kanban  # noqa: F401
from . import receipts as _receipts  # noqa: F401
from . import safety as _safety  # noqa: F401
from . import v2_pipeline as _v2_pipeline  # noqa: F401

# Re-export endpoint functions referenced directly by tests.
from .safety import (  # noqa: E402
    list_artifacts_endpoint,
    pipeline_heartbeat,
    pipeline_receipts,
    promotion_checklist,
    wedge_alerter_history,
    wedge_alerter_run_now,
    wedge_alerter_status,
)

__all__ = [
    "get_db",
    "list_artifacts_endpoint",
    "logger",
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
