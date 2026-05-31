"""MC Brain API Quickstart v1 — contract tripwires.

Locks in the wire shapes documented in
``MC_BRAIN_API_QUICKSTART_v1.md`` (sent to brain authors 2026-05-31).
If MC's spec drifts and breaks our wire, these tests scream first
so we don't notice via 422s in prod.

Three families of contract:

  1. § 3 Intent emission — required + recommended field shapes,
     ``may_execute`` always False, action vocabulary.
  2. § 4 Opinion emission — stance vocabulary, topic format,
     ``may_execute`` always False, size caps.
  3. § 7 Memory modulator bounds — Alpha never emits out-of-band
     ``value``; the clamp logs but never silently submits the wrong
     thing.

These tests are pure-Python contract checks against the helpers
brains call; they do NOT hit the live MC endpoint. Live verification
is the operator's ``curl`` smoke at deploy time per § 10.
"""
from __future__ import annotations

import pytest

from sovereign.intent_bridge import (
    _build_emission_kwargs,
    _build_opinion_payload,
)


# Spec § 4 — canonical stance vocabulary. If MC adds/removes a stance,
# update this set explicitly so the diff lands in code review.
_VALID_STANCES = frozenset({
    "long", "short", "veto", "endorse", "question", "observation",
    "agree", "disagree", "refine", "retract", "hypothesis",
})

# Spec § 3 — action verbs accepted on the intent wire.
_VALID_INTENT_ACTIONS = frozenset({
    "BUY", "SELL", "SHORT", "COVER", "HOLD", "OPEN", "CLOSE",
})


def _receipt(**over):
    base = {
        "symbol": "NVDA",
        "raw_action": "BUY",
        "final_confidence": 70,
        "raw_confidence": 75,
        "market_decision": "BUY",
        "summary": "post-earnings continuation; VWAP support",
        "entry_price": 142.05,
    }
    base.update(over)
    return base


# ── § 3 Intent emission contract ───────────────────────────────────────


def test_intent_contract_has_all_required_fields():
    """Spec § 3 required fields: stack, action, symbol, lane,
    confidence, rationale."""
    out = _build_emission_kwargs(_receipt(), qty=1.0, notes="r")
    assert out is not None
    for field in ("stack", "action", "symbol", "lane", "confidence", "rationale"):
        assert field in out, f"intent missing required spec § 3 field: {field}"


def test_intent_contract_ships_recommended_fields():
    """Spec § 3 recommended: target_price, stop_price.
    doctrine_snapshot is enriched downstream — verify the price
    pair, which is what unblocks the R:R gate."""
    out = _build_emission_kwargs(_receipt(), qty=1.0, notes="r")
    assert "target_price" in out
    assert "stop_price" in out


def test_intent_contract_action_in_valid_vocabulary():
    out = _build_emission_kwargs(_receipt(), qty=1.0, notes="r")
    assert out["action"] in _VALID_INTENT_ACTIONS


def test_intent_contract_lane_is_lowercase():
    """Spec § 3: lane is lowercase (``equity`` / ``crypto`` / ...)."""
    out = _build_emission_kwargs(_receipt(), qty=1.0, notes="r")
    assert out["lane"] == out["lane"].lower()


def test_intent_contract_confidence_in_unit_scale():
    """Spec § 3: confidence is unit-scale [0, 1]. Receipt carries
    percent; the bridge must scale."""
    out = _build_emission_kwargs(_receipt(final_confidence=70), qty=1.0, notes="")
    assert 0.0 <= out["confidence"] <= 1.0


def test_intent_contract_rr_coherence_buy():
    """Spec § 3 R:R coherence: BUY → target > entry > stop.
    Incoherent prices → MC 422; we must derive coherently."""
    out = _build_emission_kwargs(
        _receipt(raw_action="BUY", entry_price=100.0), qty=1.0, notes="",
    )
    assert out["target_price"] > 100.0
    assert out["stop_price"] < 100.0


def test_intent_contract_rr_coherence_short():
    """Spec § 3 R:R coherence: SHORT → target < entry < stop."""
    out = _build_emission_kwargs(
        _receipt(
            raw_action="SHORT", market_decision="SHORT", entry_price=100.0,
        ),
        qty=1.0, notes="",
    )
    assert out["target_price"] < 100.0
    assert out["stop_price"] > 100.0


def test_intent_contract_does_not_set_may_execute():
    """Spec § 3 invariant: ``may_execute=True`` is rejected at the
    schema validator. Bridge must NEVER set it. MC pins ``False``
    server-side; we just have to not contradict it."""
    out = _build_emission_kwargs(_receipt(), qty=1.0, notes="")
    assert "may_execute" not in out or out.get("may_execute") is False


# ── § 4 Opinion emission contract ──────────────────────────────────────


def test_opinion_contract_topic_uses_canonical_symbol_format():
    """Spec § 4: topic is either ``"free"`` or ``"<kind>:<value>"``
    snake_case. Canonical example for symbol-keyed is ``symbol:NVDA``."""
    out = _build_opinion_payload(_receipt())
    assert out["topic"] == "symbol:NVDA"


def test_opinion_contract_topic_is_snake_case_kind_value():
    """Validate the kind:value shape — colon-separated, kind is
    snake_case and ASCII."""
    out = _build_opinion_payload(_receipt())
    kind, _, value = out["topic"].partition(":")
    assert kind and value, "topic must be <kind>:<value>"
    assert kind == kind.lower(), "kind must be snake_case (lowercase)"
    assert kind.replace("_", "").isalnum()


@pytest.mark.parametrize("action", ["BUY", "SHORT", "HOLD", "SELL", "COVER"])
def test_opinion_contract_stance_in_valid_vocabulary(action):
    """Spec § 4 stance vocabulary tripwire."""
    out = _build_opinion_payload(_receipt(
        raw_action=action, market_decision=action,
    ))
    assert out["stance"] in _VALID_STANCES, (
        f"stance {out['stance']!r} not in MC vocabulary {_VALID_STANCES}"
    )


def test_opinion_contract_does_not_set_may_execute():
    """Spec § 4: ``may_execute=True`` rejected at validator.
    The payload builder must never include it; the wire layer
    (``post_opinion``) pins ``may_execute=False`` server-bound."""
    out = _build_opinion_payload(_receipt())
    assert "may_execute" not in out


def test_opinion_contract_body_size_under_8kb():
    """Spec § 4: body ≤ 8 KB. Our bodies are always summaries —
    just lock the ceiling so a future "stuff the whole hypothesis
    in body" change trips this."""
    out = _build_opinion_payload(_receipt())
    assert len(out["body"].encode("utf-8")) < 8 * 1024


def test_opinion_contract_evidence_size_under_16kb():
    """Spec § 4: evidence ≤ 16 KB serialized."""
    import json
    out = _build_opinion_payload(_receipt())
    assert len(json.dumps(out["evidence"]).encode("utf-8")) < 16 * 1024


def test_opinion_contract_confidence_in_unit_scale():
    out = _build_opinion_payload(_receipt())
    assert 0.0 <= out["confidence"] <= 1.0


def test_opinion_contract_no_regime_placeholder():
    """Operator directive 2026-06: do NOT ship ``regime: "unknown"``
    placeholders — MC's ``_regime_format`` validator handles missing
    gracefully, but placeholder strings pollute the
    "endorse hit rate by regime" scoring view. Bridge must NOT emit
    regime until we have a real tag to send."""
    out = _build_opinion_payload(_receipt())
    assert "regime" not in out


# ── § 7 Memory modulator bounds ────────────────────────────────────────


def test_modulator_contract_bounds_are_negative_25_to_positive_10():
    """Spec § 3 / § 7: ``memory_modulator.value`` must be in
    ``[-0.25, +0.10]``. MC will 422 hard — no silent clamp on their
    side. Alpha clamps locally AND warns when clamping fires (signal
    the upstream math is suspect)."""
    from shared.memory_modulator import MAX_UP, MAX_DOWN, _clamp_modulator
    assert MAX_UP == 0.10
    assert MAX_DOWN == -0.25
    # In-band passes through unchanged.
    assert _clamp_modulator(-0.20) == pytest.approx(-0.20)
    assert _clamp_modulator(0.05) == pytest.approx(0.05)
    # Out-of-band clamps (and the implementation logs a warning — see
    # test_memory_modulator.py for the existing clamp coverage).
    assert _clamp_modulator(-0.30) == pytest.approx(-0.25)
    assert _clamp_modulator(0.15) == pytest.approx(0.10)


def test_modulator_contract_non_finite_collapses_to_zero():
    """NaN / inf → 0.0 with a warning. We never ship a non-finite
    value to MC — that would 422 and is also nonsense."""
    import math
    from shared.memory_modulator import _clamp_modulator
    assert _clamp_modulator(float("nan")) == 0.0
    assert _clamp_modulator(float("inf")) == 0.0
    assert _clamp_modulator(-math.inf) == 0.0
