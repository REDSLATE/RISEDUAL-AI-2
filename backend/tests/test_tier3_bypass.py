"""Tripwire coverage for the Tier 3 operator bypass (2026-06-01).

Operator decision: the Tier 3 pre-launch evidence gate has done its
job. ``TIER3_BYPASS_UNLOCKED=1`` short-circuits ``check_tier3_unlock``
to ``unlocked=True`` so the adversarial brain can graduate past
shadow phase and the equity Phase-2 brake becomes a no-op.

These tests lock in:

  1. Bypass flag set → unlocked=True regardless of stats (including
     empty stats that would normally land 6 lock reasons).
  2. Bypass flag unset → original evidence checks resume.
  3. Bypass payload carries a ``bypass`` field so the operator can
     read "why is Tier 3 showing unlocked?" off the API/dashboard.
  4. ``confidence_score`` clamps to 100.0 under bypass — the
     readiness composite still feeds dashboards but reports "fully
     ready" not "barely passing".
  5. Truthy variations of the flag (``1``, ``true``, ``yes``, ``on``)
     all activate the bypass.
  6. Falsy / unrelated values (``0``, ``false``, ``""``, unset) leave
     the original logic intact.
"""
from __future__ import annotations

import importlib

import pytest


def _reload_module():
    """Reload to pick up any env changes since module import."""
    import services.tier3_readiness as t3
    importlib.reload(t3)
    return t3


def _empty_stats():
    """Stats dict that would normally fail every check."""
    return {
        "days": 0,
        "total_trades": 0,
        "high_conf_trades": 0,
        "high_conf_win_rate": 0.0,
        "avg_confidence": 0.0,
        "strong_miss_rate": 1.0,
        "last_7d_win_rate": 0.0,
        "overall_win_rate": 0.0,
        "clamp_total": 0,
    }


# ── Bypass active ──────────────────────────────────────────────────────


def test_bypass_unlocks_empty_stats(monkeypatch):
    monkeypatch.setenv("TIER3_BYPASS_UNLOCKED", "1")
    from services.tier3_readiness import check_tier3_unlock
    out = check_tier3_unlock(_empty_stats())
    assert out["unlocked"] is True
    assert out["reasons"] == []
    assert out["bypass"] == "TIER3_BYPASS_UNLOCKED"


def test_bypass_clamps_confidence_score_to_100(monkeypatch):
    monkeypatch.setenv("TIER3_BYPASS_UNLOCKED", "1")
    from services.tier3_readiness import check_tier3_unlock
    out = check_tier3_unlock(_empty_stats())
    assert out["confidence_score"] == 100.0


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on"])
def test_bypass_recognizes_truthy_variations(monkeypatch, value):
    monkeypatch.setenv("TIER3_BYPASS_UNLOCKED", value)
    from services.tier3_readiness import check_tier3_unlock
    out = check_tier3_unlock(_empty_stats())
    assert out["unlocked"] is True


def test_bypass_works_with_completely_missing_stats(monkeypatch):
    """Even {} (zero keys) must unlock under bypass — the doctrine
    is "operator override beats every check"."""
    monkeypatch.setenv("TIER3_BYPASS_UNLOCKED", "1")
    from services.tier3_readiness import check_tier3_unlock
    out = check_tier3_unlock({})
    assert out["unlocked"] is True


# ── Bypass off → original gate logic resumes ────────────────────────────


def test_unset_flag_keeps_original_lock(monkeypatch):
    monkeypatch.delenv("TIER3_BYPASS_UNLOCKED", raising=False)
    from services.tier3_readiness import check_tier3_unlock
    out = check_tier3_unlock(_empty_stats())
    assert out["unlocked"] is False
    assert "Insufficient live days" in out["reasons"]
    assert "bypass" not in out  # no bypass field on the non-bypass path


@pytest.mark.parametrize("value", ["0", "false", "", "no", "off"])
def test_falsy_flag_keeps_original_lock(monkeypatch, value):
    monkeypatch.setenv("TIER3_BYPASS_UNLOCKED", value)
    from services.tier3_readiness import check_tier3_unlock
    out = check_tier3_unlock(_empty_stats())
    assert out["unlocked"] is False


def test_unrelated_value_does_not_activate_bypass(monkeypatch):
    """Defensive: ``TIER3_BYPASS_UNLOCKED=banana`` is NOT truthy."""
    monkeypatch.setenv("TIER3_BYPASS_UNLOCKED", "banana")
    from services.tier3_readiness import check_tier3_unlock
    out = check_tier3_unlock(_empty_stats())
    assert out["unlocked"] is False


# ── Doctrine: bypass payload is auditable ──────────────────────────────


def test_bypass_payload_carries_audit_marker(monkeypatch):
    """An operator looking at the API/dashboard must be able to tell
    "Tier 3 is showing unlocked because the bypass is on, not because
    the evidence cleared". The ``bypass`` field is that signal."""
    monkeypatch.setenv("TIER3_BYPASS_UNLOCKED", "1")
    from services.tier3_readiness import check_tier3_unlock
    out = check_tier3_unlock(_empty_stats())
    assert out.get("bypass") == "TIER3_BYPASS_UNLOCKED"
