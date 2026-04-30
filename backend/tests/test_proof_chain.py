from services.proof_chain import (
    InMemoryProofChainStore,
    ProofEvent,
    ProofEventType,
    append_proof_event,
    verify_chain,
)


def test_proof_chain_links_blocks():
    store = InMemoryProofChainStore()

    b1 = append_proof_event(
        store,
        ProofEvent(
            event_type=ProofEventType.SIGNAL_CREATED,
            entity_id="trade-1",
            payload={"symbol": "BTCUSD", "action": "BUY"},
        ),
    )

    b2 = append_proof_event(
        store,
        ProofEvent(
            event_type=ProofEventType.RISK_BUDGET_APPLIED,
            entity_id="trade-1",
            payload={"multiplier": 0.5},
        ),
    )

    assert b2.prev_hash == b1.block_hash

    valid, reasons = verify_chain(store.blocks)
    assert valid is True
    assert reasons == []


def test_proof_chain_detects_tampering():
    store = InMemoryProofChainStore()

    append_proof_event(
        store,
        ProofEvent(
            event_type=ProofEventType.SIGNAL_CREATED,
            entity_id="trade-1",
            payload={"symbol": "ETHUSD", "action": "SELL"},
        ),
    )

    tampered = store.blocks[0]
    object.__setattr__(tampered, "payload", {"symbol": "ETHUSD", "action": "BUY"})

    valid, reasons = verify_chain(store.blocks)
    assert valid is False
    assert "payload_hash_mismatch_at_index_0" in reasons
