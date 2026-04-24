"""Regression tests for the daily ML-health digest service.

Covers:
  * `collect_ml_health_data` reads audit + adaptation rows correctly
  * Subject line reflects mode (LIVE / SHADOW / OFF) + action count
  * Body HTML contains the key numbers (counts, config, top metrics)
  * Recommendation surfaces once ≥20 shadow observations accumulate
  * `run_ml_health_digest` is idempotent per UTC date
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))


def _mongo_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed_audit(db, tag: str, rows: list[dict]) -> None:
    base = datetime.now(timezone.utc)
    to_insert = []
    for i, r in enumerate(rows):
        to_insert.append({
            "adaptation_id": f"{tag}-{i}",
            "action": r["action"],
            "metric": r.get("metric", "volume.liquidity"),
            "direction": "LONG",
            "decision_score": r.get("decision_score", 0.002),
            "decision_threshold": 0.001,
            "decision_ratio": r.get("decision_score", 0.002) / 0.001,
            "shadow": r["action"].startswith("shadow_"),
            "at": (base - timedelta(minutes=i)).isoformat(),
            "seed_tag": tag,
        })
    if to_insert:
        await db.adaptation_audit.insert_many(to_insert)


async def _seed_active_adaptation(db, tag: str) -> None:
    now = datetime.now(timezone.utc)
    await db.model_adaptations.insert_one({
        "adaptation_id": f"{tag}-ad",
        "metric": "rsi.overbought",
        "direction": "LONG",
        "column": "rsi_14",
        "description": "overbought rows (RSI > 70)",
        "adjustment_factor": 0.9,
        "auto_softening_steps": 1,
        "evidence_count": 5,
        "contrast": 1.6,
        "bucket_rate": 0.42,
        "global_rate": 0.26,
        "severity": 0.04,
        "created_at": now.isoformat(),
        "expires_at": now + timedelta(days=14),
        "active": True,
        "seed_tag": tag,
    })


async def _cleanup(db, tag: str) -> None:
    await db.adaptation_audit.delete_many({"seed_tag": tag})
    await db.model_adaptations.delete_many({"seed_tag": tag})
    await db.ml_health_digest_history.delete_many(
        {"summary.recipient": {"$regex": f"^mlh-{tag}"}},
    )


def test_collect_counts_aggregate_audit_rows_correctly():
    from services.ml_health_digest_service import collect_ml_health_data

    async def _run():
        db = _mongo_db()
        tag = f"mlh-counts-{uuid.uuid4().hex[:8]}"
        try:
            await db.adaptation_audit.delete_many({
                "action": {"$in": ["auto_soften", "auto_revert",
                                    "shadow_soften", "shadow_revert"]},
            })
            await _seed_audit(db, tag, [
                {"action": "shadow_soften", "metric": "volume.liquidity"},
                {"action": "shadow_soften", "metric": "volume.liquidity"},
                {"action": "shadow_revert", "metric": "rsi.overbought"},
                {"action": "auto_soften", "metric": "sector.momentum"},
            ])
            data = await collect_ml_health_data(db, window_hours=24)
            assert data["counts"]["shadow_soften"] == 2
            assert data["counts"]["shadow_revert"] == 1
            assert data["counts"]["auto_soften"] == 1
            assert data["counts"]["total"] == 4
            # Top metric
            metric_names = [m for m, _n in data["top_metrics"]]
            assert metric_names[0] == "volume.liquidity"  # most frequent
            # Config echoes through
            assert "effect_size" in data["config"]
        finally:
            await _cleanup(db, tag)

    asyncio.get_event_loop().run_until_complete(_run())


def test_subject_reflects_mode_and_activity():
    from services.ml_health_digest_service import _format_subject

    # No actions, no mode set → "quiet (OFF)"
    subj = _format_subject({
        "counts": {"total": 0},
        "modes": {"live": False, "shadow": False},
    })
    assert "quiet" in subj and "OFF" in subj

    # Shadow mode with actions
    subj = _format_subject({
        "counts": {"total": 3},
        "modes": {"live": False, "shadow": True},
    })
    assert "3 actions" in subj and "SHADOW" in subj

    # Live mode
    subj = _format_subject({
        "counts": {"total": 1},
        "modes": {"live": True, "shadow": False},
    })
    assert "1 action" in subj and "LIVE" in subj


def test_body_html_contains_critical_content():
    from services.ml_health_digest_service import _format_body_html

    html = _format_body_html({
        "window_hours": 24,
        "counts": {
            "auto_soften": 1, "auto_revert": 0,
            "shadow_soften": 2, "shadow_revert": 1, "total": 4,
        },
        "top_metrics": [("volume.liquidity", 3), ("rsi.overbought", 1)],
        "active_count": 2,
        "recent_active": [{
            "metric": "rsi.overbought", "direction": "LONG",
            "adjustment_factor": 0.9, "auto_softening_steps": 1,
        }],
        "config": {
            "consecutive_negative": 3, "epsilon": 0.01,
            "min_coverage": 0.05, "effect_size": 0.001,
        },
        "modes": {"live": False, "shadow": True},
        "recommendation": {
            "suggested_effect_size": 0.0042,
            "current_effect_size": 0.001,
            "direction": "tighten",
            "observations": 25,
        },
    })
    assert "volume.liquidity" in html
    assert "ML Safety-Rail Health" in html
    assert "effect_size=0.001" in html
    assert "SHADOW" in html
    # Recommendation is rendered
    assert "0.0042" in html
    assert "tighten" in html
    # Roster row
    assert "rsi.overbought" in html
    assert "softened" in html


def test_recommendation_only_fires_above_threshold():
    """<20 observations → no recommendation in the collected data."""
    from services.ml_health_digest_service import collect_ml_health_data

    async def _run():
        db = _mongo_db()
        tag = f"mlh-rec-{uuid.uuid4().hex[:8]}"
        try:
            await db.adaptation_audit.delete_many({
                "action": {"$in": ["auto_soften", "auto_revert",
                                    "shadow_soften", "shadow_revert"]},
            })
            # Seed only 10 observations
            rows = [
                {"action": "shadow_soften", "decision_score": 0.002 + i * 0.0001}
                for i in range(10)
            ]
            await _seed_audit(db, tag, rows)
            data = await collect_ml_health_data(db, window_hours=24)
            assert data["recommendation"] is None
        finally:
            await _cleanup(db, tag)

    asyncio.get_event_loop().run_until_complete(_run())


def test_run_is_idempotent_per_date():
    """Second call on the same UTC date → {sent: False,
    reason: 'already_sent_today'} without re-rendering the email."""
    from services.ml_health_digest_service import run_ml_health_digest

    async def _run():
        db = _mongo_db()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await db.ml_health_digest_history.delete_many({"date": today})
        try:
            first = await run_ml_health_digest(db)
            # Could be sent OR no_email_provider; either way record exists now.
            second = await run_ml_health_digest(db)
            assert second["reason"] == "already_sent_today"
            assert second["sent"] is False
        finally:
            await db.ml_health_digest_history.delete_many({"date": today})

    asyncio.get_event_loop().run_until_complete(_run())
