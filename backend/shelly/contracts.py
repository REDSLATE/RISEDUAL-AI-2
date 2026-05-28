"""Dataclasses + stable-hash helpers for the 5-Shelly federation.

These contracts are the only schema commitment Shelly makes. Every
write path through Local / MC Shelly funnels through one of these
dataclasses so the operator can grep the codebase by class name to
audit "what fields can Shelly persist."
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from shelly.config import MEMORY_REASONING_ONLY


def utc_now() -> str:
    """ISO-8601 UTC timestamp. Single source of truth for ``created_at``
    fields so audit timestamps line up across local and MC layers."""
    return datetime.now(timezone.utc).isoformat()


def stable_hash(payload: dict[str, Any]) -> str:
    """Order-insensitive sha256 of a JSON-coercible dict.

    Used for ``event_hash`` and ``receipt_hash`` so dedup is robust
    to dict-key reordering and so the same logical event written
    twice doesn't double-count.
    """
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ShellyMemoryEvent:
    """One brain receipt snapshot suitable for durable storage.

    The dataclass is frozen so it can't be mutated between
    ``to_doc()`` and dedup hashing — that would silently break
    idempotency.
    """

    brain: str
    symbol: str
    direction: str
    confidence: float
    decision: str
    features: dict[str, Any]
    mc_status: str
    roadguard_status: str
    outcome: Optional[dict[str, Any]] = None
    created_at: str = ""
    # Phase 2 forward-compat (2026-02-26) — the embedding slot is
    # added now so the wire format never has to change when the
    # Chroma sidecar lands. ``None`` means "not yet embedded";
    # population happens in LocalShelly.remember() once Phase 2
    # ships. Excluded from the event_hash by virtue of being None
    # on every existing write path, so Phase 1 hashes remain
    # stable across the upgrade.
    embedding: Optional[list[float]] = None
    embedding_version: Optional[str] = None

    def to_doc(self) -> dict[str, Any]:
        """Serialize to a Mongo-ready document with stamped metadata.

        Stamps applied (in this order):
          1. ``created_at`` defaulted to UTC now if blank.
          2. ``authority`` set to the doctrine-locked constant.
          3. ``event_hash`` computed AFTER 1+2 so identical
             receipts written at different times still hash
             differently (which is the desired behaviour: we
             dedup on event content + timestamp; otherwise a
             retry would silently overwrite a real new event).
        """
        doc = asdict(self)
        if not doc["created_at"]:
            doc["created_at"] = utc_now()
        doc["authority"] = MEMORY_REASONING_ONLY
        doc["event_hash"] = stable_hash(doc)
        return doc


@dataclass(frozen=True)
class ShellyReasoningReceipt:
    """One reasoning verdict, local or MC scope.

    ``confidence_delta`` is the SUGGESTED nudge — brains may consult
    it but the brain's final confidence is unchanged unless the
    brain itself decides to incorporate Shelly's hint. Shelly does
    not write back into a brain's confidence vector.
    """

    brain: str
    symbol: str
    recommendation: str  # 'support' | 'warn' | 'neutral'
    confidence_delta: float
    reasons: list[str]
    evidence_hashes: list[str]
    authority: str = MEMORY_REASONING_ONLY
    extra: dict[str, Any] = field(default_factory=dict)

    def to_doc(self) -> dict[str, Any]:
        base = asdict(self)
        base["created_at"] = utc_now()
        # Hash excludes the timestamp so identical reasoning over
        # the same evidence hashes is dedup-able even when run
        # twice; that's the desired idempotency for receipts (we
        # don't want a duplicate cache-prime to write twice).
        base["receipt_hash"] = stable_hash({
            k: v for k, v in base.items() if k != "created_at"
        })
        return base


__all__ = [
    "utc_now",
    "stable_hash",
    "ShellyMemoryEvent",
    "ShellyReasoningReceipt",
]
