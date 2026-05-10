"""Operator Trading Gate — single-doctrine tests.

Per operator order (2026-05-10): "There is only one rule, no
trades until I say so."

These tests pin the gate's invariants:
1. Defaults to disabled.
2. Returns False on any error (fail-closed).
3. Synthetic ADL receipts are written when blocked.
4. Toggling is logged to history.
5. Trade chokepoints honour the gate (chokepoint integration tests).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import operator_trading_gate as gate


# ── Helpers ─────────────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _FakeColl:
    def __init__(self, doc=None):
        self._doc = dict(doc) if doc else None
        self.inserted: list[dict] = []
        self.replaced: list[tuple[dict, dict]] = []

    async def find_one(self, _q):
        return dict(self._doc) if self._doc else None

    async def insert_one(self, doc):
        self._doc = dict(doc)
        self.inserted.append(dict(doc))
        return MagicMock(inserted_id="x")

    async def replace_one(self, _q, doc, *, upsert=False):
        self._doc = dict(doc)
        self.replaced.append((_q, dict(doc)))
        return MagicMock()


class _FakeDB:
    def __init__(self, collections=None):
        self._cols = collections or {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeColl()
        return self._cols[name]


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    # Disable the pytest-bypass so the gate's own tests can assert
    # default-disabled behaviour. Production callers never flip this.
    gate._disable_test_mode_bypass(True)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    gate._state_cache.update({
        "enabled": False,
        "loaded_at": None,
        "by_operator": None,
        "note": None,
    })
    yield
    gate._disable_test_mode_bypass(False)


# ── Default state ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_authorized_defaults_false_when_no_state(monkeypatch):
    monkeypatch.delenv("OPERATOR_TRADING_AUTHORIZATION_ENABLED", raising=False)
    db = _FakeDB()
    assert await gate.is_authorized(db) is False


@pytest.mark.asyncio
async def test_is_authorized_returns_false_on_db_error():
    """Fail-closed — any backend error returns False."""
    db = MagicMock()
    db.__getitem__ = MagicMock(side_effect=RuntimeError("db blew up"))
    assert await gate.is_authorized(db) is False


@pytest.mark.asyncio
async def test_is_authorized_returns_false_when_db_is_none():
    assert await gate.is_authorized(None) is False


# ── Toggle path ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_authorized_writes_state_and_history():
    db = _FakeDB()
    out = await gate.set_authorized(
        db, enabled=True, operator_id="admin@x", note="green light",
    )
    assert out["ok"] is True
    assert out["state"]["enabled"] is True
    assert out["state"]["by_operator"] == "admin@x"

    # State doc upserted.
    state_doc = await db[gate.STATE_COLLECTION].find_one({})
    assert state_doc["enabled"] is True

    # History row written.
    history_col = db[gate.HISTORY_COLLECTION]
    assert any(h["change"] == "toggle" for h in history_col.inserted)


@pytest.mark.asyncio
async def test_toggle_off_after_on_invalidates_cache():
    db = _FakeDB()
    await gate.set_authorized(
        db, enabled=True, operator_id="op1", note="on",
    )
    assert await gate.is_authorized(db) is True
    await gate.set_authorized(
        db, enabled=False, operator_id="op1", note="off",
    )
    assert await gate.is_authorized(db) is False


# ── Synthetic counterfactual ───────────────────────────────────────


@pytest.mark.asyncio
async def test_record_paused_synthetic_writes_adl_row(monkeypatch):
    captured = {}

    async def fake_record_decision(db, **kwargs):
        captured.update(kwargs)
        return "adl-id-1"

    import services.alpha_decision_log as adl
    monkeypatch.setattr(adl, "record_decision", fake_record_decision)
    db = _FakeDB()
    rid = await gate.record_paused_synthetic(
        db,
        lane="equity_paper",
        symbol="AAPL",
        decision="PAUSED_BY_OPERATOR:BUY",
        confidence=0.62,
        extras={"qty": 5},
    )
    assert rid == "adl-id-1"
    assert captured["symbol"] == "AAPL"
    assert captured["lane"] == "equity_paper"
    assert captured["decision"] == "NO_TRADE"
    assert captured["blocked_at"] == "executor"
    assert captured["extras"]["synthetic"] is True
    assert captured["extras"]["intended_action"] == "PAUSED_BY_OPERATOR:BUY"
    assert captured["extras"]["blocker"] == "operator_trading_gate"


@pytest.mark.asyncio
async def test_record_paused_synthetic_swallows_failures(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("ADL down")
    import services.alpha_decision_log as adl
    monkeypatch.setattr(adl, "record_decision", boom)
    db = _FakeDB()
    out = await gate.record_paused_synthetic(
        db, lane="x", symbol="y", decision="z",
    )
    assert out is None


@pytest.mark.asyncio
async def test_gate_or_synthetic_returns_true_when_authorized(monkeypatch):
    db = _FakeDB()
    await gate.set_authorized(
        db, enabled=True, operator_id="op", note="",
    )

    # Stub the synthetic writer so we can assert it's NOT called.
    called = []
    async def fake_record(*_a, **_k):
        called.append(True)
    monkeypatch.setattr(gate, "record_paused_synthetic", fake_record)

    ok = await gate.gate_or_synthetic(
        db, lane="equity_paper", symbol="AAPL",
        intended_decision="BUY", confidence=0.5,
    )
    assert ok is True
    assert called == []


@pytest.mark.asyncio
async def test_gate_or_synthetic_returns_false_and_logs_when_disabled(monkeypatch):
    db = _FakeDB()  # default: no state row → disabled
    called = []
    async def fake_record(_db, **kwargs):
        called.append(kwargs)
    monkeypatch.setattr(gate, "record_paused_synthetic", fake_record)

    ok = await gate.gate_or_synthetic(
        db, lane="crypto_paper", symbol="BTC-USD",
        intended_decision="BUY", confidence=0.7,
    )
    assert ok is False
    assert len(called) == 1
    assert called[0]["lane"] == "crypto_paper"
    assert called[0]["symbol"] == "BTC-USD"
    assert "PAUSED_BY_OPERATOR" in called[0]["decision"]


# ── Bootstrapping ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_load_creates_state_row_from_env(monkeypatch):
    monkeypatch.setenv("OPERATOR_TRADING_AUTHORIZATION_ENABLED", "true")
    db = _FakeDB()
    state = await gate._load_state(db)
    assert state["enabled"] is True
    history = db[gate.HISTORY_COLLECTION]
    assert any(h["change"] == "bootstrap" for h in history.inserted)


@pytest.mark.asyncio
async def test_first_load_defaults_disabled_when_env_absent(monkeypatch):
    monkeypatch.delenv("OPERATOR_TRADING_AUTHORIZATION_ENABLED", raising=False)
    db = _FakeDB()
    state = await gate._load_state(db)
    assert state["enabled"] is False
