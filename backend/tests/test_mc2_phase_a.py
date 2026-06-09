"""MC2 Phase A — standalone-mode wire severance tests (2026-06-09).

Doctrine pins:
    1. ``is_standalone()`` reads ``RISEDUAL_STANDALONE_MODE`` exactly.
       Default OFF (no env var → False).
    2. When standalone, ``intent_bridge.emit_opinion_from_consensus``
       writes to ``mc2_opinions`` and does NOT call
       ``risedual_monorepo_client.post_opinion``.
    3. When standalone, ``intent_bridge.emit_intent_from_consensus``
       writes to ``mc2_intents`` and does NOT call
       ``MCClient.post_intent``.
    4. When standalone, ``mc_checkin.checkin_now`` returns the
       synthetic ``standalone_local`` verdict and does NOT issue an
       outbound HTTP POST.
    5. ``sovereign_outcome_bridge.enqueue_outcome`` mirrors into
       ``mc2_outcomes`` whenever standalone is on (additive — the
       legacy ``sovereign_outcomes_inbox`` write still happens).
    6. MC2 writers no-op cleanly when ``set_db`` has not been called.
    7. The scorecard rollup returns the zeroed shape on a fresh DB.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.mc2 import (
    get_scorecard,
    get_state,
    is_standalone,
    post_intent_local,
    post_opinion_local,
    record_outcome_local,
    set_db,
)
from services.mc2 import standalone as _mc2_standalone


# ── 1. is_standalone — env var contract ────────────────────────────


def test_is_standalone_default_off(monkeypatch):
    monkeypatch.delenv("RISEDUAL_STANDALONE_MODE", raising=False)
    assert is_standalone() is False


@pytest.mark.parametrize("val", ["1", "true", "True", "yes", "on"])
def test_is_standalone_truthy_values(monkeypatch, val):
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", val)
    assert is_standalone() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", "", "garbage"])
def test_is_standalone_non_truthy_values(monkeypatch, val):
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", val)
    assert is_standalone() is False


# ── 2. Writers no-op cleanly without db ──────────────────────────


@pytest.mark.asyncio
async def test_post_intent_local_unbound_db():
    set_db(None)
    out = await post_intent_local({"symbol": "BTC", "action": "BUY"})
    assert out["ok"] is False
    assert "not bound" in out["error"]


@pytest.mark.asyncio
async def test_post_opinion_local_unbound_db():
    set_db(None)
    out = await post_opinion_local({"symbol": "BTC", "verdict": "HOLD"})
    assert out["ok"] is False
    assert "not bound" in out["error"]


@pytest.mark.asyncio
async def test_record_outcome_local_unbound_db():
    set_db(None)
    out = await record_outcome_local({"brain": "alpha", "outcome_label": "win"})
    assert out["ok"] is False
    assert "not bound" in out["error"]


# ── 3. Writers persist with shape + provenance ────────────────────


class _FakeColl:
    def __init__(self) -> None:
        self.inserted: list[dict] = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return MagicMock(inserted_id="fake-id")

    async def count_documents(self, _q):
        return len(self.inserted)

    def find(self, _q, _proj):
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.limit.return_value = cursor

        async def _to_list(length):
            return self.inserted[-length:]
        cursor.to_list = _to_list
        return cursor

    def aggregate(self, _pipeline):
        # Aggregate by outcome_label.
        from collections import Counter
        c = Counter(d.get("outcome_label", "flat") for d in self.inserted)

        async def _async_iter(self):
            for label, count in c.items():
                yield {"_id": label, "count": count}
        agg = MagicMock()
        agg.__aiter__ = _async_iter
        return agg


class _FakeDB:
    def __init__(self) -> None:
        self.mc2_intents = _FakeColl()
        self.mc2_opinions = _FakeColl()
        self.mc2_outcomes = _FakeColl()

    def __getitem__(self, key):
        return getattr(self, key)


@pytest.mark.asyncio
async def test_post_intent_local_persists():
    db = _FakeDB()
    set_db(db)
    out = await post_intent_local({
        "brain_id": "alpha",
        "symbol": "BTC",
        "action": "BUY",
        "trace_id": "t-1",
    })
    assert out["ok"] is True
    assert out["gate_state"] == "accepted_no_gates"
    assert out["destination"] == "mc2_local"
    assert "intent_id" in out

    assert len(db.mc2_intents.inserted) == 1
    doc = db.mc2_intents.inserted[0]
    # Doctrine pin: every Phase-A intent is forced headless.
    assert doc["may_execute"] is False
    assert doc["gate_state"] == "accepted_no_gates"
    assert doc["destination"] == "mc2_local"
    assert doc["brain_id"] == "alpha"
    assert doc["symbol"] == "BTC"
    assert doc["trace_id"] == "t-1"
    assert "recorded_at" in doc


@pytest.mark.asyncio
async def test_post_intent_does_not_mutate_caller_dict():
    """Pin: defensive shallow-copy. The same payload object is
    reused by the sovereign sidecar across logs — mutating it
    here would leak intent_id / gate_state into upstream logs."""
    db = _FakeDB()
    set_db(db)
    caller_payload = {"brain_id": "alpha", "symbol": "BTC", "action": "BUY"}
    await post_intent_local(caller_payload)
    assert "intent_id" not in caller_payload
    assert "gate_state" not in caller_payload
    assert "may_execute" not in caller_payload


@pytest.mark.asyncio
async def test_record_outcome_local_coerces_invalid_label():
    db = _FakeDB()
    set_db(db)
    out = await record_outcome_local({
        "brain": "alpha",
        "outcome_label": "GARBAGE_LABEL",
    })
    assert out["ok"] is True
    assert db.mc2_outcomes.inserted[0]["outcome_label"] == "flat"


# ── 4. Scorecard rollup ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_scorecard_empty_db():
    set_db(None)
    sc = await get_scorecard("alpha")
    assert sc == {
        "brain": "alpha",
        "total_resolved": 0,
        "win": 0,
        "loss": 0,
        "flat": 0,
        "win_rate": 0.0,
    }


@pytest.mark.asyncio
async def test_scorecard_rolls_up_outcomes():
    db = _FakeDB()
    set_db(db)
    for label in ["win", "win", "win", "loss", "flat"]:
        await record_outcome_local({"brain": "alpha", "outcome_label": label})
    sc = await get_scorecard("alpha")
    assert sc["win"] == 3
    assert sc["loss"] == 1
    assert sc["flat"] == 1
    assert sc["total_resolved"] == 5
    # 3 / (3 + 1) = 0.75
    assert sc["win_rate"] == 0.75


# ── 5. get_state reflects standalone flag ────────────────────────


@pytest.mark.asyncio
async def test_get_state_includes_standalone_flag(monkeypatch):
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", "1")
    set_db(None)
    state = await get_state()
    assert state["standalone_mode"] is True
    assert state["intents"]["count"] == 0
    assert state["opinions"]["count"] == 0
    assert state["outcomes"]["count"] == 0


# ── 6. intent_bridge.emit_opinion routes to MC2 when standalone ──


@pytest.mark.asyncio
async def test_emit_opinion_routes_to_mc2_when_standalone(monkeypatch):
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", "1")
    db = _FakeDB()
    set_db(db)

    # Patch the legacy wire-out so we can assert it is NOT called.
    legacy_post = AsyncMock(return_value={"ok": True})
    with patch(
        "services.risedual_monorepo_client.post_opinion",
        new=legacy_post,
    ):
        from sovereign.intent_bridge import emit_opinion_from_consensus
        receipt = {
            "symbol": "BTC", "verdict": "HOLD",
            "confidence": 0.55, "model_label": "consensus",
            "trace_id": "trace-1",
        }
        result = await emit_opinion_from_consensus(receipt)

    assert result is not None
    assert result["ok"] is True
    assert result["destination"] == "mc2_local"
    # The wire path was NEVER called.
    legacy_post.assert_not_called()
    # MC2 collected the opinion.
    assert len(db.mc2_opinions.inserted) == 1
    doc = db.mc2_opinions.inserted[0]
    # The payload shape is the wire shape — symbol lives inside
    # ``evidence`` since the discussion-layer protocol nests it there.
    assert doc["topic"] == "symbol:BTC"
    assert doc["evidence"]["symbol"] == "BTC"
    assert doc["may_execute"] is False


@pytest.mark.asyncio
async def test_emit_opinion_still_uses_wire_when_not_standalone(monkeypatch):
    monkeypatch.delenv("RISEDUAL_STANDALONE_MODE", raising=False)
    db = _FakeDB()
    set_db(db)

    legacy_post = AsyncMock(return_value={"ok": True, "via": "legacy"})
    with patch(
        "services.risedual_monorepo_client.post_opinion",
        new=legacy_post,
    ):
        from sovereign.intent_bridge import emit_opinion_from_consensus
        receipt = {
            "symbol": "BTC", "verdict": "HOLD",
            "confidence": 0.55, "model_label": "consensus",
            "trace_id": "trace-1",
        }
        await emit_opinion_from_consensus(receipt)

    # Legacy wire fired; MC2 did NOT collect.
    legacy_post.assert_called_once()
    assert len(db.mc2_opinions.inserted) == 0


# ── 7. mc_checkin no-ops in standalone mode ──────────────────────


@pytest.mark.asyncio
async def test_mc_checkin_severs_wire_in_standalone(monkeypatch):
    monkeypatch.setenv("RISEDUAL_STANDALONE_MODE", "1")
    # Even if MC URL / token are unset, the standalone branch must
    # short-circuit before the env-var lookups raise.
    monkeypatch.delenv("RISEDUAL_MC_URL", raising=False)
    monkeypatch.delenv("ALPHA_MC_INGEST_TOKEN", raising=False)

    from services.mc_checkin import checkin_now
    body = await checkin_now()
    assert body["ok"] is True
    assert body["verdict"] == "standalone_local"
    assert body["destination"] == "mc2_local"
