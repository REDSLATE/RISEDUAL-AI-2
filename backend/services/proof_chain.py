"""
RISEDUAL Patent J — Proof-of-Decision Chain

Tamper-evident, append-only proof chain for AI trading decisions.

Purpose:
    Hash-link each decision event to the prior event so that the full lifecycle
    of a signal can be audited later.

This does not place trades. It only records cryptographic proof events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Protocol
import hashlib
import json


class ProofEventType(str, Enum):
    SIGNAL_CREATED = "SIGNAL_CREATED"
    STRATEGIST_DECISION = "STRATEGIST_DECISION"
    ADVERSARIAL_DECISION = "ADVERSARIAL_DECISION"
    AUDITOR_VERDICT = "AUDITOR_VERDICT"
    AUTHORITY_VALIDATED = "AUTHORITY_VALIDATED"
    FAILURE_MODE_CLASSIFIED = "FAILURE_MODE_CLASSIFIED"
    RISK_BUDGET_APPLIED = "RISK_BUDGET_APPLIED"
    SMART_MONEY_VERIFIED = "SMART_MONEY_VERIFIED"
    SOVEREIGN_DECISION = "SOVEREIGN_DECISION"
    EXECUTION_ATTEMPTED = "EXECUTION_ATTEMPTED"
    EXECUTION_FILLED = "EXECUTION_FILLED"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    OUTCOME_VERIFIED = "OUTCOME_VERIFIED"
    MUTATION_PROPOSED = "MUTATION_PROPOSED"
    PROMOTION_SIGNED = "PROMOTION_SIGNED"
    KILL_SWITCH_TRIGGERED = "KILL_SWITCH_TRIGGERED"


@dataclass(frozen=True)
class ProofEvent:
    event_type: ProofEventType
    entity_id: str
    payload: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str = "system"
    schema_version: str = "risedual.proof_chain.v1"


@dataclass(frozen=True)
class ProofBlock:
    block_hash: str
    prev_hash: str
    event_type: ProofEventType
    entity_id: str
    payload_hash: str
    payload: dict[str, Any]
    actor: str
    schema_version: str
    created_at: datetime


class ProofChainStore(Protocol):
    def get_latest_block_hash(self, entity_id: Optional[str] = None) -> Optional[str]:
        ...

    def insert_block(self, block: ProofBlock) -> None:
        ...


GENESIS_HASH = "0" * 64


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_created_at(dt: datetime) -> str:
    """Canonical ISO form for hash material.

    Two normalisation steps so the hash is reproducible after a Mongo
    round-trip:

    1. Strip ``tzinfo`` (Mongo's BSON Date returns naive UTC).
    2. Truncate sub-millisecond precision (BSON Date stores
       milliseconds; microseconds beyond ms are silently dropped on
       insert).

    Both ``build_proof_block`` (write) and ``verify_chain`` (read) MUST
    pipe ``created_at`` through this helper so insert-time and
    verify-time hash material is byte-identical.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    # Truncate microseconds to milliseconds (3-digit precision).
    ms = (dt.microsecond // 1000) * 1000
    dt = dt.replace(microsecond=ms)
    return dt.isoformat()


def build_proof_block(event: ProofEvent, prev_hash: Optional[str]) -> ProofBlock:
    actual_prev_hash = prev_hash or GENESIS_HASH
    payload_hash = stable_hash(event.payload)

    block_material = {
        "prev_hash": actual_prev_hash,
        "event_type": event.event_type.value,
        "entity_id": event.entity_id,
        "payload_hash": payload_hash,
        "actor": event.actor,
        "schema_version": event.schema_version,
        "created_at": canonical_created_at(event.created_at),
    }

    block_hash = stable_hash(block_material)

    return ProofBlock(
        block_hash=block_hash,
        prev_hash=actual_prev_hash,
        event_type=event.event_type,
        entity_id=event.entity_id,
        payload_hash=payload_hash,
        payload=event.payload,
        actor=event.actor,
        schema_version=event.schema_version,
        created_at=event.created_at,
    )


def append_proof_event(store: ProofChainStore, event: ProofEvent) -> ProofBlock:
    prev_hash = store.get_latest_block_hash(event.entity_id)
    block = build_proof_block(event, prev_hash)
    store.insert_block(block)
    return block


# ── Index management ─────────────────────────────────────────────────


async def ensure_indexes(db: Any, collection_name: str = "decision_proof_chain") -> None:
    """Create the indexes the Mongo-backed proof chain needs.

    Mirrors ``scripts/create_risedual_indexes.py`` for the
    ``decision_proof_chain`` collection. Idempotent; safe to call on
    every startup. Async (motor) variant — the sync pymongo version
    lives in the scripts module for offline ops use.
    """
    if db is None:
        return
    coll = db[collection_name]
    try:
        await coll.create_index([("entity_id", 1), ("created_at", 1)])
        await coll.create_index("block_hash", unique=True)
        await coll.create_index("prev_hash")
        await coll.create_index("event_type")
    except Exception:
        # Index conflicts (e.g. existing index with a different name)
        # are non-fatal — the collection still works.
        pass


class InMemoryProofChainStore:
    """
    Test/local implementation.

    Production should back this with MongoDB collection:
        decision_proof_chain
    """

    def __init__(self) -> None:
        self.blocks: list[ProofBlock] = []

    def get_latest_block_hash(self, entity_id: Optional[str] = None) -> Optional[str]:
        candidates = self.blocks
        if entity_id is not None:
            candidates = [b for b in self.blocks if b.entity_id == entity_id]

        if not candidates:
            return None

        return candidates[-1].block_hash

    def insert_block(self, block: ProofBlock) -> None:
        self.blocks.append(block)


class MongoProofChainStore:
    """
    Mongo-backed store.

    Usage:
        store = MongoProofChainStore(db)
        append_proof_event(store, event)

    Recommended index:
        db.decision_proof_chain.create_index([("entity_id", 1), ("created_at", 1)])
        db.decision_proof_chain.create_index("block_hash", unique=True)
    """

    def __init__(self, db: Any, collection_name: str = "decision_proof_chain") -> None:
        self.collection = db[collection_name]

    def get_latest_block_hash(self, entity_id: Optional[str] = None) -> Optional[str]:
        query: dict[str, Any] = {}
        if entity_id is not None:
            query["entity_id"] = entity_id

        doc = self.collection.find_one(query, sort=[("created_at", -1)])
        if not doc:
            return None

        return str(doc["block_hash"])

    def insert_block(self, block: ProofBlock) -> None:
        self.collection.insert_one(
            {
                "block_hash": block.block_hash,
                "prev_hash": block.prev_hash,
                "event_type": block.event_type.value,
                "entity_id": block.entity_id,
                "payload_hash": block.payload_hash,
                "payload": block.payload,
                "actor": block.actor,
                "schema_version": block.schema_version,
                "created_at": block.created_at,
            }
        )


class AsyncMongoProofChainStore:
    """
    Motor-compatible (async) Mongo-backed store.

    The sync ``MongoProofChainStore`` is for offline / pymongo use; this
    one is for the live FastAPI app where ``db`` is an
    ``AsyncIOMotorDatabase``. Use ``async_append_proof_event`` to write
    events through this store.
    """

    def __init__(self, db: Any, collection_name: str = "decision_proof_chain") -> None:
        # Keep the raw db handle so callers (e.g., Smart Money
        # Verification in the IP contract) can attach additional
        # proof blocks without plumbing a second Mongo reference
        # through every context.
        self._db = db
        self.collection = db[collection_name]

    async def get_latest_block_hash(
        self, entity_id: Optional[str] = None,
    ) -> Optional[str]:
        query: dict[str, Any] = {}
        if entity_id is not None:
            query["entity_id"] = entity_id

        doc = await self.collection.find_one(query, sort=[("created_at", -1)])
        if not doc:
            return None
        return str(doc["block_hash"])

    async def insert_block(self, block: ProofBlock) -> None:
        await self.collection.insert_one(
            {
                "block_hash": block.block_hash,
                "prev_hash": block.prev_hash,
                "event_type": block.event_type.value,
                "entity_id": block.entity_id,
                "payload_hash": block.payload_hash,
                "payload": block.payload,
                "actor": block.actor,
                "schema_version": block.schema_version,
                "created_at": block.created_at,
            }
        )


async def async_append_proof_event(
    store: "AsyncMongoProofChainStore", event: ProofEvent,
) -> ProofBlock:
    """Async variant of ``append_proof_event`` for Motor-backed stores."""
    prev_hash = await store.get_latest_block_hash(event.entity_id)
    block = build_proof_block(event, prev_hash)
    await store.insert_block(block)
    return block


def verify_chain(blocks: list[ProofBlock]) -> tuple[bool, list[str]]:
    """
    Verifies local chain continuity and block hashes.

    Returns:
        (valid, reasons)
    """
    reasons: list[str] = []

    for idx, block in enumerate(blocks):
        expected_prev = GENESIS_HASH if idx == 0 else blocks[idx - 1].block_hash

        if block.prev_hash != expected_prev:
            reasons.append(f"prev_hash_mismatch_at_index_{idx}")

        expected_payload_hash = stable_hash(block.payload)
        if block.payload_hash != expected_payload_hash:
            reasons.append(f"payload_hash_mismatch_at_index_{idx}")

        expected_block_hash = stable_hash(
            {
                "prev_hash": block.prev_hash,
                "event_type": block.event_type.value,
                "entity_id": block.entity_id,
                "payload_hash": block.payload_hash,
                "actor": block.actor,
                "schema_version": block.schema_version,
                "created_at": canonical_created_at(block.created_at),
            }
        )

        if block.block_hash != expected_block_hash:
            reasons.append(f"block_hash_mismatch_at_index_{idx}")

    return len(reasons) == 0, reasons
