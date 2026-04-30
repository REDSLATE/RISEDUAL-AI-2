from services.adversarial_enforcer import (
    AgentDecision,
    CommanderDecision,
    enforce_adversarial_decision,
    EnforcementReason,
)


def test_low_dissent_forces_hold():
    result = enforce_adversarial_decision(
        bull=AgentDecision(name="bull", action="BUY", confidence=0.72),
        bear=AgentDecision(name="bear", action="SELL", confidence=0.70),
    )

    assert result.action == "HOLD"
    assert result.allowed_to_trade is False
    assert result.reason == EnforcementReason.HIGH_CONFLICT_FORCE_HOLD


def test_commander_signed_override_allowed():
    result = enforce_adversarial_decision(
        bull=AgentDecision(name="bull", action="BUY", confidence=0.72),
        bear=AgentDecision(name="bear", action="SELL", confidence=0.82),
        commander=CommanderDecision(
            action="BUY",
            confidence=0.90,
            override=True,
            signature_hash="commander-signed",
            reason="regime_override",
        ),
    )

    assert result.action == "BUY"
    assert result.allowed_to_trade is True
    assert result.reason == EnforcementReason.COMMANDER_OVERRIDE_ACCEPTED


def test_unsigned_commander_override_denied():
    result = enforce_adversarial_decision(
        bull=AgentDecision(name="bull", action="BUY", confidence=0.90),
        bear=AgentDecision(name="bear", action="SELL", confidence=0.20),
        commander=CommanderDecision(
            action="SELL",
            confidence=0.95,
            override=True,
            signature_hash=None,
            reason="unsigned",
        ),
    )

    assert result.action == "BUY"
    assert result.allowed_to_trade is True


def test_invalid_confidence_forces_hold():
    result = enforce_adversarial_decision(
        bull=AgentDecision(name="bull", action="BUY", confidence=1.50),
        bear=AgentDecision(name="bear", action="SELL", confidence=0.20),
    )

    assert result.action == "HOLD"
    assert result.allowed_to_trade is False
    assert result.reason == EnforcementReason.INVALID_CONFIDENCE
