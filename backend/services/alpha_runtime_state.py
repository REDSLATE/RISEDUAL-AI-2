"""Alpha Day Trader — runtime activation state.

Operator toggles that override the ``RISEDUAL_ALPHA_DAYTRADER_*`` env
flags at runtime. Persisted in Mongo so operator changes survive
restarts. When the runtime doc is missing or a field is None, the
env variable is used as the fallback.

Collection: ``alpha_runtime_state`` (single doc, ``_id="active"``).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DOC_ID = "active"


def _env_flag(key: str) -> bool:
    return (os.environ.get(key) or "").strip().lower() in ("1", "true", "yes", "on")


async def get_state(db: Any) -> dict:
    """Return the effective toggle state.

    Precedence: Mongo runtime override → env flag → False.
    """
    scan_env = _env_flag("RISEDUAL_ALPHA_DAYTRADER_SCAN")
    exec_env = _env_flag("RISEDUAL_ALPHA_DAYTRADER_EXECUTE")
    doc: dict = {}
    if db is not None:
        try:
            doc = (await db.alpha_runtime_state.find_one({"_id": _DOC_ID})) or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("[alpha_runtime_state] read failed: %s", exc)
    scan = doc.get("scan_enabled") if doc.get("scan_enabled") is not None else scan_env
    execute = doc.get("execute_enabled") if doc.get("execute_enabled") is not None else exec_env
    return {
        "scan_enabled": bool(scan),
        "execute_enabled": bool(execute),
        "scan_source": "override" if doc.get("scan_enabled") is not None else "env",
        "execute_source": "override" if doc.get("execute_enabled") is not None else "env",
        "env": {"scan": scan_env, "execute": exec_env},
        "updated_at": doc.get("updated_at").isoformat() if isinstance(doc.get("updated_at"), datetime) else doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


async def set_state(
    db: Any,
    *,
    scan_enabled: Optional[bool] = None,
    execute_enabled: Optional[bool] = None,
    operator: str = "system",
) -> dict:
    """Persist an operator override. Passing ``None`` for a field clears
    the override (so the env variable takes effect again)."""
    if db is None:
        raise RuntimeError("alpha_runtime_state: db not wired")
    now = datetime.now(timezone.utc)
    update: dict[str, Any] = {"updated_at": now, "updated_by": operator}
    unset: dict[str, str] = {}
    if scan_enabled is None:
        unset["scan_enabled"] = ""
    else:
        update["scan_enabled"] = bool(scan_enabled)
    if execute_enabled is None:
        unset["execute_enabled"] = ""
    else:
        update["execute_enabled"] = bool(execute_enabled)
    ops: dict[str, dict] = {"$set": update}
    if unset:
        ops["$unset"] = unset
    await db.alpha_runtime_state.update_one({"_id": _DOC_ID}, ops, upsert=True)
    return await get_state(db)


__all__ = ["get_state", "set_state"]
