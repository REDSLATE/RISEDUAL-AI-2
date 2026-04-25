"""Tests for the crypto Strategist/Auditor audit log + veto-rate stats."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from services.crypto_signal_audit import (
    _compute_summary_from_rows,
    get_strategist_stats,
    log_adversarial_decision,
)


# ── log_adversarial_decision ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_writes_full_decision_record():
    captured: dict = {}

    class _DB:
        def __init__(self):
            self.crypto_signal_audit_log = AsyncMock()
            self.crypto_signal_audit_log.insert_one = self._insert

        def __getitem__(self, key):
            return getattr(self, key)

        async def _insert(self, doc):
            captured.update(doc)

    db = _DB()
    signal = {
        "direction": "LONG",
        "confidence": 0.78,
        "strategist": {"direction": "LONG", "confidence": 0.85,
                       "reason": "trend",
                       "indicators": {"rsi": 64.0, "ema20": 100.0,
                                      "momentum_5b": 0.012}},
        "auditor": {"verdict": "CONFIRM", "confidence": 0.72,
                    "reason": "head_room_ok"},
    }

    await log_adversarial_decision(db, symbol="BTC", signal=signal,
                                   final_direction="LONG")

    assert captured["symbol"] == "BTC"
    assert captured["strategist_direction"] == "LONG"
    assert captured["strategist_confidence"] == 0.85
    assert captured["auditor_verdict"] == "CONFIRM"
    assert captured["auditor_confidence"] == 0.72
    assert captured["combined_confidence"] == 0.78
    assert captured["final_direction"] == "LONG"
    assert captured["indicators"]["rsi"] == 64.0
    assert isinstance(captured["timestamp"], datetime)


@pytest.mark.asyncio
async def test_log_swallows_db_errors_silently():
    """Audit-log write failures must NEVER crash the bot tick."""
    class _BrokenDB:
        def __getitem__(self, k):
            class _Col:
                async def insert_one(self, _doc):
                    raise RuntimeError("mongo down")
            return _Col()

    # Should not raise
    await log_adversarial_decision(_BrokenDB(), symbol="BTC",
                                   signal={"strategist": {}, "auditor": {}},
                                   final_direction="HOLD")


@pytest.mark.asyncio
async def test_log_no_op_when_db_is_none():
    # No exception, no side effects
    await log_adversarial_decision(None, symbol="BTC",
                                   signal={"strategist": {}, "auditor": {}},
                                   final_direction="HOLD")


# ── _compute_summary_from_rows ────────────────────────────────────────────────


def test_summary_empty_returns_no_data_calibration():
    s = _compute_summary_from_rows([])
    assert s["total_decisions"] == 0
    assert s["calibration"] == "no_data"
    assert s["veto_rate"] == 0.0
    assert s["top_veto_reasons"] == []


def test_summary_in_target_band_30_pct_veto():
    """30% veto rate → in_target_band."""
    rows = (
        # 7 confirms (LONG/SHORT proposals)
        [{"strategist_direction": "LONG", "auditor_verdict": "CONFIRM",
          "symbol": "BTC", "auditor_reason": "ok"} for _ in range(7)]
        # 3 vetoes
        + [{"strategist_direction": "LONG", "auditor_verdict": "VETO",
            "symbol": "BTC", "auditor_reason": "rsi_overbought_72.0"}
           for _ in range(3)]
    )
    s = _compute_summary_from_rows(rows)
    assert s["proposals_count"] == 10
    assert s["audit"]["veto"] == 3
    assert s["audit"]["confirm"] == 7
    assert s["veto_rate"] == 30.0
    assert s["calibration"] == "in_target_band"


def test_summary_too_strict_above_70_pct():
    rows = (
        [{"strategist_direction": "LONG", "auditor_verdict": "CONFIRM",
          "symbol": "BTC", "auditor_reason": "ok"} for _ in range(2)]
        + [{"strategist_direction": "LONG", "auditor_verdict": "VETO",
            "symbol": "BTC", "auditor_reason": "rsi_overbought_75.0"}
           for _ in range(8)]
    )
    s = _compute_summary_from_rows(rows)
    assert s["veto_rate"] == 80.0
    assert s["calibration"] == "too_strict"


def test_summary_too_lenient_below_10_pct():
    rows = (
        [{"strategist_direction": "LONG", "auditor_verdict": "CONFIRM",
          "symbol": "BTC", "auditor_reason": "ok"} for _ in range(95)]
        + [{"strategist_direction": "LONG", "auditor_verdict": "VETO",
            "symbol": "BTC", "auditor_reason": "rsi_overbought_72.0"}
           for _ in range(5)]
    )
    s = _compute_summary_from_rows(rows)
    assert s["veto_rate"] == 5.0
    assert s["calibration"] == "too_lenient"


def test_summary_above_band_45_to_70_pct():
    rows = (
        [{"strategist_direction": "LONG", "auditor_verdict": "CONFIRM",
          "symbol": "BTC", "auditor_reason": "ok"} for _ in range(4)]
        + [{"strategist_direction": "LONG", "auditor_verdict": "VETO",
            "symbol": "BTC", "auditor_reason": "rsi_overbought_72.0"}
           for _ in range(6)]
    )
    s = _compute_summary_from_rows(rows)
    assert s["veto_rate"] == 60.0
    assert s["calibration"] == "above_band"


def test_summary_strategist_holds_excluded_from_veto_denominator():
    """HOLDs from the Strategist must NOT count toward veto rate —
    the Auditor never had a proposal to veto."""
    rows = (
        # 80 strategist HOLDs (no proposal)
        [{"strategist_direction": "HOLD", "auditor_verdict": "HOLD",
          "symbol": "BTC", "auditor_reason": ""} for _ in range(80)]
        # 6 confirms, 4 vetoes (40% veto on actual proposals)
        + [{"strategist_direction": "LONG", "auditor_verdict": "CONFIRM",
            "symbol": "BTC", "auditor_reason": "ok"} for _ in range(6)]
        + [{"strategist_direction": "LONG", "auditor_verdict": "VETO",
            "symbol": "BTC", "auditor_reason": "rsi_overbought_72.0"}
           for _ in range(4)]
    )
    s = _compute_summary_from_rows(rows)
    # Veto rate = 4 / 10 (proposals only), not 4 / 90 (everything)
    assert s["proposals_count"] == 10
    assert s["veto_rate"] == 40.0
    assert s["calibration"] == "in_target_band"


def test_summary_top_veto_reasons_canonicalised():
    """Reasons like ``rsi_overbought_72.4`` and ``rsi_overbought_77.1``
    should bucket together as ``rsi_overbought``."""
    rows = (
        [{"strategist_direction": "LONG", "auditor_verdict": "VETO",
          "symbol": "BTC", "auditor_reason": "rsi_overbought_72.4"}
         for _ in range(5)]
        + [{"strategist_direction": "LONG", "auditor_verdict": "VETO",
            "symbol": "ETH", "auditor_reason": "rsi_overbought_77.1"}
           for _ in range(3)]
        + [{"strategist_direction": "LONG", "auditor_verdict": "VETO",
            "symbol": "SOL", "auditor_reason": "parabolic_long_momentum_9.5%"}
           for _ in range(2)]
    )
    s = _compute_summary_from_rows(rows)
    top = s["top_veto_reasons"]
    assert len(top) >= 2
    # Top reason should be rsi_overbought with count 8
    assert top[0]["reason"] == "rsi_overbought"
    assert top[0]["count"] == 8


def test_summary_per_symbol_breakdown():
    rows = [
        {"strategist_direction": "LONG", "auditor_verdict": "CONFIRM",
         "symbol": "BTC", "auditor_reason": "ok"},
        {"strategist_direction": "LONG", "auditor_verdict": "VETO",
         "symbol": "BTC", "auditor_reason": "rsi_overbought_72.0"},
        {"strategist_direction": "LONG", "auditor_verdict": "CONFIRM",
         "symbol": "ETH", "auditor_reason": "ok"},
    ]
    s = _compute_summary_from_rows(rows)
    assert s["by_symbol"]["BTC"] == {"proposed": 2, "vetoed": 1, "confirmed": 1}
    assert s["by_symbol"]["ETH"] == {"proposed": 1, "vetoed": 0, "confirmed": 1}


# ── get_strategist_stats (rolling window query) ───────────────────────────────


@pytest.mark.asyncio
async def test_get_stats_filters_by_window():
    """Rows older than the window cutoff should not affect the summary."""
    now = datetime.now(timezone.utc)
    fresh = [
        {"timestamp": now - timedelta(hours=1),
         "strategist_direction": "LONG", "auditor_verdict": "CONFIRM",
         "symbol": "BTC", "auditor_reason": "ok"}
    ]

    class _DB:
        def __init__(self):
            self.crypto_signal_audit_log = AsyncMock()
            self.crypto_signal_audit_log.find = self._find

        def __getitem__(self, k):
            return getattr(self, k)

        def _find(self, query, projection=None):
            class _Cursor:
                async def to_list(_self, length=None):
                    cutoff = query.get("timestamp", {}).get("$gte")
                    if cutoff:
                        return [r for r in fresh if r["timestamp"] >= cutoff]
                    return fresh
            return _Cursor()

    s = await get_strategist_stats(_DB(), hours=24)
    assert s["total_decisions"] == 1
    assert s["window_hours"] == 24
