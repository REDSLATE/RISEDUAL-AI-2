"""
Regime Clustering Layer — thin compatibility shim over the
canonical ``services.regime_memory_retrieval`` engine.

Patent M layer (c) — "memory router".

Why a shim and not a rewrite
----------------------------
The canonical engine (`RegimeMemoryRetrievalEngine`, 596 lines,
covered by the existing 2451-test suite) already implements:

* direction canonicalisation through ``canonical_ai_dir``
* bounded risk multipliers (0.50–1.00)
* shadow / risk_only / full_context modes
* "HOLD/UNKNOWN cannot be promoted" guard
* "context-only output, no execution authority" guarantee

We must not damage that. Per the operator's directive (option 1A),
this module:

* re-exports ``RegimeFingerprint`` from the canonical engine,
* aliases ``RegimeLabeledMemory`` to the canonical ``RegimeMemory``,
* exposes ``RegimeClusteringEngine`` whose method names match the
  spec used by the new ``risedual_learning_core`` orchestrator
  (``assign_regime_cluster``, ``detect_pretell_cluster``,
  ``retrieve_regime_context``, ``check_pretell_warning``,
  ``get_cluster_report``).

Internally everything funnels into a single ``ingest_memory`` call
per memory so the canonical safety wiring is hit exactly once
(no double-append into clusters, no double-counted stats).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.regime_memory_retrieval import (
    RegimeFingerprint,
    RegimeMemory,
    RegimeMemoryRetrievalEngine,
)


# ─── Public alias ────────────────────────────────────────────────
# The orchestrator imports ``RegimeLabeledMemory``; the canonical
# name is ``RegimeMemory``. We don't subclass — that would
# silently let us drift the schema. Direct alias keeps the two
# names referring to the *same* class, so isinstance checks and
# pickling work either way.

RegimeLabeledMemory = RegimeMemory


__all__ = [
    "RegimeFingerprint",
    "RegimeLabeledMemory",
    "RegimeClusteringEngine",
]


class RegimeClusteringEngine:
    """Wrapper exposing the spec's method names over the canonical
    ``RegimeMemoryRetrievalEngine``.

    The wrapper keeps a small ``_ingest_cache`` keyed by
    ``memory_id`` so calling ``assign_regime_cluster`` and
    ``detect_pretell_cluster`` on the *same* memory only triggers
    one canonical ingest call. Without this, the canonical engine
    would append the memory to its cluster twice.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.70,
        engine: Optional[RegimeMemoryRetrievalEngine] = None,
    ) -> None:
        # Allow tests to inject the process-wide singleton or a
        # fresh engine. Default: own private engine so the shim
        # is safe in unit tests without polluting the singleton.
        self._engine = engine or RegimeMemoryRetrievalEngine(
            similarity_threshold=similarity_threshold,
        )
        self._ingest_cache: Dict[str, Dict[str, Any]] = {}

    # ─── ingestion ──────────────────────────────────────────────

    def assign_regime_cluster(
        self, memory: RegimeLabeledMemory,
    ) -> Optional[str]:
        env = self._ingest_once(memory)
        if not env.get("accepted"):
            return None
        return env.get("regime_cluster_id")

    def detect_pretell_cluster(
        self, memory: RegimeLabeledMemory,
    ) -> Optional[str]:
        env = self._ingest_once(memory)
        if not env.get("accepted"):
            return None
        return env.get("pretell_cluster_id")

    def _ingest_once(
        self, memory: RegimeLabeledMemory,
    ) -> Dict[str, Any]:
        """Idempotent wrapper around canonical ``ingest_memory``."""
        cached = self._ingest_cache.get(memory.memory_id)
        if cached is not None:
            return cached
        env = self._engine.ingest_memory(memory)
        self._ingest_cache[memory.memory_id] = env
        return env

    # ─── retrieval ──────────────────────────────────────────────

    def retrieve_regime_context(
        self,
        current_regime: RegimeFingerprint,
        ticker: str,
        direction: str,
        n_results: int = 5,
    ) -> List[Dict[str, Any]]:
        return self._engine.retrieve_context(
            current_regime=current_regime,
            ticker=ticker,
            direction=direction,
            limit=n_results,
        )

    def check_pretell_warning(
        self,
        current_regime: RegimeFingerprint,  # accepted for spec parity
        current_pretell: RegimeFingerprint,
    ) -> Optional[Dict[str, Any]]:
        # The canonical engine only needs ``current_pretell`` — the
        # ``current_regime`` arg is preserved in the signature so
        # the orchestrator's call site reads cleanly and so a
        # future revision can use it for cross-axis confirmation
        # without another shim bump.
        _ = current_regime
        return self._engine.check_pretell_warning(
            current_pretell=current_pretell,
        )

    # ─── diagnostics ────────────────────────────────────────────

    def get_cluster_report(self) -> Dict[str, Any]:
        return self._engine.report()
