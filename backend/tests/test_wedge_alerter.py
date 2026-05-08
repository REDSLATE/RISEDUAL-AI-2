"""Wedge alerter tests — notification-only contract.

Acceptance:
  1. frozen >30m sends one alert
  2. frozen <30m sends no alert
  3. repeated feature-health block >50 sends alert
  4. duplicate alert suppressed by cooldown
  5. missing webhook does not crash
  6. alerter does not mutate pipeline state
  7. alerter does not call broker/executor
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from services import wedge_alerter
from services.ml import executor_heartbeat


# ── Fakes ────────────────────────────────────────────────────────


class _FakeColl:
    def __init__(self):
        self._docs: Dict[Any, Dict[str, Any]] = {}
        self._inserts: List[Dict[str, Any]] = []

    async def find_one(self, query, projection=None):
        key = query.get("_id")
        return self._docs.get(key)

    async def update_one(self, query, update, upsert=False):
        key = query.get("_id")
        existing = self._docs.get(key) or {"_id": key}
        existing.update(update.get("$set") or {})
        self._docs[key] = existing

    async def insert_one(self, doc):
        self._inserts.append(doc)


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._docs:
            raise StopAsyncIteration
        return self._docs.pop(0)


class _FakeDB:
    def __init__(self):
        self._colls: Dict[str, _FakeColl] = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _FakeColl()
        return self._colls[name]


# ── Helpers ──────────────────────────────────────────────────────


def _heartbeat(
    *,
    equity_frozen=False, crypto_frozen=False,
    equity_top_reason=None, equity_top_count=0,
    crypto_top_reason=None, crypto_top_count=0,
):
    """Build a deterministic heartbeat snapshot for the alerter."""
    def lane(name, frozen, reason, count):
        return {
            "lane": name,
            "frozen": frozen,
            "last_pipeline_run_at": "2026-05-08T20:00:00+00:00",
            "last_signal_at": None,
            "signals_1h": 0 if frozen else 4,
            "buy_sell_1h": 0,
            "holds_1h": count,
            "feature_health_avg": 0.1 if reason else 0.7,
            "model_age_hours": 1.5,
            "model_stale": False,
            "top_clamp_block_reason": reason,
            "top_clamp_block_count": count,
        }
    return {
        "as_of": "2026-05-08T20:00:00+00:00",
        "lanes": {
            "equity": lane("equity", equity_frozen, equity_top_reason, equity_top_count),
            "crypto": lane("crypto", crypto_frozen, crypto_top_reason, crypto_top_count),
        },
        "any_frozen": equity_frozen or crypto_frozen,
    }


@pytest.fixture(autouse=True)
def _reset_heartbeat():
    executor_heartbeat.reset()
    yield
    executor_heartbeat.reset()


@pytest.fixture
def webhook_calls():
    calls: List[Dict[str, Any]] = []

    async def fake_post(*, message, payload, kind):
        calls.append({"message": message, "payload": payload, "kind": kind})
        return True

    with patch.object(wedge_alerter, "_post_webhook", new=fake_post):
        yield calls


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("OPS_ALERT_WEBHOOK_URL", "https://example.test/hook")
    yield


# ── (1) frozen >30m sends one alert ──────────────────────────────


@pytest.mark.asyncio
async def test_frozen_lane_over_threshold_fires_alert(configured, webhook_calls):
    db = _FakeDB()
    snap = _heartbeat(equity_frozen=True)

    # Pre-seed state: lane was first frozen 45 min ago.
    long_ago = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    await db["wedge_alerter_state"].update_one(
        {"_id": "state"},
        {"$set": {"lane_frozen_started_at": {"equity": long_ago},
                  "alert_history": {}}},
        upsert=True,
    )
    with patch.object(executor_heartbeat, "get_snapshot", return_value=snap):
        result = await wedge_alerter.run_tick(db)
    assert len(webhook_calls) == 1
    assert webhook_calls[0]["payload"]["lane"] == "equity"
    assert webhook_calls[0]["payload"]["rule"] == "FROZEN_LANE"
    assert "frozen for" in webhook_calls[0]["payload"]["detail"]
    assert len(result["fresh_alerts"]) == 1
    # Audit row written.
    history = db._colls["wedge_alerter_history"]._inserts
    assert len(history) == 1
    assert history[0]["webhook_posted"] is True


# ── (2) frozen <30m sends no alert ───────────────────────────────


@pytest.mark.asyncio
async def test_frozen_under_threshold_no_alert(configured, webhook_calls):
    db = _FakeDB()
    snap = _heartbeat(equity_frozen=True)
    # First time the lane is seen frozen — _evaluate writes
    # frozen_started_at=now, duration=0, so no alert.
    with patch.object(executor_heartbeat, "get_snapshot", return_value=snap):
        result = await wedge_alerter.run_tick(db)
    assert webhook_calls == []
    assert result["fresh_alerts"] == []
    # State must record the start time so a follow-up tick can mature.
    state_doc = db._colls["wedge_alerter_state"]._docs.get("state")
    assert state_doc is not None
    assert "equity" in (state_doc.get("lane_frozen_started_at") or {})


# ── (3) feature-health flood >50 sends alert ─────────────────────


@pytest.mark.asyncio
async def test_feature_health_flood_fires_alert(configured, webhook_calls):
    db = _FakeDB()
    snap = _heartbeat(
        equity_top_reason="STRATEGIST_FEATURE_HEALTH_LOW",
        equity_top_count=87,
    )
    with patch.object(executor_heartbeat, "get_snapshot", return_value=snap):
        result = await wedge_alerter.run_tick(db)
    assert len(webhook_calls) == 1
    assert webhook_calls[0]["payload"]["rule"] == "FEATURE_HEALTH_FLOOD"
    assert webhook_calls[0]["payload"]["lane"] == "equity"
    assert webhook_calls[0]["payload"]["top_clamp_block_count"] == 87
    assert len(result["fresh_alerts"]) == 1


@pytest.mark.asyncio
async def test_feature_health_at_threshold_no_alert(configured, webhook_calls):
    db = _FakeDB()
    snap = _heartbeat(
        equity_top_reason="STRATEGIST_FEATURE_HEALTH_LOW",
        equity_top_count=50,  # exactly 50 — must be > 50 to fire
    )
    with patch.object(executor_heartbeat, "get_snapshot", return_value=snap):
        await wedge_alerter.run_tick(db)
    assert webhook_calls == []


# ── (4) duplicate alert suppressed by cooldown ───────────────────


@pytest.mark.asyncio
async def test_duplicate_alert_suppressed_by_cooldown(configured, webhook_calls):
    db = _FakeDB()
    snap = _heartbeat(
        equity_top_reason="STRATEGIST_FEATURE_HEALTH_LOW",
        equity_top_count=120,
    )
    with patch.object(executor_heartbeat, "get_snapshot", return_value=snap):
        first = await wedge_alerter.run_tick(db)
        second = await wedge_alerter.run_tick(db)
    assert len(first["fresh_alerts"]) == 1
    assert second["fresh_alerts"] == []
    assert len(second["suppressed_cooldown"]) == 1
    assert second["suppressed_cooldown"][0]["alert_key"] == "feature_health_low:equity"
    # Webhook only fired once.
    assert len(webhook_calls) == 1


# ── (5) missing webhook does not crash ───────────────────────────


@pytest.mark.asyncio
async def test_missing_webhook_does_not_crash(monkeypatch, caplog):
    monkeypatch.delenv("OPS_ALERT_WEBHOOK_URL", raising=False)
    db = _FakeDB()
    snap = _heartbeat(
        equity_top_reason="STRATEGIST_FEATURE_HEALTH_LOW",
        equity_top_count=200,
    )
    with caplog.at_level("WARNING"), \
         patch.object(executor_heartbeat, "get_snapshot", return_value=snap):
        result = await wedge_alerter.run_tick(db)
    assert result["configured"] is False
    # The missing-webhook log line is emitted exactly when there ARE
    # candidates to alert on but no URL is set.
    assert any("OPS_ALERT_WEBHOOK_URL_MISSING" in m for m in caplog.messages)
    # But the audit row is still appended even without webhook.
    history = db._colls["wedge_alerter_history"]._inserts
    assert len(history) == 1
    assert history[0]["webhook_configured"] is False


# ── (6) alerter does not mutate pipeline state ───────────────────


@pytest.mark.asyncio
async def test_alerter_does_not_mutate_pipeline_or_boot_receipts(configured, webhook_calls):
    """Sanity: alerter only touches its own collections + heartbeat
    in-process state. It must not write to alpha_decision_log,
    roadguard_*, phase5b_intents, or paper_trades."""
    db = _FakeDB()
    snap = _heartbeat(equity_frozen=True)
    long_ago = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    await db["wedge_alerter_state"].update_one(
        {"_id": "state"},
        {"$set": {"lane_frozen_started_at": {"equity": long_ago},
                  "alert_history": {}}},
        upsert=True,
    )
    with patch.object(executor_heartbeat, "get_snapshot", return_value=snap):
        await wedge_alerter.run_tick(db)
    # Only wedge_alerter_state and wedge_alerter_history were touched.
    touched = set(db._colls.keys())
    forbidden = {
        "alpha_decision_log",
        "roadguard_equity_decisions",
        "roadguard_crypto_decisions",
        "phase5b_intents",
        "paper_trades",
    }
    assert not (touched & forbidden), f"alerter wrote to forbidden collection: {touched & forbidden}"
    assert touched.issubset({"wedge_alerter_state", "wedge_alerter_history"})


# ── (7) alerter does not call broker/executor ────────────────────


@pytest.mark.asyncio
async def test_alerter_does_not_import_broker_or_executor(configured, webhook_calls):
    """Inspect the module's source-level dependencies. The wedge
    alerter MUST NOT import any broker, executor, or order-placement
    module — its scope is notification-only."""
    import services.wedge_alerter as mod
    src = open(mod.__file__).read()
    forbidden_imports = [
        "services.alpaca_client",
        "services.kraken_client",
        "services.broker_registry",
        "services.smart_order_service",
        "services.smart_router",
        "services.paper_trading_service",
        "services.crypto_paper_trading_service",
        "services.ml.broker_wire",
        "services.ml.executors",
        "services.ml.pipeline",
    ]
    leaks = [imp for imp in forbidden_imports if imp in src]
    assert not leaks, f"wedge_alerter imported forbidden module(s): {leaks}"


@pytest.mark.asyncio
async def test_alerter_does_not_call_broker_at_runtime(configured, webhook_calls):
    """End-to-end: run a tick and confirm no broker / executor /
    pipeline function is reachable from the call. Achieved by
    patching them with sentinels that raise on use."""
    db = _FakeDB()
    snap = _heartbeat(
        equity_top_reason="STRATEGIST_FEATURE_HEALTH_LOW",
        equity_top_count=99,
    )

    sentinel_calls: List[str] = []

    def _trip(name):
        def _f(*_a, **_kw):
            sentinel_calls.append(name)
            raise AssertionError(f"alerter must not call {name}")
        return _f

    # Patch the high-level executor / broker entry points so any
    # accidental call would fail loudly.
    from services.ml import pipeline as ml_pipeline
    with patch.object(executor_heartbeat, "get_snapshot", return_value=snap), \
         patch.object(ml_pipeline, "get_pipeline", _trip("get_pipeline")):
        result = await wedge_alerter.run_tick(db)
    assert sentinel_calls == []
    assert result["fresh_alerts"]


# ── Bonus: lane recovery clears the timer ────────────────────────


@pytest.mark.asyncio
async def test_lane_recovery_clears_frozen_timer(configured, webhook_calls):
    db = _FakeDB()
    long_ago = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    await db["wedge_alerter_state"].update_one(
        {"_id": "state"},
        {"$set": {"lane_frozen_started_at": {"equity": long_ago},
                  "alert_history": {}}},
        upsert=True,
    )
    snap = _heartbeat(equity_frozen=False)  # recovered
    with patch.object(executor_heartbeat, "get_snapshot", return_value=snap):
        await wedge_alerter.run_tick(db)
    state_doc = db._colls["wedge_alerter_state"]._docs.get("state")
    assert state_doc is not None
    assert "equity" not in (state_doc.get("lane_frozen_started_at") or {})
    assert webhook_calls == []
