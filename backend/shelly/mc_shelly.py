"""MC Shelly — the head sidecar that reasons across all brains.

MCShelly ingests rollups from every LocalShelly and maintains a
shared-memory collection that any layer can read. Its reasoning
verdicts span the whole federation — useful when one brain hasn't
seen a setup but another brain has, or when brains DISAGREE about
how a setup historically resolves.

Same doctrine: every write is stamped ``authority: memory_reasoning_only``.
MC Shelly has no execution power.
"""
from __future__ import annotations

import logging
from typing import Any

from shelly.config import (
    MC_CONFIDENCE_DELTA_SUPPORT,
    MC_CONFIDENCE_DELTA_WARN,
    MC_LOSS_RATE_SUPPORT,
    MC_LOSS_RATE_WARN,
    MC_MIN_SAMPLES,
    MC_SIMILAR_LIMIT,
    MEMORY_REASONING_ONLY,
)
from shelly.contracts import stable_hash, utc_now

logger = logging.getLogger(__name__)


class MCShelly:
    """One per MC. Aggregates rollups + cross-brain reasoning."""

    def __init__(self, db: Any):
        self.db = db
        self.shared = db["shelly_mc_shared_memory"]
        self.receipts = db["shelly_mc_reasoning_receipts"]

    async def ingest_rollup(
        self, brain: str, memories: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Merge a per-brain rollup into the shared collection.

        Dedup uses ``event_hash`` — duplicates are a no-op and
        increment the ``duplicates`` counter so the operator can
        verify rollup health.
        """
        inserted = 0
        duplicates = 0
        for memory in memories:
            mem = dict(memory)
            mem.pop("_id", None)
            mem["source_brain"] = brain
            mem["shelly_scope"] = "mc_shared"
            mem["ingested_at_mc"] = utc_now()
            mem["authority"] = MEMORY_REASONING_ONLY
            try:
                result = await self.shared.update_one(
                    {"event_hash": mem["event_hash"]},
                    {"$setOnInsert": mem},
                    upsert=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[mc_shelly] ingest failed for hash=%s brain=%s: %s",
                    mem.get("event_hash"), brain, exc,
                )
                continue
            if getattr(result, "upserted_id", None) is not None:
                inserted += 1
            else:
                duplicates += 1
        return {
            "ok": True,
            "brain": brain,
            "inserted": inserted,
            "duplicates": duplicates,
            "authority": MEMORY_REASONING_ONLY,
        }

    async def reason_across_shellys(
        self, current_case: dict[str, Any],
    ) -> dict[str, Any]:
        """Tally outcomes across all brains for a (symbol, direction)."""
        symbol = current_case.get("symbol")
        direction = current_case.get("direction")

        try:
            cursor = self.shared.find(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "outcome.pnl_pct": {"$exists": True},
                },
                {"_id": 0, "event_hash": 1, "outcome": 1, "source_brain": 1},
            ).sort("created_at", -1).limit(MC_SIMILAR_LIMIT)
            matches = await cursor.to_list(length=MC_SIMILAR_LIMIT)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[mc_shelly] reason lookup failed: %s", exc)
            matches = []

        by_brain: dict[str, dict[str, int]] = {}
        for m in matches:
            brain = m.get("source_brain") or "unknown"
            pnl = _safe_float(m.get("outcome", {}).get("pnl_pct"), 0.0)
            slot = by_brain.setdefault(
                brain, {"wins": 0, "losses": 0, "total": 0},
            )
            slot["total"] += 1
            if pnl > 0:
                slot["wins"] += 1
            elif pnl < 0:
                slot["losses"] += 1

        reasons: list[str] = []
        confidence_delta = 0.0
        recommendation = "neutral"

        total = sum(v["total"] for v in by_brain.values())
        losses = sum(v["losses"] for v in by_brain.values())

        if total >= MC_MIN_SAMPLES:
            loss_rate = losses / max(1, total)
            if loss_rate >= MC_LOSS_RATE_WARN:
                recommendation = "warn"
                confidence_delta = MC_CONFIDENCE_DELTA_WARN
                reasons.append(
                    "Shared Shelly memory shows high loss rate across "
                    f"brains ({losses}/{total})."
                )
            elif loss_rate <= MC_LOSS_RATE_SUPPORT:
                recommendation = "support"
                confidence_delta = MC_CONFIDENCE_DELTA_SUPPORT
                reasons.append(
                    "Shared Shelly memory shows favourable historical "
                    f"pattern ({total - losses}/{total} wins)."
                )
            else:
                reasons.append(
                    f"Pattern is mixed across brains ({total} samples)."
                )
            # Conflict detection — if brains disagree (each polarized
            # in opposite directions), surface it explicitly.
            polarized_up = [
                b for b, v in by_brain.items()
                if v["total"] >= 3 and v["wins"] / max(1, v["total"]) >= 0.7
            ]
            polarized_dn = [
                b for b, v in by_brain.items()
                if v["total"] >= 3 and v["losses"] / max(1, v["total"]) >= 0.7
            ]
            if polarized_up and polarized_dn:
                reasons.append(
                    f"Brain conflict: {', '.join(polarized_up)} historically "
                    f"win this setup; {', '.join(polarized_dn)} historically lose."
                )
        else:
            reasons.append("Not enough shared Shelly memory yet.")

        receipt: dict[str, Any] = {
            "symbol": symbol,
            "direction": direction,
            "recommendation": recommendation,
            "confidence_delta": confidence_delta,
            "by_brain": by_brain,
            "reasons": reasons,
            "evidence_hashes": [m["event_hash"] for m in matches[:20]],
            "authority": MEMORY_REASONING_ONLY,
            "created_at": utc_now(),
        }
        receipt["receipt_hash"] = stable_hash({
            k: v for k, v in receipt.items() if k != "created_at"
        })
        try:
            await self.receipts.insert_one(receipt)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[mc_shelly] receipt write failed: %s", exc)
        receipt.pop("_id", None)
        return receipt


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


__all__ = ["MCShelly"]
