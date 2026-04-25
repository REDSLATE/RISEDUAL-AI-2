"""Crypto Adaptation Service — closed-loop learning starter.

Mirrors the equity ML adaptation engine
(``services/model_adaptation.py``) at miniature scale, restricted to
the crypto lane:

* :func:`detect_crypto_adaptations` periodically scans
  ``crypto_trade_memory`` for repeated ``failure_code × regime``
  patterns. When ≥``MIN_EVIDENCE_COUNT`` losing trades share a key,
  a 14-day adaptation row is upserted into
  ``crypto_model_adaptations`` with ``factor=0.85`` (down-weight
  confidence by 15% on matching future setups).

* :func:`apply_crypto_adaptations_to_signal` is the runtime hook —
  call it BEFORE the bot's HOLD/fill branch. Active adaptations
  multiply ``signal["confidence"]`` by their factor; if combined
  confidence falls under the 0.60 floor the signal is forced to
  HOLD with a tagged reason.

Boundaries
----------
This is the *minimum viable* adaptive loop — no SHAP, no
counterfactuals, no auto-revert (that's the equity engine's
maturity level). Crypto needs to accumulate fills before those
mechanisms make sense. Caps:

* ``MAX_ACTIVE_CRYPTO_ADAPTATIONS = 4`` — bounded blast radius if
  the detector mis-fires.
* ``COOLDOWN_DAYS = 7`` — same key can't recreate a fresh
  adaptation more than once a week.
* ``ADAPTATION_TTL_DAYS = 14`` — every adaptation auto-expires;
  no adaptation is "permanent" until the equity engine's audit
  trail comes online for crypto too.

Architecture rule
-----------------
Reads/writes ONLY:

    * crypto_trade_memory       (read)
    * crypto_model_adaptations  (read + write)

Never touches:

    * model_adaptations         (equity)
    * adaptation_audit          (equity)
    * paper_trades              (equity)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────
MIN_EVIDENCE_COUNT = 5
BASE_DOWN_WEIGHT = 0.85
ADAPTATION_TTL_DAYS = 14
COOLDOWN_DAYS = 7
MAX_ACTIVE_CRYPTO_ADAPTATIONS = 4
LOOKBACK_DAYS = 14
CONFIDENCE_FLOOR = 0.60


async def detect_crypto_adaptations(db: Any) -> list[dict[str, Any]]:
    """Scan ``crypto_trade_memory`` for repeated failure patterns.

    Buckets losing trades by ``failure_code:regime``. When a bucket
    crosses ``MIN_EVIDENCE_COUNT`` and the same key isn't already
    in cooldown, inserts a fresh adaptation row.
    """
    if db is None:
        return []

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=LOOKBACK_DAYS)

    # Cap how many adaptations can be active simultaneously — bounded
    # blast radius on a noisy detector run.
    active_count = await db.crypto_model_adaptations.count_documents({
        "active": True,
        "expires_at": {"$gt": now},
    })

    if active_count >= MAX_ACTIVE_CRYPTO_ADAPTATIONS:
        logger.info(
            "[crypto-adaptation] %d active adaptations already — "
            "skipping detection pass.", active_count,
        )
        return []

    cursor = db.crypto_trade_memory.find({
        "created_at": {"$gte": since},
        "failure_code": {"$ne": None},
        "r_multiple": {"$lt": 0},
    })

    buckets: dict[str, list[dict[str, Any]]] = {}

    async for row in cursor:
        failure_code = row.get("failure_code")
        regime = row.get("regime")
        key = f"{failure_code}:{regime}"
        buckets.setdefault(key, []).append(row)

    created: list[dict[str, Any]] = []

    for key, rows in buckets.items():
        if len(rows) < MIN_EVIDENCE_COUNT:
            continue

        failure_code, regime = key.split(":", 1)

        # Cooldown — same key can't fire more than once per week
        # (avoids whipsawing the live signal layer if a single bad
        # day pushes a bucket over the threshold repeatedly).
        cooldown_doc = await db.crypto_model_adaptations.find_one({
            "failure_code": failure_code,
            "regime": regime,
            "created_at": {"$gte": now - timedelta(days=COOLDOWN_DAYS)},
        })
        if cooldown_doc:
            continue

        avg_r = sum(float(r.get("r_multiple", 0) or 0) for r in rows) / len(rows)

        adaptation = {
            "asset_class": "crypto",
            "active": True,
            "failure_code": failure_code,
            "regime": regime,
            "factor": BASE_DOWN_WEIGHT,
            "evidence_count": len(rows),
            "avg_r_multiple": round(avg_r, 4),
            "reason": f"{failure_code} under {regime} failed repeatedly",
            "created_at": now,
            "expires_at": now + timedelta(days=ADAPTATION_TTL_DAYS),
            "source": "crypto_adaptation_service",
        }

        await db.crypto_model_adaptations.insert_one(adaptation)
        # Strip the Mongo-mutated _id so the returned summary is
        # JSON-clean for the route layer.
        adaptation.pop("_id", None)
        created.append(adaptation)

        logger.info(
            "[crypto-adaptation] created factor=%.2f for %s × %s "
            "(evidence=%d, avg_r=%.3f)",
            BASE_DOWN_WEIGHT, failure_code, regime,
            len(rows), avg_r,
        )

    return created


async def apply_crypto_adaptations_to_signal(
    db: Any,
    signal: dict[str, Any],
) -> dict[str, Any]:
    """Decision-time hook — multiply confidence by every matching factor.

    Matching rule: an adaptation matches when EITHER

    * its ``regime`` matches the signal's regime, OR
    * its ``failure_code`` matches the signal's
      ``failure_context.likely_failure_code``.

    Multiple adaptations compound (conservative), and any signal that
    falls below ``CONFIDENCE_FLOOR`` (0.60) after the multiplications
    is forced to HOLD with a tagged reason so the audit log shows
    *why* the trade didn't fire.

    Returns the (possibly-mutated) signal dict — never raises.
    """
    if db is None:
        return signal

    now = datetime.now(timezone.utc)

    failure_context = signal.get("failure_context", {}) or {}
    regime = signal.get("regime")
    confidence = float(signal.get("confidence", 0))

    cursor = db.crypto_model_adaptations.find({
        "active": True,
        "expires_at": {"$gt": now},
    })

    applied: list[dict[str, Any]] = []

    async for adaptation in cursor:
        matches_regime = adaptation.get("regime") == regime
        matches_failure = (
            adaptation.get("failure_code")
            == failure_context.get("likely_failure_code")
        )

        if not (matches_regime or matches_failure):
            continue

        factor = float(adaptation.get("factor", 1.0))
        confidence *= factor

        applied.append({
            "failure_code": adaptation.get("failure_code"),
            "regime": adaptation.get("regime"),
            "factor": factor,
            "reason": adaptation.get("reason"),
        })

    signal["confidence"] = round(confidence, 4)
    signal["crypto_adaptations_applied"] = applied

    # Only override the direction/reason when at least one adaptation
    # actually fired AND it pushed the trade below the floor. Without
    # this guard we'd masquerade a Strategist-side HOLD (confidence
    # 0.0 from the start) as an adaptation-driven HOLD, which would
    # confuse the audit log + calibration tile.
    if applied and confidence < CONFIDENCE_FLOOR:
        signal["direction"] = "HOLD"
        signal["reason"] = "crypto_adaptation_reduced_confidence_below_floor"

    return signal
