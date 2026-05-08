"""Tests for the Council Risk Modulator + Tier Gate.

Coverage discipline mirrors the user's spec — same 8 invariants,
plus 3 additions covering the corrected version's new safety rails:

- Stats fetch failure must default the gate closed (never raises).
- Cache must serve a fresh result within the 60s window.
- HOLD-vs-LONG disagreement (a non-opposite asymmetric case) must
  fall through to no-op, NOT trigger downweight (that would be
  v2 tuning, not a v1 bound).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.council_risk_modulator import (
    apply_council_risk_modulation,
    is_opposite,
    normalize_action,
)
from services.council_tier_gate import (
    _reset_cache_for_tests,
    council_tier_open_for_bucket,
    get_cached_council_stats,
)


# ── Test helpers ──────────────────────────────────────────────────────────────


def _enable(monkeypatch):
    monkeypatch.setattr(
        "services.council_risk_modulator.COUNCIL_RISK_MODULATOR_ENABLED",
        True,
    )


def _disable(monkeypatch):
    monkeypatch.setattr(
        "services.council_risk_modulator.COUNCIL_RISK_MODULATOR_ENABLED",
        False,
    )


# ── Per-bucket tier gate ──────────────────────────────────────────────────────


def test_bucket_gate_opens_only_matching_bucket():
    """Council might be actionable on crypto but noise on stocks —
    the gate must check per (engine, asset_type), not globally."""
    stats = {
        "buckets": [
            {
                "shadow_engine": "council",
                "asset_type": "crypto",
                "dissent_count": 35,
                "win_rate": 0.60,
                "total_delta_usd": 12.5,
            },
            {
                "shadow_engine": "council",
                "asset_type": "stock",
                "dissent_count": 10,
                "win_rate": 0.40,
                "total_delta_usd": -5,
            },
        ]
    }
    assert council_tier_open_for_bucket(
        stats, engine="council", asset_type="crypto",
    ) is True
    assert council_tier_open_for_bucket(
        stats, engine="council", asset_type="stock",
    ) is False


def test_bucket_gate_requires_all_three_thresholds():
    """Joint test: dissent_count, win_rate, AND total_delta_usd all
    must pass. A single threshold fail closes the gate."""
    base = {
        "shadow_engine": "council",
        "asset_type": "crypto",
        "dissent_count": 35,
        "win_rate": 0.60,
        "total_delta_usd": 12.5,
    }
    # Dissent count too low.
    s = {"buckets": [{**base, "dissent_count": 29}]}
    assert council_tier_open_for_bucket(s, engine="council", asset_type="crypto") is False
    # Win rate at threshold (must be strictly greater) — must close.
    s = {"buckets": [{**base, "win_rate": 0.55}]}
    assert council_tier_open_for_bucket(s, engine="council", asset_type="crypto") is False
    # Win rate just over threshold — must open.
    s = {"buckets": [{**base, "win_rate": 0.5501}]}
    assert council_tier_open_for_bucket(s, engine="council", asset_type="crypto") is True
    # Total delta non-positive.
    s = {"buckets": [{**base, "total_delta_usd": 0.0}]}
    assert council_tier_open_for_bucket(s, engine="council", asset_type="crypto") is False


def test_empty_stats_defaults_closed():
    assert council_tier_open_for_bucket(
        {}, engine="council", asset_type="crypto",
    ) is False
    assert council_tier_open_for_bucket(
        None, engine="council", asset_type="crypto",
    ) is False


def test_no_matching_bucket_defaults_closed():
    """A request for a bucket that doesn't exist (e.g., council on
    options before any options dissent has fired) must close the
    gate, not raise."""
    stats = {"buckets": [
        {"shadow_engine": "council", "asset_type": "crypto",
         "dissent_count": 50, "win_rate": 0.7, "total_delta_usd": 100},
    ]}
    assert council_tier_open_for_bucket(
        stats, engine="council", asset_type="options",
    ) is False


def test_null_field_values_default_closed():
    """Defensive against partial-dict bugs upstream."""
    stats = {"buckets": [
        {"shadow_engine": "council", "asset_type": "crypto",
         "dissent_count": None, "win_rate": None, "total_delta_usd": None},
    ]}
    assert council_tier_open_for_bucket(
        stats, engine="council", asset_type="crypto",
    ) is False


# ── Cache + stats failure ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_default_closed_on_stats_failure(monkeypatch):
    """Stats fetch raises → cache returns {} → gate stays closed.
    A logging or DB hiccup must NEVER accidentally open the gate."""
    _reset_cache_for_tests()

    async def _broken_fetch(*_a, **_kw):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(
        "services.research_shadow_stats.fetch_shadow_stats", _broken_fetch,
    )
    out = await get_cached_council_stats(None)
    assert out == {}


@pytest.mark.asyncio
async def test_cache_serves_within_ttl(monkeypatch):
    """Within the 60s TTL, the cache returns the prior payload
    without re-querying Mongo."""
    _reset_cache_for_tests()

    call_count = {"n": 0}

    async def _counting_fetch(*_a, **_kw):
        call_count["n"] += 1
        return {"buckets": [], "min_dissent_samples_required": 30}

    monkeypatch.setattr(
        "services.research_shadow_stats.fetch_shadow_stats", _counting_fetch,
    )

    await get_cached_council_stats(None)
    await get_cached_council_stats(None)
    await get_cached_council_stats(None)

    assert call_count["n"] == 1  # only the first call hit the function


@pytest.mark.asyncio
async def test_cache_refreshes_after_ttl(monkeypatch):
    """Past the 60s TTL, the cache forces a refetch."""
    from services import council_tier_gate as ctg

    _reset_cache_for_tests()
    call_count = {"n": 0}

    async def _counting_fetch(*_a, **_kw):
        call_count["n"] += 1
        return {"buckets": []}

    monkeypatch.setattr(
        "services.research_shadow_stats.fetch_shadow_stats", _counting_fetch,
    )

    await get_cached_council_stats(None)

    # Simulate cache age > TTL.
    ctg._CACHE["ts"] = datetime.now(timezone.utc) - timedelta(seconds=120)

    await get_cached_council_stats(None)
    assert call_count["n"] == 2


# ── Modulator: kill switches ──────────────────────────────────────────────────


def test_disabled_does_not_change(monkeypatch):
    """COUNCIL_RISK_MODULATOR_ENABLED=false short-circuits."""
    _disable(monkeypatch)
    out = apply_council_risk_modulation(
        commander_action="BUY",
        commander_risk_multiplier=1.0,
        council_action="BUY",
        council_confidence=0.9,
        council_tier_open=True,
    )
    assert out["risk_multiplier"] == 1.0
    assert out["council_applied"] is False
    assert out["reason"] == "council_modulator_disabled"


def test_tier_closed_no_change(monkeypatch):
    """council_tier_open=false short-circuits."""
    _enable(monkeypatch)
    out = apply_council_risk_modulation(
        commander_action="BUY",
        commander_risk_multiplier=1.0,
        council_action="BUY",
        council_confidence=0.95,
        council_tier_open=False,
    )
    assert out["risk_multiplier"] == 1.0
    assert out["council_applied"] is False
    assert out["reason"] == "council_tier_not_open"


# ── Modulator: agreement upweight ─────────────────────────────────────────────


def test_agreement_upweights_within_cap(monkeypatch):
    _enable(monkeypatch)
    out = apply_council_risk_modulation(
        commander_action="BUY",
        commander_risk_multiplier=1.0,
        council_action="BUY",
        council_confidence=0.9,
        council_tier_open=True,
    )
    assert out["risk_multiplier"] == 1.10
    assert out["council_applied"] is True


def test_agreement_caps_at_max_upweight(monkeypatch):
    """Hard ceiling: 1.25× regardless of Commander's base multiplier."""
    _enable(monkeypatch)
    out = apply_council_risk_modulation(
        commander_action="BUY",
        commander_risk_multiplier=1.2,
        council_action="BUY",
        council_confidence=0.9,
        council_tier_open=True,
    )
    assert out["risk_multiplier"] == 1.25


def test_agreement_normalises_engine_specific_actions(monkeypatch):
    """Commander emits SHORT_OR_AVOID; Council emits SHORT — these
    must be treated as agreement, not opposite."""
    _enable(monkeypatch)
    out = apply_council_risk_modulation(
        commander_action="SHORT_OR_AVOID",
        commander_risk_multiplier=1.0,
        council_action="SHORT",
        council_confidence=0.9,
        council_tier_open=True,
    )
    assert out["council_applied"] is True
    assert out["reason"] == "council_agreed_small_upweight"


# ── Modulator: opposite disagreement downweight ──────────────────────────────


def test_high_confidence_opposite_downweights(monkeypatch):
    _enable(monkeypatch)
    out = apply_council_risk_modulation(
        commander_action="BUY",
        commander_risk_multiplier=1.0,
        council_action="SELL",
        council_confidence=0.8,
        council_tier_open=True,
    )
    assert out["risk_multiplier"] == 0.5
    assert out["council_applied"] is True


def test_high_confidence_opposite_floors_at_half(monkeypatch):
    """Hard floor: 0.5× even with extreme low base multiplier."""
    _enable(monkeypatch)
    out = apply_council_risk_modulation(
        commander_action="BUY",
        commander_risk_multiplier=0.6,
        council_action="SELL",
        council_confidence=0.95,
        council_tier_open=True,
    )
    # 0.6 × 0.5 = 0.3, but floor protects to 0.5.
    assert out["risk_multiplier"] == 0.5


def test_low_confidence_disagreement_no_change(monkeypatch):
    """Confidence below 0.7 = no modulation. Operator can see the
    near-miss in the reason field."""
    _enable(monkeypatch)
    out = apply_council_risk_modulation(
        commander_action="BUY",
        commander_risk_multiplier=1.0,
        council_action="SELL",
        council_confidence=0.55,
        council_tier_open=True,
    )
    assert out["risk_multiplier"] == 1.0
    assert out["council_applied"] is False
    assert out["reason"] == "council_disagreement_low_confidence_no_change"


# ── Modulator: HOLD invariants ────────────────────────────────────────────────


def test_commander_hold_cannot_be_promoted(monkeypatch):
    """The firmest invariant: Council can never turn HOLD into a
    trade, no matter how confident."""
    _enable(monkeypatch)
    out = apply_council_risk_modulation(
        commander_action="HOLD",
        commander_risk_multiplier=0.0,
        council_action="BUY",
        council_confidence=0.95,
        council_tier_open=True,
    )
    assert out["risk_multiplier"] == 0.0
    assert out["council_applied"] is False
    assert out["reason"] == "commander_hold_not_promoted"


def test_council_hold_vs_commander_buy_no_change(monkeypatch):
    """v1 design choice: HOLD-vs-direction disagreement falls through
    to no-op. Future v2 tuning may add a small downweight here once
    real data shows Council's HOLD calls have positive Δ$ value."""
    _enable(monkeypatch)
    out = apply_council_risk_modulation(
        commander_action="BUY",
        commander_risk_multiplier=1.0,
        council_action="HOLD",
        council_confidence=0.95,
        council_tier_open=True,
    )
    assert out["risk_multiplier"] == 1.0
    assert out["council_applied"] is False
    assert out["reason"] == "council_disagreement_low_confidence_no_change"


# ── Action canonicalisation ───────────────────────────────────────────────────


def test_normalize_action_known_values():
    assert normalize_action("LONG") == "BUY"
    assert normalize_action("BUY") == "BUY"
    assert normalize_action("SHORT") == "SELL"
    assert normalize_action("SHORT_OR_AVOID") == "SELL"
    assert normalize_action("HOLD") == "HOLD"
    assert normalize_action("NO_TRADE") == "HOLD"
    assert normalize_action(None) == "HOLD"


def test_normalize_action_unknown_falls_to_hold():
    assert normalize_action("MOON") == "HOLD"
    assert normalize_action("") == "HOLD"


def test_is_opposite():
    assert is_opposite("BUY", "SELL") is True
    assert is_opposite("SELL", "BUY") is True
    assert is_opposite("BUY", "BUY") is False
    assert is_opposite("HOLD", "BUY") is False
    assert is_opposite("HOLD", "SELL") is False


# ── Bound discipline (regression guards) ──────────────────────────────────────


def test_bounds_pinned_in_code_not_env():
    """Hard bounds must be code-level constants — not env-overridable.
    An operator misconfiguration must never unlock 0× or 2× scaling."""
    from services import council_risk_modulator as crm
    assert crm.MAX_COUNCIL_UPWEIGHT == 1.25
    assert crm.MIN_COUNCIL_DOWNWEIGHT_FLOOR == 0.50
    assert crm.HIGH_CONFIDENCE == 0.70
    assert crm.AGREEMENT_UPWEIGHT == 1.10
    assert crm.OPPOSITE_DISAGREE_DOWNWEIGHT == 0.50


def test_modulator_never_returns_negative_or_above_cap(monkeypatch):
    """Sanity: across the canonical four cases, the multiplier
    stays in [0, MAX_UPWEIGHT]."""
    _enable(monkeypatch)
    cases = [
        ("BUY", 1.0, "BUY", 0.9, True),     # agreement
        ("BUY", 1.0, "SELL", 0.9, True),    # opposite high-conf
        ("BUY", 1.0, "SELL", 0.4, True),    # opposite low-conf
        ("HOLD", 0.0, "BUY", 0.9, True),    # commander hold
    ]
    for cmd_a, cmd_rm, c_a, c_conf, tier in cases:
        out = apply_council_risk_modulation(
            commander_action=cmd_a,
            commander_risk_multiplier=cmd_rm,
            council_action=c_a,
            council_confidence=c_conf,
            council_tier_open=tier,
        )
        assert 0.0 <= out["risk_multiplier"] <= 1.25, (
            f"out-of-bounds for {cmd_a}/{c_a}/{c_conf}: {out}"
        )
