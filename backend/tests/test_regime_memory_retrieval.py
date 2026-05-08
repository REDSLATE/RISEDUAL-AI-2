"""Tests for the RISEDUAL Regime Memory Retrieval layer.

Covers the safety rails the user explicitly requested:

  * Module is a no-op when ``REGIME_MEMORY_ENABLED=false``.
  * Non-canonical direction tokens are REJECTED at ingest.
  * HOLD / UNKNOWN / NO_TRADE direction never surfaces memories or
    moves the risk multiplier below 1.0 (no HOLD promotion).
  * Risk multiplier is bounded to ``[0.50, 1.00]``.
  * ``shadow`` mode emits ``shadow_risk_multiplier`` but forces the
    live ``risk_multiplier`` back to 1.0.
  * Shared ``canonical_ai_dir`` is used (no local drift): ``BUY``
    canonicalizes to ``LONG``, ``SELL`` to ``SHORT``, ``HOLD`` to
    ``UNKNOWN`` — and memories tagged with ``BUY`` retrieve under
    direction=``LONG``.
  * Module performs zero MongoDB writes (in-memory only).
"""
from __future__ import annotations

import importlib

import pytest


# ─── helpers ─────────────────────────────────────────────────────────


def _reload_module(monkeypatch, **env):
    """Re-import the regime memory module with the given env vars."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import services.regime_memory_retrieval as mod
    importlib.reload(mod)
    return mod


def _fp(mod, **overrides):
    base = dict(
        vix_level="normal",
        yield_curve="flat",
        dxy_trend="strong",
        credit_spreads="tight",
        liquidity="normal",
        macro_phase="late_cycle",
    )
    base.update(overrides)
    return mod.RegimeFingerprint(**base)


def _mem(mod, *, memory_id, ticker, direction, pnl_pct, regime, **kw):
    return mod.RegimeMemory(
        memory_id=memory_id,
        timestamp="2026-01-15T00:00:00Z",
        ticker=ticker,
        direction=direction,
        entry_price=100.0,
        exit_price=kw.get("exit_price", 105.0),
        pnl_pct=pnl_pct,
        holding_days=kw.get("holding_days", 5),
        regime_at_entry=regime,
        regime_at_exit=kw.get("regime_at_exit"),
        pretell_30d=kw.get("pretell_30d"),
        regime_shift_detected=kw.get("regime_shift_detected", False),
        shift_type=kw.get("shift_type"),
        strategist_thesis=kw.get("strategist_thesis"),
    )


# ─── disabled-by-default no-op ───────────────────────────────────────


def test_disabled_by_default_returns_noop_envelope(monkeypatch):
    mod = _reload_module(monkeypatch, REGIME_MEMORY_ENABLED="false")
    engine = mod.RegimeMemoryRetrievalEngine()

    out = engine.build_risk_context(
        current_regime=_fp(mod),
        current_pretell=None,
        ticker="AAPL",
        direction="BUY",
    )

    assert out["enabled"] is False
    assert out["risk_multiplier"] == 1.0
    assert out["allow_direction_change"] is False
    assert out["allow_hold_promotion"] is False
    assert out["memories"] == []
    assert any("disabled" in r.lower() for r in out["reasons"])


def test_disabled_ingest_is_rejected(monkeypatch):
    mod = _reload_module(monkeypatch, REGIME_MEMORY_ENABLED="false")
    engine = mod.RegimeMemoryRetrievalEngine()
    memory = _mem(
        mod, memory_id="m1", ticker="AAPL", direction="BUY",
        pnl_pct=2.0, regime=_fp(mod),
    )
    result = engine.ingest_memory(memory)
    assert result["accepted"] is False
    assert result["enabled"] is False


# ─── direction guard ─────────────────────────────────────────────────


def test_non_canonical_direction_is_rejected_at_ingest(monkeypatch):
    mod = _reload_module(monkeypatch, REGIME_MEMORY_ENABLED="true")
    engine = mod.RegimeMemoryRetrievalEngine()
    bad = _mem(
        mod, memory_id="bad", ticker="AAPL", direction="MAYBE_LATER",
        pnl_pct=1.0, regime=_fp(mod),
    )
    result = engine.ingest_memory(bad)
    assert result["accepted"] is False
    assert result["reason"] == "non_canonical_direction"
    assert result["raw_direction"] == "MAYBE_LATER"
    # And it must NOT have been clustered.
    assert engine.report()["total_memories"] == 0


def test_buy_token_is_canonicalized_to_long(monkeypatch):
    """User explicitly required: no local direction drift. Use shared
    canonical_ai_dir which maps BUY→LONG, SELL→SHORT, HOLD→UNKNOWN."""
    mod = _reload_module(monkeypatch, REGIME_MEMORY_ENABLED="true")
    engine = mod.RegimeMemoryRetrievalEngine()
    regime = _fp(mod)
    engine.ingest_memory(_mem(
        mod, memory_id="m1", ticker="AAPL", direction="BUY",
        pnl_pct=3.0, regime=regime,
    ))
    # Retrieval under canonical "LONG" must find it.
    longs = engine.retrieve_context(regime, "AAPL", "LONG")
    assert len(longs) == 1
    assert longs[0]["direction"] == "LONG"
    # Retrieval under raw "BUY" must also resolve via canonical map.
    buys = engine.retrieve_context(regime, "AAPL", "BUY")
    assert len(buys) == 1


# ─── HOLD does not promote into a trade ─────────────────────────────


def test_hold_direction_never_returns_memories_or_changes_risk(monkeypatch):
    mod = _reload_module(monkeypatch, REGIME_MEMORY_ENABLED="true")
    engine = mod.RegimeMemoryRetrievalEngine()
    regime = _fp(mod)
    engine.ingest_memory(_mem(
        mod, memory_id="m1", ticker="AAPL", direction="BUY",
        pnl_pct=3.0, regime=regime,
    ))

    out = engine.build_risk_context(
        current_regime=regime,
        current_pretell=None,
        ticker="AAPL",
        direction="HOLD",
    )

    assert out["direction"] == "UNKNOWN"
    assert out["risk_multiplier"] == 1.0
    assert out["allow_direction_change"] is False
    assert out["allow_hold_promotion"] is False
    assert out["memories"] == []


def test_unresolved_memory_without_pnl_is_rejected(monkeypatch):
    mod = _reload_module(monkeypatch, REGIME_MEMORY_ENABLED="true")
    engine = mod.RegimeMemoryRetrievalEngine()
    pending = _mem(
        mod, memory_id="open1", ticker="AAPL", direction="BUY",
        pnl_pct=None, regime=_fp(mod),
    )
    result = engine.ingest_memory(pending)
    assert result["accepted"] is False
    assert result["reason"] == "unresolved_memory_missing_pnl_pct"


# ─── retrieval + risk multiplier behaviour ───────────────────────────


def test_negative_avg_pnl_reduces_risk_multiplier_in_full_mode(monkeypatch):
    mod = _reload_module(
        monkeypatch,
        REGIME_MEMORY_ENABLED="true",
        REGIME_MEMORY_MODE="full_context",
    )
    engine = mod.RegimeMemoryRetrievalEngine()
    regime = _fp(mod)
    for i, pnl in enumerate([-2.0, -3.0, -1.5]):
        engine.ingest_memory(_mem(
            mod, memory_id=f"m{i}", ticker="AAPL", direction="BUY",
            pnl_pct=pnl, regime=regime,
        ))

    out = engine.build_risk_context(
        current_regime=regime,
        current_pretell=None,
        ticker="AAPL",
        direction="BUY",
    )
    # Negative-pnl memories trigger the 0.85 multiplier.
    assert 0.50 <= out["risk_multiplier"] <= 1.0
    assert out["risk_multiplier"] < 1.0
    assert any("negative" in r.lower() for r in out["reasons"])


def test_risk_multiplier_is_bounded_at_minimum_floor(monkeypatch):
    """Stack a strong pre-tell warning with negative memories — the
    multiplier must still floor at REGIME_MEMORY_MIN_RISK_MULTIPLIER.
    """
    mod = _reload_module(
        monkeypatch,
        REGIME_MEMORY_ENABLED="true",
        REGIME_MEMORY_MODE="full_context",
        REGIME_PRETELL_MIN_SAMPLES="3",
        REGIME_MEMORY_MIN_RISK_MULTIPLIER="0.50",
    )
    engine = mod.RegimeMemoryRetrievalEngine()
    regime = _fp(mod)
    pretell = _fp(mod, vix_level="elevated")

    # Three negative memories with the same pretell signature.
    for i in range(3):
        engine.ingest_memory(_mem(
            mod, memory_id=f"m{i}", ticker="AAPL", direction="BUY",
            pnl_pct=-5.0, regime=regime,
            pretell_30d=pretell, regime_shift_detected=True,
            shift_type="liquidity_crunch",
        ))

    out = engine.build_risk_context(
        current_regime=regime,
        current_pretell=pretell,
        ticker="AAPL",
        direction="BUY",
    )
    assert out["risk_multiplier"] >= 0.50
    assert out["risk_multiplier"] <= 1.0


def test_shadow_mode_logs_multiplier_but_keeps_live_at_one(monkeypatch):
    mod = _reload_module(
        monkeypatch,
        REGIME_MEMORY_ENABLED="true",
        REGIME_MEMORY_MODE="shadow",
    )
    engine = mod.RegimeMemoryRetrievalEngine()
    regime = _fp(mod)
    for i in range(2):
        engine.ingest_memory(_mem(
            mod, memory_id=f"m{i}", ticker="AAPL", direction="BUY",
            pnl_pct=-4.0, regime=regime,
        ))
    out = engine.build_risk_context(
        current_regime=regime,
        current_pretell=None,
        ticker="AAPL",
        direction="BUY",
    )
    assert out["mode"] == "shadow"
    assert out["risk_multiplier"] == 1.0
    assert "shadow_risk_multiplier" in out
    assert out["shadow_risk_multiplier"] < 1.0
    assert any("shadow" in r.lower() for r in out["reasons"])


# ─── pre-tell warning ───────────────────────────────────────────────


def test_pretell_warning_requires_min_samples(monkeypatch):
    mod = _reload_module(
        monkeypatch,
        REGIME_MEMORY_ENABLED="true",
        REGIME_PRETELL_MIN_SAMPLES="3",
    )
    engine = mod.RegimeMemoryRetrievalEngine()
    pretell = _fp(mod, vix_level="elevated")
    # Only 2 samples — below threshold.
    for i in range(2):
        engine.ingest_memory(_mem(
            mod, memory_id=f"m{i}", ticker="AAPL", direction="BUY",
            pnl_pct=-2.0, regime=_fp(mod),
            pretell_30d=pretell, regime_shift_detected=True,
            shift_type="vol_spike",
        ))
    assert engine.check_pretell_warning(pretell) is None


def test_pretell_warning_fires_above_threshold(monkeypatch):
    mod = _reload_module(
        monkeypatch,
        REGIME_MEMORY_ENABLED="true",
        REGIME_PRETELL_MIN_SAMPLES="3",
    )
    engine = mod.RegimeMemoryRetrievalEngine()
    pretell = _fp(mod, vix_level="elevated")
    for i in range(3):
        engine.ingest_memory(_mem(
            mod, memory_id=f"m{i}", ticker="AAPL", direction="BUY",
            pnl_pct=-2.0, regime=_fp(mod),
            pretell_30d=pretell, regime_shift_detected=True,
            shift_type="vol_spike",
        ))
    warning = engine.check_pretell_warning(pretell)
    assert warning is not None
    assert warning["shift_type"] == "vol_spike"
    assert warning["historical_frequency"] >= 3
    assert warning["similarity"] >= 0.70


# ─── flags & guarantees ─────────────────────────────────────────────


def test_envelope_flags_never_change(monkeypatch):
    mod = _reload_module(
        monkeypatch,
        REGIME_MEMORY_ENABLED="true",
        REGIME_MEMORY_MODE="full_context",
    )
    engine = mod.RegimeMemoryRetrievalEngine()
    out = engine.build_risk_context(
        current_regime=_fp(mod),
        current_pretell=None,
        ticker="AAPL",
        direction="SELL",
    )
    assert out["allow_direction_change"] is False
    assert out["allow_hold_promotion"] is False


def test_module_does_not_import_mongo_writers():
    """Light architectural check — the module must not pull in any of
    the persistence layers it is forbidden to write to."""
    import services.regime_memory_retrieval as mod
    src = open(mod.__file__).read()
    forbidden_writes = [
        "toxic_lessons.insert",
        "toxic_lessons.update",
        "prediction_tracker.insert",
        "paper_trades.insert",
        "ai_alerts.insert",
    ]
    for token in forbidden_writes:
        assert token not in src, f"forbidden write found: {token}"


def test_report_shape(monkeypatch):
    mod = _reload_module(monkeypatch, REGIME_MEMORY_ENABLED="true")
    engine = mod.RegimeMemoryRetrievalEngine()
    rep = engine.report()
    for key in (
        "enabled", "mode", "similarity_threshold",
        "total_regime_clusters", "total_pretell_clusters",
        "total_memories", "top_clusters",
    ):
        assert key in rep
    assert isinstance(rep["top_clusters"], list)
