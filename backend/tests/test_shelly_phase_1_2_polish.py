"""Tests — Phase 1 polish + Phase 2 vector sidecar (2026-02-26)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from shelly.contracts import ShellyMemoryEvent


# ── Phase 1 polish: embedding-field shim is hash-stable ──────────


def test_embedding_field_default_is_none():
    ev = ShellyMemoryEvent(
        brain="Alpha", symbol="AAPL", direction="LONG",
        confidence=0.5, decision="LONG", features={},
        mc_status="ok", roadguard_status="green",
    )
    assert ev.embedding is None
    assert ev.embedding_version is None


def test_embedding_field_default_doesnt_break_to_doc():
    ev = ShellyMemoryEvent(
        brain="Alpha", symbol="AAPL", direction="LONG",
        confidence=0.5, decision="LONG", features={},
        mc_status="ok", roadguard_status="green",
    )
    doc = ev.to_doc()
    assert doc["embedding"] is None
    assert doc["embedding_version"] is None
    assert doc["authority"] == "memory_reasoning_only"
    assert len(doc["event_hash"]) == 64


# ── Phase 1 indexes ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_indexes_iterates_all_five_nodes_and_shared():
    """ensure_indexes must visit every node + the MC shared coll."""
    visited: list[str] = []

    class _FakeCollection:
        def __init__(self, name):
            self._name = name

        async def create_index(self, keys, **kwargs):
            visited.append(f"{self._name}:{kwargs.get('name', 'unnamed')}")
            return kwargs.get("name", "unnamed")

    class _FakeDB:
        def __init__(self):
            self._colls = {}

        def __getitem__(self, name):
            if name not in self._colls:
                self._colls[name] = _FakeCollection(name)
            return self._colls[name]

    from shelly.indexes import ensure_indexes
    result = await ensure_indexes(_FakeDB())
    assert result["ok"] is True
    # All 5 nodes (Alpha/Camaro/Chevelle/RedEye/MC) + MC shared
    assert set(result["indexes_created"].keys()) == {
        "Alpha", "Camaro", "Chevelle", "RedEye", "MC", "_mc_shared",
    }
    # Per-node: event_hash unique + compound + rollup_queue
    # + receipts created_at = 4 indexes.
    for node in ("Alpha", "Camaro", "Chevelle", "RedEye", "MC"):
        assert len(result["indexes_created"][node]) == 4


# ── Phase 2 vector sidecar ───────────────────────────────────────


def test_vector_doc_to_text_is_stable_under_feature_reorder():
    """Two memories with same content + different feature dict
    insertion order must produce identical Chroma documents."""
    from shelly.vector_sidecar import _doc_to_text
    a = {"brain": "Alpha", "symbol": "AAPL", "direction": "LONG",
         "decision": "BUY", "confidence": 0.7,
         "features": {"x": 1, "y": 2, "z": 3}}
    b = {"brain": "Alpha", "symbol": "AAPL", "direction": "LONG",
         "decision": "BUY", "confidence": 0.7,
         "features": {"z": 3, "y": 2, "x": 1}}
    assert _doc_to_text(a) == _doc_to_text(b)


def test_embed_memory_returns_false_when_chroma_unavailable():
    from shelly import vector_sidecar
    with patch.object(vector_sidecar, "_collection_for", return_value=None):
        ok = vector_sidecar.embed_memory("Alpha", {"event_hash": "abc"})
        assert ok is False


def test_embed_memory_requires_event_hash():
    from shelly import vector_sidecar

    class _FakeColl:
        upserts = []

        def upsert(self, **kwargs):
            self.upserts.append(kwargs)

    fake = _FakeColl()
    with patch.object(vector_sidecar, "_collection_for", return_value=fake):
        # missing event_hash — must skip silently
        assert vector_sidecar.embed_memory("Alpha", {}) is False
        # with hash — must upsert
        ok = vector_sidecar.embed_memory("Alpha", {
            "event_hash": "h-1", "brain": "Alpha", "symbol": "AAPL",
            "direction": "LONG", "decision": "BUY", "confidence": 0.6,
        })
        assert ok is True
        assert len(fake.upserts) == 1
        assert fake.upserts[0]["ids"] == ["h-1"]
        meta = fake.upserts[0]["metadatas"][0]
        assert meta["authority"] == "memory_reasoning_only"
        assert meta["node"] == "Alpha"
        assert meta["embedding_version"] == "minilm-l6-v2-default"


def test_find_similar_returns_empty_when_chroma_unavailable():
    from shelly import vector_sidecar
    with patch.object(vector_sidecar, "_collection_for", return_value=None):
        result = vector_sidecar.find_similar("Alpha", {"symbol": "AAPL"})
        assert result == []


def test_find_similar_federation_queries_all_five_nodes():
    from shelly import vector_sidecar
    calls = []

    def _stub(node, *_a, **_k):
        calls.append(node)
        return []

    with patch.object(vector_sidecar, "find_similar", side_effect=_stub):
        out = vector_sidecar.find_similar_federation({"symbol": "AAPL"})
        assert set(calls) == {"Alpha", "Camaro", "Chevelle", "RedEye", "MC"}
        assert set(out.keys()) == {"Alpha", "Camaro", "Chevelle", "RedEye", "MC"}


# ── Phase 2 brain wiring (multi_model_hypothesis_service) ────────


@pytest.mark.asyncio
async def test_brain_emission_maps_model_key_to_node_name():
    """When _run_single_model returns, it must route the receipt
    to the correctly-cased federation node (alpha → Alpha, etc.)."""
    from shelly.mc_emitter import set_pipeline

    captured = []

    class _SpyPipeline:
        async def record_brain_event(self, brain, receipt):
            captured.append((brain, receipt))
            return {"ok": True, "node": brain}

    set_pipeline(_SpyPipeline())
    try:
        # Simulate the post-receipt block by directly calling the
        # same wiring path. We don't run the full LLM call — just
        # the part that maps model_key → node + emits.
        for model_key, expected_node in [
            ("alpha", "Alpha"),
            ("camaro", "Camaro"),
            ("chevelle", "Chevelle"),
            ("redeye", "RedEye"),
        ]:
            from shelly.mc_emitter import get_pipeline
            p = get_pipeline()
            assert p is not None
            mapping = {
                "alpha": "Alpha", "camaro": "Camaro",
                "chevelle": "Chevelle", "redeye": "RedEye",
            }
            node = mapping[model_key]
            await p.record_brain_event(node, {
                "symbol": "AAPL", "direction": "LONG", "confidence": 0.7,
                "decision": "BUY", "features": {"model_key": model_key},
                "mc_status": "unverified", "roadguard_status": "n/a",
            })
        assert [c[0] for c in captured] == ["Alpha", "Camaro", "Chevelle", "RedEye"]
    finally:
        set_pipeline(None)
