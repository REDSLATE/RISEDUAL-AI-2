"""Regression tests for the auto-revert safety rail.

Covers all four guard gates:
  * Gate 1 — ΔR < -EPSILON across N consecutive retrains
  * Gate 2 — MIN_COVERAGE (≥5% of rows) on at least one run
  * Gate 3 — Δwin_rate ≤ 0 (no risk-compression exemption)
  * Feature flag off → no-op
  * Grace period — adaptation with only 2 runs stays active

Each test seeds a minimal adaptation + ml_training_log rows, calls
``evaluate_auto_revert_candidates``, asserts the expected state,
and tears down.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(__file__))


def _mongo_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed_adaptation(db, aid: str, factor: float = 0.85) -> None:
    now = datetime.now(timezone.utc)
    await db.model_adaptations.insert_one({
        "adaptation_id": aid,
        "metric": "volume.liquidity",
        "direction": "LONG",
        "column": "volume_ratio",
        "description": "low-volume rows (volume_ratio < 0.8x)",
        "adjustment_factor": factor,
        "evidence_count": 5,
        "contrast": 1.52,
        "bucket_rate": 0.38,
        "global_rate": 0.25,
        "severity": 0.042,
        "created_at": now.isoformat(),
        "expires_at": now.isoformat(),
        "active": True,
        "reverted_at": None,
    })


async def _seed_training_log(db, aid: str, delta_r_list: list[float],
                             delta_wr_list: list[float],
                             rows_matched_list: list[int],
                             samples: int = 1000) -> None:
    """Insert N ml_training_log rows (one per retrain), newest last.
    Each carries `adaptations_applied` with the given per-run deltas."""
    base = datetime.now(timezone.utc).timestamp()
    for i, (dr, dwr, rm) in enumerate(
        zip(delta_r_list, delta_wr_list, rows_matched_list)
    ):
        ts = datetime.fromtimestamp(base - (len(delta_r_list) - i) * 3600,
                                    tz=timezone.utc)
        await db.ml_training_log.insert_one({
            "started_at": ts,
            "finished_at": ts,
            "status": "success",
            "samples": samples,
            "model_version": 900 + i,
            "adaptations_applied": [{
                "adaptation_id": aid,
                "metric": "volume.liquidity",
                "direction": "LONG",
                "rows_matched": rm,
                "factor": 0.85,
                "delta_mean_r": dr,
                "delta_win_rate": dwr,
            }],
        })


async def _cleanup(db, aid: str) -> None:
    await db.model_adaptations.delete_many({"adaptation_id": aid})
    await db.ml_training_log.delete_many({"model_version": {"$gte": 900}})
    await db.adaptation_audit.delete_many({"adaptation_id": aid})
    await db.agent_activity.delete_many({"type": "adaptation_auto_reverted"})


@pytest.fixture()
def patch_auto_revert_enabled(monkeypatch):
    """Flip ML_ADAPTATION_AUTO_REVERT_ENABLED on for the test."""
    monkeypatch.setenv("ML_ADAPTATION_AUTO_REVERT_ENABLED", "true")


def test_auto_revert_disabled_by_default():
    """When the env flag is off, the scan is a no-op."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        aid = f"test-ar-noop-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid)
        # 3 runs of strongly-negative ΔR
        await _seed_training_log(
            db, aid,
            delta_r_list=[-0.05, -0.04, -0.06],
            delta_wr_list=[-0.02, -0.03, -0.02],
            rows_matched_list=[150, 150, 150],
        )
        try:
            reverted = await evaluate_auto_revert_candidates(db)
            assert reverted == []  # flag is off → no action
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True  # still live
        finally:
            await _cleanup(db, aid)

    asyncio.get_event_loop().run_until_complete(_run())


def test_auto_revert_reverts_on_3_consecutive_negative(patch_auto_revert_enabled):
    """Happy path — 3 consecutive negative ΔR with good coverage."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        from services import agent_activity_service
        agent_activity_service.set_db(db)  # wire so activity log fires
        aid = f"test-ar-hit-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid)
        await _seed_training_log(
            db, aid,
            delta_r_list=[-0.03, -0.02, -0.04],
            delta_wr_list=[-0.02, -0.03, -0.02],
            rows_matched_list=[150, 150, 150],  # 15% coverage
            samples=1000,
        )
        try:
            reverted = await evaluate_auto_revert_candidates(db)
            assert len(reverted) == 1
            assert reverted[0]["adaptation_id"] == aid
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0},
            )
            assert doc["active"] is False
            assert doc["auto_reverted"] is True
            # Audit row written
            audit = await db.adaptation_audit.find_one(
                {"adaptation_id": aid}, {"_id": 0},
            )
            assert audit is not None
            assert audit["action"] == "auto_revert"
            assert len(audit["deltas_r"]) == 3
            # Activity event narrated
            evt = await db.agent_activity.find_one(
                {"type": "adaptation_auto_reverted"},
                {"_id": 0},
                sort=[("timestamp", -1)],
            )
            assert evt is not None
        finally:
            await _cleanup(db, aid)

    asyncio.get_event_loop().run_until_complete(_run())


def test_auto_revert_respects_grace_period(patch_auto_revert_enabled):
    """Only 2 runs of history → adaptation stays active (grace)."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        aid = f"test-ar-grace-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid)
        await _seed_training_log(
            db, aid,
            delta_r_list=[-0.05, -0.04],  # only 2 runs
            delta_wr_list=[-0.02, -0.03],
            rows_matched_list=[150, 150],
        )
        try:
            reverted = await evaluate_auto_revert_candidates(db)
            assert reverted == []
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True
        finally:
            await _cleanup(db, aid)

    asyncio.get_event_loop().run_until_complete(_run())


def test_auto_revert_respects_epsilon(patch_auto_revert_enabled):
    """Negative but under the epsilon (noise) → no action."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        aid = f"test-ar-eps-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid)
        await _seed_training_log(
            db, aid,
            delta_r_list=[-0.003, -0.005, -0.007],  # all below 0.01
            delta_wr_list=[-0.01, -0.01, -0.01],
            rows_matched_list=[150, 150, 150],
        )
        try:
            reverted = await evaluate_auto_revert_candidates(db)
            assert reverted == []
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True
        finally:
            await _cleanup(db, aid)

    asyncio.get_event_loop().run_until_complete(_run())


def test_auto_revert_skips_on_low_coverage(patch_auto_revert_enabled):
    """ΔR strongly negative but <5% coverage → no revert."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        aid = f"test-ar-cov-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid)
        await _seed_training_log(
            db, aid,
            delta_r_list=[-0.05, -0.04, -0.06],
            delta_wr_list=[-0.03, -0.02, -0.04],
            rows_matched_list=[20, 20, 20],  # 2% coverage
            samples=1000,
        )
        try:
            reverted = await evaluate_auto_revert_candidates(db)
            assert reverted == []
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True
        finally:
            await _cleanup(db, aid)

    asyncio.get_event_loop().run_until_complete(_run())


def test_auto_revert_respects_risk_compression(patch_auto_revert_enabled):
    """Negative ΔR + POSITIVE Δwin_rate = risk compression → keep."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        aid = f"test-ar-rc-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid)
        await _seed_training_log(
            db, aid,
            delta_r_list=[-0.03, -0.02, -0.04],  # ΔR negative
            delta_wr_list=[+0.02, +0.01, +0.03],  # but win-rate UP
            rows_matched_list=[150, 150, 150],
        )
        try:
            reverted = await evaluate_auto_revert_candidates(db)
            # Risk compression detected → don't revert
            assert reverted == []
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True
        finally:
            await _cleanup(db, aid)

    asyncio.get_event_loop().run_until_complete(_run())


def test_auto_revert_skips_on_missing_attribution(patch_auto_revert_enabled):
    """If any of the 3 runs lacks delta_mean_r (pre-impact-layer row)
    we defer the decision — no revert yet."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        aid = f"test-ar-missing-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid)
        # Insert 3 rows manually, one missing delta_mean_r
        now = datetime.now(timezone.utc).timestamp()
        await db.ml_training_log.insert_many([
            {
                "started_at": datetime.fromtimestamp(now - 3600, tz=timezone.utc),
                "samples": 1000, "model_version": 900,
                "adaptations_applied": [{
                    "adaptation_id": aid, "metric": "volume.liquidity",
                    "direction": "LONG", "rows_matched": 150, "factor": 0.85,
                    # delta_mean_r missing
                }],
            },
            {
                "started_at": datetime.fromtimestamp(now - 1800, tz=timezone.utc),
                "samples": 1000, "model_version": 901,
                "adaptations_applied": [{
                    "adaptation_id": aid, "metric": "volume.liquidity",
                    "direction": "LONG", "rows_matched": 150, "factor": 0.85,
                    "delta_mean_r": -0.04, "delta_win_rate": -0.02,
                }],
            },
            {
                "started_at": datetime.fromtimestamp(now, tz=timezone.utc),
                "samples": 1000, "model_version": 902,
                "adaptations_applied": [{
                    "adaptation_id": aid, "metric": "volume.liquidity",
                    "direction": "LONG", "rows_matched": 150, "factor": 0.85,
                    "delta_mean_r": -0.03, "delta_win_rate": -0.02,
                }],
            },
        ])
        try:
            reverted = await evaluate_auto_revert_candidates(db)
            assert reverted == []  # incomplete history → defer
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True
        finally:
            await _cleanup(db, aid)

    asyncio.get_event_loop().run_until_complete(_run())
