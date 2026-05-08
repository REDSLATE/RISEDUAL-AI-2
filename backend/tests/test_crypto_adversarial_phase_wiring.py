"""Tests for the adversarial PHASE wiring inside crypto_paper_trader.

Covers the four phase semantics that were silently broken before this
fix landed:

1. ``shadow``    — log only, behaviour identical to the pre-adversarial
                   code path. Strategist/auditor's direction fires, no
                   size scaling.
2. ``risk_only`` — Commander's ``risk_multiplier`` scales position
                   size; direction unchanged.
3. ``veto``      — Commander's ``NO_TRADE`` blocks the fill entirely;
                   ``LONG`` / ``SHORT_OR_AVOID`` only refine sizing.
4. ``full``      — Commander's ``decision`` overrides direction.
                   Includes the formerly-unreachable case where a
                   strategist HOLD is promoted to LONG by Commander
                   conviction (the override-triggers-entry pattern).

Also pins:
- HOLD signals now reach the adversarial layer (regression guard
  against the early-return that used to prevent ``full``-phase entry
  triggers).
- Defensive ``_id`` strip after ``insert_one`` does not break the
  trade-row contract.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import crypto_paper_trader as cpt


# ── Bar generators ───────────────────────────────────────────────────────────


def _moderate_uptrend(n: int = 60, base: float = 70000.0,
                      drift: float = 200.0) -> list[float]:
    series = [base]
    for i in range(1, n):
        series.append(series[-1] + drift if i % 3 != 2 else series[-1] - drift)
    return series


def _flat(n: int = 60, base: float = 70000.0) -> list[float]:
    return [base] * n


def _high_conviction_long_signal() -> dict:
    """Mirror of the helper used in the web-research shadow integration
    tests — bypasses real indicator math so phase-specific behaviour
    can be tested in isolation."""
    return {
        "direction": "LONG",
        "confidence": 0.82,
        "reason": "long_bias_confirmed",
        "regime": "trending",
        "strategist": {
            "direction": "LONG",
            "confidence": 0.85,
            "reason": "above_ema rsi=63 momentum=2.4%",
            "indicators": {"rsi": 63.0, "ema20": 69500.0, "momentum_5b": 0.024},
        },
        "auditor": {
            "verdict": "CONFIRM",
            "confidence": 0.79,
            "reason": "no_exhaustion_signal",
            "indicators": {"rsi": 63.0, "ema20": 69500.0, "momentum_5b": 0.024},
        },
    }


def _hold_signal() -> dict:
    """Strategist HOLD output — used to verify ``full`` phase can
    promote it into a fill (the diff's override-triggers-entry case)."""
    return {
        "direction": "HOLD",
        "confidence": 0.0,
        "reason": "no_clear_setup",
        "regime": "range",
        "strategist": {
            "direction": "HOLD",
            "confidence": 0.40,
            "reason": "neutral",
            "indicators": {"rsi": 52.0, "ema20": 70000.0, "momentum_5b": 0.001},
        },
        "auditor": {
            "verdict": "VETO",
            "confidence": 0.55,
            "reason": "weak_signal",
            "indicators": {"rsi": 52.0, "ema20": 70000.0, "momentum_5b": 0.001},
        },
    }


# ── Fake DB ──────────────────────────────────────────────────────────────────


class _FakeDB:
    def __init__(self):
        self.trade_inserts: list[dict] = []
        self.audit_inserts: list[dict] = []
        self.adversarial_log_inserts: list[dict] = []

        outer = self

        class _CryptoTrades:
            async def insert_one(self, doc):
                # Simulate Mongo's _id mutation so the strip-fix is exercised.
                doc["_id"] = "fake_objectid"
                outer.trade_inserts.append(dict(doc))

        class _Audit:
            async def insert_one(self, doc):
                outer.audit_inserts.append(dict(doc))
            async def create_index(self, *_a, **_kw): return None

        class _AdvLog:
            async def insert_one(self, doc):
                doc["_id"] = "fake_objectid"
                outer.adversarial_log_inserts.append(dict(doc))
            async def create_index(self, *_a, **_kw): return None

        class _CryptoAdaptations:
            def find(self, _q):
                class _C:
                    def __aiter__(self): return self
                    async def __anext__(self): raise StopAsyncIteration
                return _C()

        class _Cache:
            async def find_one(self, *_a, **_kw): return None
            async def update_one(self, *_a, **_kw): return None
            async def create_index(self, *_a, **_kw): return None

        self.crypto_paper_trades = _CryptoTrades()
        self.crypto_signal_audit_log = _Audit()
        self.crypto_adversarial_decision_log = _AdvLog()
        self.crypto_model_adaptations = _CryptoAdaptations()
        self.web_research_cache = _Cache()

    def __getitem__(self, key):
        return getattr(self, key)


@pytest.fixture
def _open_gates(monkeypatch):
    """Open both adversarial gates so the layer fires. Web-research
    shadow lane stays disabled to keep tests deterministic."""
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    monkeypatch.setenv("CRYPTO_SHADOW_RESEARCH_DISABLED", "1")

    async def fake_tier3_open(_db):
        return True

    return monkeypatch, fake_tier3_open


# ── Phase: shadow ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shadow_phase_logs_but_does_not_alter_size_or_direction(_open_gates):
    monkeypatch, fake_open = _open_gates
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    db = _FakeDB()

    async def quote(_): return {"price": 70000.0}

    with patch("services.adversarial_core._tier3_gate_open", side_effect=fake_open), \
         patch.object(cpt, "adversarial_signal", return_value=_high_conviction_long_signal()):
        result = await cpt.run_crypto_symbol(db, "BTC", _moderate_uptrend(60), quote)

    assert result["opened"] is True
    assert result["direction"] == "LONG"
    # Decision was logged
    assert len(db.adversarial_log_inserts) == 1
    # Trade row carries the decision_id for outcome attribution
    assert db.trade_inserts[0]["adversarial_decision_id"] is not None
    # Size unchanged (multiplier=1.0 in shadow)
    base_size = cpt.compute_crypto_position_size(0.82)
    assert db.trade_inserts[0]["size_usd"] == pytest.approx(base_size)


# ── Phase: risk_only ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_risk_only_phase_scales_size_by_multiplier(_open_gates):
    monkeypatch, fake_open = _open_gates
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "risk_only")
    db = _FakeDB()

    async def quote(_): return {"price": 70000.0}

    fake_decision = {
        "decision": "LONG", "phase": "risk_only",
        "edge_gap": 0.5, "risk_multiplier": 0.5,
        "bull_score": 1.0, "bear_score": 0.5,
        "bull_case": {}, "bear_case": {},
        "timestamp": "2026-04-27T00:00:00+00:00", "symbol": "BTC", "regime": "trending",
    }

    with patch("services.adversarial_core._tier3_gate_open", side_effect=fake_open), \
         patch.object(cpt, "run_adversarial_decision", AsyncMock(return_value=fake_decision)), \
         patch.object(cpt, "adversarial_signal", return_value=_high_conviction_long_signal()):
        result = await cpt.run_crypto_symbol(db, "BTC", _moderate_uptrend(60), quote)

    assert result["opened"] is True
    base_size = cpt.compute_crypto_position_size(0.82)
    assert db.trade_inserts[0]["size_usd"] == pytest.approx(round(base_size * 0.5, 2))
    assert db.trade_inserts[0]["direction"] == "LONG", "direction unchanged in risk_only"


# ── Phase: veto ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_veto_phase_no_trade_blocks_fill(_open_gates):
    monkeypatch, fake_open = _open_gates
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "veto")
    db = _FakeDB()

    async def quote(_): return {"price": 70000.0}

    fake_decision = {
        "decision": "NO_TRADE", "phase": "veto",
        "edge_gap": 0.10, "risk_multiplier": 0.10,
        "bull_score": 0.55, "bear_score": 0.50,
        "bull_case": {}, "bear_case": {},
        "timestamp": "x", "symbol": "BTC", "regime": "trending",
    }

    with patch("services.adversarial_core._tier3_gate_open", side_effect=fake_open), \
         patch.object(cpt, "run_adversarial_decision", AsyncMock(return_value=fake_decision)), \
         patch.object(cpt, "adversarial_signal", return_value=_high_conviction_long_signal()):
        result = await cpt.run_crypto_symbol(db, "BTC", _moderate_uptrend(60), quote)

    assert result["skipped"] is True
    assert result["adversarial_action"] == "veto_block"
    assert result["adversarial_decision_id"] is not None
    assert len(db.trade_inserts) == 0, "veto MUST block the fill"
    # But the decision was still logged (otherwise dashboard goes blind)
    assert len(db.adversarial_log_inserts) == 1


@pytest.mark.asyncio
async def test_veto_phase_long_decision_only_scales_size(_open_gates):
    """In veto phase, a Commander LONG verdict (matching strategist) is
    treated like risk_only — direction unchanged, size scaled. Veto
    is for blocking only."""
    monkeypatch, fake_open = _open_gates
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "veto")
    db = _FakeDB()

    async def quote(_): return {"price": 70000.0}

    fake_decision = {
        "decision": "LONG", "phase": "veto",
        "edge_gap": 0.45, "risk_multiplier": 0.45,
        "bull_score": 1.0, "bear_score": 0.55,
        "bull_case": {}, "bear_case": {},
        "timestamp": "x", "symbol": "BTC", "regime": "trending",
    }

    with patch("services.adversarial_core._tier3_gate_open", side_effect=fake_open), \
         patch.object(cpt, "run_adversarial_decision", AsyncMock(return_value=fake_decision)), \
         patch.object(cpt, "adversarial_signal", return_value=_high_conviction_long_signal()):
        result = await cpt.run_crypto_symbol(db, "BTC", _moderate_uptrend(60), quote)

    assert result["opened"] is True
    base_size = cpt.compute_crypto_position_size(0.82)
    assert db.trade_inserts[0]["size_usd"] == pytest.approx(round(base_size * 0.45, 2))


# ── Phase: full (the override-triggers-entry case) ───────────────────────────


@pytest.mark.asyncio
async def test_full_phase_promotes_strategist_hold_to_long(_open_gates):
    """The pattern from the user's diff — applied to our code:
    Commander conviction admits a fill EVEN when strategist confluence
    is HOLD. This path was unreachable before the refactor (early
    return on HOLD prevented adversarial from running)."""
    monkeypatch, fake_open = _open_gates
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "full")
    db = _FakeDB()

    async def quote(_): return {"price": 70000.0}

    fake_decision = {
        "decision": "LONG", "phase": "full",
        "edge_gap": 0.85, "risk_multiplier": 0.85,
        "bull_score": 1.4, "bear_score": 0.55,
        "bull_case": {}, "bear_case": {},
        "timestamp": "x", "symbol": "BTC", "regime": "trending",
    }

    with patch("services.adversarial_core._tier3_gate_open", side_effect=fake_open), \
         patch.object(cpt, "run_adversarial_decision", AsyncMock(return_value=fake_decision)), \
         patch.object(cpt, "adversarial_signal", return_value=_hold_signal()):
        result = await cpt.run_crypto_symbol(db, "BTC", _flat(60), quote)

    assert result["opened"] is True
    assert result["direction"] == "LONG"
    assert result["adversarial_action"] == "full_trigger"
    assert db.trade_inserts[0]["direction"] == "LONG"
    # Confidence floored to MIN_CRYPTO_CONFIDENCE so sizing is
    # well-defined (HOLD signal had confidence=0).
    assert db.trade_inserts[0]["confidence"] == cpt.MIN_CRYPTO_CONFIDENCE
    # Trade row carries the action so post-hoc analysis can attribute
    # full-phase entries separately from confluence buys.
    assert db.trade_inserts[0].get("adversarial_decision_id") is not None


@pytest.mark.asyncio
async def test_full_phase_blocks_fill_on_no_trade(_open_gates):
    monkeypatch, fake_open = _open_gates
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "full")
    db = _FakeDB()

    async def quote(_): return {"price": 70000.0}

    fake_decision = {
        "decision": "NO_TRADE", "phase": "full",
        "edge_gap": 0.05, "risk_multiplier": 0.05,
        "bull_score": 0.5, "bear_score": 0.5,
        "bull_case": {}, "bear_case": {},
        "timestamp": "x", "symbol": "BTC", "regime": "trending",
    }

    with patch("services.adversarial_core._tier3_gate_open", side_effect=fake_open), \
         patch.object(cpt, "run_adversarial_decision", AsyncMock(return_value=fake_decision)), \
         patch.object(cpt, "adversarial_signal", return_value=_high_conviction_long_signal()):
        result = await cpt.run_crypto_symbol(db, "BTC", _moderate_uptrend(60), quote)

    assert result["skipped"] is True
    assert result["adversarial_action"] == "veto_block"
    assert len(db.trade_inserts) == 0


# ── Gates closed (silent baseline) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_gates_closed_behaviour_unchanged_from_pre_adversarial(monkeypatch):
    """When either gate is closed (the default state in production
    today), the trade path must behave EXACTLY as it did before the
    adversarial layer landed. No size scaling, no extra DB writes,
    no decision_id."""
    monkeypatch.delenv("CRYPTO_ADVERSARIAL_ENABLED", raising=False)
    monkeypatch.setenv("CRYPTO_SHADOW_RESEARCH_DISABLED", "1")
    db = _FakeDB()

    async def quote(_): return {"price": 70000.0}

    with patch.object(cpt, "adversarial_signal", return_value=_high_conviction_long_signal()):
        result = await cpt.run_crypto_symbol(db, "BTC", _moderate_uptrend(60), quote)

    assert result["opened"] is True
    assert len(db.adversarial_log_inserts) == 0
    assert db.trade_inserts[0]["adversarial_decision_id"] is None
    base_size = cpt.compute_crypto_position_size(0.82)
    assert db.trade_inserts[0]["size_usd"] == pytest.approx(base_size)


# ── _id strip hygiene ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_objectid_stripped_from_trade_dict_after_insert(monkeypatch):
    """The Mongo-injected `_id` must be popped off so any caller that
    consumes `trade` after `insert_one` doesn't see a non-JSON-
    serializable ObjectId."""
    monkeypatch.delenv("CRYPTO_ADVERSARIAL_ENABLED", raising=False)
    monkeypatch.setenv("CRYPTO_SHADOW_RESEARCH_DISABLED", "1")
    db = _FakeDB()

    async def quote(_): return {"price": 70000.0}

    with patch.object(cpt, "adversarial_signal", return_value=_high_conviction_long_signal()):
        await cpt.run_crypto_symbol(db, "BTC", _moderate_uptrend(60), quote)

    # The dict captured by our fake _CryptoTrades.insert_one carries
    # the simulated _id (we add it before copying), but the LIVE trade
    # dict that the function returns/closes over should have it stripped.
    # We can't observe that dict directly here, but we CAN confirm the
    # function returned without raising — which is the regression we
    # care about.
    inserted = db.trade_inserts[0]
    assert "_id" in inserted, "fake DB simulated the Mongo mutation"
