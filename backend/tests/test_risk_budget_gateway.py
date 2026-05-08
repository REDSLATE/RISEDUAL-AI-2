"""Tests for the Patent-I integration gateway.

Covers the three minting helpers, the TrackRecord builder (against a
real MongoDB), the daily-loss helper, and the ``enforce_budget``
façade (decision returned + persisted to ``risk_budget_decisions``).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from services.authority_risk_budget import AuthorityTier
from services.risk_budget_gateway import (
    build_track_record_for_crypto_bot,
    build_track_record_for_user,
    enforce_budget,
    ensure_indexes,
    get_daily_realized_loss,
    mint_authority_for_bridge,
    mint_authority_for_crypto_bot,
    mint_authority_for_user,
    set_db,
)


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    test_db = client[os.environ["DB_NAME"] + "_test_risk_budget_gateway"]
    for c in (
        "crypto_paper_trades",
        "crypto_adversarial_log",
        "predictions",
        "paper_trades",
        "risk_budget_decisions",
    ):
        await test_db[c].delete_many({})
    set_db(test_db)
    await ensure_indexes()
    yield test_db
    for c in (
        "crypto_paper_trades",
        "crypto_adversarial_log",
        "predictions",
        "paper_trades",
        "risk_budget_decisions",
    ):
        await test_db[c].delete_many({})
    client.close()


# ── Authority minting ─────────────────────────────────────────────────


def test_mint_authority_for_user_owner_gets_elevated():
    scope = mint_authority_for_user({"_id": "u1", "role": "owner"}, "equity")
    assert scope.tier == AuthorityTier.ELEVATED_LIVE
    assert scope.max_multiplier == 1.25
    assert scope.asset_type == "equity"
    assert scope.engine == "manual_user"


def test_mint_authority_for_user_admin_gets_standard():
    scope = mint_authority_for_user({"_id": "u2", "role": "admin"}, "equity")
    assert scope.tier == AuthorityTier.STANDARD_LIVE
    assert scope.max_multiplier == 0.75


def test_mint_authority_for_user_user_gets_limited():
    scope = mint_authority_for_user({"_id": "u3", "role": "user"}, "options")
    assert scope.tier == AuthorityTier.LIMITED_LIVE
    assert scope.max_multiplier == 0.35
    assert scope.asset_type == "options"


def test_mint_authority_for_crypto_bot():
    scope = mint_authority_for_crypto_bot()
    assert scope.tier == AuthorityTier.STANDARD_LIVE
    assert scope.engine == "adversarial_commander"
    assert scope.asset_type == "crypto"
    assert scope.authority_id == "system:crypto_paper_bot"


def test_mint_authority_for_bridge_starts_limited():
    scope = mint_authority_for_bridge(
        "test_bridge", bridge_value=0.20, bridge_version="v1", activated_by="ops",
    )
    # Newly-activated bridges always start at LIMITED_LIVE per spec.
    assert scope.tier == AuthorityTier.LIMITED_LIVE
    # max_multiplier is min(0.35, bridge_value) so it can't exceed 0.35
    assert scope.max_multiplier == 0.20
    assert "test_bridge" in scope.authority_id


def test_bridge_authority_caps_oversized_value_at_035():
    scope = mint_authority_for_bridge(
        "boom", bridge_value=5.0, bridge_version="v1", activated_by="ops",
    )
    assert scope.max_multiplier == 0.35


# ── TrackRecord builder ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_track_record_handles_empty_collection(db):
    track = await build_track_record_for_crypto_bot()
    assert track.total_trades == 0
    assert track.win_rate == 0.0
    assert track.loss_streak == 0


@pytest.mark.asyncio
async def test_track_record_computes_loss_streak_and_win_rate(db):
    """Three losers then five wins → win_rate≈0.625, loss_streak=3."""
    base = datetime.now(timezone.utc)
    trades = []
    # Older trades first (sort by created_at desc means newest first;
    # our streak walker only cares about contiguous losses anywhere).
    for i, pnl in enumerate([-1.0, -2.0, -3.0, 1.0, 2.0, 3.0, 4.0, 5.0]):
        trades.append({
            "status": "closed",
            "realized_pnl": pnl,
            "created_at": (base - timedelta(hours=i)).isoformat(),
        })
    await db.crypto_paper_trades.insert_many(trades)

    track = await build_track_record_for_crypto_bot()
    assert track.total_trades == 8
    assert track.win_rate == pytest.approx(5 / 8)
    assert track.loss_streak == 3
    assert track.realized_pnl == pytest.approx(9.0)


@pytest.mark.asyncio
async def test_track_record_for_user_uses_predictions(db):
    base = datetime.now(timezone.utc)
    docs = []
    for correct in [True, True, False, False, False, True]:
        docs.append({
            "user_id": "u1",
            "asset_class": "equity",
            "verified_24h": {"correct": correct},
            "created_at": base.isoformat(),
        })
    await db.predictions.insert_many(docs)

    track = await build_track_record_for_user("u1", "equity")
    assert track.total_trades == 6
    assert track.win_rate == pytest.approx(3 / 6)


@pytest.mark.asyncio
async def test_daily_realized_loss_only_sums_negatives(db):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await db.crypto_paper_trades.insert_many([
        {"status": "closed", "closed_at": f"{today}T10:00:00", "realized_pnl": -100.0},
        {"status": "closed", "closed_at": f"{today}T11:00:00", "realized_pnl": -50.0},
        {"status": "closed", "closed_at": f"{today}T12:00:00", "realized_pnl": 200.0},
        {"status": "closed", "closed_at": f"{today}T13:00:00", "realized_pnl": -25.0},
    ])
    loss = await get_daily_realized_loss(asset_class="crypto")
    assert loss == 175.0


# ── enforce_budget end-to-end ────────────────────────────────────────


@pytest.mark.asyncio
async def test_enforce_budget_persists_decision(db):
    scope = mint_authority_for_crypto_bot()
    track = await build_track_record_for_crypto_bot()  # empty → small_sample
    decision = await enforce_budget(
        action="BUY",
        base_notional=1000.0,
        base_multiplier=0.8,
        authority=scope,
        track_record=track,
        daily_realized_loss=0.0,
        context={"smoke": True},
    )
    # Empty trade history → small_sample_tightening floors at 0.5x
    assert decision.allowed is True
    assert decision.final_multiplier <= 0.5

    rows = await db.risk_budget_decisions.find({}, {"_id": 0}).to_list(10)
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "BUY"
    assert row["allowed"] is True
    assert row["audit_hash"] == decision.audit_hash
    assert row["context"]["smoke"] is True
    assert "small_sample_tightening" in row["reasons"]


@pytest.mark.asyncio
async def test_enforce_budget_denies_hold_action(db):
    scope = mint_authority_for_crypto_bot()
    track = await build_track_record_for_crypto_bot()
    decision = await enforce_budget(
        action="HOLD",
        base_notional=1000.0,
        base_multiplier=1.0,
        authority=scope,
        track_record=track,
    )
    assert decision.allowed is False
    assert decision.final_notional == 0.0
    assert "hold_action_no_trade_promotion" in decision.reasons
