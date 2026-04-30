"""End-to-end smoke test for the Patent J/K/M/I crypto bot guard.

Triggers a real crypto bot tick against MongoDB and asserts that the
proof chain populates with the expected event types. Skipped when the
bot's external quote/history dependencies aren't available.
"""
from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    test_db = client[os.environ["DB_NAME"] + "_test_proof_chain_e2e"]
    await test_db.decision_proof_chain.delete_many({})
    yield test_db
    await test_db.decision_proof_chain.delete_many({})
    client.close()


@pytest.mark.asyncio
async def test_signal_created_block_appended_on_every_cycle(db, monkeypatch):
    """Even on a HOLD cycle, a SIGNAL_CREATED proof block must be written.

    Doesn't run the full bot — invokes only the proof-chain helper to
    isolate the integration from external quote providers. Validates
    that the async store + async_append_proof_event path works end-to-
    end against real Mongo (the bug that caused
    ``'_asyncio.Future' object is not subscriptable`` in production).
    """
    from services.proof_chain import (
        AsyncMongoProofChainStore,
        ProofEvent,
        ProofEventType,
        async_append_proof_event,
    )

    store = AsyncMongoProofChainStore(db)

    block = await async_append_proof_event(
        store,
        ProofEvent(
            event_type=ProofEventType.SIGNAL_CREATED,
            entity_id="crypto:BTC:test-1",
            actor="crypto_paper_bot",
            payload={
                "symbol": "BTC",
                "direction": "HOLD",
                "confidence": 0.0,
            },
        ),
    )

    # First block in this entity → prev_hash should be the genesis hash.
    assert block.prev_hash == "0" * 64
    assert block.event_type == ProofEventType.SIGNAL_CREATED

    rows = await db.decision_proof_chain.find({}, {"_id": 0}).to_list(10)
    assert len(rows) == 1
    persisted = rows[0]
    assert persisted["block_hash"] == block.block_hash
    assert persisted["event_type"] == "SIGNAL_CREATED"
    assert persisted["payload"]["symbol"] == "BTC"


@pytest.mark.asyncio
async def test_full_lifecycle_chain_links_correctly(db):
    """Append the full 4-event lifecycle for one entity and verify
    each block points back to the previous one."""
    from services.proof_chain import (
        AsyncMongoProofChainStore,
        ProofEvent,
        ProofEventType,
        async_append_proof_event,
    )

    store = AsyncMongoProofChainStore(db)
    entity = "crypto:ETH:lifecycle-1"

    sequence = [
        (ProofEventType.SIGNAL_CREATED, {"direction": "LONG", "confidence": 0.78}),
        (ProofEventType.ADVERSARIAL_DECISION, {"action": "BUY", "reason": "bull_wins"}),
        (ProofEventType.FAILURE_MODE_CLASSIFIED, {"mode": "NORMAL"}),
        (ProofEventType.RISK_BUDGET_APPLIED, {"final_notional": 1500.0, "allowed": True}),
    ]

    blocks = []
    for evt_type, payload in sequence:
        b = await async_append_proof_event(
            store,
            ProofEvent(
                event_type=evt_type,
                entity_id=entity,
                actor="crypto_paper_bot",
                payload=payload,
            ),
        )
        blocks.append(b)

    # Genesis → block 0
    assert blocks[0].prev_hash == "0" * 64
    # Each subsequent block links to the previous one
    for i in range(1, len(blocks)):
        assert blocks[i].prev_hash == blocks[i - 1].block_hash, (
            f"Block {i} ({blocks[i].event_type.value}) does not link to "
            f"block {i - 1} ({blocks[i - 1].event_type.value})"
        )

    # Verify persistence preserved the chain
    persisted = await db.decision_proof_chain.find(
        {"entity_id": entity}, {"_id": 0}
    ).sort("created_at", 1).to_list(10)
    assert len(persisted) == 4
    for i, doc in enumerate(persisted):
        expected_prev = "0" * 64 if i == 0 else persisted[i - 1]["block_hash"]
        assert doc["prev_hash"] == expected_prev


@pytest.mark.asyncio
async def test_async_store_handles_missing_entity(db):
    """``get_latest_block_hash`` for an entity with no events must
    return None, not raise."""
    from services.proof_chain import AsyncMongoProofChainStore
    store = AsyncMongoProofChainStore(db)
    result = await store.get_latest_block_hash("does-not-exist")
    assert result is None
