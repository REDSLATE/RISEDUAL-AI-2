"""Pytest coverage for the MC inbox poller.

Mocks the monorepo client and ``shelly.perceive`` so the tests are
fully offline and deterministic.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.mc_inbox_poller as ip


@pytest.fixture(autouse=True)
def _redirect_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Force the state file into a tmp dir so tests are hermetic."""
    p = tmp_path / "state.json"
    monkeypatch.setenv("MC_INBOX_STATE_PATH", str(p))
    return p


# ── State persistence ─────────────────────────────────────────────


def test_load_state_returns_fresh_when_file_absent(_redirect_state: Path) -> None:
    s = ip.load_state()
    assert s["last_seen_opinion_at"] is None
    assert s["last_seen_opinion_id"] is None
    assert s["stats"] == {
        "opinions_ingested": 0,
        "opinions_quarantined": 0,
        "by_peer": {},
    }


def test_save_then_load_roundtrip(_redirect_state: Path) -> None:
    ip.save_state({
        "last_seen_opinion_at": "2026-05-14T08:00:00Z",
        "last_seen_opinion_id": "op-1",
        "stats": {"opinions_ingested": 5, "opinions_quarantined": 1,
                  "by_peer": {"chevelle": 4, "redeye": 1}},
    })
    s = ip.load_state()
    assert s["last_seen_opinion_id"] == "op-1"
    assert s["stats"]["by_peer"]["chevelle"] == 4


def test_save_atomic_no_tmp_leftover(_redirect_state: Path) -> None:
    ip.save_state({"x": 1})
    leftover = list(_redirect_state.parent.glob(".state.*.tmp"))
    assert leftover == []


def test_load_state_handles_corrupt_file(_redirect_state: Path) -> None:
    _redirect_state.parent.mkdir(parents=True, exist_ok=True)
    _redirect_state.write_text("{not json")
    s = ip.load_state()
    assert s["last_seen_opinion_at"] is None  # fresh fallback


# ── Cursor logic ───────────────────────────────────────────────────


def test_filter_new_opinions_all_when_no_cursor() -> None:
    items = [{"id": "a", "created_at": "2026-05-14T01:00:00Z"}]
    assert ip.filter_new_opinions(items, last_seen_at=None, last_seen_id=None) == items


def test_filter_new_opinions_drops_old() -> None:
    items = [
        {"id": "a", "created_at": "2026-05-14T01:00:00Z"},
        {"id": "b", "created_at": "2026-05-14T02:00:00Z"},
        {"id": "c", "created_at": "2026-05-14T03:00:00Z"},
    ]
    fresh = ip.filter_new_opinions(
        items, last_seen_at="2026-05-14T01:30:00Z", last_seen_id="anything",
    )
    assert [i["id"] for i in fresh] == ["b", "c"]


def test_filter_new_opinions_id_tiebreak_on_same_timestamp() -> None:
    items = [
        {"id": "a", "created_at": "2026-05-14T01:00:00Z"},
        {"id": "b", "created_at": "2026-05-14T01:00:00Z"},
        {"id": "c", "created_at": "2026-05-14T01:00:00Z"},
    ]
    fresh = ip.filter_new_opinions(
        items, last_seen_at="2026-05-14T01:00:00Z", last_seen_id="a",
    )
    assert [i["id"] for i in fresh] == ["b", "c"]


def test_advance_cursor_picks_latest() -> None:
    state = {"last_seen_opinion_at": None, "last_seen_opinion_id": None}
    ip.advance_cursor(state, [
        {"id": "a", "created_at": "2026-05-14T01:00:00Z"},
        {"id": "c", "created_at": "2026-05-14T03:00:00Z"},
        {"id": "b", "created_at": "2026-05-14T02:00:00Z"},
    ])
    assert state["last_seen_opinion_at"] == "2026-05-14T03:00:00Z"
    assert state["last_seen_opinion_id"] == "c"


def test_advance_cursor_noop_on_empty() -> None:
    state = {"last_seen_opinion_at": "old", "last_seen_opinion_id": "old"}
    ip.advance_cursor(state, [])
    assert state["last_seen_opinion_at"] == "old"


# ── Balanced reader ────────────────────────────────────────────────


def test_balanced_recent_opinions_caps_chatty_peer() -> None:
    # Chevelle posts 15 in a row; redeye 1; camaro 2; alpha 1.
    items = (
        [{"id": f"c{i}", "runtime": "chevelle"} for i in range(15)]
        + [{"id": "r1", "runtime": "redeye"}]
        + [{"id": "ca1", "runtime": "camaro"}, {"id": "ca2", "runtime": "camaro"}]
        + [{"id": "a1", "runtime": "alpha"}]
    )
    out = ip.balanced_recent_opinions(items, max_per_peer=3, total_cap=10)
    peers = [i["runtime"] for i in out]
    assert peers.count("chevelle") <= 3
    assert peers.count("camaro") <= 3
    assert "redeye" in peers
    assert "alpha" in peers
    assert len(out) <= 10


def test_balanced_recent_opinions_handles_empty() -> None:
    assert ip.balanced_recent_opinions([], max_per_peer=3, total_cap=10) == []


def test_balanced_recent_opinions_total_cap() -> None:
    items = [{"id": f"i{i}", "runtime": "alpha"} for i in range(50)]
    out = ip.balanced_recent_opinions(items, max_per_peer=100, total_cap=7)
    assert len(out) == 7


def test_balanced_recent_opinions_round_robins_across_peers() -> None:
    """Two peers, plenty available → first three picks should alternate."""
    items = [
        {"id": "c1", "runtime": "chevelle"},
        {"id": "c2", "runtime": "chevelle"},
        {"id": "c3", "runtime": "chevelle"},
        {"id": "r1", "runtime": "redeye"},
        {"id": "r2", "runtime": "redeye"},
    ]
    out = ip.balanced_recent_opinions(items, max_per_peer=2, total_cap=4)
    # Expect alternation: chevelle, redeye, chevelle, redeye
    assert [i["id"] for i in out] == ["c1", "r1", "c2", "r2"]


# ── _opinion_text coercion ─────────────────────────────────────────


def test_opinion_text_uses_body_when_present() -> None:
    assert ip._opinion_text({"body": "  buy AAPL on weakness "}) == "buy AAPL on weakness"


def test_opinion_text_fallback_when_body_empty() -> None:
    t = ip._opinion_text({"body": "", "runtime": "chevelle", "stance": "bull",
                          "topic": "symbol:AAPL"})
    assert "chevelle" in t and "bull" in t and "symbol:AAPL" in t


# ── Ingest pipeline (with mocked client + perceive) ────────────────


@pytest.fixture
def _patched_perceive(monkeypatch: pytest.MonkeyPatch):
    """Capture every ``perceive`` call. Returns the list."""
    captured: list[dict] = []

    async def fake_perceive(db, *, payload, source, text=None, metadata=None):
        captured.append({"source": source, "text": text, "metadata": metadata,
                         "payload_kind": type(payload).__name__})
        return {"ok": True, "lane": "memory", "doc": {"id": "doc-1"}}

    # Re-export path avoids the shelly_perception ↔ shelly_memory cycle.
    monkeypatch.setattr(
        "services.shelly_memory.perceive", fake_perceive,
    )
    return captured


def _fake_client_with_opinions(items: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        read_opinions=AsyncMock(return_value={"items": items, "count": len(items)}),
        read_roles_manifest=AsyncMock(return_value={"items": [], "count": 0}),
        read_my_scorecard=AsyncMock(return_value={"runtime": "alpha", "summary": {}}),
    )


@pytest.mark.asyncio
async def test_ingest_opinions_routes_each_through_perceive(
    _patched_perceive, _redirect_state: Path,
) -> None:
    items = [
        {"id": "op-1", "runtime": "chevelle", "topic": "symbol:AAPL",
         "stance": "observation", "body": "Council BUY @ 1.0",
         "created_at": "2026-05-14T08:00:00Z",
         "evidence": {"audit_sequence": 1}},
        {"id": "op-2", "runtime": "redeye", "topic": "symbol:AAPL",
         "stance": "abstain", "body": "contrary case weak",
         "created_at": "2026-05-14T08:01:00Z"},
    ]
    client = _fake_client_with_opinions(items)
    stats = await ip.ingest_opinions_once(db=None, client=client)
    assert stats["fresh"] == 2
    assert stats["ingested"] == 2
    assert stats["quarantined"] == 0
    assert len(_patched_perceive) == 2
    assert _patched_perceive[0]["source"] == "mc.opinion.chevelle"
    assert _patched_perceive[1]["source"] == "mc.opinion.redeye"
    md = _patched_perceive[0]["metadata"]
    assert md["peer"] == "chevelle"
    assert md["topic"] == "symbol:AAPL"
    assert md["stance"] == "observation"
    assert md["evidence_keys"] == ["audit_sequence"]


@pytest.mark.asyncio
async def test_ingest_advances_cursor(_patched_perceive, _redirect_state: Path) -> None:
    items = [
        {"id": "op-9", "runtime": "alpha", "body": "x",
         "created_at": "2026-05-14T08:30:00Z"},
    ]
    client = _fake_client_with_opinions(items)
    await ip.ingest_opinions_once(db=None, client=client)
    s = ip.load_state()
    assert s["last_seen_opinion_at"] == "2026-05-14T08:30:00Z"
    assert s["last_seen_opinion_id"] == "op-9"


@pytest.mark.asyncio
async def test_ingest_skips_already_seen(_patched_perceive, _redirect_state: Path) -> None:
    ip.save_state({
        "last_seen_opinion_at": "2026-05-14T08:00:00Z",
        "last_seen_opinion_id": "op-1",
        "stats": {"opinions_ingested": 0, "opinions_quarantined": 0, "by_peer": {}},
    })
    items = [
        {"id": "op-1", "runtime": "chevelle", "body": "old",
         "created_at": "2026-05-14T08:00:00Z"},
        {"id": "op-2", "runtime": "chevelle", "body": "new",
         "created_at": "2026-05-14T09:00:00Z"},
    ]
    client = _fake_client_with_opinions(items)
    stats = await ip.ingest_opinions_once(db=None, client=client)
    assert stats["fresh"] == 1
    assert stats["ingested"] == 1
    assert len(_patched_perceive) == 1
    assert _patched_perceive[0]["metadata"]["opinion_id"] == "op-2"


@pytest.mark.asyncio
async def test_ingest_counts_quarantine(monkeypatch: pytest.MonkeyPatch,
                                          _redirect_state: Path) -> None:
    async def perceive_quarantines(db, **_kw):
        return {"ok": True, "lane": "malformed", "doc": {"doc_number": 1}}

    monkeypatch.setattr("services.shelly_memory.perceive", perceive_quarantines)
    client = _fake_client_with_opinions([
        {"id": "op-1", "runtime": "alpha", "body": "weird",
         "created_at": "2026-05-14T08:00:00Z"},
    ])
    stats = await ip.ingest_opinions_once(db=None, client=client)
    assert stats["quarantined"] == 1
    assert stats["ingested"] == 0


@pytest.mark.asyncio
async def test_ingest_handles_client_error(monkeypatch: pytest.MonkeyPatch,
                                            _redirect_state: Path) -> None:
    client = SimpleNamespace(read_opinions=AsyncMock(return_value={
        "items": [], "count": 0, "error": "sidecar_disabled"
    }))
    stats = await ip.ingest_opinions_once(db=None, client=client)
    assert stats["polled"] == 0
    assert stats["ingested"] == 0
    assert stats["error"] == "sidecar_disabled"


@pytest.mark.asyncio
async def test_ingest_swallows_perceive_failures(monkeypatch: pytest.MonkeyPatch,
                                                   _redirect_state: Path) -> None:
    async def perceive_raises(db, **_kw):
        raise RuntimeError("shelly is down")

    monkeypatch.setattr("services.shelly_memory.perceive", perceive_raises)
    client = _fake_client_with_opinions([
        {"id": "op-1", "runtime": "alpha", "body": "x",
         "created_at": "2026-05-14T08:00:00Z"},
    ])
    # Must not raise.
    stats = await ip.ingest_opinions_once(db=None, client=client)
    assert stats["fresh"] == 1
    assert stats["ingested"] == 0  # all failed


# ── Roles + scorecard refresh ──────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_roles_persists_snapshot(_redirect_state: Path) -> None:
    client = SimpleNamespace(read_roles_manifest=AsyncMock(return_value={
        "items": [{"runtime": "alpha", "role": "trader"}],
        "count": 1,
    }))
    manifest = await ip.refresh_roles_once(client=client)
    assert manifest["count"] == 1
    s = ip.load_state()
    assert s["roles_snapshot"]["items"][0]["runtime"] == "alpha"
    assert s["last_roles_at"] is not None


@pytest.mark.asyncio
async def test_refresh_scorecard_persists_snapshot(_redirect_state: Path) -> None:
    client = SimpleNamespace(read_my_scorecard=AsyncMock(return_value={
        "runtime": "alpha", "summary": {"wins": 96, "losses": 77},
    }))
    sc = await ip.refresh_scorecard_once(client=client)
    assert sc["summary"]["wins"] == 96
    s = ip.load_state()
    assert s["scorecard_snapshot"]["summary"]["losses"] == 77
