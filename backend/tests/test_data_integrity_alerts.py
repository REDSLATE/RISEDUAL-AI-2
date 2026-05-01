"""
Tests for the data-integrity alert rule evaluator.

Pins the 4-stage contract: read metric → compare to threshold →
respect throttle → dispatch to every channel in parallel AND persist
the event row + update the rule throttle.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_db(rules, metric_rows=None):
    """Minimal Motor-like async db stub wired against
    data_integrity_alert_rules + the metric backing collection."""
    metric_rows = metric_rows or []
    db = MagicMock()

    class _Cursor:
        def __init__(self, rows): self.rows = list(rows)
        def __aiter__(self): return self
        async def __anext__(self):
            if not self.rows: raise StopAsyncIteration
            return self.rows.pop(0)

    # data_integrity_alert_rules
    db.data_integrity_alert_rules.find = lambda q=None: _Cursor(list(rules))
    db.data_integrity_alert_rules.update_one = AsyncMock()
    db.data_integrity_alert_events.insert_one = AsyncMock()

    # data_integrity_metrics backing the "unknown_direction_tokens" metric
    async def count(q):
        since = q.get("fired_at", {}).get("$gte")
        return sum(
            1 for r in metric_rows
            if r.get("metric") == q.get("metric")
            and (since is None or r.get("fired_at", "") >= since)
        )
    db.data_integrity_metrics.count_documents = count

    # Zero-count stubs for the remaining metric tables
    async def zero_count(_q): return 0
    db.prediction_grade_backfill_log.count_documents = zero_count
    db.learning_engine_trade_repair_log.count_documents = zero_count
    db.brute_force_events.count_documents = zero_count
    return db


@pytest.mark.asyncio
async def test_no_breach_produces_no_fire():
    from services.data_integrity_alerts import evaluate_rules_once
    rules = [{
        "_id": "r1", "rule_id": "r1", "enabled": True,
        "metric": "unknown_direction_tokens",
        "window_hours": 24, "threshold": 1, "comparator": "gte",
        "channels": ["slack"], "throttle_hours": 12,
    }]
    db = _make_db(rules, metric_rows=[])
    summary = await evaluate_rules_once(db)
    assert summary["fired"] == 0
    assert summary["breached"] == 0
    db.data_integrity_alert_rules.update_one.assert_not_awaited()
    db.data_integrity_alert_events.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_breach_fires_and_persists_throttle_and_event():
    from services.data_integrity_alerts import evaluate_rules_once
    now_iso = datetime.now(timezone.utc).isoformat()
    rules = [{
        "_id": "r1", "rule_id": "unknown-dir-immediate", "enabled": True,
        "metric": "unknown_direction_tokens",
        "window_hours": 24, "threshold": 1, "comparator": "gte",
        "channels": [],  # empty → no dispatch, but event still persists
        "throttle_hours": 12,
    }]
    metric_rows = [
        {"metric": "unknown_direction_token", "fired_at": now_iso},
        {"metric": "unknown_direction_token", "fired_at": now_iso},
    ]
    db = _make_db(rules, metric_rows=metric_rows)
    summary = await evaluate_rules_once(db)
    assert summary["fired"] == 1
    assert summary["breached"] == 1
    db.data_integrity_alert_rules.update_one.assert_awaited_once()
    db.data_integrity_alert_events.insert_one.assert_awaited_once()
    # The event must carry the actual breach count for the timeline.
    inserted = db.data_integrity_alert_events.insert_one.await_args.args[0]
    assert inserted["count"] == 2
    assert inserted["rule_id"] == "unknown-dir-immediate"
    assert inserted["metric"] == "unknown_direction_tokens"


@pytest.mark.asyncio
async def test_throttle_suppresses_repeat_fire_within_window():
    from services.data_integrity_alerts import evaluate_rules_once
    now = datetime.now(timezone.utc)
    # Rule last fired 1 hour ago; throttle is 12h → must suppress.
    recent = (now - timedelta(hours=1)).isoformat()
    rules = [{
        "_id": "r1", "rule_id": "throttled-rule", "enabled": True,
        "metric": "unknown_direction_tokens",
        "window_hours": 24, "threshold": 1, "comparator": "gte",
        "channels": [], "throttle_hours": 12,
        "last_fired_at": recent, "last_fired_value": 5,
    }]
    db = _make_db(rules, metric_rows=[
        {"metric": "unknown_direction_token", "fired_at": now.isoformat()},
    ])
    summary = await evaluate_rules_once(db)
    assert summary["breached"] == 1
    assert summary["fired"] == 0
    assert summary["throttled"] == 1
    db.data_integrity_alert_events.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_rule_is_skipped():
    from services.data_integrity_alerts import evaluate_rules_once
    rules = [{
        "_id": "r1", "rule_id": "silent", "enabled": False,
        "metric": "unknown_direction_tokens",
        "window_hours": 24, "threshold": 0, "comparator": "gte",
        "channels": ["slack"], "throttle_hours": 12,
    }]
    db = _make_db(rules)
    summary = await evaluate_rules_once(db)
    assert summary["evaluated"] == 1
    assert summary["skipped_disabled"] == 1
    assert summary["fired"] == 0


@pytest.mark.asyncio
async def test_unknown_metric_is_error_not_crash():
    from services.data_integrity_alerts import evaluate_rules_once
    rules = [{
        "_id": "r1", "rule_id": "bogus", "enabled": True,
        "metric": "made_up_metric",  # not in METRIC_COUNTERS
        "window_hours": 24, "threshold": 1, "comparator": "gte",
        "channels": [], "throttle_hours": 12,
    }]
    db = _make_db(rules)
    summary = await evaluate_rules_once(db)
    assert summary["errors"] == 1
    assert summary["fired"] == 0


@pytest.mark.asyncio
async def test_gt_comparator_triggers_only_strictly_above():
    from services.data_integrity_alerts import evaluate_rules_once
    now_iso = datetime.now(timezone.utc).isoformat()
    # Threshold of 2 with "gt" must NOT fire on count=2, must fire on count=3.
    rules = [{
        "_id": "r1", "rule_id": "strict", "enabled": True,
        "metric": "unknown_direction_tokens",
        "window_hours": 24, "threshold": 2, "comparator": "gt",
        "channels": [], "throttle_hours": 0,
    }]

    db_two = _make_db(rules, metric_rows=[
        {"metric": "unknown_direction_token", "fired_at": now_iso}
        for _ in range(2)
    ])
    s1 = await evaluate_rules_once(db_two)
    assert s1["fired"] == 0

    db_three = _make_db(rules, metric_rows=[
        {"metric": "unknown_direction_token", "fired_at": now_iso}
        for _ in range(3)
    ])
    s2 = await evaluate_rules_once(db_three)
    assert s2["fired"] == 1


@pytest.mark.asyncio
async def test_channels_dispatch_in_parallel_with_event_persist():
    from services.data_integrity_alerts import evaluate_rules_once
    now_iso = datetime.now(timezone.utc).isoformat()
    rules = [{
        "_id": "r1", "rule_id": "multi-channel", "enabled": True,
        "metric": "unknown_direction_tokens",
        "window_hours": 24, "threshold": 1, "comparator": "gte",
        "channels": ["slack", "email:ops@example.com"],
        "throttle_hours": 0,
    }]
    db = _make_db(rules, metric_rows=[
        {"metric": "unknown_direction_token", "fired_at": now_iso},
    ])

    with patch(
        "services.data_integrity_alerts._dispatch_slack",
        new=AsyncMock(return_value=True),
    ) as mock_slack, patch(
        "services.data_integrity_alerts._dispatch_email",
        new=AsyncMock(return_value=True),
    ) as mock_email:
        summary = await evaluate_rules_once(db)

    assert summary["fired"] == 1
    mock_slack.assert_awaited_once()
    mock_email.assert_awaited_once()
    inserted = db.data_integrity_alert_events.insert_one.await_args.args[0]
    assert {r["channel"] for r in inserted["dispatch_results"]} == {
        "slack", "email:ops@example.com",
    }
    assert all(r["sent"] is True for r in inserted["dispatch_results"])
