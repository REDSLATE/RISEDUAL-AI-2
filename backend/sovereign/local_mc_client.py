"""LocalMCClient — drop-in replacement for ``MCClient`` that closes
the loop internally instead of POSTing to a remote Mission Control.

Why this exists
---------------
The supervisor-level ``alpha-sidecar`` process was the last outbound
path to ``https://mission.risedual.ai``. The remote MC was severed
weeks ago and no longer recognises this brain, so every call has been
400/404-ing and accumulating into a 5MB+ error log while the watchdog
respawns the process every ~2 minutes. The in-process MC2 layer
(``services/mc2/*``) already handles intents / opinions / outcomes
locally for the async backend; this client gives the **sync sidecar
process** the same closed-loop behaviour.

What it does
------------
Mirrors the exact public surface of ``mc_client.MCClient``:

  * ``heartbeat(body=...)``          → writes to ``mc2_heartbeats``
  * ``post_contribution(...)``       → writes to ``mc2_contributions``
  * ``post_stance(position_id, ...)``→ writes to ``mc2_stances``
  * ``post_intent(**kwargs)``        → writes to ``mc2_intents``
                                       (same collection as the async
                                       backend's ``post_intent_local``
                                       so operator triage stays
                                       single-pane)
  * ``close()``                      → closes the pymongo client

Doctrine
--------
* Best-effort. If Mongo is unreachable the methods log and return a
  benign envelope so the sidecar's tick loop never crashes — same
  contract as the HTTP path was.
* Schemas re-use ``mc_client.build_*`` helpers for validation so a
  malformed payload still fails fast locally.
* Every doc is stamped with ``destination="mc2_local"``, ``brain``,
  ``runtime_token_fingerprint`` (last 4 of token), ``recorded_at``.
  Operators grep ``[mc2.local]`` to see the sidecar's writes.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

try:
    # Same-dir imports when launched via supervisor
    from mc_client import (  # type: ignore
        MCClientError,
        MCContractError,
        auth_headers,
        build_contribution_body,
        build_intent_body,
        build_stance_body,
    )
except ImportError:
    from .mc_client import (
        MCClientError,
        MCContractError,
        auth_headers,
        build_contribution_body,
        build_intent_body,
        build_stance_body,
    )

log = logging.getLogger("sovereign.local_mc_client")


# Collection names — aligned with the async ``services/mc2/*`` layer.
HEARTBEAT_COLLECTION = "mc2_heartbeats"
CONTRIBUTION_COLLECTION = "mc2_contributions"
STANCE_COLLECTION = "mc2_stances"
INTENT_COLLECTION = "mc2_intents"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


class LocalMCClient:
    """Sync, closed-loop replacement for :class:`MCClient`.

    Same constructor signature and public methods so the sidecar can
    swap clients with a single import line. ``base_url`` is accepted
    for compatibility but is purely diagnostic — no HTTP is ever
    issued.
    """

    def __init__(
        self,
        *,
        base_url: str,
        brain: str,
        runtime_token: str,
        timeout: float = 5.0,  # noqa: ARG002 (kept for API parity)
    ) -> None:
        if not brain:
            raise MCContractError("brain is empty")
        self.base_url = (base_url or "local://standalone").rstrip("/")
        self.brain = brain
        self.token = runtime_token or ""
        self._token_fp = (self.token[-4:] if self.token else "----")
        self._db = None
        self._mongo = None
        # Build the Mongo handle lazily so a missing MONGO_URL doesn't
        # crash at import time. The sidecar's tick loop already
        # tolerates partial Mongo outages.
        self._connect()
        log.info(
            "[mc2.local] LocalMCClient bound: brain=%s base=%s token_fp=%s",
            self.brain, self.base_url, self._token_fp,
        )

    # ── Mongo plumbing ────────────────────────────────────────────────

    def _connect(self) -> None:
        try:
            from pymongo import MongoClient
        except ImportError as exc:  # noqa: BLE001
            log.warning("[mc2.local] pymongo not installed: %s", exc)
            return
        mongo_url = os.environ.get("MONGO_URL", "").strip()
        db_name = os.environ.get("DB_NAME", "").strip()
        if not mongo_url or not db_name:
            log.warning(
                "[mc2.local] MONGO_URL / DB_NAME unset — writes will no-op",
            )
            return
        try:
            self._mongo = MongoClient(
                mongo_url, serverSelectionTimeoutMS=2000,
            )
            self._db = self._mongo[db_name]
        except Exception as exc:  # noqa: BLE001
            log.warning("[mc2.local] MongoClient init failed: %s", exc)

    def _stamp(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Attach common metadata so every local write is grep-able
        back to this sidecar process and brain."""
        doc.setdefault("brain", self.brain)
        doc["destination"] = "mc2_local"
        doc["transport"] = "supervisor_local"
        doc["runtime_token_fp"] = self._token_fp
        doc["recorded_at"] = _now_iso()
        doc["recorded_at_dt"] = _now_dt()
        return doc

    def _write(self, collection: str, doc: dict[str, Any]) -> dict[str, Any]:
        """Single write path with best-effort semantics. Returns the
        envelope the caller used to get from MC's HTTP response so
        the sidecar's success-path logic doesn't change."""
        if self._db is None:
            log.debug(
                "[mc2.local] db not bound — skipping %s write", collection,
            )
            return {"ok": False, "error": "db_not_bound", "skipped": True}
        try:
            stamped = self._stamp(doc)
            doc_id = str(uuid.uuid4())
            stamped["_id"] = doc_id
            self._db[collection].insert_one(stamped)
            log.info(
                "[mc2.local] %s_WRITE id=%s brain=%s",
                collection.upper(), doc_id, self.brain,
            )
            # MC's HTTP responses used various id field names; we mirror
            # the most common ones so callers can pick either.
            return {
                "ok": True,
                "id": doc_id,
                "intent_id": doc_id,
                "contribution_id": doc_id,
                "stance_id": doc_id,
                "destination": "mc2_local",
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("[mc2.local] %s write failed: %s", collection, exc)
            return {"ok": False, "error": str(exc)}

    # ── Public API parity with MCClient ───────────────────────────────

    def close(self) -> None:
        try:
            if self._mongo is not None:
                self._mongo.close()
        except Exception:  # noqa: BLE001
            pass

    def heartbeat(self, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Local heartbeat — single doc per beat in ``mc2_heartbeats``.

        Optional TTL-style pruning is handled at read time by MC2's
        scorecard reader; we just append.
        """
        doc: dict[str, Any] = {
            "kind": "heartbeat",
            "payload": dict(body or {}),
        }
        return self._write(HEARTBEAT_COLLECTION, doc)

    def post_contribution(
        self,
        *,
        mode: str,
        weights: Mapping[str, float],
        learning_rate: float,
        recent_outcomes: Iterable[Mapping[str, Any]] | None = None,
        notes: str = "",
        confidence_delta: float = 0.0,
        delta_reason: str = "",
        training_signal: bool = False,
    ) -> dict[str, Any]:
        # Validate via the same helper the HTTP path used so a bad
        # payload fails fast with the same error class.
        body = build_contribution_body(
            mode=mode,
            weights=weights,
            learning_rate=learning_rate,
            recent_outcomes=recent_outcomes,
            notes=notes,
            confidence_delta=confidence_delta,
            delta_reason=delta_reason,
            training_signal=training_signal,
        )
        body["kind"] = "contribution"
        return self._write(CONTRIBUTION_COLLECTION, body)

    def post_stance(
        self,
        *,
        position_id: str,
        stance: str,
        confidence: float,
        notes: str = "",
        memory_sources: Iterable[str] | None = None,
        confidence_origin: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        if not position_id:
            raise MCContractError("position_id is empty")
        body = build_stance_body(
            stance=stance,
            confidence=confidence,
            notes=notes,
            memory_sources=memory_sources,
            confidence_origin=confidence_origin,
        )
        body["kind"] = "stance"
        body["position_id"] = position_id
        return self._write(STANCE_COLLECTION, body)

    def post_intent(self, **kwargs: Any) -> dict[str, Any]:
        body = build_intent_body(**kwargs)
        body["kind"] = "intent"
        # Mirror the doctrine of the async ``post_intent_local`` — these
        # are headless brain intents; the local writer never sets
        # ``may_execute=True``.
        body.setdefault("may_execute", False)
        body.setdefault("gate_state", "accepted_no_gates")
        return self._write(INTENT_COLLECTION, body)


# ── Helper exposed for the sidecar to make the swap explicit ─────────

def is_standalone_mode() -> bool:
    """True when the operator has set ``RISEDUAL_STANDALONE_MODE=1`` or
    the configured MC base URL points at a ``local://`` sentinel."""
    flag = (os.environ.get("RISEDUAL_STANDALONE_MODE") or "").strip()
    if flag == "1":
        return True
    base = (os.environ.get("MC_BASE_URL") or "").strip().lower()
    return base.startswith("local://") or base == ""


__all__ = [
    "LocalMCClient",
    "is_standalone_mode",
    "MCClientError",
    "MCContractError",
    "HEARTBEAT_COLLECTION",
    "CONTRIBUTION_COLLECTION",
    "STANCE_COLLECTION",
    "INTENT_COLLECTION",
]
