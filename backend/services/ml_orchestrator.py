"""ML orchestrator — runs all three autonomous tiers after every signal.

Call :func:`run_post_signal_pipeline` immediately after a prediction is
persisted.  It will:

1. Load the latest trained model (hot-reload safe via ``mtime`` check).
2. Load current CalibrationStats from the model artefact.
3. Run ``check_all_gates()`` to determine which tiers are unlocked.
4. For each unlocked tier, call the corresponding service.
5. Return an :class:`OrchestratorResult` summary (never raises).

Design constraints
------------------
- Never raises — all exceptions are caught and logged.
- Shared ``httpx.AsyncClient`` is passed through to each tier service.
- Model hot-reload uses mtime: model file is re-read only when it changes on
  disk, avoiding redundant joblib deserialization on every request.
- The orchestrator is stateless except for the ``_model_cache`` dict below.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from risedual_core.ml.calibration import GateResult, Tier, check_all_gates
from risedual_core.ml.signal_model import SignalModel
from risedual_core.schemas.market import FeaturesSnapshot, SignalResult

from services.ml_alert_service import maybe_send_alert
from services.ml_paper_trader import maybe_paper_trade
from services.ml_alpaca_broker import maybe_execute_live

log = logging.getLogger(__name__)

_MODELS_DIR: Path = Path(os.getenv("MODELS_DIR", "models"))
_LIVE_DAYS_KEY: str = "RISEDUAL_LIVE_DAYS"


# ── Model hot-reload cache ────────────────────────────────────────────────────

@dataclass
class _ModelCache:
    model: SignalModel | None = None
    path: Path | None = None
    mtime: float = 0.0


_cache = _ModelCache()


def _latest_model_path() -> Path | None:
    """Find the highest-versioned signal model in MODELS_DIR."""
    if not _MODELS_DIR.exists():
        return None
    candidates = sorted(
        _MODELS_DIR.glob("signal_model_v*.joblib"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _load_model_if_stale() -> SignalModel | None:
    """Return a :class:`SignalModel`, reloading from disk only when mtime changes."""
    path = _latest_model_path()
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    if _cache.model is not None and _cache.path == path and _cache.mtime == mtime:
        return _cache.model

    try:
        model = SignalModel.load(path)
        _cache.model = model
        _cache.path = path
        _cache.mtime = mtime
        log.info("[orchestrator] Loaded model from %s", path.name)
        return model
    except Exception as exc:
        log.warning("[orchestrator] Model load failed: %s", exc)
        return None


# ── Result container ──────────────────────────────────────────────────────────

@dataclass
class OrchestratorResult:
    """Summary of what the orchestrator did on this signal cycle."""

    ticker: str
    prediction_id: str
    highest_tier: Tier = Tier.LOCKED
    gate_result: GateResult | None = None
    alert_sent: bool = False
    paper_trade_id: str | None = None
    live_order_id: str | None = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "prediction_id": self.prediction_id,
            "highest_tier": self.highest_tier.value,
            "alert_sent": self.alert_sent,
            "paper_trade_id": self.paper_trade_id,
            "live_order_id": self.live_order_id,
            "errors": self.errors,
        }


# ── Public API ────────────────────────────────────────────────────────────────


async def run_post_signal_pipeline(
    ticker: str,
    signal: SignalResult,
    snapshot: FeaturesSnapshot,
    regime: str,
    db: Any,
    http_client: httpx.AsyncClient | None = None,
) -> OrchestratorResult:
    """Run all three autonomous tiers in sequence after a signal is generated.

    This function never raises.  All exceptions are caught, logged, and
    appended to :attr:`OrchestratorResult.errors`.
    """
    result = OrchestratorResult(
        ticker=ticker,
        prediction_id=signal.prediction_id or "",
    )

    # ── 1. Load model + CalibrationStats ────────────────────────────────────
    model = _load_model_if_stale()
    if model is None:
        log.info(
            "[orchestrator] No trained model available — skipping autonomous tiers for %s.",
            ticker,
        )
        result.errors.append("no_trained_model")
        return result

    stats = model.calibration_stats
    if stats is None:
        log.info(
            "[orchestrator] Model has no CalibrationStats — skipping tiers for %s.",
            ticker,
        )
        result.errors.append("no_calibration_stats")
        return result

    # ── 2. Gate check ────────────────────────────────────────────────────────
    live_days = int(os.getenv(_LIVE_DAYS_KEY, "0"))
    user_opted_in: bool = os.getenv("RISEDUAL_LIVE_EXECUTION", "0") == "1"

    meta: dict[str, Any] = getattr(model, "_metadata", {}) or {}
    sharpe: float = float(meta.get("sharpe", 0.0))
    max_drawdown: float = float(meta.get("max_drawdown", 1.0))

    try:
        gate = check_all_gates(
            accuracy=stats.accuracy,
            n_predictions=stats.n_predictions,
            ece=stats.ece,
            sharpe=sharpe,
            max_drawdown=max_drawdown,
            live_days=live_days,
            user_opted_in=user_opted_in,
        )
        result.gate_result = gate
        result.highest_tier = gate.highest_unlocked
    except Exception as exc:
        log.warning("[orchestrator] Gate check failed: %s", exc)
        result.errors.append(f"gate_check_error:{exc}")
        return result

    if gate.highest_unlocked == Tier.LOCKED:
        log.debug(
            "[orchestrator] All tiers locked for %s "
            "(acc=%.3f n=%d ece=%.3f) — no autonomous action.",
            ticker,
            stats.accuracy,
            stats.n_predictions,
            stats.ece,
        )
        return result

    # ── 3. Shared HTTP client ────────────────────────────────────────────────
    _own_client = http_client is None
    client = http_client or httpx.AsyncClient()

    try:
        # ── Tier 1: Alerts ───────────────────────────────────────────────────
        if gate.tier1.unlocked:
            try:
                result.alert_sent = await maybe_send_alert(
                    ticker=ticker,
                    signal=signal,
                    snapshot=snapshot,
                    regime=regime,
                    db=db,
                    http_client=client,
                )
            except Exception as exc:
                log.warning("[orchestrator] Alert service error: %s", exc)
                result.errors.append(f"alert_error:{exc}")

        # ── Tier 2: Paper trading ────────────────────────────────────────────
        if gate.tier2.unlocked:
            try:
                result.paper_trade_id = await maybe_paper_trade(
                    ticker=ticker,
                    signal=signal,
                    snapshot=snapshot,
                    regime=regime,
                    db=db,
                    http_client=client,
                )
            except Exception as exc:
                log.warning("[orchestrator] Paper trader error: %s", exc)
                result.errors.append(f"paper_error:{exc}")

        # ── Tier 3: Live execution ────────────────────────────────────────────
        if gate.tier3.unlocked:
            try:
                result.live_order_id = await maybe_execute_live(
                    ticker=ticker,
                    signal=signal,
                    snapshot=snapshot,
                    regime=regime,
                    db=db,
                    http_client=client,
                )
            except Exception as exc:
                log.warning("[orchestrator] Live broker error: %s", exc)
                result.errors.append(f"live_error:{exc}")

    finally:
        if _own_client:
            await client.aclose()

    return result
