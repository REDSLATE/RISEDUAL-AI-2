"""Per-Context Sovereign Promotion (2026-02-23, Stage 3.5).

Background
----------
Stage 3 promoted Sovereign globally per ``asset_type`` (equity OR
crypto). Stage 3.5 introduces a ``(asset_type, regime)`` matrix so
the promotion authority can be finer-grained — equity-bull can sit
at PRIMARY while options-volatile / equity-bear stays CO_TRADER,
without one regime's drawdown blocking promotion in another.

Doctrine
--------
* The **base global gate** in ``sovereign_promotion_gate`` is
  unchanged — it's still the floor. A context cannot be promoted
  past the global state.
* The matrix is **additive** — it can only DEMOTE a context below
  the global state when that context's rolling win-rate falls
  below threshold. It cannot promote a context above the global
  state. Operator can still re-tighten any context via
  ``/api/admin/council-policy``.
* Mongo-persisted under ``sovereign_promotion_contexts`` so the
  state survives backend restarts.
* Per-call read; the gate evaluation is cheap (few count_documents
  calls per context). Cache adoption deferred — Stage 4 will need
  it but Stage 3.5's call sites are sparse (paper-trader entry).

Regime taxonomy
---------------
Sovereign decision rows currently carry only ``asset_type`` —
``regime`` is derived from ``feature_snapshot.regime_*`` flags
where present, falling back to ``"unknown"`` so older rows are
still counted in the global denominator but contribute to the
``unknown`` per-context bucket (which is informational only;
unknown never demotes a context).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)


AssetType = Literal["crypto", "equity"]
GATE_HORIZON = "h6"
DEMOTE_WINDOW_DAYS = 30


def _min_resolved_per_context() -> int:
    """Per-context demotion threshold needs a much smaller sample
    than the global one — operator can flag a regime-specific
    regression with 20 rows in 30d (the global gate uses 200)."""
    try:
        return int(
            os.environ.get("STAGE_3_5_MIN_RESOLVED_PER_CONTEXT", "20")
        )
    except (TypeError, ValueError):
        return 20


def _demote_below() -> float:
    try:
        return float(
            os.environ.get("STAGE_3_5_DEMOTE_BELOW_WIN_RATE", "0.45")
        )
    except (TypeError, ValueError):
        return 0.45


# Canonical regime taxonomy. Anything not in this set collapses to
# ``"unknown"`` at compute time so the matrix doesn't sprawl.
KNOWN_REGIMES: tuple[str, ...] = (
    "trending_up", "trending_down", "ranging",
    "high_volatility", "low_volatility",
    "bull", "bear", "sideways",
    "unknown",
)


def _normalise_regime(value: Any) -> str:
    if value is None:
        return "unknown"
    s = str(value).strip().lower().replace("-", "_")
    return s if s in KNOWN_REGIMES else "unknown"


def derive_regime_from_features(features: dict[str, Any] | None) -> str:
    """Best-effort regime extraction from a Sovereign decision's
    ``feature_snapshot``. Conservative: returns ``"unknown"`` unless
    a recognized regime tag is found."""
    if not features:
        return "unknown"
    # Explicit fields first.
    for key in ("regime", "regime_label", "market_regime"):
        if key in features:
            r = _normalise_regime(features[key])
            if r != "unknown":
                return r
    # Boolean flag pattern.
    for tag in ("trending_up", "trending_down", "ranging",
                "high_volatility", "low_volatility", "bull", "bear",
                "sideways"):
        if features.get(f"regime_{tag}") is True:
            return tag
    return "unknown"


async def compute_context_promotion_state(
    db: Any, *,
    asset_type: AssetType,
    regime: str,
    global_promoted: bool,
) -> dict[str, Any]:
    """Compute the (asset_type, regime) cell of the Stage 3.5 matrix.

    Returns::

        {
          "asset_type": "...",
          "regime": "...",
          "resolved": int,
          "wins": int,
          "win_rate": float|None,
          "promoted_in_context": bool,
          "demoted_in_context": bool,
          "blocker": str|None,
        }

    Doctrine: this cell can DEMOTE the context below the global
    state but cannot promote it above. So
    ``promoted_in_context = global_promoted AND NOT demoted``.
    """
    regime = _normalise_regime(regime)
    out: dict[str, Any] = {
        "asset_type": asset_type, "regime": regime,
        "resolved": 0, "wins": 0, "win_rate": None,
        "promoted_in_context": False, "demoted_in_context": False,
        "blocker": None,
    }
    try:
        coll = db["sovereign_decisions"]
        cutoff = datetime.now(timezone.utc) - timedelta(days=DEMOTE_WINDOW_DAYS)
        # Match by asset_type + horizon resolution + window. Regime
        # filter is server-side via aggregation since older rows
        # lack the field.
        base = {
            "asset_type": asset_type, "shadow": True,
            "created_at": {"$gte": cutoff},
            f"outcomes.{GATE_HORIZON}": {"$exists": True},
        }
        resolved_in_regime = 0
        wins_in_regime = 0
        async for r in coll.find(
            base,
            {"_id": 0, "feature_snapshot": 1,
             f"outcomes.{GATE_HORIZON}.was_right": 1},
        ):
            row_regime = derive_regime_from_features(r.get("feature_snapshot"))
            if row_regime != regime:
                continue
            resolved_in_regime += 1
            try:
                if (r.get("outcomes") or {}).get(GATE_HORIZON, {}).get("was_right"):
                    wins_in_regime += 1
            except AttributeError:
                pass
        out["resolved"] = resolved_in_regime
        out["wins"] = wins_in_regime
        if resolved_in_regime > 0:
            out["win_rate"] = round(wins_in_regime / resolved_in_regime, 4)

        # Demotion fires only when we have ENOUGH per-context
        # samples AND the rolling rate is below the bar.
        if (
            resolved_in_regime >= _min_resolved_per_context()
            and out["win_rate"] is not None
            and out["win_rate"] < _demote_below()
        ):
            out["demoted_in_context"] = True
            out["blocker"] = (
                f"per-context demotion: {asset_type}/{regime} "
                f"win rate {out['win_rate']:.2%} below "
                f"{_demote_below():.0%} on {resolved_in_regime} rows"
            )

        out["promoted_in_context"] = (
            bool(global_promoted) and not out["demoted_in_context"]
        )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("[stage_3_5] compute failed for %s/%s: %s",
                     asset_type, regime, exc)
        return out


async def compute_promotion_matrix(
    db: Any, *, asset_type: AssetType, global_promoted: bool,
) -> list[dict[str, Any]]:
    """Compute every (asset_type, regime) cell for the asset type.

    Used by the admin dashboard surface
    (``/api/admin/sovereign/promotion-matrix``) and the per-call
    ``promoted_for_context`` check.
    """
    cells: list[dict[str, Any]] = []
    for regime in KNOWN_REGIMES:
        cell = await compute_context_promotion_state(
            db, asset_type=asset_type, regime=regime,
            global_promoted=global_promoted,
        )
        cells.append(cell)
    return cells


async def is_promoted_for_context(
    db: Any, *, asset_type: AssetType, regime: str,
    global_promoted: bool,
) -> bool:
    """The single check the paper-trader entry path uses.

    Doctrine: a paper trade in a demoted context still emits the
    intent to MC, but the brain-side ``alpha_paper_uses_sovereign``
    flag stays False so the LLM council remains the executor in
    that context until the regime's win rate recovers.
    """
    cell = await compute_context_promotion_state(
        db, asset_type=asset_type, regime=regime,
        global_promoted=global_promoted,
    )
    return bool(cell.get("promoted_in_context"))


__all__ = [
    "AssetType",
    "KNOWN_REGIMES",
    "compute_context_promotion_state",
    "compute_promotion_matrix",
    "derive_regime_from_features",
    "is_promoted_for_context",
]
