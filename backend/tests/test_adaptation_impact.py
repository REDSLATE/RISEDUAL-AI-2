"""Regression tests for the counterfactual `estimate_adaptation_impact`
helper + its wire-up through `apply_adaptations_to_weights(return_masks=True)`.

Covers:
- Shape + sign of ΔR and Δwin-rate when losses are down-weighted
- Missing `r_multiple` → empty dict (warm-start safety)
- Per-adaptation mask alignment with the summary
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(__file__))


def _mongo_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture()
def seeded_adaptation():
    """Seed one volume.liquidity/LONG adaptation, then tear down."""
    aid = f"test-impact-{uuid.uuid4().hex[:8]}"
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

    asyncio.run(_seed())
    yield aid
    asyncio.run(_teardown())


def test_estimate_adaptation_impact_empty_frame():
    """No r_multiple column → empty dict, no exceptions."""
    import pandas as pd
    from services.ml_retrain_service import estimate_adaptation_impact
    df = pd.DataFrame({"foo": [1, 2, 3]})
    impact = estimate_adaptation_impact(df, [1.0, 1.0, 1.0], [0.9, 0.9, 0.9])
    assert impact == {}


def test_estimate_adaptation_impact_shifts_expectancy():
    """Down-weighting the losers should shift ΔR and Δwin-rate UP."""
    import pandas as pd
    from services.ml_retrain_service import estimate_adaptation_impact
    # 5 winners (R=+1) and 5 losers (R=-1) — baseline E[R] = 0, win-rate = 0.5
    df = pd.DataFrame({"r_multiple": [+1, +1, +1, +1, +1, -1, -1, -1, -1, -1]})
    w_base = [1.0] * 10
    # Adapted weights: down-weight all 5 losers to 0.5
    w_adapt = [1.0, 1.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5, 0.5]
    impact = estimate_adaptation_impact(df, w_base, w_adapt)
    assert impact
    assert impact["baseline_mean_r"] == pytest.approx(0.0)
    assert impact["adapted_mean_r"] > 0.0
    assert impact["delta_mean_r"] == pytest.approx(impact["adapted_mean_r"])
    # win rate: baseline = 0.5, adapted = 5 / (5 + 2.5) ≈ 0.667
    assert impact["adapted_win_rate"] > impact["baseline_win_rate"]
    assert impact["delta_win_rate"] > 0
    assert 0 <= impact["rows_covered_frac"] <= 1


def test_estimate_adaptation_impact_zero_delta_when_weights_equal():
    """w_base == w_adapt → zero deltas across the board."""
    import pandas as pd
    from services.ml_retrain_service import estimate_adaptation_impact
    df = pd.DataFrame({"r_multiple": [1.0, -1.0, 0.5, -0.5]})
    w = [1.0, 1.0, 1.0, 1.0]
    impact = estimate_adaptation_impact(df, w, w)
    assert impact["delta_mean_r"] == pytest.approx(0.0)
    assert impact["delta_win_rate"] == pytest.approx(0.0)
    assert impact["rows_covered_frac"] == pytest.approx(0.0)


def test_apply_adaptations_return_masks_shape(seeded_adaptation):
    """`return_masks=True` returns a 3-tuple whose third element is
    the per-adaptation boolean mask list aligned 1:1 with the summary."""
    import pandas as pd
    from services.model_adaptation import apply_adaptations_to_weights

    async def _run():
        db = _mongo_db()
        df = pd.DataFrame({
            "volume_ratio": [0.5, 0.6, 0.7, 0.55, 1.2, 1.5, 1.0, 2.0, 1.3, 0.95],
        })
        y = pd.Series([0, 0, 0, 0, 1, 1, 1, 1, 1, 0])
        w = pd.Series([1.0] * 10)
        res = await apply_adaptations_to_weights(
            db, df, w, y=y, return_masks=True,
        )
        return res

    res = asyncio.run(_run())
    assert len(res) == 3, "expected (adjusted, summary, masks) 3-tuple"
    _, summary, masks = res
    # masks align 1:1 with summary
    assert len(masks) == len(summary)
    # Find our adaptation's mask (should match 4 rows: first 4 which
    # are all low-volume AND y==0 bearish).
    for s, m in zip(summary, masks):
        if s["adaptation_id"] == seeded_adaptation:
            assert int(m.sum()) == 4
            break
    else:
        pytest.fail("seeded adaptation missing from summary")
