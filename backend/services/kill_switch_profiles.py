"""Named Kill-Switch Profiles — discipline overlays for paper / live cores
(2026-02-26, P2).

This module defines a registry of **named behavioural discipline
profiles** that overlay on top of the existing fleet-wide
``ai_core.kill_switch`` engine. Where the fleet kill-switch trips on
infrastructure-scale signals (drawdown across the whole equity curve,
broker error rate), a *profile* encodes **per-account, per-session**
trading discipline: daily max loss, consecutive-loser streaks,
session profit caps, etc.

The first profile — ``small_account_warrior`` — is a faithful
translation of the public Warrior Trading "Small Account Toolkit"
discipline rules (Rules 1-3 in the toolkit PDF). A small-account
trader who selects this profile gets a hard halt the moment any of
the documented rules trips, exactly matching the discipline the
toolkit recommends.

Design choices
--------------
* **Pure-function evaluator** — ``evaluate_profile`` is sync and
  takes a plain ``AccountStats`` dict, so it can be called from
  the paper-trade emission path, the admin endpoint, or a unit
  test without any DB / async coupling.
* **Profiles are static and code-versioned** — defined as
  module-level frozen dataclasses. Profile changes go through PR
  review, never through admin write surfaces. This matches the
  council-policy doctrine: "admin can RAISE the bar but not
  fundamentally redefine the safety surface".
* **Triggers are explicit** — every trip emits a
  ``ProfileTriggerHit`` row naming the rule that fired, the
  numeric trigger value, and the threshold that was breached.
  Operator readouts and post-mortems show "rule_3_loss_streak"
  rather than "halted: kill switch active".
* **Profiles compose with the fleet kill-switch, never replace
  it** — a profile halt is a more granular, per-account trip;
  the fleet switch still wins if it fires first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Profile:
    """A named discipline profile.

    Numeric thresholds are stored as plain floats; an evaluator
    function pulled out of ``EVALUATORS`` at runtime does the work.
    """

    key: str
    name: str
    description: str
    source: str
    # Pct-of-account loss that ends the session (e.g. 0.10 = -10%).
    daily_max_loss_pct: float | None = None
    # Consecutive-loser count that ends the session.
    consecutive_loss_limit: int | None = None
    # Pct-of-account profit cap (give-back guard). ``None`` = no cap.
    daily_profit_cap_pct: float | None = None
    # Hard floor in absolute USD. Belt-and-suspenders for tiny
    # accounts where ``daily_max_loss_pct`` rounds to <$1.
    daily_max_loss_usd: float | None = None
    # Operator-friendly tags surfaced in the admin response.
    tags: tuple[str, ...] = field(default_factory=tuple)


# ── Profile registry ────────────────────────────────────────────────


WARRIOR_SMALL_ACCOUNT = Profile(
    key="small_account_warrior",
    name="Small Account — Warrior Discipline",
    description=(
        "Discipline overlay for small-account ($500–$5,000) traders. "
        "Mirrors the three written rules in the public Warrior "
        "Trading Small Account Toolkit: (1) risk small to make small, "
        "(2) daily max loss at -10% of account or -$100 hard floor, "
        "(3) three consecutive losers and the session is over."
    ),
    source="Warrior Trading — 2025 Small Account Toolkit",
    daily_max_loss_pct=0.10,
    daily_max_loss_usd=100.0,
    consecutive_loss_limit=3,
    daily_profit_cap_pct=None,  # toolkit explicitly says "don't stop
                                # until momentum cools" — no give-back
                                # cap baked in.
    tags=("small_account", "discipline", "warrior_trading"),
)


PROFILES: dict[str, Profile] = {
    WARRIOR_SMALL_ACCOUNT.key: WARRIOR_SMALL_ACCOUNT,
}


def list_profiles() -> list[dict[str, Any]]:
    """JSON-safe profile list for the admin endpoint."""
    return [
        {
            "key": p.key,
            "name": p.name,
            "description": p.description,
            "source": p.source,
            "rules": {
                "daily_max_loss_pct": p.daily_max_loss_pct,
                "daily_max_loss_usd": p.daily_max_loss_usd,
                "consecutive_loss_limit": p.consecutive_loss_limit,
                "daily_profit_cap_pct": p.daily_profit_cap_pct,
            },
            "tags": list(p.tags),
        }
        for p in PROFILES.values()
    ]


def get_profile(key: str) -> Profile | None:
    return PROFILES.get((key or "").strip().lower())


# ── Evaluator ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProfileTriggerHit:
    """One concrete rule fired against the supplied account state."""

    rule: str
    threshold: float | int
    observed: float | int
    message: str


@dataclass(frozen=True)
class ProfileEvaluation:
    """Snapshot of every rule against current account state.

    ``halt`` is the operator-facing verdict: if True, the caller
    (paper trader, live router) must NOT emit a new trade for this
    account until the session resets.
    """

    profile_key: str
    halt: bool
    triggers: tuple[ProfileTriggerHit, ...]
    inputs: Mapping[str, Any]


def evaluate_profile(
    profile_key: str,
    *,
    starting_equity_usd: float,
    realized_pnl_usd_today: float,
    consecutive_losses_today: int,
) -> ProfileEvaluation:
    """Run every rule on ``profile_key`` against the inputs.

    Parameters
    ----------
    profile_key
        Registry key (e.g. ``small_account_warrior``).
    starting_equity_usd
        Account equity at session start (00:00 ET). Used to convert
        the toolkit's pct rules to absolute USD.
    realized_pnl_usd_today
        Cumulative realized P&L since session start. Negative when
        the trader is down.
    consecutive_losses_today
        Number of consecutive losing trades closed since session
        start; resets to zero on the next winning trade.

    Returns
    -------
    ProfileEvaluation
        ``halt=True`` iff at least one rule trips. ``triggers`` lists
        every rule that fired (not just the first) so the operator
        readout shows the full discipline picture.
    """
    profile = get_profile(profile_key)
    if profile is None:
        raise ValueError(f"unknown profile: {profile_key!r}")

    triggers: list[ProfileTriggerHit] = []

    # Rule 2 from the toolkit — daily max loss at 10% of account.
    # Encoded as the WORSE of (pct-of-equity) or (hard USD floor),
    # so a $1,000 account at -10% ($100) hits both arms simultaneously
    # and a $200 account is still capped by the $100 absolute floor.
    if profile.daily_max_loss_pct is not None and starting_equity_usd > 0:
        loss_threshold_usd = -1.0 * profile.daily_max_loss_pct * starting_equity_usd
        if realized_pnl_usd_today <= loss_threshold_usd:
            triggers.append(ProfileTriggerHit(
                rule="rule_2_daily_max_loss_pct",
                threshold=round(loss_threshold_usd, 2),
                observed=round(realized_pnl_usd_today, 2),
                message=(
                    f"daily loss {realized_pnl_usd_today:+.2f} ≤ "
                    f"{loss_threshold_usd:+.2f} "
                    f"({profile.daily_max_loss_pct:.0%} of starting equity)"
                ),
            ))

    if profile.daily_max_loss_usd is not None:
        usd_floor = -1.0 * abs(profile.daily_max_loss_usd)
        if realized_pnl_usd_today <= usd_floor:
            triggers.append(ProfileTriggerHit(
                rule="rule_2_daily_max_loss_usd",
                threshold=usd_floor,
                observed=round(realized_pnl_usd_today, 2),
                message=(
                    f"daily loss {realized_pnl_usd_today:+.2f} ≤ "
                    f"{usd_floor:+.2f} (hard USD floor)"
                ),
            ))

    # Rule 3 — three consecutive losers and the session is over.
    if (
        profile.consecutive_loss_limit is not None
        and consecutive_losses_today >= profile.consecutive_loss_limit
    ):
        triggers.append(ProfileTriggerHit(
            rule="rule_3_consecutive_losses",
            threshold=profile.consecutive_loss_limit,
            observed=consecutive_losses_today,
            message=(
                f"{consecutive_losses_today} consecutive losing trades "
                f"≥ limit of {profile.consecutive_loss_limit}"
            ),
        ))

    # Optional give-back / profit-cap rule — surfaces a halt at the
    # day's peak when configured (Warrior profile leaves this None;
    # future profiles can opt in).
    if profile.daily_profit_cap_pct is not None and starting_equity_usd > 0:
        cap_usd = profile.daily_profit_cap_pct * starting_equity_usd
        if realized_pnl_usd_today >= cap_usd:
            triggers.append(ProfileTriggerHit(
                rule="profit_cap_pct",
                threshold=round(cap_usd, 2),
                observed=round(realized_pnl_usd_today, 2),
                message=(
                    f"daily gain {realized_pnl_usd_today:+.2f} ≥ "
                    f"{cap_usd:+.2f} ({profile.daily_profit_cap_pct:.0%} cap)"
                ),
            ))

    return ProfileEvaluation(
        profile_key=profile.key,
        halt=bool(triggers),
        triggers=tuple(triggers),
        inputs={
            "starting_equity_usd": starting_equity_usd,
            "realized_pnl_usd_today": realized_pnl_usd_today,
            "consecutive_losses_today": consecutive_losses_today,
        },
    )


__all__ = [
    "Profile",
    "ProfileTriggerHit",
    "ProfileEvaluation",
    "WARRIOR_SMALL_ACCOUNT",
    "PROFILES",
    "list_profiles",
    "get_profile",
    "evaluate_profile",
]
