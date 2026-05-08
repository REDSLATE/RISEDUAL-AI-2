"""Role-scoped Mongo handles — enforcement-by-capability.

Three named clients, each capability-restricted to a single domain:

* :class:`DtdClient` — read+write on DTD collections, **forbidden** on
  PRD/bridge collections. Used by Strategist/Auditor/Commander/Council/
  Regime/Order-fill code paths.
* :class:`PrdReadOnlyClient` — read+write on PRD collections,
  **read-only on DTD collections** (no insert/update/delete). PRD
  modules may consult DTD state for cold-start hydration but may
  never mutate it.
* :class:`BridgeCalibrationClient` — read PRD collections, write only
  to ``bridge_activations`` and the bounded calibration adapter.

The clients wrap motor's :class:`AsyncIOMotorDatabase` rather than
replacing it, so existing callers keep working. New dual-stack code
opts in by importing the role-scoped handle.

Why this works
--------------
The motor collection's ``insert_one`` / ``update_*`` / ``delete_*``
methods are coroutines we delegate through. Forbidden methods are
shadowed by raise-only stubs on the wrapper, so any attempt to mutate
across the boundary surfaces immediately as a ``PermissionError`` at
the call site — not as a silent data leak hours later.
"""
from __future__ import annotations

__domain__ = "BRIDGE"

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# ── Collection ownership map ─────────────────────────────────────────
# Listing here is the single source of truth for which collection
# belongs to which domain. Changes require a doc update and an
# invariant-test refresh.

DTD_COLLECTIONS: frozenset[str] = frozenset({
    # Live execution + decision state
    "paper_trades",
    "crypto_paper_trades",
    "predictions",
    "trading_bots",
    "smart_orders",
    "broker_orders",
    "council_decisions",
    "council_buckets",
    "regime_weights",
    "crypto_adversarial_decision_log",
})

PRD_COLLECTIONS: frozenset[str] = frozenset({
    "ai_core_trades",
    "ai_core_rejections",
    "ai_core_alerts",
    "prd_resolved_outcomes",
    "dtd_decision_replay",
    "research_shadow_decisions",
    "memory_cleanup_log",
    "ops_alerter_state",
    "symbol_sector_cache",
})

BRIDGE_COLLECTIONS: frozenset[str] = frozenset({
    "bridge_activations",
    "bridge_audit_log",
})


# ── Read-only proxy ──────────────────────────────────────────────────


class _ReadOnlyCollection:
    """Wraps a motor collection; mutating methods raise."""

    _FORBIDDEN: tuple[str, ...] = (
        "insert_one", "insert_many",
        "update_one", "update_many", "replace_one",
        "delete_one", "delete_many",
        "find_one_and_update", "find_one_and_replace", "find_one_and_delete",
        "drop", "rename", "create_index", "drop_index", "drop_indexes",
        "bulk_write",
    )

    def __init__(self, coll: Any, *, owner_role: str) -> None:
        self._coll = coll
        self._owner_role = owner_role

    def __getattr__(self, name: str) -> Any:
        if name in self._FORBIDDEN:
            raise PermissionError(
                f"{self._owner_role} client may not call "
                f"{self._coll.name}.{name}() — cross-domain mutation forbidden"
            )
        return getattr(self._coll, name)


# ── Domain clients ───────────────────────────────────────────────────


class _BaseClient:
    """Common housekeeping for all role-scoped clients."""

    role: str = "BASE"
    allow_rw: frozenset[str] = frozenset()
    allow_ro: frozenset[str] = frozenset()
    deny: frozenset[str] = frozenset()

    def __init__(self, db: Any) -> None:
        self._db = db

    def _resolve(self, name: str) -> Any:
        if name in self.deny:
            raise PermissionError(
                f"{self.role} client cannot access collection '{name}'"
            )
        if name in self.allow_rw:
            return self._db[name]
        if name in self.allow_ro:
            return _ReadOnlyCollection(self._db[name], owner_role=self.role)
        # Unknown collection: log + read-only by default. Surfacing as a
        # warning rather than raising prevents incidental access during
        # migrations / index management from killing the app, while
        # still flagging the omission in the invariant-test grep.
        logger.warning(
            "[role-scoped-db] %s client touched untagged collection '%s' — "
            "treating as read-only. Update DTD/PRD/BRIDGE_COLLECTIONS.",
            self.role, name,
        )
        return _ReadOnlyCollection(self._db[name], owner_role=self.role)

    def __getitem__(self, name: str) -> Any:
        return self._resolve(name)

    def collection(self, name: str) -> Any:  # explicit alias
        return self._resolve(name)


class DtdClient(_BaseClient):
    """DTD: read+write DTD collections; forbidden on PRD/BRIDGE."""
    role = "DTD"
    allow_rw = DTD_COLLECTIONS
    allow_ro = frozenset()  # DTD does not read PRD
    deny = PRD_COLLECTIONS | BRIDGE_COLLECTIONS


class PrdReadOnlyClient(_BaseClient):
    """PRD: read+write PRD collections; read-only on DTD."""
    role = "PRD"
    allow_rw = PRD_COLLECTIONS
    allow_ro = DTD_COLLECTIONS
    deny = BRIDGE_COLLECTIONS


class BridgeCalibrationClient(_BaseClient):
    """Bridge: read PRD; write only BRIDGE collections."""
    role = "BRIDGE"
    allow_rw = BRIDGE_COLLECTIONS
    allow_ro = PRD_COLLECTIONS
    deny = DTD_COLLECTIONS


# ── Module-level factory ─────────────────────────────────────────────
_db: Any = None


def set_db(db: Any) -> None:
    global _db
    _db = db


def dtd_client() -> DtdClient:
    if _db is None:
        raise RuntimeError("role-scoped DB not initialised")
    return DtdClient(_db)


def prd_client() -> PrdReadOnlyClient:
    if _db is None:
        raise RuntimeError("role-scoped DB not initialised")
    return PrdReadOnlyClient(_db)


def bridge_client() -> BridgeCalibrationClient:
    if _db is None:
        raise RuntimeError("role-scoped DB not initialised")
    return BridgeCalibrationClient(_db)


def all_known_collections() -> Iterable[str]:
    """For invariant tests."""
    return DTD_COLLECTIONS | PRD_COLLECTIONS | BRIDGE_COLLECTIONS
