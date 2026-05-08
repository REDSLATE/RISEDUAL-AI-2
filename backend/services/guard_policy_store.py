"""
Decision Pipeline Guard — runtime policy store.

Single source of truth for per-patent enforcement flags. Persists the
operator's promotion choices in Mongo so:

  * Restarts don't lose them.
  * The Guard Shadow admin UI ("Promote → Enforcement") writes once,
    every subsequent ``EnforcementPolicy.from_env_or_db()`` call sees
    the new value across all bot loops, route handlers, and workers.
  * The legacy env-var path (``PATENT_K_ENFORCE`` etc.) is preserved
    as the default; Mongo overrides only the flags an operator has
    explicitly set.

Why not write to ``.env`` from code: ``.env`` is the deploy-time
seed, not the runtime config. Mutating it at runtime mixes
configuration sources and breaks immutable-deploy assumptions.

Schema (single document):
    db.guard_policy_state.find_one({"_id": "policy"})
    {
      "_id": "policy",
      "overrides": {
        "enforce_adversarial": true,
        "enforce_auditor": false,
        ...
      },
      "history": [
        {"flag": "enforce_adversarial", "value": true,
         "actor": "admin@risedual.ai", "at": ISODate(...), "note": ""},
        ...
      ],
      "updated_at": ISODate(...),
    }

The ``history`` array is capped client-side to the last 50 entries
so a runaway promote/demote cycle doesn't bloat the doc.
"""
from __future__ import annotations

__domain__ = "DTD"

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

COLLECTION = "guard_policy_state"
DOC_ID = "policy"
HISTORY_CAP = 50

VALID_FLAGS = {
    "enforce_adversarial",   # Patent K
    "enforce_auditor",       # Auditor calibration veto
    "enforce_authority",     # Patent H/I
    "enforce_failure_mode",  # Patent M
    "enforce_risk_budget",   # Patent I
}


async def get_overrides(db: Any) -> dict[str, bool]:
    """Read the current Mongo overrides. Empty dict if none.

    Never raises — a missing collection / doc just means "no
    overrides, fall back to env defaults".
    """
    if db is None:
        return {}
    try:
        doc = await db[COLLECTION].find_one({"_id": DOC_ID}, {"_id": 0, "overrides": 1})
        return dict((doc or {}).get("overrides") or {})
    except Exception as e:  # noqa: BLE001
        logger.warning("[guard_policy_store] read failed: %s", e)
        return {}


async def set_override(
    db: Any,
    *,
    flag: str,
    value: bool,
    actor: str,
    note: str = "",
) -> dict[str, Any]:
    """Set one flag's override and append a history entry.

    Validates ``flag`` against ``VALID_FLAGS`` so the UI can't
    inadvertently write a typo into the policy doc.
    """
    if flag not in VALID_FLAGS:
        raise ValueError(f"unknown policy flag: {flag!r}")
    if db is None:
        raise RuntimeError("guard_policy_store: db is None")

    now = datetime.now(timezone.utc)
    history_entry = {
        "flag": flag,
        "value": bool(value),
        "actor": actor,
        "at": now,
        "note": (note or "")[:200],
    }
    await db[COLLECTION].update_one(
        {"_id": DOC_ID},
        {
            "$set": {
                f"overrides.{flag}": bool(value),
                "updated_at": now,
            },
            "$push": {
                "history": {
                    "$each": [history_entry],
                    "$slice": -HISTORY_CAP,
                },
            },
        },
        upsert=True,
    )
    logger.info(
        "[guard_policy_store] %s set %s=%s (note=%r)",
        actor, flag, value, note,
    )
    return history_entry


async def clear_override(
    db: Any,
    *,
    flag: str,
    actor: str,
) -> None:
    """Remove an override so the env-var default takes over again."""
    if flag not in VALID_FLAGS:
        raise ValueError(f"unknown policy flag: {flag!r}")
    if db is None:
        raise RuntimeError("guard_policy_store: db is None")
    now = datetime.now(timezone.utc)
    await db[COLLECTION].update_one(
        {"_id": DOC_ID},
        {
            "$unset": {f"overrides.{flag}": ""},
            "$set": {"updated_at": now},
            "$push": {
                "history": {
                    "$each": [{
                        "flag": flag, "value": None, "actor": actor,
                        "at": now, "note": "cleared (revert to env default)",
                    }],
                    "$slice": -HISTORY_CAP,
                },
            },
        },
        upsert=True,
    )


async def get_state(db: Any) -> dict[str, Any]:
    """Full state for the admin UI: overrides + last N history rows."""
    if db is None:
        return {"overrides": {}, "history": [], "updated_at": None}
    try:
        doc = await db[COLLECTION].find_one(
            {"_id": DOC_ID}, {"_id": 0},
        )
        if not doc:
            return {"overrides": {}, "history": [], "updated_at": None}
        # Convert datetimes to iso for transport
        for h in doc.get("history") or []:
            if isinstance(h.get("at"), datetime):
                h["at"] = h["at"].isoformat()
        if isinstance(doc.get("updated_at"), datetime):
            doc["updated_at"] = doc["updated_at"].isoformat()
        return doc
    except Exception as e:  # noqa: BLE001
        logger.warning("[guard_policy_store] state read failed: %s", e)
        return {"overrides": {}, "history": [], "updated_at": None}


_cached_db: Optional[Any] = None


def set_db(db: Any) -> None:
    """Module-level db handle — set once at server startup so the
    sync `EnforcementPolicy.from_env_or_db()` helper can read overrides
    without dragging a db arg through every call site."""
    global _cached_db
    _cached_db = db


def get_db() -> Optional[Any]:
    return _cached_db


async def load_overrides_into_cache() -> dict[str, bool]:
    """Pull the current overrides from Mongo into a process-local cache.

    Called on a short interval (~30s) by the lazy reader below so
    every IP-contract evaluation pays at most one Mongo read every
    30s instead of one per call.
    """
    global _cached_overrides, _cached_at
    if _cached_db is None:
        return {}
    overrides = await get_overrides(_cached_db)
    _cached_overrides = overrides
    _cached_at = datetime.now(timezone.utc)
    return overrides


# Process-local cache so the per-decision read is O(1).
_cached_overrides: dict[str, bool] = {}
_cached_at: Optional[datetime] = None
_CACHE_TTL_SECONDS = 30


def get_cached_overrides() -> dict[str, bool]:
    """Synchronous view of the cached overrides. Returns ``{}`` if no
    refresh has happened yet — the env defaults still apply."""
    return dict(_cached_overrides)


def cache_is_stale() -> bool:
    if _cached_at is None:
        return True
    age = (datetime.now(timezone.utc) - _cached_at).total_seconds()
    return age >= _CACHE_TTL_SECONDS


async def refresh_cache_if_stale() -> None:
    """Refresh the override cache if it's older than the TTL.

    Cheap fast-path when called frequently — only does the Mongo read
    every ``_CACHE_TTL_SECONDS``. Safe to call from per-decision
    code; the IP contract calls this once per evaluation.
    """
    if cache_is_stale():
        try:
            await load_overrides_into_cache()
        except Exception as e:  # noqa: BLE001
            logger.warning("[guard_policy_store] cache refresh failed: %s", e)
