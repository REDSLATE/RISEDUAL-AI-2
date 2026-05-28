"""ShellyPipeline — orchestrator over the 5-Shelly federation.

Five federation nodes: four execution-authority brains
(Alpha / Camaro / Chevelle / RedEye) and one verifier/notary
(MC). Each node owns its own LocalShelly. MCShelly (the
aggregator-head function) reasons across all five.

The brain-receipt emission code calls
``pipeline.record_brain_event(node, receipt)`` once per receipt
— ``node`` is any of the 5 federation participants, NOT just
brains. The historical method name is kept for source
compatibility but accepts "MC" as a valid node.

The rollup scheduler calls ``pipeline.rollup_all_to_mc()`` on a
timer (typically nightly). All 5 nodes — brains AND MC — drain
into the shared aggregator collection.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from shelly.config import MC_NODE_NAME, MEMORY_REASONING_ONLY, NODE_NAMES
from shelly.contracts import ShellyMemoryEvent
from shelly.local_shelly import LocalShelly
from shelly.mc_shelly import MCShelly

logger = logging.getLogger(__name__)


class ShellyPipeline:
    """Federation orchestrator. Lifetime = one per process."""

    def __init__(self, db: Any, *, node_names: Optional[tuple[str, ...]] = None):
        self.db = db
        names = tuple(node_names) if node_names else NODE_NAMES
        # 5 LocalShelly instances by default: Alpha, Camaro, Chevelle,
        # RedEye, MC. Each owns its own memory + receipts collections
        # so the operator can audit a single node's history without
        # filter conditions.
        self.locals: dict[str, LocalShelly] = {
            name: LocalShelly(name, db) for name in names
        }
        # The aggregator-head is a SEPARATE concept from the MC node's
        # LocalShelly. The aggregator is a *function* that reads from
        # all 5 LocalShellys and writes to the shared collection; it
        # is NOT a sixth Shelly.
        self.mc_shelly = MCShelly(db)

    async def record_brain_event(
        self, brain: str, receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Store a node's receipt + emit local + MC reasoning.

        ``brain`` is the historical name; the parameter accepts any
        of the 5 federation node names (the 4 brains plus "MC").
        Returns the local memory doc, the local reasoning receipt,
        and the MC reasoning receipt. The original receipt is NOT
        mutated — the caller decides whether to surface Shelly
        context onto it.
        """
        if brain not in self.locals:
            return {
                "ok": False,
                "reason": "UNKNOWN_NODE",
                "node": brain,
                "valid_nodes": list(self.locals.keys()),
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
                "[shelly_pipeline] record_brain_event failed node=%s: %s",
                brain, exc,
            )
            return {
                "ok": False,
                "reason": "PIPELINE_ERROR",
                "node": brain,
                "error": str(exc),
                "authority": MEMORY_REASONING_ONLY,
            }

        for d in (local_doc, local_reasoning, mc_reasoning):
            if isinstance(d, dict):
                d.pop("_id", None)

        return {
            "ok": True,
            "node": brain,
            "is_mc_node": brain == MC_NODE_NAME,
            "local_memory": local_doc,
            "local_reasoning": local_reasoning,
            "mc_reasoning": mc_reasoning,
            "authority": MEMORY_REASONING_ONLY,
        }

    async def rollup_all_to_mc(self) -> dict[str, Any]:
        """Drain unrolled memories from EVERY LocalShelly (all 5
        nodes — brains AND MC) into the shared aggregator collection."""
        results: dict[str, Any] = {}
        for node, shelly in self.locals.items():
            memories = await shelly.rollup_for_mc(limit=500)
            result = await self.mc_shelly.ingest_rollup(node, memories)
            await shelly.mark_rolled_to_mc(
                [m["event_hash"] for m in memories if "event_hash" in m],
            )
            results[node] = result
        return {
            "ok": True,
            "results": results,
            "authority": MEMORY_REASONING_ONLY,
        }


__all__ = ["ShellyPipeline"]
