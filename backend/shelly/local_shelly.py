"""Per-brain LocalShelly — one instance per execution-authority brain.

LocalShelly is a tight feedback loop tied to a single brain. It
stores memories scoped to ``owner_brain``, runs single-brain
reasoning over its own past, and rolls fresh memories upward to MC
Shelly. It does NOT trade, block, approve, or override — the only
side-effect it produces is two Mongo collections, both stamped
``authority: memory_reasoning_only``.

Async (motor) all the way down, matching the rest of the RISEDUAL
backend.
"""
from __future__ import annotations

import logging
from typing import Any

from shelly.config import (
    LOCAL_CONFIDENCE_DELTA_WARN,
    LOCAL_LOSS_RATE_WARN,
    LOCAL_MIN_SAMPLES,
    LOCAL_SIMILAR_LIMIT,
    MEMORY_REASONING_ONLY,
)
from shelly.contracts import ShellyMemoryEvent, ShellyReasoningReceipt

logger = logging.getLogger(__name__)


class LocalShelly:
    """One per brain — Shelly-Alpha, Shelly-Camaro, etc."""

    def __init__(self, brain_name: str, db: Any):
        self.brain_name = brain_name
        self.memories = db[f"shelly_{brain_name.lower()}_memories"]
        self.receipts = db[f"shelly_{brain_name.lower()}_reasoning_receipts"]

    async def remember(self, event: ShellyMemoryEvent) -> dict[str, Any]:
        """Persist a single brain receipt. Idempotent on ``event_hash``."""
        doc = event.to_doc()
        doc["shelly_scope"] = "local"
        doc["owner_brain"] = self.brain_name
        await self.memories.update_one(
            {"event_hash": doc["event_hash"]},
            {"$setOnInsert": doc},
            upsert=True,
        )
        return doc

    async def reason(self, current_case: dict[str, Any]) -> dict[str, Any]:
        """Compare ``current_case`` against this brain's own past.

        Pulls the last ``LOCAL_SIMILAR_LIMIT`` resolved memories for
        the same (symbol, direction) pair. If the sample size is
        large enough AND the loss rate clears the WARN threshold,
        the receipt recommends a confidence haircut.
        """
        symbol = current_case.get("symbol")
        direction = current_case.get("direction")

        try:
            cursor = self.memories.find(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "outcome.pnl_pct": {"$exists": True},
                },
                {"_id": 0, "event_hash": 1, "outcome": 1, "created_at": 1},
            ).sort("created_at", -1).limit(LOCAL_SIMILAR_LIMIT)
            similar = await cursor.to_list(length=LOCAL_SIMILAR_LIMIT)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[shelly_%s] local reason lookup failed: %s",
                self.brain_name, exc,
            )
            similar = []

        losses = [
            m for m in similar
            if _safe_float(m.get("outcome", {}).get("pnl_pct"), 0.0) < 0
        ]

        reasons: list[str] = []
        confidence_delta = 0.0
        recommendation = "neutral"

        if (
            len(similar) >= LOCAL_MIN_SAMPLES
            and len(losses) / max(1, len(similar)) >= LOCAL_LOSS_RATE_WARN
        ):
            recommendation = "warn"
            confidence_delta = LOCAL_CONFIDENCE_DELTA_WARN
            reasons.append(
                f"{self.brain_name} has seen this setup "
                f"{len(similar)} times; loss rate is high "
                f"({len(losses)}/{len(similar)})."
            )
        elif not similar:
            reasons.append("No strong local memory match.")
        else:
            reasons.append(
                f"{len(similar)} prior local samples; pattern not adverse."
            )

        receipt = ShellyReasoningReceipt(
            brain=self.brain_name,
            symbol=symbol or "UNKNOWN",
            recommendation=recommendation,
            confidence_delta=confidence_delta,
            reasons=reasons,
            evidence_hashes=[m["event_hash"] for m in similar[:10]],
        ).to_doc()
        try:
            await self.receipts.insert_one(receipt)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[shelly_%s] receipt write failed (non-fatal): %s",
                self.brain_name, exc,
            )
        # The persisted document mutates with _id; return a copy
        # without it so callers don't accidentally serialize ObjectId.
        receipt.pop("_id", None)
        return receipt

    async def rollup_for_mc(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return memories not yet rolled to MC Shelly."""
        cursor = self.memories.find(
            {"rolled_to_mc": {"$ne": True}},
            # Strip _id at query time — never let ObjectId leak.
            {"_id": 0},
        ).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def mark_rolled_to_mc(self, event_hashes: list[str]) -> int:
        if not event_hashes:
            return 0
        res = await self.memories.update_many(
            {"event_hash": {"$in": event_hashes}},
            {"$set": {"rolled_to_mc": True}},
        )
        return int(getattr(res, "modified_count", 0) or 0)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


__all__ = ["LocalShelly", "MEMORY_REASONING_ONLY"]
