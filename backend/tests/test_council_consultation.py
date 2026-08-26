"""Tests for the Council Consultation service.

Covers the doctrine invariants:
* Modulator is always in [MIN_MODULATOR, 1.0] — never > 1, never < floor.
* Kill switch OFF ⇒ modulator forced to 1.0 (behavior unchanged).
* Kill switch OFF ⇒ shadow modulator still computed + logged.
* Failure of the DB reads ⇒ neutral modulator (trade proceeds).
* Consultation never raises.
"""
from __future__ import annotations

import pytest

from services import council_consultation as cc


class TestModulatorMath:
    def test_no_votes_returns_full_size(self):
        m, cons, diss = cc._derive_modulator("BUY", [])
        assert m == 1.0
        assert cons == "unknown"
        assert diss == 0.0

    def test_all_agree_returns_full_size(self):
        votes = [
            {"brain": "s1", "direction": "up", "confidence": 0.9},
            {"brain": "s2", "direction": "up", "confidence": 0.8},
        ]
        m, cons, _ = cc._derive_modulator("BUY", votes)
        assert m == 1.0
        assert cons == "up"

    def test_all_disagree_hits_floor(self):
        votes = [
            {"brain": "s1", "direction": "down", "confidence": 0.9},
            {"brain": "s2", "direction": "down", "confidence": 0.9},
            {"brain": "s3", "direction": "down", "confidence": 0.8},
        ]
        m, cons, diss = cc._derive_modulator("BUY", votes)
        assert m == cc.MIN_MODULATOR
        assert cons == "down"
        assert diss == 1.0

    def test_mixed_scales_between(self):
        # 2 agree, 1 dissent → ratio ~0.33 → mild shrink.
        votes = [
            {"brain": "s1", "direction": "up", "confidence": 0.9},
            {"brain": "s2", "direction": "up", "confidence": 0.9},
            {"brain": "s3", "direction": "down", "confidence": 0.9},
        ]
        m, _, _ = cc._derive_modulator("BUY", votes)
        assert cc.MIN_MODULATOR < m < 1.0

    def test_modulator_never_grows(self):
        # Even with unanimous high-confidence agreement the ceiling
        # is 1.0. The council can NEVER grow Alpha's size.
        votes = [{"brain": f"b{i}", "direction": "up", "confidence": 1.0}
                 for i in range(10)]
        m, _, _ = cc._derive_modulator("BUY", votes)
        assert m == 1.0

    def test_modulator_never_negative_or_zero(self):
        votes = [{"brain": "s1", "direction": "down", "confidence": 1.0}]
        m, _, _ = cc._derive_modulator("BUY", votes)
        assert m >= cc.MIN_MODULATOR
        assert m > 0


class _FakeCursor:
    def __init__(self, docs): self._docs = list(docs)
    def sort(self, *_a, **_kw): return self
    def limit(self, *_a, **_kw): return self
    def __aiter__(self):
        self._i = 0; return self
    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]; self._i += 1
        return d


class _FakeColl:
    def __init__(self, docs=None):
        self._docs = list(docs or [])
        self.inserts = []
    def find(self, *_a, **_kw): return _FakeCursor(self._docs)
    async def find_one(self, *_a, **_kw):
        return self._docs[0] if self._docs else None
    async def insert_one(self, doc):
        self.inserts.append(doc)


class _FakeDB:
    def __init__(self, **collections):
        self._colls = {k: _FakeColl(v) for k, v in collections.items()}
    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeColl())
    def __getattr__(self, name):
        return self[name]


@pytest.mark.asyncio
async def test_kill_switch_off_forces_modulator_one(monkeypatch):
    """Doctrine: default OFF ⇒ modulator = 1.0 even if council dissents."""
    monkeypatch.delenv(cc.ENV_ENFORCE, raising=False)
    db = _FakeDB(mc2_contributions=[
        {"brain": "sov", "direction": "down", "confidence": 0.9,
         "recorded_at_dt": __import__("datetime").datetime.now(
             __import__("datetime").timezone.utc)},
    ])
    r = await cc.consult_council(db, symbol="AAPL", alpha_direction="BUY")
    assert r["modulator"] == 1.0, "kill-switch off must force modulator = 1.0"
    assert r["shadow_modulator"] < 1.0, "shadow must still compute dissent"
    assert r["enforced"] is False


@pytest.mark.asyncio
async def test_kill_switch_on_applies_shadow_modulator(monkeypatch):
    monkeypatch.setenv(cc.ENV_ENFORCE, "1")
    db = _FakeDB(mc2_contributions=[
        {"brain": "sov", "direction": "down", "confidence": 0.9,
         "recorded_at_dt": __import__("datetime").datetime.now(
             __import__("datetime").timezone.utc)},
    ])
    r = await cc.consult_council(db, symbol="AAPL", alpha_direction="BUY")
    assert r["enforced"] is True
    assert r["modulator"] == r["shadow_modulator"]
    assert r["modulator"] < 1.0


@pytest.mark.asyncio
async def test_always_logs_even_when_shadow(monkeypatch):
    monkeypatch.delenv(cc.ENV_ENFORCE, raising=False)
    db = _FakeDB()
    _ = await cc.consult_council(db, symbol="AAPL", alpha_direction="BUY")
    inserts = db[cc.LOG_COLLECTION].inserts
    assert len(inserts) == 1
    assert inserts[0]["symbol"] == "AAPL"
    assert inserts[0]["alpha_direction"] == "BUY"


@pytest.mark.asyncio
async def test_consult_never_raises_on_broken_db():
    """Doctrine: consultation is best-effort. Trade must proceed
    even if Mongo throws."""

    class _BrokenDB:
        def __getitem__(self, _name):
            raise RuntimeError("mongo down")
        def __getattr__(self, _name):
            raise RuntimeError("mongo down")

    r = await cc.consult_council(_BrokenDB(), symbol="AAPL", alpha_direction="BUY")
    assert r["modulator"] == 1.0
    assert r["consensus"] == "unknown"
    assert r["vote_count"] == 0


@pytest.mark.asyncio
async def test_no_votes_yields_neutral():
    db = _FakeDB()
    r = await cc.consult_council(db, symbol="MSFT", alpha_direction="BUY")
    assert r["modulator"] == 1.0
    assert r["consensus"] == "unknown"
    assert r["dissent_ratio"] == 0.0
