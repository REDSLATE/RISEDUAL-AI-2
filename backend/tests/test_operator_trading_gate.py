"""Operator Trading Gate — MC-OR-NOTHING DOCTRINE (2026-05-20) invariants.

The gate was restored on 2026-05-20 after the operator order:
*"All trading that is not going through MC must stop."*

The four local trade-insert chokepoints (ml_paper_trader,
crypto_paper_trader, paper_options_service, paper_trading_service)
consult ``gate_or_synthetic`` and must abort when the gate denies.
MC-routed intent emissions are NOT routed through this gate and
remain unaffected.

State precedence: pytest bypass → Mongo runtime override → env default.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pytest

from services import operator_trading_gate as gate


# ── Tiny async Mongo stand-in ──────────────────────────────────────


class _Coll:
    def __init__(self):
        self.docs: list[dict] = []

    async def find_one(self, q=None, proj=None):
        q = q or {}
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                out = dict(d)
                if proj and proj.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    async def insert_one(self, doc):
        d = dict(doc)
        if "_id" not in d:
            d["_id"] = f"id-{len(self.docs) + 1}"
        self.docs.append(d)
        return type("R", (), {"inserted_id": d["_id"]})()

    async def update_one(self, q, upd, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                for k, v in upd.get("$set", {}).items():
                    d[k] = v
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            new_doc = {**q, **upd.get("$set", {})}
            await self.insert_one(new_doc)
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    async def count_documents(self, q):
        return sum(1 for d in self.docs if all(d.get(k) == v for k, v in (q or {}).items()))


class _DB:
    def __init__(self):
        self._cs: dict[str, _Coll] = {}

    def __getitem__(self, name):
        return self._cs.setdefault(name, _Coll())


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_env_and_bypass(monkeypatch):
    """Reset env knobs + the pytest bypass between tests.

    The module default is ``_TEST_MODE_FORCE_AUTHORIZED=True`` so
    legacy tests that DON'T know about the gate are auto-bypassed.
    These tests want to assert the production semantics, so we
    explicitly disable the bypass here.
    """
    monkeypatch.delenv(gate.ENV_KEY_BLOCKED, raising=False)
    monkeypatch.delenv(gate.ENV_KEY_LEGACY, raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    gate._force_test_mode_authorized(False)
    gate._disable_test_mode_bypass(False)
    yield
    # Restore module default so the next test file's autouse-bypass
    # state is what it expects.
    gate._force_test_mode_authorized(True)
    gate._disable_test_mode_bypass(False)


# ── Default-deny invariants ────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_env_blocks_local_trades(monkeypatch):
    """When no env knob is set, the gate denies by default."""
    db = _DB()
    assert await gate.is_authorized(db) is False
    assert gate._env_default_enabled() is False


@pytest.mark.asyncio
async def test_env_blocked_true_means_denied(monkeypatch):
    monkeypatch.setenv("RISEDUAL_LOCAL_TRADES_BLOCKED", "true")
    db = _DB()
    assert await gate.is_authorized(db) is False


@pytest.mark.asyncio
async def test_env_blocked_false_means_allowed(monkeypatch):
    monkeypatch.setenv("RISEDUAL_LOCAL_TRADES_BLOCKED", "false")
    db = _DB()
    # No runtime override yet → env default rules.
    assert await gate.is_authorized(db) is True


@pytest.mark.asyncio
async def test_legacy_env_alias_still_read(monkeypatch):
    """OPERATOR_TRADING_AUTHORIZATION_ENABLED=true → allowed."""
    monkeypatch.setenv("OPERATOR_TRADING_AUTHORIZATION_ENABLED", "true")
    db = _DB()
    assert await gate.is_authorized(db) is True


# ── Runtime override invariants ────────────────────────────────────


@pytest.mark.asyncio
async def test_runtime_override_beats_env_default(monkeypatch):
    """An owner flip in Mongo wins over the env default."""
    monkeypatch.setenv("RISEDUAL_LOCAL_TRADES_BLOCKED", "true")  # env says BLOCK
    db = _DB()
    await gate.set_authorized(db, enabled=True, operator_id="owner@x",
                              note="re-enabling temporarily")
    # Runtime override now says ALLOW → wins.
    assert await gate.is_authorized(db) is True

    await gate.set_authorized(db, enabled=False, operator_id="owner@x",
                              note="stopping again")
    assert await gate.is_authorized(db) is False


@pytest.mark.asyncio
async def test_set_authorized_writes_history():
    db = _DB()
    await gate.set_authorized(db, enabled=True, operator_id="op@x", note="flip-on")
    await gate.set_authorized(db, enabled=False, operator_id="op@x", note="flip-off")
    history = db[gate.HISTORY_COLLECTION].docs
    assert len(history) == 2
    assert history[0]["enabled"] is True
    assert history[1]["enabled"] is False
    assert history[0]["operator_id"] == "op@x"
    assert history[0]["note"] == "flip-on"


@pytest.mark.asyncio
async def test_set_authorized_raises_without_db():
    with pytest.raises(RuntimeError):
        await gate.set_authorized(None, enabled=True, operator_id="x")


# ── gate_or_synthetic chokepoint contract ──────────────────────────


@pytest.mark.asyncio
async def test_gate_or_synthetic_blocks_and_records_synthetic():
    db = _DB()
    # default-deny env
    out = await gate.gate_or_synthetic(
        db, lane="ml_equity_paper", symbol="AAPL",
        intended_decision="BUY", confidence=0.7,
        extras={"trace_id": "abc"},
    )
    assert out is False
    rows = db[gate.SYNTHETIC_COLLECTION].docs
    assert len(rows) == 1
    r = rows[0]
    assert r["decision"] == "NO_TRADE"
    assert r["reason"] == "paused_by_operator_mc_or_nothing"
    assert r["lane"] == "ml_equity_paper"
    assert r["symbol"] == "AAPL"
    assert r["extras"]["synthetic"] is True
    assert r["extras"]["intended_action"] == "PAUSED_BY_OPERATOR:BUY"
    assert r["extras"]["blocker"] == "operator_trading_gate"
    assert r["extras"]["trace_id"] == "abc"


@pytest.mark.asyncio
async def test_gate_or_synthetic_proceeds_when_allowed(monkeypatch):
    monkeypatch.setenv("RISEDUAL_LOCAL_TRADES_BLOCKED", "false")
    db = _DB()
    out = await gate.gate_or_synthetic(
        db, lane="equity", symbol="MSFT",
        intended_decision="BUY", confidence=0.65,
    )
    assert out is True
    # When allowed, no synthetic counterfactual is written.
    assert db[gate.SYNTHETIC_COLLECTION].docs == []


# ── Status payload ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_status_reports_mc_or_nothing_doctrine():
    db = _DB()
    status = await gate.get_status(db)
    assert status["enabled"] is False
    assert status["source"] == "env_default"
    assert status["by_operator"] == "mc_or_nothing_doctrine"
    assert "mc-or-nothing" in status["note"].lower()


@pytest.mark.asyncio
async def test_get_status_reflects_runtime_override(monkeypatch):
    db = _DB()
    await gate.set_authorized(db, enabled=True, operator_id="op@x")
    status = await gate.get_status(db)
    assert status["enabled"] is True
    assert status["source"] == "runtime_override"


# ── Pytest bypass (back-compat with existing fixtures) ─────────────


@pytest.mark.asyncio
async def test_pytest_bypass_lets_legacy_tests_proceed(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/foo.py::bar")
    gate._force_test_mode_authorized(True)
    gate._disable_test_mode_bypass(False)
    db = _DB()
    assert await gate.is_authorized(db) is True


@pytest.mark.asyncio
async def test_pytest_bypass_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/foo.py::bar")
    gate._force_test_mode_authorized(True)
    gate._disable_test_mode_bypass(True)
    db = _DB()
    # Bypass disabled → env default (deny) wins.
    assert await gate.is_authorized(db) is False


def test_back_compat_shims_are_callable():
    """Existing callers must keep working."""
    gate._force_test_mode_authorized(True)
    assert gate._TEST_MODE_FORCE_AUTHORIZED is True
    gate._force_test_mode_authorized(False)
    assert gate._TEST_MODE_FORCE_AUTHORIZED is False
    gate._disable_test_mode_bypass(True)
    assert gate._TEST_MODE_DISABLED_BY_FIXTURE() is True
    gate._disable_test_mode_bypass(False)
