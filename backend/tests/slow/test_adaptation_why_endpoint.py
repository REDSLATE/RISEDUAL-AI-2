"""Regression tests for `GET /api/admin/adaptations/why/{id}` and
the enriched `retrain_adaptation_applied` activity payload.

Closes the loop: ``apply_adaptations_to_weights`` now emits
`mean_weight_before/after/delta` + per-row `lift`/`severity`/
`evidence_count`/`description`, and the admin endpoint turns a
persisted adaptation into a plain-English explanation + projected
effect.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

import pytest
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


@pytest.fixture()
def seeded_adaptation():
    """Insert a volume.liquidity/LONG adaptation, yield its id,
    then delete it (and any activity rows that mention it)."""
    aid = f"test-adap-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    async def _seed():
        db = _mongo_db()
        await db.model_adaptations.insert_one({
            "adaptation_id": aid,
            "metric": "volume.liquidity",
            "direction": "LONG",
            "column": "volume_ratio",
            "description": "low-volume rows (volume_ratio < 0.8x)",
            "adjustment_factor": 0.85,
            "evidence_count": 5,
            "contrast": 1.52,
            "bucket_rate": 0.38,
            "global_rate": 0.25,
            "severity": 0.042,
            "created_at": now.isoformat(),
            "expires_at": now + timedelta(days=14),
            "active": True,
            "reverted_at": None,
        })

    async def _teardown():
        db = _mongo_db()
        await db.model_adaptations.delete_many({"adaptation_id": aid})
        await db.agent_activity.delete_many(
            {"metadata.adaptations.adaptation_id": aid}
        )

    asyncio.get_event_loop().run_until_complete(_seed())
    yield aid
    asyncio.get_event_loop().run_until_complete(_teardown())


def test_why_returns_404_for_unknown_adaptation():
    s = _auth_session()
    r = s.get(f"{BASE_URL}/api/admin/adaptations/why/does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"] == "Adaptation not found"


def test_why_requires_admin():
    # no cookies → 401
    r = requests.get(f"{BASE_URL}/api/admin/adaptations/why/anything")
    assert r.status_code == 401


def test_why_returns_full_payload(seeded_adaptation):
    aid = seeded_adaptation
    s = _auth_session()
    r = s.get(f"{BASE_URL}/api/admin/adaptations/why/{aid}")
    assert r.status_code == 200
    d = r.json()
    # Core identity
    assert d["adaptation_id"] == aid
    assert d["metric"] == "volume.liquidity"
    assert d["direction"] == "LONG"
    # Factor / weight reduction math
    assert d["factor"] == 0.85
    assert d["weight_reduction_pct"] == 15
    # Contrast + rates preserved
    assert d["lift"] == 1.52
    assert d["bucket_rate"] == 0.38
    assert d["global_rate"] == 0.25
    assert d["evidence_count"] == 5
    # Narrative
    assert "Low-liquidity" in d["explanation"]
    assert "1.52" in d["explanation"]
    assert "bullish side" in d["explanation"]  # LONG → bullish
    assert "15%" in d["projected_effect"]


def test_apply_adaptations_emits_enriched_payload(seeded_adaptation):
    """Direct call into `apply_adaptations_to_weights` should log
    an activity event with mean_weight_* and per-row narrative
    fields."""
    import pandas as pd
    from services import agent_activity_service
    from services.model_adaptation import apply_adaptations_to_weights

    async def _run():
        db = _mongo_db()
        agent_activity_service.set_db(db)
        df = pd.DataFrame({
            "volume_ratio": [0.5, 0.6, 0.7, 0.55, 1.2, 1.5, 1.0, 2.0, 1.3, 0.95],
        })
        y = pd.Series([0, 0, 0, 0, 1, 1, 1, 1, 1, 0])
        w = pd.Series([1.0] * 10)
        _adjusted, summary = await apply_adaptations_to_weights(db, df, w, y=y)
        return summary, await db.agent_activity.find_one(
            {
                "type": "retrain_adaptation_applied",
                "metadata.adaptations.adaptation_id": seeded_adaptation,
            },
            {"_id": 0},
            sort=[("timestamp", -1)],
        )

    summary, evt = asyncio.get_event_loop().run_until_complete(_run())
    # summary carries the narrative fields
    row = next(s for s in summary if s["adaptation_id"] == seeded_adaptation)
    assert row["lift"] == 1.52
    assert row["severity"] == 0.042
    assert row["evidence_count"] == 5
    assert row["description"].startswith("low-volume rows")
    assert row["direction"] == "LONG"

    # activity event carries mean_weight_* and the same row
    assert evt is not None
    md = evt["metadata"]
    assert "mean_weight_before" in md
    assert "mean_weight_after" in md
    assert "mean_weight_delta" in md
    # dry-run mode (the default) leaves weights untouched
    assert md["mean_weight_before"] == 1.0
    assert md["mean_weight_after"] == 1.0
    assert md["mean_weight_delta"] == 0.0
    # the adaptation row is mirrored into the event
    mirrored = next(
        a for a in md["adaptations"] if a["adaptation_id"] == seeded_adaptation
    )
    assert mirrored["lift"] == 1.52
