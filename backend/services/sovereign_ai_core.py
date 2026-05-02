"""
Sovereign AI Core — independent, non-LLM decision engine.

Runs in **shadow mode** on both RISEDUAL cores (equity + crypto), writes every
decision to ``sovereign_decisions``. Once the per-core promotion gate unlocks
(see ``sovereign_promotion_gate``) Sovereign AI becomes the sizing/direction
authority for that core; Commander / Strategist / Council outputs fall back to
**advisory votes** logged on the decision row but with zero sizing control.

Pinned invariants (enforced by tests):

* **Pure deterministic compute** — 6 native sub-models, zero LLM calls, zero
  network I/O. Every tick is microseconds.
* **No BSON leakage** — every Mongo read excludes ``_id``; every write goes
  through dict literals, never document spreads.
* **Hard-veto supremacy** — ``LIQUIDITY_TRAP`` / ``NEWS_SHOCK`` restricted /
  ``CIRCUIT_BREAKER`` always win. Sovereign cannot override, even promoted.
* **Shadow fail-safe** — any exception in ``sovereign_decide`` MUST return a
  HOLD + shadow=True record rather than raise. A broken core can never block
  the live Commander path when called from the paper-traders.
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

AssetType = Literal["equity", "crypto"]
ConvictionTier = Literal["low", "medium", "high", "extreme"]
Direction = Literal["LONG", "SHORT", "HOLD"]


# ─── Feature contract ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SovereignFeatures:
    """Immutable snapshot of every input Sovereign AI consults.

    Stored verbatim on ``sovereign_decisions.feature_snapshot`` via ``asdict``
    so future ML back-tests can replay a decision from the exact inputs.
    """

    symbol: str
    asset_type: AssetType
    # Technicals
    rsi: Optional[float] = None
    momentum_5b: Optional[float] = None
    atr_pct: Optional[float] = None
    volume_zscore: Optional[float] = None
    dollar_volume: Optional[float] = None
    dollar_volume_baseline: Optional[float] = None
    # Regime
    regime: Optional[str] = None
    # Catalyst / news shock
    news_shock_state: Optional[str] = None  # "normal" | "elevated" | "high"
    event_risk: Optional[str] = None  # "normal" | "elevated" | "restricted"
    news_sentiment: Optional[float] = None  # [-1, 1]
    news_volume_zscore: Optional[float] = None
    # Options (optional)
    options_flow_skew: Optional[float] = None  # [-1, 1] where + = bullish
    # Upstream strategist vote (advisory only)
    strategist_action: Optional[Direction] = None
    strategist_confidence: Optional[float] = None


# ─── Decision payload ──────────────────────────────────────────────────────────


@dataclass
class SovereignDecision:
    symbol: str
    asset_type: AssetType
    action: Direction
    direction: Direction  # alias for downstream trader compatibility
    confidence: float
    conviction_tier: ConvictionTier
    size_multiplier: float
    reasons: list[str] = field(default_factory=list)
    vetoes: list[str] = field(default_factory=list)
    model_votes: dict[str, Any] = field(default_factory=dict)
    advisory_votes: dict[str, Any] = field(default_factory=dict)
    feature_snapshot: dict[str, Any] = field(default_factory=dict)
    calibration_score: float = 0.5
    shadow: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decision_id: str = field(default_factory=lambda: str(uuid4()))

    def to_mongo_doc(self) -> dict[str, Any]:
        d = asdict(self)
        # Ensure datetime is stored tz-aware (Mongo accepts it directly)
        d["created_at"] = self.created_at
        # Resolution fields start empty
        d.setdefault("resolved", False)
        d.setdefault("outcomes", {})
        return d


# ─── 6 Native sub-models ────────────────────────────────────────────────────────
# Every model returns a dict with a pinned shape so the coordinator can fan-in
# results without conditional hell.


def _strategist_model(f: SovereignFeatures) -> dict[str, Any]:
    """Native RSI + momentum → directional vote.

    Independent of the upstream Strategist (the upstream one becomes advisory
    post-promotion; we need our own directional brain).
    """
    rsi = f.rsi if f.rsi is not None else 50.0
    mom = f.momentum_5b if f.momentum_5b is not None else 0.0

    # Oversold + positive momentum → LONG bias; overbought + negative → SHORT.
    # Mid-range with any strong momentum gets a half-weight vote.
    bullish = 0.0
    bearish = 0.0
    if rsi < 35 and mom > 0:
        bullish += 0.6
    elif rsi < 45 and mom > 0.01:
        bullish += 0.3
    if rsi > 65 and mom < 0:
        bearish += 0.6
    elif rsi > 55 and mom < -0.01:
        bearish += 0.3

    # Momentum-only contribution (even if RSI is neutral)
    bullish += max(0.0, min(0.3, mom * 10))
    bearish += max(0.0, min(0.3, -mom * 10))

    net = bullish - bearish
    if net > 0.15:
        action: Direction = "LONG"
        conf = min(0.95, 0.5 + net)
    elif net < -0.15:
        action = "SHORT"
        conf = min(0.95, 0.5 + abs(net))
    else:
        action = "HOLD"
        conf = 0.5 - abs(net)

    return {
        "action": action,
        "confidence": round(conf, 4),
        "bull": round(bullish, 4),
        "bear": round(bearish, 4),
    }


_TRADEABLE_REGIMES = {
    "trend_up", "trend_down", "low_vol_drift", "momentum_breakout",
    "mean_reverting", "breakout_continuation",
}


def _regime_model(f: SovereignFeatures) -> dict[str, Any]:
    """Regime alignment → sizing multiplier in [0, 1]."""
    regime = (f.regime or "").lower()
    if regime in _TRADEABLE_REGIMES:
        return {"gate": 1.0, "reason": f"regime_ok:{regime}"}
    if regime in {"high_vol", "chop", "uncertainty"}:
        return {"gate": 0.5, "reason": f"regime_cautious:{regime}"}
    if regime in {"halt", "unknown", ""}:
        return {"gate": 0.3, "reason": "regime_unknown"}
    return {"gate": 0.6, "reason": f"regime_default:{regime}"}


def _options_intent_model(f: SovereignFeatures) -> dict[str, Any]:
    """Options flow alignment.

    If options flow skew disagrees with our directional lean, discount size.
    Neutral / missing data → full pass-through.
    """
    skew = f.options_flow_skew
    if skew is None:
        return {"gate": 1.0, "aligned": None, "reason": "no_options_flow"}

    bullish = skew > 0.15
    bearish = skew < -0.15
    return {
        "gate": 1.0,
        "aligned": None,  # coordinator resolves against final action
        "bullish_flow": bullish,
        "bearish_flow": bearish,
        "skew": skew,
    }


def _catalyst_model(f: SovereignFeatures) -> dict[str, Any]:
    """News-shock / catalyst gate.

    Mirrors ``catalyst_risk_gate`` + ``news_shock_service`` discipline but
    inline so Sovereign stays self-contained.
    """
    state = (f.news_shock_state or "normal").lower()
    risk = (f.event_risk or "normal").lower()
    sentiment = f.news_sentiment if f.news_sentiment is not None else 0.0

    vetoes: list[str] = []
    delta = 0.0

    # Hard veto: restricted event risk blocks all new entries
    if risk == "restricted":
        vetoes.append("NEWS_SHOCK_RESTRICTED")
    elif state == "high":
        delta -= 0.10
    elif state == "elevated":
        # Delta depends on alignment — coordinator resolves against final dir.
        delta -= 0.05

    return {
        "vetoes": vetoes,
        "delta": round(delta, 4),
        "sentiment": round(sentiment, 4),
        "state": state,
        "event_risk": risk,
    }


_DOLLAR_VOLUME_FLOOR_RATIO = 0.30


def _risk_model(f: SovereignFeatures) -> dict[str, Any]:
    """Liquidity + volatility filter → ``size_multiplier`` + hard vetoes."""
    vetoes: list[str] = []
    mult = 1.0

    # Dollar-volume starvation
    if (
        f.dollar_volume is not None
        and f.dollar_volume_baseline
        and f.dollar_volume_baseline > 0
        and f.dollar_volume > 0
    ):
        ratio = f.dollar_volume / f.dollar_volume_baseline
        if ratio < _DOLLAR_VOLUME_FLOOR_RATIO:
            vetoes.append("LIQUIDITY_TRAP")

    # Volatility extremes — ATR > 8% → downsize, ATR > 15% → veto
    if f.atr_pct is not None:
        if f.atr_pct > 0.15:
            vetoes.append("VOLATILITY_EXTREME")
        elif f.atr_pct > 0.08:
            mult = min(mult, 0.5)
        elif f.atr_pct > 0.05:
            mult = min(mult, 0.75)

    # Volume z-score negative → thin tape
    if f.volume_zscore is not None and f.volume_zscore < -1.5:
        mult = min(mult, 0.6)

    return {"vetoes": vetoes, "multiplier": round(mult, 4)}


def _calibration_model(
    f: SovereignFeatures,
    strategist_vote: dict[str, Any],
) -> dict[str, Any]:
    """Self-consistency score — how trustworthy is this tick's read.

    Returns a [0, 1] score. Used as a tiebreaker + logged for ML back-test.
    """
    score = 0.5

    # More data = more confidence
    populated = sum(
        1 for v in (f.rsi, f.momentum_5b, f.atr_pct, f.volume_zscore, f.regime)
        if v is not None and v != ""
    )
    score += 0.05 * populated

    # Extreme readings on single indicators lower trust
    if f.rsi is not None and (f.rsi < 15 or f.rsi > 85):
        score -= 0.10

    # Bull/bear agreement (strong one-sided vote) → higher confidence
    gap = abs(strategist_vote.get("bull", 0) - strategist_vote.get("bear", 0))
    score += min(0.20, gap * 0.5)

    # Regime known
    if f.regime:
        score += 0.05

    return {"score": round(max(0.0, min(1.0, score)), 4)}


# ─── Conviction tier + size sizing ─────────────────────────────────────────────


def _classify_tier(confidence: float) -> ConvictionTier:
    if confidence >= 0.85:
        return "extreme"
    if confidence >= 0.70:
        return "high"
    if confidence >= 0.55:
        return "medium"
    return "low"


_TIER_BASE_MULT = {
    "low": 0.25,
    "medium": 0.50,
    "high": 0.75,
    "extreme": 1.00,
}


# ─── Coordinator ────────────────────────────────────────────────────────────────


async def sovereign_decide(
    db: Any,
    features: SovereignFeatures,
    *,
    shadow: bool = True,
    advisory_votes: Optional[dict[str, Any]] = None,
    persist: bool = True,
) -> SovereignDecision:
    """Run all 6 models, resolve, optionally persist.

    Fail-safe: any exception degrades to a ``HOLD`` decision with
    ``vetoes=["SOVEREIGN_INTERNAL_ERROR"]`` so callers can keep going.
    """
    try:
        strategist = _strategist_model(features)
        regime = _regime_model(features)
        options = _options_intent_model(features)
        catalyst = _catalyst_model(features)
        risk = _risk_model(features)
        calibration = _calibration_model(features, strategist)

        # Start from strategist vote
        action: Direction = strategist["action"]  # type: ignore[assignment]
        confidence = float(strategist["confidence"])
        reasons: list[str] = [f"strategist:{action}@{confidence:.2f}"]
        vetoes: list[str] = []

        # Catalyst delta
        confidence = max(0.0, min(1.0, confidence + float(catalyst["delta"])))
        if catalyst["delta"]:
            reasons.append(f"catalyst_delta:{catalyst['delta']:+.2f}")

        # Options alignment resolution (coordinator owns the alignment check
        # because it needs the final action)
        if options.get("bullish_flow") and action == "LONG":
            confidence = min(1.0, confidence + 0.03)
            reasons.append("options_aligned_bull")
        elif options.get("bearish_flow") and action == "SHORT":
            confidence = min(1.0, confidence + 0.03)
            reasons.append("options_aligned_bear")
        elif options.get("bullish_flow") and action == "SHORT":
            confidence = max(0.0, confidence - 0.05)
            reasons.append("options_contra_bull")
        elif options.get("bearish_flow") and action == "LONG":
            confidence = max(0.0, confidence - 0.05)
            reasons.append("options_contra_bear")

        # Accumulate vetoes
        vetoes.extend(catalyst["vetoes"])
        vetoes.extend(risk["vetoes"])

        # Apply size
        mult = _TIER_BASE_MULT[_classify_tier(confidence)]
        mult *= float(regime["gate"])
        mult *= float(risk["multiplier"])

        # Vetoes zero out sizing and force HOLD
        if vetoes:
            action = "HOLD"
            mult = 0.0
            reasons.append(f"vetoed:{','.join(vetoes)}")

        tier = _classify_tier(confidence if action != "HOLD" else 0.0)
        mult = max(0.0, min(1.0, mult))

        decision = SovereignDecision(
            symbol=features.symbol,
            asset_type=features.asset_type,
            action=action,
            direction=action,
            confidence=round(confidence, 4),
            conviction_tier=tier,
            size_multiplier=round(mult, 4),
            reasons=reasons,
            vetoes=vetoes,
            model_votes={
                "strategist": strategist,
                "regime": regime,
                "options_intent": options,
                "catalyst": catalyst,
                "risk": risk,
                "calibration": calibration,
            },
            advisory_votes=advisory_votes or {},
            feature_snapshot=asdict(features),
            calibration_score=float(calibration["score"]),
            shadow=shadow,
        )

        if persist and db is not None:
            try:
                await db["sovereign_decisions"].insert_one(decision.to_mongo_doc())
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[sovereign_ai] persist failed for %s: %s",
                    features.symbol, exc,
                )
            # Proof-chain audit: emit a SOVEREIGN_DECISION block so the
            # tamper-evident ledger carries every sovereign verdict alongside
            # Strategist / Commander / Council opinions. Failure is silent —
            # a broken proof chain write cannot block the decision path.
            try:
                from services.proof_chain import (
                    AsyncMongoProofChainStore,
                    ProofEvent,
                    ProofEventType,
                    async_append_proof_event,
                )
                store = AsyncMongoProofChainStore(db)
                await async_append_proof_event(
                    store,
                    ProofEvent(
                        event_type=ProofEventType.SOVEREIGN_DECISION,
                        entity_id=f"sovereign:{features.symbol}:{decision.decision_id[:8]}",
                        actor=f"sovereign_ai[{features.asset_type}]",
                        payload={
                            "decision_id": decision.decision_id,
                            "symbol": decision.symbol,
                            "asset_type": decision.asset_type,
                            "action": decision.action,
                            "confidence": decision.confidence,
                            "conviction_tier": decision.conviction_tier,
                            "size_multiplier": decision.size_multiplier,
                            "reasons": decision.reasons,
                            "vetoes": decision.vetoes,
                            "model_votes": decision.model_votes,
                            "advisory_votes": decision.advisory_votes,
                            "calibration_score": decision.calibration_score,
                            "shadow": decision.shadow,
                        },
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[sovereign_ai] proof chain append failed for %s: %s",
                    features.symbol, exc,
                )
        return decision

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sovereign_ai] decide failed for %s — safe HOLD (%s)",
            getattr(features, "symbol", "?"), exc,
        )
        return SovereignDecision(
            symbol=getattr(features, "symbol", "?"),
            asset_type=getattr(features, "asset_type", "equity"),
            action="HOLD",
            direction="HOLD",
            confidence=0.0,
            conviction_tier="low",
            size_multiplier=0.0,
            reasons=["sovereign_internal_error"],
            vetoes=["SOVEREIGN_INTERNAL_ERROR"],
            model_votes={},
            advisory_votes=advisory_votes or {},
            feature_snapshot={},
            calibration_score=0.0,
            shadow=True,
        )


# ─── Shadow wire helpers (called by paper-traders) ────────────────────────────


async def run_shadow_for_equity(
    db: Any,
    *,
    symbol: str,
    snapshot: Any,
    regime: Optional[str],
    strategist_action: Optional[Direction],
    strategist_confidence: Optional[float],
    advisory_votes: Optional[dict[str, Any]] = None,
) -> Optional[SovereignDecision]:
    """Thin adapter: build features from an ``ml_paper_trader`` FeaturesSnapshot
    + catalyst snapshot and run the engine. Always fail-silent, returns None
    on any error so the equity path is never impacted.
    """
    try:
        catalyst = None
        try:
            catalyst = await db.catalyst_snapshots.find_one(
                {"symbol": symbol.upper()}, {"_id": 0},
            )
        except Exception:  # noqa: BLE001
            catalyst = None

        dv = getattr(snapshot, "dollar_volume", None)
        dv_base = getattr(snapshot, "dollar_volume_baseline", None)
        features = SovereignFeatures(
            symbol=symbol.upper(),
            asset_type="equity",
            rsi=getattr(snapshot, "rsi", None),
            momentum_5b=getattr(snapshot, "momentum_5b", None),
            atr_pct=getattr(snapshot, "atr_pct", None),
            volume_zscore=getattr(snapshot, "volume_zscore", None),
            dollar_volume=dv if (dv is None or not math.isnan(dv)) else None,
            dollar_volume_baseline=dv_base,
            regime=regime,
            news_shock_state=(catalyst or {}).get("news_shock", {}).get("state")
                if catalyst else None,
            event_risk=(catalyst or {}).get("event_risk") if catalyst else None,
            news_sentiment=(catalyst or {}).get("news_shock", {}).get("sentiment_score")
                if catalyst else None,
            news_volume_zscore=(catalyst or {}).get("news_shock", {}).get("zscore")
                if catalyst else None,
            strategist_action=strategist_action,
            strategist_confidence=strategist_confidence,
        )
        return await sovereign_decide(
            db, features, shadow=True, advisory_votes=advisory_votes,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[sovereign_ai] equity shadow run failed for %s: %s", symbol, exc)
        return None


async def run_shadow_for_crypto(
    db: Any,
    *,
    symbol: str,
    signal: dict[str, Any],
    regime: Optional[str],
    advisory_votes: Optional[dict[str, Any]] = None,
) -> Optional[SovereignDecision]:
    """Thin adapter for the crypto path. ``signal`` is the strategist dict."""
    try:
        indicators = (signal.get("strategist") or {}).get("indicators", {}) or {}
        # Map canonical direction to sovereign vocabulary
        raw_dir = str(signal.get("direction", "HOLD")).upper()
        action: Optional[Direction] = None
        if raw_dir in {"LONG", "BUY", "STRONG_BUY", "UP", "BULLISH"}:
            action = "LONG"
        elif raw_dir in {"SHORT", "SELL", "STRONG_SELL", "DOWN", "BEARISH"}:
            action = "SHORT"
        elif raw_dir == "HOLD":
            action = "HOLD"

        features = SovereignFeatures(
            symbol=symbol.upper(),
            asset_type="crypto",
            rsi=indicators.get("rsi"),
            momentum_5b=indicators.get("momentum_5b"),
            atr_pct=indicators.get("atr_pct"),
            volume_zscore=indicators.get("volume_zscore"),
            regime=regime or signal.get("regime"),
            strategist_action=action,
            strategist_confidence=float(signal.get("confidence") or 0.0),
        )
        return await sovereign_decide(
            db, features, shadow=True, advisory_votes=advisory_votes,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[sovereign_ai] crypto shadow run failed for %s: %s", symbol, exc)
        return None


# ─── Resolution (back-patch outcomes) ───────────────────────────────────────────


async def resolve_sovereign_decision(
    db: Any,
    decision_id: str,
    *,
    horizon: Literal["60m", "4h", "eod"],
    pnl_pct: float,
    was_right: bool,
) -> bool:
    """Back-patch a single decision with its realised outcome.

    Returns True if the update matched, False otherwise. Idempotent per
    ``(decision_id, horizon)`` — re-running the same resolution is a no-op.
    """
    if db is None or not decision_id:
        return False
    try:
        res = await db["sovereign_decisions"].update_one(
            {"decision_id": decision_id},
            {
                "$set": {
                    f"outcomes.{horizon}": {
                        "pnl_pct": round(float(pnl_pct), 6),
                        "was_right": bool(was_right),
                        "resolved_at": datetime.now(timezone.utc),
                    },
                    "resolved": True,
                },
            },
        )
        return res.matched_count > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sovereign_ai] resolve failed id=%s: %s", decision_id, exc)
        return False


async def ensure_sovereign_indexes(db: Any) -> dict[str, Any]:
    """Create required indexes (idempotent, safe to call on every boot)."""
    if db is None:
        return {"status": "no_db"}
    try:
        coll = db["sovereign_decisions"]
        await coll.create_index([("symbol", 1), ("created_at", -1)])
        await coll.create_index("decision_id", unique=True)
        await coll.create_index([("asset_type", 1), ("resolved", 1), ("created_at", -1)])
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sovereign_ai] ensure_indexes failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ─── Adapter-facing wrappers ──────────────────────────────────────────────────
# Thin helpers the DTD / PRD adapters call into. They keep the adapter modules
# free of SovereignFeatures assembly boilerplate, and route everything through
# a single canonical entry point so future feature enrichment (options flow,
# smart money, liquidity metrics) can be added in one place.


async def run_sovereign_ai_decision(
    db: Any,
    *,
    symbol: str,
    asset_type: AssetType = "equity",
    shadow: bool = True,
    features: Optional[SovereignFeatures] = None,
    advisory_votes: Optional[dict[str, Any]] = None,
    persist: bool = True,
) -> SovereignDecision:
    """Unified entry point used by both adapters.

    If ``features`` is not supplied, builds a minimal feature set from the
    latest ``catalyst_snapshots`` row for the symbol. Downstream callers
    (the adapter thin wrappers) are expected to pass enriched features when
    the caller has richer context (ml_paper_trader's FeaturesSnapshot,
    crypto_paper_trader's signal dict).
    """
    if features is None:
        catalyst = None
        try:
            if db is not None:
                catalyst = await db.catalyst_snapshots.find_one(
                    {"symbol": symbol.upper()}, {"_id": 0},
                )
        except Exception:  # noqa: BLE001
            catalyst = None

        features = SovereignFeatures(
            symbol=symbol.upper(),
            asset_type=asset_type,
            news_shock_state=(catalyst or {}).get("news_shock", {}).get("state")
                if catalyst else None,
            event_risk=(catalyst or {}).get("event_risk") if catalyst else None,
            news_sentiment=(catalyst or {}).get("news_shock", {}).get("sentiment_score")
                if catalyst else None,
            news_volume_zscore=(catalyst or {}).get("news_shock", {}).get("zscore")
                if catalyst else None,
        )
    return await sovereign_decide(
        db, features, shadow=shadow,
        advisory_votes=advisory_votes, persist=persist,
    )


async def nightly_sovereign_retrain_placeholder(db: Any) -> dict[str, Any]:
    """Placeholder for the nightly Sovereign retraining job.

    Real model retraining (XGBoost / LightGBM on the accumulated
    ``sovereign_decisions`` rows) lands in P2 once the first 500 resolved
    shadow decisions are on disk. For now this is a stats probe that the
    DTD adapter can trigger to verify the full research loop is running.

    **Guarded**: the DTD adapter calls ``require_dtd()`` before hitting this
    function, so it can never execute in PRD mode.
    """
    if db is None:
        return {"status": "no_db", "candidate_rows": 0}
    try:
        coll = db["sovereign_decisions"]
        resolved = await coll.count_documents({"resolved": True})
        total = await coll.count_documents({})
        resolved_equity = await coll.count_documents(
            {"resolved": True, "asset_type": "equity"},
        )
        resolved_crypto = await coll.count_documents(
            {"resolved": True, "asset_type": "crypto"},
        )
        return {
            "status": "ok",
            "ran_at": datetime.now(timezone.utc),
            "total_rows": total,
            "resolved_rows": resolved,
            "resolved_equity": resolved_equity,
            "resolved_crypto": resolved_crypto,
            "model_trained": False,
            "reason": "placeholder_pending_xgboost_p2",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sovereign_ai] retrain placeholder failed: %s", exc)
        return {"status": "error", "error": str(exc)}
