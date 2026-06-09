"""MC2 module-level Mongo handle + state snapshot.

Follows the same ``set_db(database)`` pattern used by other route
modules in this codebase. Server startup calls
``services.mc2.set_db(db)`` once after Mongo is connected; every
MC2 collection writer reads from this module-level handle.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from services.mc2.standalone import is_standalone


_db: Any = None


def set_db(database: Any) -> None:
    """Bind the shared Motor database handle for MC2 writers."""
    global _db
    _db = database


def get_db() -> Any:
    """Return the bound database handle. ``None`` if ``set_db`` was
    never called — writers must guard against this and silently no-op
    so an uninitialised MC2 cannot crash a consensus tick."""
    return _db


def now_utc_iso() -> str:
    """Canonical timestamp shape for MC2 collections."""
    return datetime.now(timezone.utc).isoformat()


async def get_state() -> dict[str, Any]:
    """Read-only snapshot of MC2's collection volumes + latest rows.

    Returns a dict shaped::

        {
          "standalone_mode": bool,
          "intents":  {"count": int, "latest": [<last 10 rows>]},
          "opinions": {"count": int, "latest": [<last 10 rows>]},
          "outcomes": {"count": int, "latest": [<last 10 rows>]},
        }

    If the DB handle is unset, every count is ``0`` and ``latest``
    is empty. Used by ``GET /api/admin/mc2/state``.
    """
    out: dict[str, Any] = {
        "standalone_mode": is_standalone(),
        "intents":  {"count": 0, "latest": []},
        "opinions": {"count": 0, "latest": []},
        "outcomes": {"count": 0, "latest": []},
    }
    db = _db
    if db is None:
        return out

    for key, coll in (
        ("intents",  "mc2_intents"),
        ("opinions", "mc2_opinions"),
        ("outcomes", "mc2_outcomes"),
    ):
        try:
            count = await db[coll].count_documents({})
            cursor = (
                db[coll].find({}, {"_id": 0})
                .sort("recorded_at", -1).limit(10)
            )
            latest = await cursor.to_list(length=10)
            out[key] = {"count": int(count), "latest": latest}
        except Exception:  # noqa: BLE001
            # Collection may not exist yet — leave the zeroed default.
            continue

    return out


__all__ = ["set_db", "get_db", "now_utc_iso", "get_state"]
