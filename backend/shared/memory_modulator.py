"""RISEDUAL Memory Modulator (2026-02-23, P0 safety adds applied).

Doctrine
--------
* Symmetric across all 4 brains (Alpha / Camaro / Chevelle / REDEYE)
  — same code path, no per-brain branches, the scoreboard decides.
* Directional intents only (BUY / SHORT). HOLD is NEVER modulated.
* Confidence-only modulation. Never creates direction. Never
  flips a vote. Never bypasses gates / ladder / RoadGuard.
* Bounded ``[-0.25, +0.10]`` — clamped locally AND clamped by MC
  on receipt (defense in depth).
* Quarantine-aware — quarantined memories (Chevelle firewall
  trust_weight == 0.0 OR rejection_reason set) MUST NOT
  influence the modulator. The firewall isn't decoration; it's
  the boundary between observed and trained-on memory.
* Feature normalization — we cosine-compare a stable subset of
  features clipped to [-3.0, +3.0] so a single high-magnitude
  field can't dominate the similarity score.

Wiring (this repo = Alpha)
--------------------------
``services/ml_paper_trader.maybe_paper_trade`` invokes
``compute_memory_modulator`` right before
``emit_intent_from_consensus`` and folds the modulator value into
``final_confidence``. The receipt rides on the intent payload as
``memory_modulator`` for the MC-side audit + tripwire to enforce.

Source data (this repo)
-----------------------
* ``alpha_decision_log``    — Alpha's own decisions (entry side)
* ``paper_trades`` (closed) — outcome side, joined by
  ``prediction_id`` / ``sovereign_decision_id`` / synthetic
  ``trade_id`` overlap. Resolution rows are passed through the
  ``chevelle_memory_labeler`` firewall before similarity is
  computed; quarantined / toxic rows are excluded.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from services.chevelle_memory_labeler import label_memory
from services.chevelle_memory_labels import TRUST_QUARANTINED

logger = logging.getLogger(__name__)


# ── Doctrine constants (operator-tunable via env) ─────────────────


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _b(name: str, default: bool) -> bool:
    v = (os.environ.get(name, "") or "").strip().lower()
    if v == "":
        return default
    return v in {"1", "true", "yes", "on"}


# Hard doctrine bounds — clamped here AND independently by MC.
MAX_UP: float = 0.10
MAX_DOWN: float = -0.25


def _max_up() -> float:
    return min(MAX_UP, _f("MEMORY_MODULATOR_MAX_UP", MAX_UP))


def _max_down() -> float:
    return max(MAX_DOWN, _f("MEMORY_MODULATOR_MAX_DOWN", MAX_DOWN))


def _lookback_days() -> int:
    return _i("MEMORY_MODULATOR_LOOKBACK_DAYS", 90)


def _sim_threshold() -> float:
    return _f("MEMORY_MODULATOR_SIM_THRESHOLD", 0.85)


def _min_up() -> int:
    return _i("MEMORY_MODULATOR_MIN_MATCHES_FOR_UP", 5)


def _min_down() -> int:
    return _i("MEMORY_MODULATOR_MIN_MATCHES_FOR_DOWN", 2)


def is_enabled() -> bool:
    """Default ON — but ``MEMORY_MODULATOR_ENABLED=false`` flips it
    off at runtime without a redeploy if the operator needs to
    quarantine the modulator itself."""
    return _b("MEMORY_MODULATOR_ENABLED", True)


# ── Feature normalization (P0 safety add) ─────────────────────────

# Stable feature whitelist — cosine similarity over a fixed key set
# so one new high-magnitude field added upstream can't quietly
# rebalance the modulator. Order doesn't matter (cosine is
# scale-invariant within a dimension) but the *set* does.
_STABLE_FEATURE_KEYS: tuple[str, ...] = (
    "macd", "rsi", "trend", "volume_zscore",
    "atr_pct", "spread_bps",
    "session_phase", "regime_bull", "regime_bear",
    "sentiment", "news_intensity",
    "iv_rank", "skew",
)

# Per-feature clip range. Defends against an unnormalised z-score
# of, say, ``volume_zscore=42`` dominating the cosine. Doctrine:
# any single feature contributes at most this magnitude to either
# vector.
_FEATURE_CLIP_MIN: float = -3.0
_FEATURE_CLIP_MAX: float = 3.0


def _normalise_features(raw: Mapping[str, Any]) -> dict[str, float]:
    """Return the raw feature mapping projected onto the stable
    whitelist + clipped per-feature. Missing keys default to 0.0
    (the cosine treats them as orthogonal absence — desired)."""
    out: dict[str, float] = {}
    for k in _STABLE_FEATURE_KEYS:
        try:
            v = float(raw.get(k, 0.0) or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        if v > _FEATURE_CLIP_MAX:
            v = _FEATURE_CLIP_MAX
        elif v < _FEATURE_CLIP_MIN:
            v = _FEATURE_CLIP_MIN
        out[k] = v
    return out


# ── Cosine similarity ─────────────────────────────────────────────


def _cosine_similarity(
    a: Mapping[str, float], b: Mapping[str, float],
) -> float:
    """Pure cosine — no early returns to keep behaviour deterministic
    across {missing key, key=0.0}."""
    keys = sorted(set(a.keys()) | set(b.keys()))
    va = [float(a.get(k, 0.0)) for k in keys]
    vb = [float(b.get(k, 0.0)) for k in keys]
    dot = sum(x * y for x, y in zip(va, vb))
    mag_a = math.sqrt(sum(x * x for x in va))
    mag_b = math.sqrt(sum(x * x for x in vb))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── Quarantine filter (P0-1) ──────────────────────────────────────


def _is_quarantined(memory_row: Mapping[str, Any]) -> bool:
    """Honour the Chevelle firewall. Any row where the labeler
    returns ``trust_weight == TRUST_QUARANTINED`` (== 0.0) or sets
    a ``rejection_reason`` is excluded from the modulator.

    Defensive: if the labeler itself raises, treat the row as
    quarantined. A noisy upstream row MUST NOT silently leak past
    the firewall.
    """
    try:
        rec = label_memory(memory_row)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[memory_modulator] labeler raised → quarantine: %s",
                     exc)
        return True
    if rec.trust_weight <= TRUST_QUARANTINED:
        return True
    if rec.rejection_reason:
        return True
    return False


def _clamp_modulator(value: float) -> float:
    """Local doctrine clamp. MC will independently 422-reject any
    out-of-band value (no silent clamp on their side), so Alpha must
    never even emit an out-of-bounds value. If this clamp ever fires,
    that's a signal the upstream modulator math went sideways — we
    log it so the operator can investigate rather than silently
    submitting the clamped value as if it were correct.
    """
    if not math.isfinite(value):
        logger.warning(
            "memory_modulator non-finite value rejected: %r — clamped to 0.0",
            value,
        )
        return 0.0
    lo, hi = _max_down(), _max_up()
    if value < lo or value > hi:
        logger.warning(
            "memory_modulator out-of-band raw=%.4f bounds=[%.2f,%.2f] — "
            "upstream math suspect, clamping locally",
            value, lo, hi,
        )
        return max(lo, min(hi, value))
    return value


# ── Outcome / direction helpers ───────────────────────────────────


def _is_directional(action: str) -> bool:
    """HOLD is NEVER modulated. Map both common encodings."""
    a = (action or "").upper().strip()
    return a in {"BUY", "SHORT", "SELL", "UP", "DOWN"}


def _normalise_direction(action: str) -> str:
    """Map to the lowercased ``up``/``down`` token this repo's
    decision log stores."""
    a = (action or "").upper().strip()
    if a in {"BUY", "UP"}:
        return "up"
    if a in {"SHORT", "SELL", "DOWN"}:
        return "down"
    return ""


# ── Resolved memory loader (this repo: alpha_decision_log ⨯ paper_trades) ──


async def _load_resolved_memories(
    db: Any, *, brain: str, symbol: str, direction: str,
    lookback_days: int,
) -> list[dict[str, Any]]:
    """Walk Alpha's decision log joined to closed paper_trades for
    the same symbol+direction within the lookback window. Each row
    returned has shape::

        {
          "memory_id": <decision_log _id as str>,
          "features": {...},
          "outcome": 1 | -1,
          "paper_trade_row": {...},  # for the firewall labeler
        }

    Per-brain segregation: this repo serves Alpha — other brains
    paste this same module, set ``brain="camaro"`` etc., and point
    at THEIR decision log collection (the resolver maps via the
    ``brain`` kwarg below).
    """
    if db is None:
        return []
    log_col = {
        "alpha": "alpha_decision_log",
        "camaro": "camaro_decision_log",
        "chevelle": "chevelle_decision_log",
        "redeye": "redeye_decision_log",
    }.get(brain, "alpha_decision_log")

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows: list[dict[str, Any]] = []
    cursor = db[log_col].find(
        {
            "symbol": symbol,
            "created_at": {"$gte": cutoff},
        },
        {"_id": 1, "symbol": 1, "created_at": 1,
         "decision": 1, "extras": 1, "lane": 1,
         "prediction_id": 1, "sovereign_decision_id": 1},
    )
    async for r in cursor:
        # Decision can be either a dict or a JSON string in this
        # repo. We only care about features + direction; default to
        # an empty dict if it isn't structured.
        decision = r.get("decision") or {}
        if isinstance(decision, str):
            # JSON-string variant — keep cheap: only parse if needed.
            import json
            try:
                decision = json.loads(decision)
            except Exception:
                decision = {}
        if not isinstance(decision, dict):
            continue
        # Direction filter — modulator is per-direction (we don't
        # want a prior LONG win to upweight a fresh SHORT).
        dec_dir = (decision.get("direction") or "").lower()
        if dec_dir and dec_dir != direction:
            continue
        features = decision.get("features") or r.get("extras", {}).get("features") or {}
        if not isinstance(features, dict):
            continue

        # Resolve outcome via the matching paper_trade row.
        pred_id = r.get("prediction_id") or decision.get("prediction_id")
        sov_id = r.get("sovereign_decision_id") or decision.get("sovereign_decision_id")
        match: dict[str, Any] | None = None
        if pred_id:
            match = await db["paper_trades"].find_one(
                {"prediction_id": pred_id, "status": "closed"},
                {"_id": 0, "outcome": 1, "pnl_pct": 1, "synthetic": 1,
                 "receipt_type": 1, "source_layer": 1, "opened_at": 1,
                 "closed_at": 1, "ticker": 1, "symbol": 1, "direction": 1,
                 "shares": 1, "qty": 1, "lane": 1},
            )
        if match is None and sov_id:
            match = await db["paper_trades"].find_one(
                {"sovereign_decision_id": sov_id, "status": "closed"},
                {"_id": 0, "outcome": 1, "pnl_pct": 1, "synthetic": 1,
                 "receipt_type": 1, "source_layer": 1, "opened_at": 1,
                 "closed_at": 1, "ticker": 1, "symbol": 1, "direction": 1,
                 "shares": 1, "qty": 1, "lane": 1},
            )
        if not match:
            continue
        outcome = match.get("outcome")
        if outcome not in (1, -1):
            continue

        rows.append({
            "memory_id": str(r.get("_id")),
            "features": features,
            "outcome": int(outcome),
            "paper_trade_row": match,
        })
    return rows


# ── Public API ────────────────────────────────────────────────────


async def compute_memory_modulator(
    db: Any, *,
    brain: str,
    symbol: str,
    direction: str,
    features: Mapping[str, Any],
) -> dict[str, Any]:
    """Symmetric memory modulator (see module docstring).

    Returns a structured receipt::

        {
          "value": <float in [-0.25, +0.10]>,
          "matched_winners": <int>,
          "matched_losers":  <int>,
          "winner_similarity": <float>,
          "loser_similarity":  <float>,
          "skipped_quarantined": <int>,
          "reason": <str>,
          "brain": <str>,
          "symbol": <str>,
          "doctrine_bounds": [-0.25, 0.10],
        }

    Always returns a well-formed receipt — even when disabled,
    out of data, or on internal error — so the caller can stamp
    it verbatim onto the intent payload.
    """
    neutral_receipt: dict[str, Any] = {
        "value": 0.0,
        "matched_winners": 0, "matched_losers": 0,
        "winner_similarity": 0.0, "loser_similarity": 0.0,
        "skipped_quarantined": 0,
        "reason": "neutral",
        "brain": brain, "symbol": symbol,
        "doctrine_bounds": [_max_down(), _max_up()],
    }

    if not is_enabled():
        neutral_receipt["reason"] = "disabled"
        return neutral_receipt

    dir_norm = _normalise_direction(direction)
    if not dir_norm:
        neutral_receipt["reason"] = "non_directional"
        return neutral_receipt

    feat_norm = _normalise_features(features or {})

    try:
        memories = await _load_resolved_memories(
            db, brain=brain, symbol=symbol, direction=dir_norm,
            lookback_days=_lookback_days(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[memory_modulator] load failed: %s", exc)
        neutral_receipt["reason"] = "load_failed"
        return neutral_receipt

    if not memories:
        neutral_receipt["reason"] = "no_resolved_memories"
        return neutral_receipt

    winners: list[float] = []
    losers: list[float] = []
    skipped = 0
    sim_threshold = _sim_threshold()

    for m in memories:
        # P0-1: quarantine exclusion. Hand the paper_trade row to
        # the Chevelle firewall; any quarantined / toxic / labeler-
        # raise row is dropped BEFORE similarity is computed.
        pt = m.get("paper_trade_row") or {}
        if _is_quarantined(pt):
            skipped += 1
            continue
        hist_norm = _normalise_features(m.get("features") or {})
        sim = _cosine_similarity(feat_norm, hist_norm)
        if sim < sim_threshold:
            continue
        if m["outcome"] == 1:
            winners.append(sim)
        else:
            losers.append(sim)

    winner_score = sum(winners) / max(len(winners), 1)
    loser_score = sum(losers) / max(len(losers), 1)

    modulator = 0.0
    reason = "neutral"
    if len(losers) >= _min_down():
        modulator = _max_down() * loser_score
        reason = f"matched_{len(losers)}_prior_losers"
    elif len(winners) >= _min_up():
        modulator = _max_up() * winner_score
        reason = f"matched_{len(winners)}_prior_winners"

    modulator = _clamp_modulator(modulator)

    return {
        "value": round(modulator, 4),
        "matched_winners": len(winners),
        "matched_losers": len(losers),
        "winner_similarity": round(winner_score, 4),
        "loser_similarity": round(loser_score, 4),
        "skipped_quarantined": skipped,
        "reason": reason,
        "brain": brain,
        "symbol": symbol,
        "doctrine_bounds": [_max_down(), _max_up()],
    }


def apply_modulator_to_confidence(
    *, confidence: float, modulator_value: float,
) -> tuple[float, float]:
    """Pure helper for the caller. Returns ``(new_conf, applied_delta)``.

    Clamps the modulator one more time at the application boundary
    so even a buggy upstream value can't slip past — and clamps the
    resulting confidence to ``[0.0, 1.0]``.
    """
    delta = _clamp_modulator(float(modulator_value))
    new_conf = float(confidence) + delta
    if new_conf < 0.0:
        new_conf = 0.0
    elif new_conf > 1.0:
        new_conf = 1.0
    return new_conf, delta


__all__ = [
    "MAX_UP", "MAX_DOWN",
    "is_enabled",
    "compute_memory_modulator",
    "apply_modulator_to_confidence",
    "_is_directional",
    "_normalise_direction",
    "_normalise_features",
    "_cosine_similarity",
    "_clamp_modulator",
    "_is_quarantined",
]
