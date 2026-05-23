"""Per-brain local state — weights, mode, decisions, resolved outcomes.

Each brain owns its own state file. MC never touches it. The doctrine
boundary is exactly this: brains keep their state local, they only
exchange ``contribution`` snapshots with MC over HTTPS.

State shape on disk::

    {
        "brain": "alpha",
        "mode": "DTD" | "PRD",
        "weights": { feature_name: float in [-3.0, +3.0], ... },  # <= 16 keys
        "learning_rate": float in [0.0, 0.5],
        "decisions": [ {symbol, action, confidence, ...}, ... ],   # local audit
        "outcomes":  [ {symbol, action, confidence, outcome, ...}, ... ],  # MC-bound
        "notes": str,
        "updated_at": iso8601,
    }
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from .wild_adaptive_core_v2 import ALLOWED_ACTIONS, ALLOWED_OUTCOMES, SUPPORTED_MODES
except ImportError:  # bare import via sidecar's sys.path tweak
    from wild_adaptive_core_v2 import ALLOWED_ACTIONS, ALLOWED_OUTCOMES, SUPPORTED_MODES

# Schema bounds (mirror MC server validation — fail-fast locally).
_MAX_WEIGHTS = 16
_WEIGHT_MIN, _WEIGHT_MAX = -3.0, 3.0
_LR_MIN, _LR_MAX = 0.0, 0.5
_MAX_OUTCOMES = 50
_MAX_DECISIONS = 200  # local audit cap (NOT sent to MC)
_CONF_MIN, _CONF_MAX = 0.0, 1.0

# Canonical recent_outcomes payload fields (2026-05-22, Gap 2).
# Sidecar's ``post_contribution`` strips down to this whitelist
# before shipping to MC so unknown future fields can't leak. The
# first 6 are MC-mandatory; the last 3 are optional provenance
# labels that complete the audit lineage (Sovereign decision →
# fill → outcome). Older snapshots without the provenance fields
# stay valid — the validator only enforces ``action`` /
# ``outcome`` / ``confidence``.
RECENT_OUTCOME_FIELDS: tuple[str, ...] = (
    "symbol",
    "action",
    "confidence",
    "outcome",
    "resolved_at",
    "notional",
    "sovereign_decision_id",
    "prediction_id",
    "source_signal",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_path(brain: str) -> Path:
    """Used when the caller passes ``path=None``. Respects SOVEREIGN_STATE_PATH."""
    env_p = os.environ.get("SOVEREIGN_STATE_PATH")
    if env_p:
        return Path(env_p)
    return Path.home() / ".sovereign" / brain / "state.json"


class LocalStateError(ValueError):
    """Raised when an invalid value would be persisted."""


class LocalState:
    """JSON-backed brain state.

    Usage::

        s = LocalState(brain="alpha", path="/app/data/sovereign/alpha/state.json", mode="DTD")
        if not s.weights:
            s.set_weights({"trend": 0.85, "macd": 0.65, "rsi": -0.25})
            s.set_learning_rate(0.06)
            s.save()
    """

    def __init__(
        self,
        brain: str,
        path: str | os.PathLike[str] | None = None,
        mode: str = "DTD",
    ) -> None:
        if not brain or not isinstance(brain, str):
            raise LocalStateError("brain name required")
        if mode not in SUPPORTED_MODES:
            raise LocalStateError(f"mode must be one of {SUPPORTED_MODES}, got {mode!r}")

        self.brain: str = brain
        self.path: Path = Path(path) if path is not None else _default_path(brain)
        self.mode: str = mode

        # Defaults populated by load() if file exists.
        self.weights: dict[str, float] = {}
        self.learning_rate: float = 0.0
        self._decisions: list[dict[str, Any]] = []
        self._outcomes: list[dict[str, Any]] = []
        self.notes: str = ""
        self.updated_at: str = _now_iso()

        self.load()

    # ── persistence ─────────────────────────────────────────────────
    def load(self) -> None:
        """Read state from disk if it exists. Missing file → defaults stay."""
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            raise LocalStateError(f"state file unreadable at {self.path}: {e}") from e

        if not isinstance(data, dict):
            raise LocalStateError(f"state file is not a JSON object: {self.path}")

        # Trust but verify — re-validate everything on load.
        w = data.get("weights") or {}
        if not isinstance(w, dict):
            raise LocalStateError("weights on disk is not an object")
        self._validate_weights(w)
        self.weights = {k: float(v) for k, v in w.items()}

        lr = float(data.get("learning_rate", 0.0))
        self._validate_learning_rate(lr)
        self.learning_rate = lr

        outs = data.get("outcomes")
        if outs is None:
            # Back-compat: prior schema used "recent_outcomes".
            outs = data.get("recent_outcomes") or []
        if not isinstance(outs, list):
            raise LocalStateError("outcomes on disk is not an array")
        self._validate_outcomes(outs)
        self._outcomes = list(outs)[-_MAX_OUTCOMES:]

        decs = data.get("decisions") or []
        if not isinstance(decs, list):
            raise LocalStateError("decisions on disk is not an array")
        self._decisions = list(decs)[-_MAX_DECISIONS:]

        self.notes = str(data.get("notes", "") or "")

        disk_mode = data.get("mode")
        if disk_mode in SUPPORTED_MODES:
            self.mode = disk_mode

        self.updated_at = str(data.get("updated_at") or _now_iso())

    def save(self) -> None:
        """Atomic write — temp file + rename."""
        self.updated_at = _now_iso()
        payload = self.snapshot()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def snapshot(self) -> dict[str, Any]:
        """Pure-data view, suitable for JSON serialization on disk."""
        return {
            "brain": self.brain,
            "mode": self.mode,
            "weights": dict(self.weights),
            "learning_rate": float(self.learning_rate),
            "decisions": list(self._decisions),
            "outcomes": list(self._outcomes),
            "notes": self.notes,
            "updated_at": self.updated_at,
        }

    # ── setters (validated) ─────────────────────────────────────────
    def set_weights(self, weights: Mapping[str, float]) -> None:
        self._validate_weights(weights)
        self.weights = {k: float(v) for k, v in weights.items()}

    def set_learning_rate(self, lr: float) -> None:
        lr = float(lr)
        self._validate_learning_rate(lr)
        self.learning_rate = lr

    def set_mode(self, mode: str) -> None:
        if mode not in SUPPORTED_MODES:
            raise LocalStateError(f"mode must be one of {SUPPORTED_MODES}")
        self.mode = mode

    # ── decisions (local audit only — NOT sent to MC) ───────────────
    def append_decision(self, decision: Mapping[str, Any]) -> None:
        """Record a decision the core just produced. Stamped with timestamp."""
        if not isinstance(decision, Mapping):
            raise LocalStateError("decision must be a mapping")
        rec = dict(decision)
        rec.setdefault("recorded_at", _now_iso())
        self._decisions.append(rec)
        if len(self._decisions) > _MAX_DECISIONS:
            self._decisions = self._decisions[-_MAX_DECISIONS:]

    def decisions(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Most recent N decisions, oldest-first within the slice."""
        if limit is None:
            return list(self._decisions)
        if limit <= 0:
            return []
        return list(self._decisions[-int(limit):])

    # ── outcomes (sent to MC in contribution snapshot) ──────────────
    def add_outcome(
        self,
        *,
        symbol: str,
        action: str,
        confidence: float,
        outcome: int,
        resolved_at: str | None = None,
        notional: float = 0.0,
        # 2026-05-22 (Gap 2): provenance labels propagated to MC
        # so the audit lineage (Sovereign decision / prediction →
        # fill → outcome) is visible in MC diagnostics. All three
        # are optional — older callers that don't have them still
        # produce valid outcomes.
        sovereign_decision_id: str | None = None,
        prediction_id: str | None = None,
        source_signal: str | None = None,
    ) -> None:
        rec: dict[str, Any] = {
            "symbol": str(symbol),
            "action": str(action),
            "confidence": float(confidence),
            "outcome": int(outcome),
            "resolved_at": resolved_at or _now_iso(),
            "notional": float(notional),
        }
        if sovereign_decision_id:
            rec["sovereign_decision_id"] = str(sovereign_decision_id)
        if prediction_id:
            rec["prediction_id"] = str(prediction_id)
        if source_signal:
            rec["source_signal"] = str(source_signal)
        self._validate_outcomes([rec])
        self._outcomes.append(rec)
        if len(self._outcomes) > _MAX_OUTCOMES:
            self._outcomes = self._outcomes[-_MAX_OUTCOMES:]

    def recent_outcomes(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Most recent N resolved outcomes (the list sent in contributions)."""
        if limit is None:
            return list(self._outcomes)
        if limit <= 0:
            return []
        return list(self._outcomes[-int(limit):])

    # ── validators ──────────────────────────────────────────────────
    @staticmethod
    def _validate_weights(weights: Mapping[str, float]) -> None:
        if not isinstance(weights, Mapping):
            raise LocalStateError("weights must be a mapping")
        if len(weights) > _MAX_WEIGHTS:
            raise LocalStateError(f"weights cap is {_MAX_WEIGHTS} keys, got {len(weights)}")
        for k, v in weights.items():
            if not isinstance(k, str) or not k:
                raise LocalStateError(f"weight key must be a non-empty string: {k!r}")
            try:
                fv = float(v)
            except (TypeError, ValueError) as e:
                raise LocalStateError(f"weight {k!r} not numeric: {v!r}") from e
            if not math.isfinite(fv):
                raise LocalStateError(f"weight {k!r} is not finite: {fv}")
            if fv < _WEIGHT_MIN or fv > _WEIGHT_MAX:
                raise LocalStateError(
                    f"weight {k!r}={fv} outside [{_WEIGHT_MIN}, {_WEIGHT_MAX}]"
                )

    @staticmethod
    def _validate_learning_rate(lr: float) -> None:
        if not math.isfinite(lr):
            raise LocalStateError(f"learning_rate not finite: {lr}")
        if lr < _LR_MIN or lr > _LR_MAX:
            raise LocalStateError(f"learning_rate {lr} outside [{_LR_MIN}, {_LR_MAX}]")

    @staticmethod
    def _validate_outcomes(outs: Iterable[Mapping[str, Any]]) -> None:
        out_list = list(outs)
        if len(out_list) > _MAX_OUTCOMES:
            raise LocalStateError(f"recent_outcomes cap is {_MAX_OUTCOMES}")
        for i, rec in enumerate(out_list):
            if not isinstance(rec, Mapping):
                raise LocalStateError(f"outcome #{i} is not an object")
            action = rec.get("action")
            if action not in ALLOWED_ACTIONS:
                raise LocalStateError(
                    f"outcome #{i} action {action!r} not in {sorted(ALLOWED_ACTIONS)}"
                )
            outcome = rec.get("outcome")
            if outcome not in ALLOWED_OUTCOMES:
                raise LocalStateError(
                    f"outcome #{i} outcome {outcome!r} not in {sorted(ALLOWED_OUTCOMES)}"
                )
            conf = float(rec.get("confidence", 0.0))
            if not math.isfinite(conf) or conf < _CONF_MIN or conf > _CONF_MAX:
                raise LocalStateError(
                    f"outcome #{i} confidence {conf} outside [{_CONF_MIN}, {_CONF_MAX}]"
                )
