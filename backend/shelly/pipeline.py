"""ShellyPipeline — orchestrator over the 5-Shelly federation.

Wires LocalShelly instances + MCShelly into one entry point. The
brain-receipt emission code calls
``pipeline.record_brain_event(brain, receipt)`` once per receipt;
the rollup scheduler calls ``pipeline.rollup_all_to_mc()`` on a
timer (typically nightly).

No execution authority anywhere in this file. Every result dict
carries ``authority: memory_reasoning_only`` so a downstream caller
that tries to upgrade a Shelly verdict into an execution gate is
caught by a single grep.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from shelly.config import BRAIN_NAMES, MEMORY_REASONING_ONLY
from shelly.contracts import ShellyMemoryEvent
from shelly.local_shelly import LocalShelly
from shelly.mc_shelly import MCShelly

logger = logging.getLogger(__name__)


class ShellyPipeline:
    """Federation orchestrator. Lifetime = one per process."""

    def __init__(self, db: Any, *, brain_names: Optional[tuple[str, ...]] = None):
        self.db = db
        names = tuple(brain_names) if brain_names else BRAIN_NAMES
        self.locals: dict[str, LocalShelly] = {
            name: LocalShelly(name, db) for name in names
        }
        self.mc_shelly = MCShelly(db)

    async def record_brain_event(
        self, brain: str, receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Store a brain's receipt + emit local + MC reasoning.

        Returns the local memory doc, the local reasoning receipt,
        and the MC reasoning receipt. The brain's original receipt
        is NOT mutated by this method — the caller decides whether
        to surface Shelly context onto the receipt.
        """
        if brain not in self.locals:
            return {
                "ok": False,
                "reason": "UNKNOWN_BRAIN",
                "brain": brain,
                "authority": MEMORY_REASONING_ONLY,
            }

        event = ShellyMemoryEvent(
            brain=brain,
            symbol=receipt.get("symbol", "UNKNOWN"),
            direction=receipt.get("direction", "HOLD"),
            confidence=float(receipt.get("confidence", 0.0) or 0.0),
            decision=receipt.get("decision", "UNKNOWN"),
            features=dict(receipt.get("features") or {}),
            mc_status=receipt.get("mc_status", "UNKNOWN"),
            roadguard_status=receipt.get("roadguard_status", "UNKNOWN"),
            outcome=receipt.get("outcome"),
        )

        try:
            local_doc = await self.locals[brain].remember(event)
            local_reasoning = await self.locals[brain].reason(local_doc)
            mc_reasoning = await self.mc_shelly.reason_across_shellys(local_doc)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[shelly_pipeline] record_brain_event failed brain=%s: %s",
                brain, exc,
            )
            return {
                "ok": False,
                "reason": "PIPELINE_ERROR",
                "brain": brain,
                "error": str(exc),
                "authority": MEMORY_REASONING_ONLY,
            }

        # Strip _id from anything that might have leaked through
        # — defense in depth.
        for d in (local_doc, local_reasoning, mc_reasoning):
            if isinstance(d, dict):
                d.pop("_id", None)

        return {
            "ok": True,
            "brain": brain,
            "local_memory": local_doc,
            "local_reasoning": local_reasoning,
            "mc_reasoning": mc_reasoning,
            "authority": MEMORY_REASONING_ONLY,
        }

    async def rollup_all_to_mc(self) -> dict[str, Any]:
        """Drain unrolled memories from every LocalShelly into MC."""
        results: dict[str, Any] = {}
        for brain, shelly in self.locals.items():
            memories = await shelly.rollup_for_mc(limit=500)
            result = await self.mc_shelly.ingest_rollup(brain, memories)
            await shelly.mark_rolled_to_mc(
                [m["event_hash"] for m in memories if "event_hash" in m],
            )
            results[brain] = result
        return {
            "ok": True,
            "results": results,
            "authority": MEMORY_REASONING_ONLY,
        }


__all__ = ["ShellyPipeline"]
