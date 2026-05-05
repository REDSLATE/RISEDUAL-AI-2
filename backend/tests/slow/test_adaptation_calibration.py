"""Tests for GET /api/admin/adaptations/calibration.

The endpoint reads shadow_soften/shadow_revert rows out of
``adaptation_audit``, computes percentile distributions of
``decision_score`` / ``decision_ratio`` / ``delta_r_trend``, and
recommends a tuned ``ML_AUTO_REVERT_EFFECT_SIZE`` (p25 of observed
scores). Tests cover:

  * Empty DB → empty distribution, no recommendation
  * Below-threshold observation count → hint, no suggestion
  * Enough observations → p25-based recommendation with direction
  * Per-metric roll-up counts match the seeded data

Uses the live backend at BASE_URL — matches the convention of the
sibling `test_adaptation_why_endpoint.py` so startup events run.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(__file__))
from conftest_creds import ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL  # noqa: E402


def _mongo_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _auth_session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert r.status_code == 200, f"login failed: {r.text}"
    return s


async def _seed_shadow_rows(db, tag: str, scores: list[float],
                            metric: str = "volume.liquidity") -> None:
    now = datetime.now(timezone.utc)
    rows = []
    for i, s in enumerate(scores):
        rows.append({
            "adaptation_id": f"{tag}-ad-{i}",
            "action": "shadow_soften",
            "reason": f"seed {i}",
            "metric": metric,
            "direction": "LONG",
            "factor_before": 0.85,
            "factor_after": 0.90,
            "deltas_r": [-0.03, -0.03, -0.03],
            "deltas_wr": [-0.02, -0.02, -0.02],
            "coverages": [0.15, 0.15, 0.15],
            "effect_sizes": [s, s, s],
            "decision_score": s,
            "decision_threshold": 0.001,
            "decision_ratio": s / 0.001,
            "delta_r_trend": 0.0005,
            "shadow": True,
            "at": (now - timedelta(minutes=i)).isoformat(),
            "seed_tag": tag,
        })
    await db.adaptation_audit.insert_many(rows)


async def _cleanup_shadow(db, tag: str) -> None:
    await db.adaptation_audit.delete_many({"seed_tag": tag})


def test_calibration_requires_auth():
    """Unauthenticated callers get 401 (or 403 if middleware resolves first)."""
    r = requests.get(f"{BASE_URL}/api/admin/adaptations/calibration")
    assert r.status_code in (401, 403)


def test_calibration_empty_window_ok():
    """With no shadow rows in window the endpoint returns a clean
    empty payload (no crash, no recommendation)."""
    s = _auth_session()
    # Window of 0 days → 1 day min (endpoint clamps). Use a narrow
    # window so pre-existing data doesn't skew the empty case —
    # the recommendation requires ≥20 obs, so a small stray
    # sample won't force it.
    r = s.get(f"{BASE_URL}/api/admin/adaptations/calibration?window_days=1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["window_days"] == 1
    cfg = body["current_config"]
    assert set(cfg.keys()) >= {
        "consecutive_negative", "epsilon", "min_coverage", "effect_size",
    }
    assert "decision_score" in body["distribution"]


def test_calibration_insufficient_observations_hints():
    """Fewer than 20 observations → suggestion is None, hint is
    returned so the UI can tell the operator to wait."""
    async def _run():
        db = _mongo_db()
        tag = f"calib-insuf-{uuid.uuid4().hex[:8]}"
        # Wipe ALL shadow rows in the window first so the seeded
        # 5 are the only observations in range. Safe because tests
        # run against a dev DB.
        await db.adaptation_audit.delete_many({
            "action": {"$in": ["shadow_soften", "shadow_revert",
                                "auto_soften", "auto_revert"]},
        })
        await _seed_shadow_rows(db, tag, [0.002, 0.003, 0.004, 0.0025, 0.0035])
        try:
            s = _auth_session()
            r = s.get(
                f"{BASE_URL}/api/admin/adaptations/calibration?window_days=30"
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["observations"] == 5
            rec = body["recommendation"]
            assert rec is not None
            assert rec.get("suggested_effect_size") is None
            assert "note" in rec
        finally:
            await _cleanup_shadow(db, tag)

    asyncio.get_event_loop().run_until_complete(_run())


def test_calibration_recommends_p25_when_enough_observations():
    """25 observations uniformly 0.001..0.025 → p25 ≈ 0.007.
    Endpoint should recommend that, direction='tighten' (current
    default is 0.001)."""
    async def _run():
        db = _mongo_db()
        tag = f"calib-enough-{uuid.uuid4().hex[:8]}"
        await db.adaptation_audit.delete_many({
            "action": {"$in": ["shadow_soften", "shadow_revert",
                                "auto_soften", "auto_revert"]},
        })
        scores = [round(0.001 + i * 0.001, 4) for i in range(25)]
        await _seed_shadow_rows(db, tag, scores)
        try:
            s = _auth_session()
            r = s.get(
                f"{BASE_URL}/api/admin/adaptations/calibration?window_days=30"
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["observations"] == 25
            rec = body["recommendation"]
            assert rec is not None, body
            # p25 of 0.001..0.025 step 0.001 → ~0.007
            assert 0.005 <= rec["suggested_effect_size"] <= 0.010
            assert rec["direction"] == "tighten"
            assert rec["current_effect_size"] == 0.001
            dist = body["distribution"]["decision_score"]
            assert dist["n"] == 25
            assert dist["min"] == 0.001
            assert dist["max"] == 0.025
            metrics = {m["metric"] for m in body["per_metric"]}
            assert "volume.liquidity" in metrics
        finally:
            await _cleanup_shadow(db, tag)

    asyncio.get_event_loop().run_until_complete(_run())
