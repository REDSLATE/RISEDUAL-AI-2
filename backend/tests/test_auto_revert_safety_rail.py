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
                             samples: int = 1000,
                             factor: float = 0.85) -> None:
    """Insert N ml_training_log rows (one per retrain), newest last.
    Each carries `adaptations_applied` with the given per-run deltas
    AT THE GIVEN FACTOR (default 0.85 matches default seed)."""
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
                "factor": factor,
                "delta_mean_r": dr,
                "delta_win_rate": dwr,
            }],
        })


async def _cleanup(db, aid: str) -> None:
    await db.model_adaptations.delete_many({"adaptation_id": aid})
    await db.ml_training_log.delete_many({"model_version": {"$gte": 900}})
    await db.adaptation_audit.delete_many({"adaptation_id": aid})
    await db.agent_activity.delete_many(
        {"type": {"$in": ["adaptation_auto_reverted", "adaptation_auto_softened"]}},
    )


@pytest.fixture()
def patch_auto_revert_enabled(monkeypatch):
    """Flip ML_ADAPTATION_AUTO_REVERT_ENABLED on for the test."""
    monkeypatch.setenv("ML_ADAPTATION_AUTO_REVERT_ENABLED", "true")


def test_auto_revert_disabled_by_default(monkeypatch):
    """When BOTH the live flag (``ML_ADAPTATION_AUTO_REVERT_ENABLED``)
    AND the shadow flag (``ML_ADAPTATION_SHADOW_MODE``) are off, the
    scan is a true no-op. Pin both explicitly: the .env ships with
    shadow mode on, which would otherwise produce ``{'shadow': True}``
    audit rows here even when the live flag is off."""
    monkeypatch.delenv("ML_ADAPTATION_AUTO_REVERT_ENABLED", raising=False)
    monkeypatch.delenv("ML_ADAPTATION_SHADOW_MODE", raising=False)
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
            actions = await evaluate_auto_revert_candidates(db)
            assert actions == []  # flag is off → no action
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True  # still live
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())


def test_auto_revert_reverts_on_3_consecutive_negative(patch_auto_revert_enabled):
    """Happy path — 3 consecutive negative ΔR with good coverage →
    factor is SOFTENED (first step) instead of binary revert.
    Seeded at 0.85 → expected 0.90."""
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
            actions = await evaluate_auto_revert_candidates(db)
            assert len(actions) == 1
            assert actions[0]["action"] == "soften"
            assert actions[0]["factor"] == 0.85
            assert actions[0]["next_factor"] == 0.90
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0},
            )
            assert doc["active"] is True  # still active — just softer
            assert doc["adjustment_factor"] == 0.90
            assert doc["auto_softened"] is True
            assert doc["auto_softening_steps"] == 1
            # Audit row tagged as soften, not revert
            audit = await db.adaptation_audit.find_one(
                {"adaptation_id": aid}, {"_id": 0},
            )
            assert audit is not None
            assert audit["action"] == "auto_soften"
            assert audit["factor_before"] == 0.85
            assert audit["factor_after"] == 0.90
            # Activity event narrated
            evt = await db.agent_activity.find_one(
                {"type": "adaptation_auto_softened"},
                {"_id": 0},
                sort=[("timestamp", -1)],
            )
            assert evt is not None
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())


def test_auto_revert_final_kill_after_ceiling(patch_auto_revert_enabled):
    """Adaptation already softened to 0.95 → next trip flips to
    inactive (final revert) because next step (1.00) exceeds
    AUTO_SOFTEN_MAX_FACTOR."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        from services import agent_activity_service
        agent_activity_service.set_db(db)
        aid = f"test-ar-final-{uuid.uuid4().hex[:8]}"
        # Seed already at the ceiling.
        await _seed_adaptation(db, aid, factor=0.95)
        # 3 retrains at factor=0.95
        base = datetime.now(timezone.utc).timestamp()
        for i, (dr, dwr, rm) in enumerate(zip(
            [-0.03, -0.02, -0.04],
            [-0.02, -0.03, -0.02],
            [150, 150, 150],
        )):
            ts = datetime.fromtimestamp(base - (3 - i) * 3600, tz=timezone.utc)
            await db.ml_training_log.insert_one({
                "started_at": ts, "finished_at": ts,
                "status": "success", "samples": 1000,
                "model_version": 920 + i,
                "adaptations_applied": [{
                    "adaptation_id": aid, "metric": "volume.liquidity",
                    "direction": "LONG", "rows_matched": rm, "factor": 0.95,
                    "delta_mean_r": dr, "delta_win_rate": dwr,
                }],
            })
        try:
            actions = await evaluate_auto_revert_candidates(db)
            assert len(actions) == 1
            assert actions[0]["action"] == "revert"
            assert actions[0]["factor"] == 0.95
            assert actions[0]["next_factor"] is None
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0},
            )
            assert doc["active"] is False
            assert doc["auto_reverted"] is True
            # Audit row tagged as auto_revert
            audit = await db.adaptation_audit.find_one(
                {"adaptation_id": aid}, {"_id": 0},
            )
            assert audit["action"] == "auto_revert"
            # Activity event
            evt = await db.agent_activity.find_one(
                {"type": "adaptation_auto_reverted"},
                {"_id": 0},
                sort=[("timestamp", -1)],
            )
            assert evt is not None
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())


def test_auto_revert_resets_counter_after_soften(patch_auto_revert_enabled):
    """After softening 0.85→0.90, retrains logged at factor=0.85
    should NOT count toward the next trip — only runs at 0.90 do."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        from services import agent_activity_service
        agent_activity_service.set_db(db)
        aid = f"test-ar-reset-{uuid.uuid4().hex[:8]}"
        # Adaptation is ALREADY at 0.90 (post-softening).
        await _seed_adaptation(db, aid, factor=0.90)
        # History: 3 bad runs at OLD factor 0.85 (pre-soften) + 1 bad at 0.90
        base = datetime.now(timezone.utc).timestamp()
        rows = [
            (0.85, -0.04, -0.02, 150),
            (0.85, -0.03, -0.02, 150),
            (0.85, -0.05, -0.03, 150),
            (0.90, -0.04, -0.02, 150),  # only 1 run at current factor
        ]
        for i, (factor, dr, dwr, rm) in enumerate(rows):
            ts = datetime.fromtimestamp(base - (len(rows) - i) * 3600, tz=timezone.utc)
            await db.ml_training_log.insert_one({
                "started_at": ts, "finished_at": ts,
                "status": "success", "samples": 1000,
                "model_version": 930 + i,
                "adaptations_applied": [{
                    "adaptation_id": aid, "metric": "volume.liquidity",
                    "direction": "LONG", "rows_matched": rm, "factor": factor,
                    "delta_mean_r": dr, "delta_win_rate": dwr,
                }],
            })
        try:
            actions = await evaluate_auto_revert_candidates(db)
            # Only 1 run at current factor → grace period holds
            assert actions == []
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "adjustment_factor": 1},
            )
            assert doc["adjustment_factor"] == 0.90  # unchanged
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())


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
            actions = await evaluate_auto_revert_candidates(db)
            assert actions == []
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())


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
            actions = await evaluate_auto_revert_candidates(db)
            assert actions == []
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())


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
            actions = await evaluate_auto_revert_candidates(db)
            assert actions == []
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())


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
            actions = await evaluate_auto_revert_candidates(db)
            # Risk compression detected → don't revert
            assert actions == []
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())


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
            actions = await evaluate_auto_revert_candidates(db)
            assert actions == []  # incomplete history → defer
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1},
            )
            assert doc["active"] is True
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())



def test_auto_revert_respects_effect_size_floor(patch_auto_revert_enabled):
    """ΔR < -0.01 and coverage ≥ 5% individually, but their product
    stays below the effect-size floor (0.001). Adaptation should
    stay alive — it's noise at its scale."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        from services import agent_activity_service
        agent_activity_service.set_db(db)
        aid = f"test-ar-es-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid)
        # ΔR just past the epsilon, coverage just past the floor →
        # effect size ≈ -0.015 × 0.05 = 0.00075 < 0.001 threshold.
        await _seed_training_log(
            db, aid,
            delta_r_list=[-0.015, -0.012, -0.013],
            delta_wr_list=[-0.01, -0.01, -0.01],
            rows_matched_list=[50, 50, 50],  # exactly 5% coverage
            samples=1000,
        )
        try:
            actions = await evaluate_auto_revert_candidates(db)
            assert actions == []  # effect too small → no action
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "active": 1,
                                          "adjustment_factor": 1},
            )
            assert doc["active"] is True
            assert doc["adjustment_factor"] == 0.85  # unchanged
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())


def test_auto_revert_cooldown_pauses_scanner(patch_auto_revert_enabled):
    """An auto_soften audit row within the last 7 days blocks a
    second action on the same adaptation — prevents oscillation."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        from services import agent_activity_service
        agent_activity_service.set_db(db)
        aid = f"test-ar-cooldown-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid, factor=0.90)  # already softened once
        # Seed a fresh audit row from 2 days ago — inside the
        # 7-day cooldown window.
        await db.adaptation_audit.insert_one({
            "adaptation_id": aid,
            "action": "auto_soften",
            "reason": "prior soften",
            "metric": "volume.liquidity",
            "direction": "LONG",
            "factor_before": 0.85,
            "factor_after": 0.90,
            "deltas_r": [-0.03, -0.03, -0.03],
            "deltas_wr": [-0.02, -0.02, -0.02],
            "coverages": [0.15, 0.15, 0.15],
            "at": (datetime.now(timezone.utc)
                   - __import__("datetime").timedelta(days=2)).isoformat(),
        })
        # Seed 3 bad runs at the current (softened) factor — would
        # normally trigger another action, but cooldown should
        # block it.
        await _seed_training_log(
            db, aid,
            delta_r_list=[-0.04, -0.05, -0.03],
            delta_wr_list=[-0.02, -0.03, -0.02],
            rows_matched_list=[150, 150, 150],
            samples=1000,
            factor=0.90,
        )
        try:
            actions = await evaluate_auto_revert_candidates(db)
            assert actions == []  # cooldown blocks
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0, "adjustment_factor": 1},
            )
            assert doc["adjustment_factor"] == 0.90  # unchanged
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())




@pytest.fixture()
def patch_shadow_mode(monkeypatch):
    """Flip ML_ADAPTATION_SHADOW_MODE on WITHOUT live flag."""
    monkeypatch.setenv("ML_ADAPTATION_SHADOW_MODE", "true")
    monkeypatch.delenv("ML_ADAPTATION_AUTO_REVERT_ENABLED", raising=False)


def test_shadow_mode_observes_without_acting(patch_shadow_mode):
    """Shadow mode runs every gate and writes a ``shadow_soften``
    audit row but leaves the adaptation doc untouched — no
    active/factor flip, no activity-feed noise."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        from services import agent_activity_service
        agent_activity_service.set_db(db)
        aid = f"test-shadow-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid)
        await _seed_training_log(
            db, aid,
            delta_r_list=[-0.03, -0.02, -0.04],
            delta_wr_list=[-0.02, -0.03, -0.02],
            rows_matched_list=[150, 150, 150],
            samples=1000,
        )
        try:
            actions = await evaluate_auto_revert_candidates(db)
            assert len(actions) == 1
            assert actions[0]["action"] == "soften"
            assert actions[0]["shadow"] is True
            # Adaptation doc NOT mutated
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0},
            )
            assert doc["active"] is True
            assert doc["adjustment_factor"] == 0.85  # unchanged
            assert "auto_softened" not in doc or doc.get("auto_softened") is not True
            # Audit row written with shadow_ prefix
            audit = await db.adaptation_audit.find_one(
                {"adaptation_id": aid}, {"_id": 0},
            )
            assert audit is not None
            assert audit["action"] == "shadow_soften"
            assert audit["shadow"] is True
            # NO activity event emitted in shadow mode
            evt = await db.agent_activity.find_one(
                {"type": {"$in": ["adaptation_auto_softened",
                                  "adaptation_auto_reverted"]}},
                {"_id": 0},
                sort=[("timestamp", -1)],
            )
            assert evt is None
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())


def test_shadow_mode_does_not_block_itself_via_cooldown(patch_shadow_mode):
    """Shadow rows don't count toward the 7-day cooldown — else
    shadow mode would only emit one observation per rule per week.
    Only live actions block."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        from services import agent_activity_service
        agent_activity_service.set_db(db)
        aid = f"test-shadow-cd-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid)
        # Seed a recent SHADOW audit row — should NOT block.
        await db.adaptation_audit.insert_one({
            "adaptation_id": aid,
            "action": "shadow_soften",
            "reason": "prior shadow",
            "metric": "volume.liquidity",
            "direction": "LONG",
            "factor_before": 0.85,
            "factor_after": 0.90,
            "deltas_r": [-0.03, -0.03, -0.03],
            "deltas_wr": [-0.02, -0.02, -0.02],
            "coverages": [0.15, 0.15, 0.15],
            "shadow": True,
            "at": (datetime.now(timezone.utc)
                   - __import__("datetime").timedelta(days=2)).isoformat(),
        })
        await _seed_training_log(
            db, aid,
            delta_r_list=[-0.03, -0.02, -0.04],
            delta_wr_list=[-0.02, -0.03, -0.02],
            rows_matched_list=[150, 150, 150],
        )
        try:
            actions = await evaluate_auto_revert_candidates(db)
            assert len(actions) == 1  # shadow rows don't cool down
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())


def test_parallel_scanner_handles_multiple_adaptations(
    patch_auto_revert_enabled,
):
    """Scanner must process multiple adaptations concurrently and
    return a stable list of action records."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    async def _run():
        db = _mongo_db()
        from services import agent_activity_service
        agent_activity_service.set_db(db)
        # Seed 5 distinct adaptations, each with 3 bad runs at 0.85
        aids = [f"test-par-{i}-{uuid.uuid4().hex[:6]}" for i in range(5)]
        for i, aid in enumerate(aids):
            await _seed_adaptation(db, aid)
            # Keep each run's model_version unique across
            # adaptations so cleanup filter still catches them.
            base = datetime.now(timezone.utc).timestamp()
            for j, (dr, dwr, rm) in enumerate(zip(
                [-0.03, -0.02, -0.04],
                [-0.02, -0.03, -0.02],
                [150, 150, 150],
            )):
                ts = datetime.fromtimestamp(base - (3 - j) * 3600,
                                            tz=timezone.utc)
                await db.ml_training_log.insert_one({
                    "started_at": ts, "finished_at": ts,
                    "status": "success", "samples": 1000,
                    "model_version": 940 + i * 10 + j,
                    "adaptations_applied": [{
                        "adaptation_id": aid,
                        "metric": "volume.liquidity",
                        "direction": "LONG", "rows_matched": rm,
                        "factor": 0.85,
                        "delta_mean_r": dr, "delta_win_rate": dwr,
                    }],
                })
        try:
            actions = await evaluate_auto_revert_candidates(db)
            # All 5 should soften (0.85 → 0.90)
            assert len(actions) == 5
            acted_ids = {a["adaptation_id"] for a in actions}
            assert acted_ids == set(aids)
            assert all(a["action"] == "soften" for a in actions)
            assert all(a["next_factor"] == 0.90 for a in actions)
        finally:
            for aid in aids:
                await _cleanup(db, aid)

    asyncio.run(_run())



# ── Threshold tuning tests ──

def test_config_defaults_when_env_unset(monkeypatch):
    """With no env overrides, get_auto_revert_config returns the
    module-level defaults. Keeps backward compat so existing tests
    don't change behaviour."""
    from services.model_adaptation import (
        get_auto_revert_config,
        DEFAULT_AUTO_REVERT_CONSECUTIVE_NEGATIVE,
        DEFAULT_AUTO_REVERT_EPSILON,
        DEFAULT_AUTO_REVERT_MIN_COVERAGE,
        DEFAULT_AUTO_REVERT_EFFECT_SIZE,
    )
    for var in (
        "ML_AUTO_REVERT_CONSECUTIVE_NEGATIVE",
        "ML_AUTO_REVERT_EPSILON",
        "ML_AUTO_REVERT_MIN_COVERAGE",
        "ML_AUTO_REVERT_EFFECT_SIZE",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = get_auto_revert_config()
    assert cfg["consecutive_negative"] == DEFAULT_AUTO_REVERT_CONSECUTIVE_NEGATIVE
    assert cfg["epsilon"] == DEFAULT_AUTO_REVERT_EPSILON
    assert cfg["min_coverage"] == DEFAULT_AUTO_REVERT_MIN_COVERAGE
    assert cfg["effect_size"] == DEFAULT_AUTO_REVERT_EFFECT_SIZE


def test_config_env_overrides(monkeypatch):
    """Env vars override the defaults verbatim (post-clamp)."""
    from services.model_adaptation import get_auto_revert_config
    monkeypatch.setenv("ML_AUTO_REVERT_CONSECUTIVE_NEGATIVE", "5")
    monkeypatch.setenv("ML_AUTO_REVERT_EPSILON", "0.02")
    monkeypatch.setenv("ML_AUTO_REVERT_MIN_COVERAGE", "0.08")
    monkeypatch.setenv("ML_AUTO_REVERT_EFFECT_SIZE", "0.0025")
    cfg = get_auto_revert_config()
    assert cfg["consecutive_negative"] == 5
    assert cfg["epsilon"] == 0.02
    assert cfg["min_coverage"] == 0.08
    assert cfg["effect_size"] == 0.0025


def test_config_invalid_env_falls_back(monkeypatch):
    """Malformed env values log and fall back — safety rail never
    goes offline because of a typo."""
    from services.model_adaptation import (
        get_auto_revert_config, DEFAULT_AUTO_REVERT_EFFECT_SIZE,
    )
    monkeypatch.setenv("ML_AUTO_REVERT_EFFECT_SIZE", "not-a-number")
    cfg = get_auto_revert_config()
    assert cfg["effect_size"] == DEFAULT_AUTO_REVERT_EFFECT_SIZE


def test_effect_size_env_override_gates_action(patch_auto_revert_enabled, monkeypatch):
    """Tightening the effect_size threshold via env blocks
    borderline actions that would've fired at the default."""
    from services.model_adaptation import evaluate_auto_revert_candidates

    # Set threshold well above the test's effect_size
    # (ΔR=-0.03, coverage=0.15 → es=0.0045). At 0.01 this should
    # NOT act.
    monkeypatch.setenv("ML_AUTO_REVERT_EFFECT_SIZE", "0.01")

    async def _run():
        db = _mongo_db()
        from services import agent_activity_service
        agent_activity_service.set_db(db)
        aid = f"test-ar-env-{uuid.uuid4().hex[:8]}"
        await _seed_adaptation(db, aid)
        await _seed_training_log(
            db, aid,
            delta_r_list=[-0.03, -0.03, -0.03],
            delta_wr_list=[-0.02, -0.02, -0.02],
            rows_matched_list=[150, 150, 150],
            samples=1000,
        )
        try:
            actions = await evaluate_auto_revert_candidates(db)
            assert actions == []  # raised threshold filtered it out
            doc = await db.model_adaptations.find_one(
                {"adaptation_id": aid}, {"_id": 0},
            )
            assert doc["adjustment_factor"] == 0.85  # unchanged
        finally:
            await _cleanup(db, aid)

    asyncio.run(_run())
