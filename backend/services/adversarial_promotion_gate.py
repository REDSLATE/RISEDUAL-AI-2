"""
Adversarial Cores promotion gate — readiness for ``shadow → risk_only →
veto → full``.

Why this module exists
----------------------
``adversarial_monitor.summarize_24h`` answers "are the cores alive?".
This module answers "have they earned the right to influence trades?".
Two different questions, two different aggregation windows:

* The 24h chip is operator situational awareness — it goes amber when
  no rows have flowed in, emerald when they're flowing.
* The promotion gate is *lifetime* over the entire decision log,
  because a 24h slice can't carry enough closed rows to declare an
  edge with statistical confidence. The gate threshold for the very
  first promotion (``shadow → risk_only``) is intentionally low (20
  closed rows) so the operator can see progression early; later
  transitions raise the bar.

Promotion criterion
-------------------
The Commander's *chosen side* is the one we measure, not Bull or Bear
in isolation. From the ``crypto_adversarial_decision_log`` schema
(see ``services.adversarial_logger.derive_winner``)::

    decision="LONG"           + winner="bull" → Commander right
    decision="SHORT_OR_AVOID" + winner="bear" → Commander right
    decision="NO_TRADE"       (shadow phase)  → not counted; Commander
        explicitly opted out of forming an opinion the trade fired on,
        so attributing an outcome to "the Commander" double-counts the
        Bull/Bear winner the trade already paid out to.

This is the single metric driving ``commander_correct_rate``. Bull−Bear
spread surfaces in the chip for operator intuition but is not the
gate's primary signal.

Thresholds are all env-tunable. Defaults match the policy stated in
``TIER3_ACTIVATION_PLAYBOOK.md``: rising bar across phases, the very
first transition can clear once 20 closed rows land.

This module is **inform-only**. It computes readiness; it never
flips an env flag or mutates phase. Promotion happens via an explicit
operator action editing ``backend/.env`` and restarting the backend
(matches the audit-stable discipline of ``commander_phase2_brake``).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Literal

logger = logging.getLogger(__name__)

CollectionName = "crypto_adversarial_decision_log"

Phase = Literal["shadow", "risk_only", "veto", "full"]

# Env flag names — duplicated from ``adversarial_monitor`` to avoid an
# import cycle (this module is the source of truth for the gate; the
# monitor only renders 24h activity).
ENV_ENABLE = "CRYPTO_ADVERSARIAL_ENABLED"
ENV_PHASE = "CRYPTO_ADVERSARIAL_PHASE"

# Thresholds per transition. Lifetime closed-row count + Commander
# correct-rate floor. Each entry is overridable via env.
def _f(env_key: str, default: float) -> float:
    try:
        return float(os.environ.get(env_key, default))
    except (TypeError, ValueError):
        return default


def _i(env_key: str, default: int) -> int:
    try:
        return int(os.environ.get(env_key, default))
    except (TypeError, ValueError):
        return default


# Trustworthy-spread row count: below this many closed rows, the chip
# shouldn't display Bull−Bear spread without a "low confidence" caveat.
# Pinned to the same number as the first promotion's row floor so
# "trustworthy" and "ready to promote (rows)" land in the same place.
TRUSTWORTHY_MIN_CLOSED: int = _i("ADV_TRUSTWORTHY_MIN_CLOSED", 20)


def _thresholds() -> dict[str, dict[str, float | int]]:
    """Per-transition floors. Read from env on every call so tests
    can ``monkeypatch.setenv`` without re-importing the module."""
    return {
        "shadow_to_risk_only": {
            "min_closed": _i("ADV_PROMOTE_S_TO_R_ROWS", 20),
            "min_correct_rate": _f("ADV_PROMOTE_S_TO_R_RATE", 0.55),
        },
        "risk_only_to_veto": {
            "min_closed": _i("ADV_PROMOTE_R_TO_V_ROWS", 50),
            "min_correct_rate": _f("ADV_PROMOTE_R_TO_V_RATE", 0.58),
        },
        "veto_to_full": {
            "min_closed": _i("ADV_PROMOTE_V_TO_F_ROWS", 100),
            "min_correct_rate": _f("ADV_PROMOTE_V_TO_F_RATE", 0.60),
        },
    }


# Phase → next-transition key. ``full`` is terminal — no further
# promotion to surface.
_NEXT_TRANSITION: dict[str, str | None] = {
    "shadow": "shadow_to_risk_only",
    "risk_only": "risk_only_to_veto",
    "veto": "veto_to_full",
    "full": None,
}


def _phase() -> Phase:
    raw = (os.environ.get(ENV_PHASE) or "shadow").lower()
    if raw not in ("shadow", "risk_only", "veto", "full"):
        return "shadow"
    return raw  # type: ignore[return-value]


def _enabled() -> bool:
    return os.environ.get(ENV_ENABLE) == "1"


def _next_phase(current: Phase) -> Phase | None:
    return {
        "shadow": "risk_only",
        "risk_only": "veto",
        "veto": "full",
        "full": None,
    }[current]  # type: ignore[return-value]


def _empty_envelope() -> dict[str, Any]:
    """Stable shape returned when DB is None / read fails / env off."""
    return {
        "enabled": _enabled(),
        "current_phase": _phase(),
        "next_phase": _next_phase(_phase()),
        "closed_lifetime": 0,
        "trustworthy": False,
        "trustworthy_min_closed": TRUSTWORTHY_MIN_CLOSED,
        "rows_to_trustworthy": TRUSTWORTHY_MIN_CLOSED,
        "commander_correct": 0,
        "commander_correct_rate": None,
        "bull_win_rate_lifetime": None,
        "bear_win_rate_lifetime": None,
        "spread_lifetime_pp": None,
        "ready_to_promote": False,
        "blocker": "no data",
        "next_transition": _NEXT_TRANSITION.get(_phase()),
        "thresholds": _thresholds(),
        "promote_env_line": _promote_env_line(_phase()),
        "collection": CollectionName,
    }


def _promote_env_line(current: Phase) -> str | None:
    """Copy-pastable env line for the operator. None when terminal."""
    nxt = _next_phase(current)
    if nxt is None:
        return None
    return f"{ENV_PHASE}={nxt}"


async def compute_promotion_status(db: Any) -> dict[str, Any]:
    """Lifetime promotion readiness for the adversarial cores.

    Never raises — Mongo errors degrade to the empty envelope so the
    chip stays renderable. The current ``CRYPTO_ADVERSARIAL_PHASE`` env
    determines which next-transition row floor + win-rate floor we
    measure against; ``full`` returns ``next_transition=None`` and
    ``ready_to_promote=False`` (terminal).
    """
    if db is None:
        return _empty_envelope()

    current = _phase()
    nxt_key = _NEXT_TRANSITION.get(current)

    try:
        coll = db[CollectionName]
        # Closed = winner field is set to one of bull/bear/neutral.
        # ``adversarial_logger.update_decision_outcome`` is the only
        # writer for these fields, so $exists is reliable.
        closed_total = await coll.count_documents({"winner": {"$in": ["bull", "bear", "neutral"]}})

        # Commander-correct rows: the rule above maps decision+winner
        # to "Commander was right". NO_TRADE is excluded — see module
        # docstring for the rationale.
        commander_correct = await coll.count_documents({
            "$or": [
                {"decision": "LONG", "winner": "bull"},
                {"decision": "SHORT_OR_AVOID", "winner": "bear"},
            ],
        })

        # Bull / Bear lifetime win counts (across ALL closed rows
        # regardless of which side Commander picked) — purely for the
        # operator-facing spread. NOT used to gate promotion.
        bull_wins = await coll.count_documents({"winner": "bull"})
        bear_wins = await coll.count_documents({"winner": "bear"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[adv-promote] read failed: %s", exc)
        env = _empty_envelope()
        env["blocker"] = f"read failed: {exc}"
        return env

    correct_rate: float | None = None
    if closed_total > 0:
        correct_rate = round(commander_correct / closed_total, 4)

    bull_wr: float | None = None
    bear_wr: float | None = None
    if closed_total > 0:
        bull_wr = round(bull_wins / closed_total, 4)
        bear_wr = round(bear_wins / closed_total, 4)
    spread_pp: float | None = None
    if bull_wr is not None and bear_wr is not None:
        spread_pp = round((bull_wr - bear_wr) * 100, 1)

    trustworthy = closed_total >= TRUSTWORTHY_MIN_CLOSED
    rows_to_trustworthy = max(0, TRUSTWORTHY_MIN_CLOSED - closed_total)

    ready = False
    blocker: str | None = None

    if nxt_key is None:
        blocker = "already at terminal phase 'full'"
    else:
        floors = _thresholds()[nxt_key]
        min_rows = int(floors["min_closed"])
        min_rate = float(floors["min_correct_rate"])
        rows_ok = closed_total >= min_rows
        rate_ok = (correct_rate is not None) and (correct_rate >= min_rate)
        ready = bool(rows_ok and rate_ok)
        if not ready:
            if not rows_ok:
                blocker = (
                    f"need {min_rows - closed_total} more closed rows "
                    f"(have {closed_total}/{min_rows})"
                )
            else:
                # rows_ok but rate not — surface the gap.
                rate_str = f"{correct_rate:.2%}" if correct_rate is not None else "n/a"
                blocker = (
                    f"commander_correct_rate {rate_str} below "
                    f"{min_rate:.0%} threshold"
                )

    return {
        "enabled": _enabled(),
        "current_phase": current,
        "next_phase": _next_phase(current),
        "closed_lifetime": closed_total,
        "trustworthy": trustworthy,
        "trustworthy_min_closed": TRUSTWORTHY_MIN_CLOSED,
        "rows_to_trustworthy": rows_to_trustworthy,
        "commander_correct": commander_correct,
        "commander_correct_rate": correct_rate,
        "bull_win_rate_lifetime": bull_wr,
        "bear_win_rate_lifetime": bear_wr,
        "spread_lifetime_pp": spread_pp,
        "ready_to_promote": ready,
        "blocker": blocker,
        "next_transition": nxt_key,
        "thresholds": _thresholds(),
        "promote_env_line": _promote_env_line(current),
        "collection": CollectionName,
    }
