"""
Tests for Smart Money Verification — Patent J proof-chain block.

Uses ``InMemoryProofChainStore`` (via a tiny async shim) + monkey-patched
``compute_smart_money_score`` to keep the tests pure. No Mongo dependency.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.smart_money_verification import (
    build_verification_payload,
    compute_alignment,
    verify_and_append,
)


# ── compute_alignment pure-function matrix ────────────────────────


@pytest.mark.parametrize(
    "action,signal,expected",
    [
        ("LONG", "bullish", "confirms"),
        ("BUY", "bullish", "confirms"),        # synonym
        ("STRONG_BUY", "bullish", "confirms"),
        ("LONG", "bearish", "contradicts"),
        ("SHORT", "bearish", "confirms"),
        ("SELL", "bearish", "confirms"),
        ("SHORT_OR_AVOID", "bearish", "confirms"),
        ("SHORT", "bullish", "contradicts"),
        ("LONG", "neutral", "neutral"),
        ("SHORT", "neutral", "neutral"),
        ("LONG", "no_data", "no_data"),
        ("LONG", "", "no_data"),
        (None, "bullish", "no_data"),
        ("HOLD", "bullish", "no_data"),        # unknown → no_data
        ("NONSENSE", "bullish", "no_data"),
    ],
)
def test_compute_alignment_matrix(action, signal, expected):
    assert compute_alignment(action, signal) == expected


# ── build_verification_payload shape ──────────────────────────────


def test_payload_shape_stable_and_schema_complete():
    score = {
        "symbol": "AAPL",
        "score": 73,
        "signal": "bullish",
        "holder_count": 12,
        "bullish_count": 8,
        "bearish_count": 2,
        "neutral_count": 2,
        "net_flow_usd": 1_200_000_000,
        "total_value_usd": 50_000_000_000,
        "contributors": [
            {"institution_name": "Berkshire", "type": "increased", "delta_value_usd": 800_000_000},
            {"institution_name": "Vanguard", "type": "increased", "delta_value_usd": 300_000_000},
            {"institution_name": "BlackRock", "type": "increased", "delta_value_usd": 100_000_000},
            {"institution_name": "StateStreet", "type": "decreased", "delta_value_usd": -50_000_000},
        ],
    }
    payload = build_verification_payload(
        symbol="aapl",
        strategist_action="LONG",
        smart_money_score=score,
    )
    # Core alignment
    assert payload["symbol"] == "AAPL"
    assert payload["strategist_action"] == "LONG"
    assert payload["alignment"] == "confirms"
    assert payload["smart_money_signal"] == "bullish"
    assert payload["smart_money_score"] == 73

    # Top-contributors is capped at 3
    assert len(payload["top_contributors"]) == 3
    assert payload["top_contributors"][0]["institution"] == "Berkshire"

    # Aggregates preserved
    assert payload["net_flow_usd"] == 1_200_000_000


def test_payload_handles_no_data_signal():
    score = {"symbol": "XYZ", "score": None, "signal": "no_data",
             "bullish_count": 0, "bearish_count": 0, "neutral_count": 0,
             "net_flow_usd": 0, "total_value_usd": 0, "contributors": []}
    payload = build_verification_payload(
        symbol="XYZ", strategist_action="LONG", smart_money_score=score,
    )
    assert payload["alignment"] == "no_data"
    assert payload["smart_money_score"] is None
    assert payload["top_contributors"] == []


def test_payload_contradicts_short_vs_bullish():
    score = {"signal": "bullish", "score": 80, "holder_count": 5,
             "bullish_count": 5, "bearish_count": 0, "neutral_count": 0,
             "net_flow_usd": 0, "total_value_usd": 0, "contributors": []}
    payload = build_verification_payload(
        symbol="NVDA", strategist_action="SHORT", smart_money_score=score,
    )
    assert payload["alignment"] == "contradicts"


# ── verify_and_append integration (with mocked Mongo + score) ──────


@pytest.mark.asyncio
async def test_verify_and_append_writes_block_on_happy_path():
    """Fully mocked: confirms the block lands with correct event_type
    and payload, using an AsyncMock that records the insert."""
    fake_collection = AsyncMock()
    fake_collection.find_one.return_value = None  # no prior blocks
    fake_collection.insert_one = AsyncMock()

    class FakeDB:
        def __getitem__(self, name):
            return fake_collection
        def __getattr__(self, name):
            return fake_collection

    fake_db = FakeDB()

    fake_score = {
        "symbol": "NVDA", "score": 72, "signal": "bullish",
        "holder_count": 10, "bullish_count": 7, "bearish_count": 1,
        "neutral_count": 2, "net_flow_usd": 500_000_000,
        "total_value_usd": 20_000_000_000, "contributors": [],
    }

    with patch(
        "services.sec_13f_service.compute_smart_money_score",
        new=AsyncMock(return_value=fake_score),
    ):
        result = await verify_and_append(
            fake_db,
            entity_id="dec_abc123",
            symbol="NVDA",
            strategist_action="LONG",
        )

    assert result is not None
    assert result["alignment"] == "confirms"
    fake_collection.insert_one.assert_awaited_once()

    # Inspect the written block
    written = fake_collection.insert_one.await_args[0][0]
    assert written["event_type"] == "SMART_MONEY_VERIFIED"
    assert written["entity_id"] == "dec_abc123"
    assert written["payload"]["alignment"] == "confirms"
    assert written["actor"] == "smart_money_verifier"


@pytest.mark.asyncio
async def test_verify_and_append_degrades_on_score_fetch_error():
    """A broken 13F upstream must STILL write a proof block (with
    no_data alignment), not silently skip."""
    fake_collection = AsyncMock()
    fake_collection.find_one.return_value = None
    fake_collection.insert_one = AsyncMock()

    class FakeDB:
        def __getitem__(self, name):
            return fake_collection
        def __getattr__(self, name):
            return fake_collection

    fake_db = FakeDB()

    with patch(
        "services.sec_13f_service.compute_smart_money_score",
        new=AsyncMock(side_effect=RuntimeError("SEC rate limited")),
    ):
        result = await verify_and_append(
            fake_db,
            entity_id="dec_err",
            symbol="TSLA",
            strategist_action="SHORT",
        )

    assert result is not None
    assert result["alignment"] == "no_data"
    fake_collection.insert_one.assert_awaited_once()
    written = fake_collection.insert_one.await_args[0][0]
    assert written["payload"]["alignment"] == "no_data"


@pytest.mark.asyncio
async def test_verify_and_append_returns_none_on_insert_error():
    """Mongo write failure must never raise into the caller."""
    fake_collection = AsyncMock()
    fake_collection.find_one.return_value = None
    fake_collection.insert_one = AsyncMock(side_effect=RuntimeError("Mongo down"))

    class FakeDB:
        def __getitem__(self, name):
            return fake_collection
        def __getattr__(self, name):
            return fake_collection

    fake_db = FakeDB()

    fake_score = {"signal": "bullish", "score": 70, "contributors": []}
    with patch(
        "services.sec_13f_service.compute_smart_money_score",
        new=AsyncMock(return_value=fake_score),
    ):
        result = await verify_and_append(
            fake_db, entity_id="e", symbol="X", strategist_action="LONG",
        )

    assert result is None


@pytest.mark.asyncio
async def test_verify_and_append_none_db_returns_none():
    result = await verify_and_append(
        None, entity_id="e", symbol="X", strategist_action="LONG",
    )
    assert result is None
