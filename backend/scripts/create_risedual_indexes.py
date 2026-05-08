# scripts/create_risedual_indexes.py

def create_risedual_indexes(db):
    db.decision_proof_chain.create_index([("entity_id", 1), ("created_at", 1)])
    db.decision_proof_chain.create_index("block_hash", unique=True)
    db.decision_proof_chain.create_index("prev_hash")
    db.decision_proof_chain.create_index("event_type")
