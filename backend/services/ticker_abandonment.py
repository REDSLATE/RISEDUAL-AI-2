"""
Ticker Abandonment / Cooldown Gate (READ-ONLY)

A ticker can be watched forever, but it cannot consume trading
bandwidth forever. This module decides whether RISEDUAL should
KEEP, COOLDOWN, or ABANDON a ticker — based purely on recent
behaviour, not on any persistent state.

Why stateless
─────────────
The decision recomputes from the rolling-window inputs every call.
Once enough time passes WITHOUT trading the ticker (because we
abandoned it), the recent_losses / recent_signals counters age
out of the window and the gate naturally flips back to KEEP. No
explicit "release the cooldown" step needed.

Side-effect-free
────────────────
Pure function. Never raises. Never mutates inputs. Safe to call
from any decision path; safe to call repeatedly. Behaviour
identical to the operator-approved spec dropped on 2026-05-04.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class TickerExitDecision:
    action: str  # KEEP, COOLDOWN, ABANDON
    reason: str
    cooldown_minutes: int = 0


def decide_ticker_exit(
    *,
    symbol: str,
    recent_signals: int,
    recent_rejections: int,
    recent_losses: int,
    recent_wins: int,
    avg_confidence: float,
    avg_rr: float,
    last_profitable_at: datetime | None,
    now: datetime | None = None,
) -> TickerExitDecision:
    """
    Determines whether RISEDUAL should keep evaluating, cool down,
    or temporarily abandon a ticker.

    This does NOT delete the ticker.
    It only removes it from active trade attention.
    """

    now = now or datetime.now(timezone.utc)

    total_resolved = recent_wins + recent_losses
    win_rate = recent_wins / total_resolved if total_resolved else 0.0
    rejection_rate = recent_rejections / recent_signals if recent_signals else 0.0

    # 1. Not enough data: keep watching
    if recent_signals < 5:
        return TickerExitDecision(
            action="KEEP",
            reason="not_enough_recent_signals",
        )

    # 2. Toxic symbol: too many losses
    if recent_losses >= 4 and win_rate < 0.35:
        return TickerExitDecision(
            action="ABANDON",
            reason="loss_cluster_low_win_rate",
            cooldown_minutes=1440,
        )

    # 3. Model keeps wanting it, gates keep rejecting it
    if rejection_rate >= 0.75 and recent_signals >= 8:
        return TickerExitDecision(
            action="COOLDOWN",
            reason="high_rejection_rate",
            cooldown_minutes=360,
        )

    # 4. Bad reward/risk
    if avg_rr < 1.2 and recent_signals >= 5:
        return TickerExitDecision(
            action="COOLDOWN",
            reason="poor_average_rr",
            cooldown_minutes=240,
        )

    # 5. Low-confidence churn
    if avg_confidence < 0.55 and recent_signals >= 8:
        return TickerExitDecision(
            action="COOLDOWN",
            reason="low_confidence_churn",
            cooldown_minutes=180,
        )

    # 6. No profitable event in a while
    if last_profitable_at is not None:
        if now - last_profitable_at > timedelta(days=7) and recent_losses >= 3:
            return TickerExitDecision(
                action="COOLDOWN",
                reason="stale_no_recent_profit",
                cooldown_minutes=720,
            )

    return TickerExitDecision(
        action="KEEP",
        reason="symbol_still_eligible",
    )
