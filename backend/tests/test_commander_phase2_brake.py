"""
Tests for the Commander Phase 2 pre-Tier-3 brake decision logic.

The brake is a pure function; every case is sub-millisecond and requires
no fixtures. These pin the safety contract:

1. brake_eligible=False → brake_applied=False ALWAYS (Phase 1 safety)
2. brake_eligible=True + directional disagreement → brake_applied=True,
   multiplier=0.5
3. strategist=HOLD never produces a brake (there's no entry to halve)
4. Unknown spellings degrade to no-brake (fail-safe)
"""
from __future__ import annotations

import pytest

from services.commander_phase2_brake import (
    BRAKE_MULTIPLIER,
    BrakeDecision,
    apply_brake_to_position,
    decide_brake,
)


# ── Phase 1 safety ────────────────────────────────────────────────


def test_phase_1_never_brakes_even_on_disagreement() -> None:
    """The core Phase 1 invariant — no brake before the promotion gate."""
    b = decide_brake(
        strategist_action="LONG",
        commander_decision="SHORT_OR_AVOID",
        brake_eligible=False,
    )
    assert b.brake_applied is False
    assert b.brake_multiplier == 1.0
    assert b.reason == "phase_1_logging_only"
    # But disagreement IS recorded, so Phase 1 evidence collection works.
    assert b.disagreement is True


def test_phase_1_on_agreement_is_quiet_no_op() -> None:
    b = decide_brake(
        strategist_action="LONG",
        commander_decision="LONG",
        brake_eligible=False,
    )
    assert b.brake_applied is False
    assert b.disagreement is False


# ── Phase 2 — brake on disagreement ───────────────────────────────


@pytest.mark.parametrize(
    "strategist,commander",
    [
        ("LONG", "SHORT_OR_AVOID"),
        ("LONG", "NO_TRADE"),
        ("SHORT", "LONG"),
        ("SHORT", "NO_TRADE"),
        ("BUY", "NO_TRADE"),          # strategist synonym
        ("STRONG_BUY", "SHORT"),      # strategist synonym
        ("SELL", "LONG"),
    ],
)
def test_phase_2_disagreement_halves_position(strategist: str, commander: str) -> None:
    b = decide_brake(
        strategist_action=strategist,
        commander_decision=commander,
        brake_eligible=True,
    )
    assert b.brake_applied is True
    assert b.brake_multiplier == BRAKE_MULTIPLIER == 0.5
    assert b.disagreement is True
    assert b.reason.startswith("phase_2_brake_")


@pytest.mark.parametrize(
    "strategist,commander",
    [
        ("LONG", "LONG"),
        ("SHORT", "SHORT_OR_AVOID"),
        ("SHORT", "SHORT"),
        ("BUY", "LONG"),
    ],
)
def test_phase_2_agreement_no_brake(strategist: str, commander: str) -> None:
    b = decide_brake(
        strategist_action=strategist,
        commander_decision=commander,
        brake_eligible=True,
    )
    assert b.brake_applied is False
    assert b.brake_multiplier == 1.0
    assert b.disagreement is False
    assert b.reason == "phase_2_agreement"


# ── HOLD + unknown fail-safe ──────────────────────────────────────


@pytest.mark.parametrize("commander", ["LONG", "SHORT_OR_AVOID", "NO_TRADE"])
def test_strategist_hold_never_brakes(commander: str) -> None:
    b = decide_brake(
        strategist_action="HOLD",
        commander_decision=commander,
        brake_eligible=True,
    )
    assert b.brake_applied is False
    assert b.reason == "strategist_hold"
    assert b.disagreement is False


def test_unknown_strategist_is_fail_safe() -> None:
    b = decide_brake(
        strategist_action="TAKE_A_WILD_GUESS",
        commander_decision="LONG",
        brake_eligible=True,
    )
    assert b.brake_applied is False
    assert b.reason == "unknown_strategist_action"


def test_unknown_commander_is_fail_safe() -> None:
    b = decide_brake(
        strategist_action="LONG",
        commander_decision=None,
        brake_eligible=True,
    )
    assert b.brake_applied is False
    assert b.reason == "unknown_commander_decision"
    # Caller still fires the trade at full size — commander silent = no veto.


def test_empty_inputs_are_fail_safe() -> None:
    b = decide_brake(
        strategist_action="",
        commander_decision="",
        brake_eligible=True,
    )
    assert b.brake_applied is False
    assert b.brake_multiplier == 1.0


# ── apply_brake_to_position helper ────────────────────────────────


def test_apply_brake_halves_position() -> None:
    brake = BrakeDecision(
        brake_applied=True,
        brake_multiplier=0.5,
        reason="phase_2_brake_long_vs_short",
        disagreement=True,
    )
    assert apply_brake_to_position(1000.0, brake) == 500.0


def test_apply_brake_noop_at_full_multiplier() -> None:
    brake = BrakeDecision(
        brake_applied=False,
        brake_multiplier=1.0,
        reason="phase_2_agreement",
        disagreement=False,
    )
    assert apply_brake_to_position(1000.0, brake) == 1000.0


def test_apply_brake_zero_position_returns_zero() -> None:
    brake = BrakeDecision(True, 0.5, "x", True)
    assert apply_brake_to_position(0.0, brake) == 0.0
    assert apply_brake_to_position(-1.0, brake) == 0.0


# ── Tier 3 bypass (brake disables after full Commander authority) ──


@pytest.mark.parametrize(
    "strategist,commander",
    [
        ("LONG", "SHORT_OR_AVOID"),  # would brake at Phase 2
        ("LONG", "NO_TRADE"),        # would brake at Phase 2
        ("SHORT", "LONG"),           # would brake at Phase 2
        ("LONG", "LONG"),            # would no-op anyway
    ],
)
def test_tier3_unlocked_never_brakes_but_records_disagreement(
    strategist: str, commander: str,
) -> None:
    """Once Tier 3 unlocks, adversarial engine gains full authority.
    The pre-Tier-3 brake must bypass to avoid double-sizing reductions,
    BUT the disagreement record persists for audit continuity."""
    b = decide_brake(
        strategist_action=strategist,
        commander_decision=commander,
        brake_eligible=True,
        tier3_unlocked=True,
    )
    assert b.brake_applied is False
    assert b.brake_multiplier == 1.0
    assert b.reason == "tier3_unlocked_full_authority"
    # Disagreement still captured for telemetry continuity.
    # (LONG/LONG is agreement; everything else here is disagreement.)
    if (strategist, commander) == ("LONG", "LONG"):
        assert b.disagreement is False
    else:
        assert b.disagreement is True


def test_tier3_unlocked_overrides_phase_1_too() -> None:
    """Belt-and-braces: tier3 bypass must win even when brake_eligible
    is False — the ``reason`` tag should reflect tier3, not phase_1."""
    b = decide_brake(
        strategist_action="LONG",
        commander_decision="SHORT_OR_AVOID",
        brake_eligible=False,
        tier3_unlocked=True,
    )
    assert b.brake_applied is False
    assert b.reason == "tier3_unlocked_full_authority"
    assert b.disagreement is True


def test_tier3_default_false_preserves_phase_2_behavior() -> None:
    """Existing callers (no tier3 kwarg) keep the Phase 2 brake exactly
    as it behaved before this patch."""
    b = decide_brake(
        strategist_action="LONG",
        commander_decision="SHORT_OR_AVOID",
        brake_eligible=True,
    )
    assert b.brake_applied is True
    assert b.brake_multiplier == 0.5


# ── Log shape ────────────────────────────────────────────────────


def test_to_log_shape_is_stable() -> None:
    b = decide_brake(
        strategist_action="LONG",
        commander_decision="NO_TRADE",
        brake_eligible=True,
    )
    log = b.to_log()
    assert set(log.keys()) == {
        "brake_applied", "brake_multiplier", "reason", "disagreement",
    }
    assert log["brake_applied"] is True
    assert log["brake_multiplier"] == 0.5
    assert log["disagreement"] is True
