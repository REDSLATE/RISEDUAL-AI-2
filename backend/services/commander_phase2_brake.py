"""
Commander Shadow — Phase 2 pre-Tier-3 size brake.

Design contract
---------------
Phase 1 (logging only, default today) accumulates ``research_shadow_decisions``
rows with back-patched ``tactical_score.shadow_was_right``. Once
:func:`services.equity_shadow_promotion.compute_equity_shadow_promotion_status`
reports ``brake_eligible=True`` (50+ scored rows AND ≥70% win rate), Phase 2
unlocks AUTOMATICALLY — no env flip, no deploy. When Commander's verdict
disagrees with Strategist's verdict on an equity entry, the position is
halved. Direction is NEVER overridden by Commander in Phase 2 — that's
reserved for Phase 3 / ``full`` authority (explicit operator flag, not
covered here).

Pure function, ZERO I/O
-----------------------
This module deliberately contains no Mongo / network / clock reads. Caller
provides the promotion state + Commander + Strategist verdicts, receives a
:class:`BrakeDecision`. That keeps the decision logic testable in
microseconds and reusable from any entry surface (``ml_paper_trader``,
``crypto_paper_trader``, future options path) without pulling the
equity-specific promotion gate into unrelated contexts.

Safety invariant (pinned by tests)
----------------------------------
``brake_eligible=False`` → ``brake_applied=False`` UNCONDITIONALLY, even on
a textbook Commander/Strategist disagreement. Phase 1 must stay a pure
logging lane until the promotion gate opens on its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


# Hard-coded per the handoff spec: Phase 2 halves size. Any future re-tune
# lives here, deliberately NOT env-configurable to keep the brake behaviour
# audit-stable across deploys (rollout playbook calls for "halve or stop",
# not "dial a knob").
BRAKE_MULTIPLIER: float = 0.5

# Commander decision values that count as "not-long" / "not-short". Mirrors
# the strings returned by ``services.adversarial_core.resolve_adversarial``
# and ``services.research_shadow``'s ``SHORT_OR_AVOID`` spelling.
_COMMANDER_LONG = {"LONG"}
_COMMANDER_SHORT = {"SHORT", "SHORT_OR_AVOID", "SHORT_OR_REJECT"}
_COMMANDER_NO_TRADE = {"NO_TRADE", "HOLD", "AVOID"}

# Strategist action values accepted at the entry surface.
_STRATEGIST_LONG = {"LONG", "BUY", "STRONG_BUY"}
_STRATEGIST_SHORT = {"SHORT", "SELL", "STRONG_SELL"}
_STRATEGIST_HOLD = {"HOLD", "FLAT", "NONE"}


@dataclass(frozen=True)
class BrakeDecision:
    """Result of a Phase 2 brake evaluation.

    * ``brake_applied`` — True iff the caller should multiply its base
      position size by ``brake_multiplier``. Callers that ignore this flag
      get Phase 1 behaviour (no change).
    * ``brake_multiplier`` — 1.0 when no brake, ``BRAKE_MULTIPLIER`` (0.5)
      when applied. Already clamped — callers should use it directly.
    * ``reason`` — short machine-readable tag for logs / telemetry.
      Empty string when Phase 2 is not active (brake_eligible=False).
    * ``disagreement`` — True iff Strategist and Commander would have
      taken different directional actions on this entry. Tracked even
      when ``brake_applied`` is False so the phase-1 logging path can
      count "Commander would have disagreed" evidence separately.
    """

    brake_applied: bool
    brake_multiplier: float
    reason: str
    disagreement: bool

    def to_log(self) -> dict[str, Any]:
        """Shape suitable for appending to a trade row's audit column.

        Kept deliberately tiny — two booleans, one float, one string —
        so a paper-trades Mongo row stays compact.
        """
        return {
            "brake_applied": self.brake_applied,
            "brake_multiplier": self.brake_multiplier,
            "reason": self.reason,
            "disagreement": self.disagreement,
        }


def _canonical_strategist(action: Optional[str]) -> Optional[str]:
    """Fold strategist variants into LONG / SHORT / HOLD, or None if unknown.

    Unknown spellings return ``None`` so ``decide_brake`` can degrade to a
    safe no-op rather than silently misclassify a typoed action as HOLD
    (which would suppress the brake on a real entry).
    """
    if not action:
        return None
    key = str(action).strip().upper()
    if key in _STRATEGIST_LONG:
        return "LONG"
    if key in _STRATEGIST_SHORT:
        return "SHORT"
    if key in _STRATEGIST_HOLD:
        return "HOLD"
    return None


def _canonical_commander(decision: Optional[str]) -> Optional[str]:
    """Fold commander variants into LONG / SHORT / NO_TRADE, or None.

    ``None`` surfaces when the caller couldn't compute a commander
    verdict (adversarial gates closed, pure-function error). In that
    case we treat it as "no evidence" → no brake.
    """
    if not decision:
        return None
    key = str(decision).strip().upper()
    if key in _COMMANDER_LONG:
        return "LONG"
    if key in _COMMANDER_SHORT:
        return "SHORT"
    if key in _COMMANDER_NO_TRADE:
        return "NO_TRADE"
    return None


def _is_disagreement(strategist: str, commander: str) -> bool:
    """True iff Strategist and Commander would fire different actions.

    Table (only rows where strategist actually fires an entry —
    strategist=HOLD never reaches the brake because there's no trade
    to halve):

        strategist=LONG   + commander=LONG     → agree
        strategist=LONG   + commander=SHORT    → disagree (brake)
        strategist=LONG   + commander=NO_TRADE → disagree (brake)
        strategist=SHORT  + commander=LONG     → disagree (brake)
        strategist=SHORT  + commander=SHORT    → agree
        strategist=SHORT  + commander=NO_TRADE → disagree (brake)
    """
    if strategist == "LONG":
        return commander != "LONG"
    if strategist == "SHORT":
        return commander != "SHORT"
    # strategist=HOLD is handled upstream — no entry, no brake.
    return False


def decide_brake(
    *,
    strategist_action: Optional[str],
    commander_decision: Optional[str],
    brake_eligible: bool,
) -> BrakeDecision:
    """Decide whether to halve an equity entry's position size.

    The hot path is intentionally short — pure compare-and-return.
    Caller contract:

    * Even with a clean disagreement, ``brake_eligible=False`` pins
      ``brake_applied=False``. Phase 1 is sacred.
    * ``strategist_action=HOLD`` returns an all-False decision — there
      is no entry to brake.
    * Unknown strategist or commander spellings degrade to no-brake
      rather than raising. Telemetry still records ``reason="unknown_*"``
      so the operator can spot a contract drift upstream.
    """
    s = _canonical_strategist(strategist_action)
    c = _canonical_commander(commander_decision)

    # No active entry — nothing to brake.
    if s == "HOLD":
        return BrakeDecision(
            brake_applied=False,
            brake_multiplier=1.0,
            reason="strategist_hold",
            disagreement=False,
        )

    # Unknown input → fail safe. The caller's trade still fires; we just
    # don't have enough evidence to modify sizing.
    if s is None:
        return BrakeDecision(
            brake_applied=False,
            brake_multiplier=1.0,
            reason="unknown_strategist_action",
            disagreement=False,
        )
    if c is None:
        return BrakeDecision(
            brake_applied=False,
            brake_multiplier=1.0,
            reason="unknown_commander_decision",
            disagreement=False,
        )

    disagreement = _is_disagreement(s, c)

    # Phase 1 — evidence-only. Always logs disagreement, never modifies size.
    if not brake_eligible:
        return BrakeDecision(
            brake_applied=False,
            brake_multiplier=1.0,
            reason="phase_1_logging_only",
            disagreement=disagreement,
        )

    # Phase 2 — brake on disagreement only.
    if disagreement:
        return BrakeDecision(
            brake_applied=True,
            brake_multiplier=BRAKE_MULTIPLIER,
            reason=f"phase_2_brake_{s.lower()}_vs_{c.lower()}",
            disagreement=True,
        )

    return BrakeDecision(
        brake_applied=False,
        brake_multiplier=1.0,
        reason="phase_2_agreement",
        disagreement=False,
    )


def apply_brake_to_position(
    position_usd: float, brake: BrakeDecision,
) -> float:
    """Convenience: apply the brake multiplier to a base position size.

    Callers may skip this and do the multiplication themselves; kept
    here so the intent reads clearly at the trade-entry surface:

        sized = apply_brake_to_position(half_kelly_usd, brake)
    """
    if position_usd <= 0:
        return 0.0
    return round(position_usd * brake.brake_multiplier, 4)
