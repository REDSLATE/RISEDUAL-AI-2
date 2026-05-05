"""RISEDUAL AI — Regime Memory Retrieval Layer.

Purpose
-------
Retrieve historical trade memories from similar market regimes and
detect pre-transition (pre-tell) regime warnings. Emits **bounded
risk context only** — never executes trades, never flips directions,
never promotes HOLD/UNKNOWN into a trade.

Hard rules (do not relax)
-------------------------
* Never changes trade direction.
* Never promotes HOLD / UNKNOWN / NO_TRADE into a trade.
* Never executes trades or writes back to ``toxic_lessons``,
  ``prediction_tracker``, or ``paper_trades``.
* Default mode is ``shadow`` — multiplier is logged but not applied.
* Direction canonicalization MUST go through the project-wide
  ``services.prediction_tracker.canonical_ai_dir`` (returns
  ``LONG``/``SHORT``/``UNKNOWN``). No local fallback tuples.
* Bounded risk multiplier: ``[REGIME_MEMORY_MIN_RISK_MULTIPLIER,
  REGIME_MEMORY_MAX_RISK_MULTIPLIER]`` (default 0.50–1.00).

Design
------
This service is in-memory only. Persisting clusters to MongoDB is
deliberately out of scope for the shadow rollout — the caller owns
ingestion, the caller decides whether to consume the output.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Single source of truth for direction canonicalization.
from services.prediction_tracker import (
    canonical_ai_dir,
    DIRECTION_BEARISH,
    DIRECTION_BULLISH,
    DIRECTION_NEUTRAL,
)


def _is_known_token(direction: Any) -> bool:
    """True iff the raw token is in one of the canonicalization sets.

    ``canonical_ai_dir`` collapses both ``HOLD`` and gibberish into
    ``UNKNOWN``; the ingest guard needs to distinguish them so it
    only rejects unrecognised tokens (the original direction-drift
    failure mode).
    """
    d = str(direction or "").upper().strip()
    return d in DIRECTION_BULLISH or d in DIRECTION_BEARISH or d in DIRECTION_NEUTRAL


# ─── ENV FLAGS ───────────────────────────────────────────────────────

REGIME_MEMORY_ENABLED = os.getenv("REGIME_MEMORY_ENABLED", "false").lower() == "true"

# shadow      — log only, multiplier forced to 1.0 in output
# risk_only   — multiplier applied, no memories surfaced
# full_context — multiplier + memories + warning
REGIME_MEMORY_MODE = os.getenv("REGIME_MEMORY_MODE", "shadow").lower()

REGIME_MEMORY_MIN_SIMILARITY = float(
    os.getenv("REGIME_MEMORY_MIN_SIMILARITY", "0.70")
)
REGIME_PRETELL_MIN_SAMPLES = int(
    os.getenv("REGIME_PRETELL_MIN_SAMPLES", "3")
)
REGIME_MEMORY_MIN_RISK_MULTIPLIER = float(
    os.getenv("REGIME_MEMORY_MIN_RISK_MULTIPLIER", "0.50")
)
REGIME_MEMORY_MAX_RISK_MULTIPLIER = float(
    os.getenv("REGIME_MEMORY_MAX_RISK_MULTIPLIER", "1.00")
)


# Canonical trade-side tokens emitted by ``canonical_ai_dir``.
# Anything outside this set means: do not trade.
_TRADE_SIDES = {"LONG", "SHORT"}


# ─── REGIME DATA STRUCTURES ──────────────────────────────────────────

# Note: these label sets are *regime feature labels*, not trade
# directions. Keep them disjoint from trade-side tokens to avoid any
# accidental collision with ``canonical_ai_dir``.
_VIX_LEVELS = {"low", "normal", "elevated", "extreme"}
_YIELD_CURVES = {"steep", "flat", "inverted"}
_DXY_TRENDS = {"weak", "strong", "breakout"}
_CREDIT_SPREADS = {"tight", "widening", "distressed"}
_LIQUIDITY = {"ample", "normal", "constrained", "frozen"}
_MACRO_PHASES = {"expansion", "late_cycle", "recession", "recovery"}


@dataclass(frozen=True)
class RegimeFingerprint:
    """Categorical, explainable snapshot of macro market regime.

    Frozen + hashable so it can be used as a dict key for centroids.
    """

    vix_level: str
    yield_curve: str
    dxy_trend: str
    credit_spreads: str
    liquidity: str
    macro_phase: str

    def to_numeric_vector(self) -> List[float]:
        mappings = {
            "vix_level": {"low": 0.00, "normal": 0.33,
                          "elevated": 0.66, "extreme": 1.00},
            "yield_curve": {"steep": 0.00, "flat": 0.50, "inverted": 1.00},
            "dxy_trend": {"weak": 0.00, "strong": 0.50, "breakout": 1.00},
            "credit_spreads": {"tight": 0.00, "widening": 0.50,
                               "distressed": 1.00},
            "liquidity": {"ample": 0.00, "normal": 0.33,
                          "constrained": 0.66, "frozen": 1.00},
            "macro_phase": {"expansion": 0.00, "late_cycle": 0.33,
                            "recession": 0.66, "recovery": 1.00},
        }
        return [
            mappings["vix_level"].get(self.vix_level, 0.33),
            mappings["yield_curve"].get(self.yield_curve, 0.50),
            mappings["dxy_trend"].get(self.dxy_trend, 0.50),
            mappings["credit_spreads"].get(self.credit_spreads, 0.50),
            mappings["liquidity"].get(self.liquidity, 0.33),
            mappings["macro_phase"].get(self.macro_phase, 0.33),
        ]

    def similarity(self, other: "RegimeFingerprint") -> float:
        """Weighted similarity in ``[0.0, 1.0]``."""
        v1 = self.to_numeric_vector()
        v2 = other.to_numeric_vector()
        weights = [0.30, 0.15, 0.15, 0.25, 0.10, 0.05]
        weighted_distance = sum(
            abs(a - b) * w for a, b, w in zip(v1, v2, weights)
        )
        max_distance = sum(weights)
        sim = 1.0 - (weighted_distance / max_distance)
        return max(0.0, min(1.0, sim))


@dataclass
class RegimeMemory:
    """One historical trade outcome anchored to its regime context."""

    memory_id: str
    timestamp: str
    ticker: str
    direction: str  # raw verdict token; canonicalized at read-time

    entry_price: float
    exit_price: Optional[float]
    pnl_pct: Optional[float]
    holding_days: Optional[int]

    regime_at_entry: RegimeFingerprint
    regime_at_exit: Optional[RegimeFingerprint] = None

    pretell_30d: Optional[RegimeFingerprint] = None
    pretell_60d: Optional[RegimeFingerprint] = None
    pretell_90d: Optional[RegimeFingerprint] = None

    regime_shift_detected: bool = False
    shift_type: Optional[str] = None

    strategist_thesis: Optional[str] = None
    auditor_warnings: Optional[List[str]] = None
    commander_decision: Optional[str] = None

    regime_cluster_id: Optional[str] = None
    pretell_cluster_id: Optional[str] = None

    def canonical_direction(self) -> str:
        """Always returns ``"LONG"`` / ``"SHORT"`` / ``"UNKNOWN"``."""
        return canonical_ai_dir(self.direction)


# ─── MAIN ENGINE ─────────────────────────────────────────────────────


class RegimeMemoryRetrievalEngine:
    """Read-mostly memory + warning layer.

    Sits BEFORE final Commander/Council sizing in the decision
    pipeline. Emits a bounded ``risk_multiplier`` and optional context.
    """

    def __init__(
        self,
        similarity_threshold: float = REGIME_MEMORY_MIN_SIMILARITY,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self.regime_clusters: Dict[str, List[RegimeMemory]] = defaultdict(list)
        self.pretell_clusters: Dict[str, List[RegimeMemory]] = defaultdict(list)
        self.cluster_centroids: Dict[str, RegimeFingerprint] = {}
        self.cluster_stats: Dict[str, Dict[str, Any]] = {}

    # ─── INGESTION ──────────────────────────────────────────────────

    def ingest_memory(self, memory: RegimeMemory) -> Dict[str, Any]:
        """Add a memory to the appropriate cluster.

        Returns an envelope with ``accepted`` flag and the cluster id
        assignments. Rejects non-canonical direction tokens (extra
        safety rail per the project's direction-drift post-mortem).
        """
        if not REGIME_MEMORY_ENABLED:
            return {
                "accepted": False,
                "enabled": False,
                "mode": REGIME_MEMORY_MODE,
                "reason": "REGIME_MEMORY_ENABLED=false",
            }

        if not _is_known_token(memory.direction):
            return {
                "accepted": False,
                "enabled": True,
                "mode": REGIME_MEMORY_MODE,
                "reason": "non_canonical_direction",
                "raw_direction": memory.direction,
            }

        # Resolved-only ingestion: only learn from trades that closed
        # with a verified pnl. This matches the "Only read resolved
        # memories with verified pnl_pct" rule from the contract.
        if memory.pnl_pct is None:
            return {
                "accepted": False,
                "enabled": True,
                "mode": REGIME_MEMORY_MODE,
                "reason": "unresolved_memory_missing_pnl_pct",
            }

        regime_cluster_id = self._assign_regime_cluster(memory)
        pretell_cluster_id = self._detect_pretell_cluster(memory)

        return {
            "accepted": True,
            "enabled": True,
            "mode": REGIME_MEMORY_MODE,
            "memory_id": memory.memory_id,
            "regime_cluster_id": regime_cluster_id,
            "pretell_cluster_id": pretell_cluster_id,
        }

    def _assign_regime_cluster(self, memory: RegimeMemory) -> str:
        best_cluster: Optional[str] = None
        best_similarity = 0.0

        for cluster_id, centroid in self.cluster_centroids.items():
            if not cluster_id.startswith("regime_"):
                continue
            sim = memory.regime_at_entry.similarity(centroid)
            if sim >= self.similarity_threshold and sim > best_similarity:
                best_similarity = sim
                best_cluster = cluster_id

        if best_cluster is None:
            best_cluster = self._new_regime_cluster_id(memory)
            self.cluster_centroids[best_cluster] = memory.regime_at_entry
            self.cluster_stats[best_cluster] = self._empty_regime_stats()

        memory.regime_cluster_id = best_cluster
        self.regime_clusters[best_cluster].append(memory)
        self._refresh_regime_stats(best_cluster)
        return best_cluster

    def _detect_pretell_cluster(
        self, memory: RegimeMemory,
    ) -> Optional[str]:
        if not memory.regime_shift_detected or memory.pretell_30d is None:
            return None

        best_cluster: Optional[str] = None
        best_similarity = 0.0

        for cluster_id, centroid in self.cluster_centroids.items():
            if not cluster_id.startswith("pretell_"):
                continue
            sim = memory.pretell_30d.similarity(centroid)
            if sim >= self.similarity_threshold and sim > best_similarity:
                best_similarity = sim
                best_cluster = cluster_id

        if best_cluster is None:
            shift = memory.shift_type or "unknown_shift"
            best_cluster = (
                f"pretell_{shift}_{len(self.pretell_clusters)}"
            )
            self.cluster_centroids[best_cluster] = memory.pretell_30d
            self.cluster_stats[best_cluster] = self._empty_pretell_stats(shift)

        memory.pretell_cluster_id = best_cluster
        self.pretell_clusters[best_cluster].append(memory)
        self._refresh_pretell_stats(best_cluster)
        return best_cluster

    # ─── RETRIEVAL ──────────────────────────────────────────────────

    def retrieve_context(
        self,
        current_regime: RegimeFingerprint,
        ticker: str,
        direction: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Return up-to-``limit`` similar-regime memories for the same
        ticker + canonical direction. Returns ``[]`` for any
        non-trade direction (HOLD/UNKNOWN)."""
        if not REGIME_MEMORY_ENABLED:
            return []

        canonical_direction = canonical_ai_dir(direction)
        if canonical_direction not in _TRADE_SIDES:
            return []

        ticker_u = ticker.upper()
        scored: List[Tuple[float, RegimeMemory]] = []

        for cluster_id, memories in self.regime_clusters.items():
            centroid = self.cluster_centroids.get(cluster_id)
            if centroid is None:
                continue
            cluster_similarity = current_regime.similarity(centroid)
            if cluster_similarity < 0.50:
                continue

            for memory in memories:
                if memory.ticker.upper() != ticker_u:
                    continue
                if memory.canonical_direction() != canonical_direction:
                    continue

                pnl_bonus = max(memory.pnl_pct or 0.0, 0.0) / 100.0
                score = cluster_similarity + min(pnl_bonus, 0.20)
                scored.append((score, memory))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "memory_id": memory.memory_id,
                "timestamp": memory.timestamp,
                "ticker": memory.ticker,
                "direction": memory.canonical_direction(),
                "pnl_pct": memory.pnl_pct,
                "holding_days": memory.holding_days,
                "regime_cluster_id": memory.regime_cluster_id,
                "shift_type": memory.shift_type,
                "strategist_thesis": memory.strategist_thesis,
                "auditor_warnings": memory.auditor_warnings or [],
                "commander_decision": memory.commander_decision,
                "score": round(score, 4),
            }
            for score, memory in scored[:limit]
        ]

    # ─── PRE-TELL WARNING ───────────────────────────────────────────

    def check_pretell_warning(
        self,
        current_pretell: RegimeFingerprint,
    ) -> Optional[Dict[str, Any]]:
        if not REGIME_MEMORY_ENABLED:
            return None

        warnings: List[Dict[str, Any]] = []
        for cluster_id, memories in self.pretell_clusters.items():
            if len(memories) < REGIME_PRETELL_MIN_SAMPLES:
                continue
            centroid = self.cluster_centroids.get(cluster_id)
            if centroid is None:
                continue

            similarity = current_pretell.similarity(centroid)
            if similarity < self.similarity_threshold:
                continue

            stats = self.cluster_stats.get(cluster_id, {})
            shift_type = stats.get("shift_type", "unknown_shift")
            warnings.append({
                "cluster_id": cluster_id,
                "similarity": round(similarity, 4),
                "shift_type": shift_type,
                "historical_frequency": len(memories),
                "avg_pnl_after_shift": stats.get("avg_pnl_after_shift", 0.0),
                "avg_days_to_shift": stats.get("avg_days_to_shift", 0.0),
                "warning": (
                    f"Current conditions resemble prior pre-{shift_type} "
                    f"pattern seen {len(memories)} times."
                ),
            })

        if not warnings:
            return None

        warnings.sort(key=lambda w: w["similarity"], reverse=True)
        return warnings[0]

    # ─── PUBLIC RISK CONTEXT (the only function callers should use) ─

    def build_risk_context(
        self,
        current_regime: RegimeFingerprint,
        current_pretell: Optional[RegimeFingerprint],
        ticker: str,
        direction: str,
    ) -> Dict[str, Any]:
        """Main entry point. Returns a bounded risk-context envelope.

        The returned dict ALWAYS contains:
            * ``risk_multiplier`` (float, bounded 0.50–1.00)
            * ``allow_direction_change`` (always ``False``)
            * ``allow_hold_promotion`` (always ``False``)
            * ``memories`` (list, may be empty)
            * ``pretell_warning`` (dict or ``None``)
            * ``reasons`` (list of human-readable strings)
        """
        canonical_direction = canonical_ai_dir(direction)

        base: Dict[str, Any] = {
            "enabled": REGIME_MEMORY_ENABLED,
            "mode": REGIME_MEMORY_MODE,
            "ticker": ticker.upper(),
            "direction": canonical_direction,
            "risk_multiplier": 1.0,
            "allow_direction_change": False,
            "allow_hold_promotion": False,
            "memories": [],
            "pretell_warning": None,
            "reasons": [],
        }

        if not REGIME_MEMORY_ENABLED:
            base["reasons"].append("Regime memory disabled.")
            return base

        if canonical_direction not in _TRADE_SIDES:
            base["reasons"].append(
                "Direction is not a trade side (HOLD/UNKNOWN); "
                "regime layer will not promote trade."
            )
            return base

        # ── Memories ──
        memories = self.retrieve_context(
            current_regime=current_regime,
            ticker=ticker,
            direction=canonical_direction,
            limit=5,
        )
        base["memories"] = memories
        if memories:
            avg_pnl = sum((m.get("pnl_pct") or 0.0) for m in memories) / len(memories)
            base["reasons"].append(
                f"Retrieved {len(memories)} similar-regime memories."
            )
            if avg_pnl < 0:
                base["risk_multiplier"] *= 0.85
                base["reasons"].append(
                    "Similar-regime memory average PnL is negative; "
                    "reducing risk."
                )

        # ── Pre-tell warning ──
        if current_pretell is not None:
            warning = self.check_pretell_warning(current_pretell)
            base["pretell_warning"] = warning
            if warning:
                similarity = float(warning.get("similarity", 0.0))
                base["reasons"].append(warning["warning"])
                if similarity >= 0.90:
                    base["risk_multiplier"] *= 0.60
                elif similarity >= 0.80:
                    base["risk_multiplier"] *= 0.75
                else:
                    base["risk_multiplier"] *= 0.90

        base["risk_multiplier"] = self._bound_risk_multiplier(
            base["risk_multiplier"]
        )

        if REGIME_MEMORY_MODE == "shadow":
            base["shadow_risk_multiplier"] = base["risk_multiplier"]
            base["risk_multiplier"] = 1.0
            base["reasons"].append(
                "Shadow mode: risk multiplier logged but not applied."
            )

        return base

    # ─── REPORT ─────────────────────────────────────────────────────

    def report(self) -> Dict[str, Any]:
        """Read-only diagnostic snapshot for the admin overlay."""
        return {
            "enabled": REGIME_MEMORY_ENABLED,
            "mode": REGIME_MEMORY_MODE,
            "similarity_threshold": self.similarity_threshold,
            "total_regime_clusters": len(self.regime_clusters),
            "total_pretell_clusters": len(self.pretell_clusters),
            "total_memories": sum(len(v) for v in self.regime_clusters.values()),
            "top_clusters": sorted(
                [
                    {"cluster_id": cluster_id, **stats}
                    for cluster_id, stats in self.cluster_stats.items()
                    if cluster_id.startswith("regime_")
                ],
                key=lambda x: x.get("count", 0),
                reverse=True,
            )[:10],
        }

    # ─── HELPERS ────────────────────────────────────────────────────

    def _bound_risk_multiplier(self, value: float) -> float:
        value = max(REGIME_MEMORY_MIN_RISK_MULTIPLIER, value)
        value = min(REGIME_MEMORY_MAX_RISK_MULTIPLIER, value)
        return round(value, 4)

    def _new_regime_cluster_id(self, memory: RegimeMemory) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m")
        return (
            f"regime_{memory.ticker.upper()}_"
            f"{len(self.regime_clusters)}_{stamp}"
        )

    def _empty_regime_stats(self) -> Dict[str, Any]:
        return {
            "count": 0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
            "avg_holding_days": 0.0,
            "regime_shifts": 0,
        }

    def _empty_pretell_stats(self, shift_type: str) -> Dict[str, Any]:
        return {
            "count": 0,
            "shift_type": shift_type,
            "avg_pnl_after_shift": 0.0,
            "avg_days_to_shift": 0.0,
        }

    def _refresh_regime_stats(self, cluster_id: str) -> None:
        memories = self.regime_clusters.get(cluster_id, [])
        if not memories:
            return
        resolved = [m for m in memories if m.pnl_pct is not None]
        wins = [m for m in resolved if (m.pnl_pct or 0.0) > 0]
        avg_pnl = (
            sum(m.pnl_pct or 0.0 for m in resolved) / len(resolved)
            if resolved else 0.0
        )
        avg_holding = (
            sum(m.holding_days or 0 for m in resolved) / len(resolved)
            if resolved else 0.0
        )
        self.cluster_stats[cluster_id].update({
            "count": len(memories),
            "avg_pnl": round(avg_pnl, 4),
            "win_rate": (
                round(len(wins) / len(resolved), 4) if resolved else 0.0
            ),
            "avg_holding_days": round(avg_holding, 2),
            "regime_shifts": sum(
                1 for m in memories if m.regime_shift_detected
            ),
        })

    def _refresh_pretell_stats(self, cluster_id: str) -> None:
        memories = self.pretell_clusters.get(cluster_id, [])
        if not memories:
            return
        shifted = [m for m in memories if m.regime_shift_detected]
        if not shifted:
            return
        avg_pnl = sum(m.pnl_pct or 0.0 for m in shifted) / len(shifted)
        avg_days = sum(m.holding_days or 0 for m in shifted) / len(shifted)
        self.cluster_stats[cluster_id].update({
            "count": len(memories),
            "avg_pnl_after_shift": round(avg_pnl, 4),
            "avg_days_to_shift": round(avg_days, 2),
        })


# ─── PROCESS-LOCAL SINGLETON ─────────────────────────────────────────

regime_memory_engine = RegimeMemoryRetrievalEngine()
