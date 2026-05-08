"""Executor heartbeat — catches "pipeline alive but frozen" states.

Every pipeline run emits a heartbeat to a thread-safe in-process
tracker. Operators consult ``GET /api/admin/ml/heartbeat`` to see:

  * last_pipeline_run_at  — wall-clock of the last decide() call
  * last_signal_at        — wall-clock of the last BUY/SELL verdict
  * signals_1h / holds_1h — rolling 1h counters
  * feature_health_avg    — rolling 1h mean of strategist health scores
  * model_age_hours       — newest reading from the most recent boot
                            receipt for the lane's executor
  * frozen                — heuristic: pipeline ran but produced 0
                            signals AND avg health < 0.3 over the last
                            1h, OR no run in > FREEZE_AFTER_MIN min.

Per-lane state. ``equity`` and ``crypto`` are tracked independently
so a frozen lane is visible in isolation.

This tracker is a SAFETY observability layer — it cannot influence
routing. It only persists in-memory; restarts wipe history (boot
receipts already cover the post-restart fingerprint).
"""
from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Deque, Dict, List, Optional

from services.ml.contracts import Verdict


_LANES = ("equity", "crypto")


def _freeze_after_min() -> float:
    try:
        return float(os.getenv("EXECUTOR_HEARTBEAT_FREEZE_AFTER_MIN", "15"))
    except (TypeError, ValueError):
        return 15.0


def _window_seconds() -> float:
    return 3600.0  # rolling 1h


@dataclass
class _LaneState:
    lane: str
    last_pipeline_run_at: Optional[float] = None
    last_signal_at: Optional[float] = None
    last_decision: Optional[str] = None
    last_blocked_at: Optional[str] = None
    last_reason: Optional[str] = None
    # rolling buffer of (ts_epoch, decision, blocked_at, feature_health)
    events: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=2048))


class _Tracker:
    def __init__(self):
        self._lock = RLock()
        self._lanes: Dict[str, _LaneState] = {
            lane: _LaneState(lane=lane) for lane in _LANES
        }

    def reset(self) -> None:
        """Test helper."""
        with self._lock:
            self._lanes = {lane: _LaneState(lane=lane) for lane in _LANES}

    def record(
        self,
        *,
        lane: str,
        decision: str,
        blocked_at: Optional[str],
        reason: Optional[str],
        feature_health: Optional[float],
        ts_epoch: Optional[float] = None,
    ) -> None:
        if lane not in _LANES:
            return
        ts = ts_epoch if ts_epoch is not None else time.time()
        with self._lock:
            state = self._lanes[lane]
            state.last_pipeline_run_at = ts
            state.last_decision = decision
            state.last_blocked_at = blocked_at
            state.last_reason = reason
            if decision in (Verdict.BUY.value, Verdict.SELL.value):
                state.last_signal_at = ts
            state.events.append({
                "ts": ts,
                "decision": decision,
                "blocked_at": blocked_at,
                "reason": reason,
                "feature_health": feature_health,
            })
            self._prune(state, ts)

    @staticmethod
    def _prune(state: _LaneState, now_ts: float) -> None:
        cutoff = now_ts - _window_seconds()
        # deque can't filter in-place efficiently; rebuild only when
        # the head is stale to keep this cheap.
        while state.events and state.events[0]["ts"] < cutoff:
            state.events.popleft()

    def snapshot(self, *, model_age_by_lane: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        now_ts = time.time()
        freeze_after = _freeze_after_min() * 60.0
        out_lanes: Dict[str, Any] = {}
        with self._lock:
            for lane, state in self._lanes.items():
                self._prune(state, now_ts)
                buy_sell = sum(
                    1 for e in state.events
                    if e["decision"] in (Verdict.BUY.value, Verdict.SELL.value)
                )
                holds = sum(
                    1 for e in state.events
                    if e["decision"] == Verdict.NO_TRADE.value
                )
                signals = len(state.events)  # total pipeline runs in window
                healths = [
                    e["feature_health"] for e in state.events
                    if isinstance(e.get("feature_health"), (int, float))
                ]
                fh_avg = (sum(healths) / len(healths)) if healths else None

                # Top clamp / block reason in the window. Counts NO_TRADE
                # reasons (block reasons) and *_CLAMPED reasons together,
                # since both are degradation signals worth surfacing.
                reason_counts: Dict[str, int] = {}
                for e in state.events:
                    reason = e.get("reason") or ""
                    decision = e.get("decision")
                    if decision == Verdict.NO_TRADE.value or "CLAMPED" in reason:
                        if reason:
                            reason_counts[reason] = reason_counts.get(reason, 0) + 1
                top_reason = None
                top_reason_count = 0
                if reason_counts:
                    top_reason, top_reason_count = max(
                        reason_counts.items(), key=lambda kv: kv[1],
                    )

                # Freeze heuristic.
                last_run = state.last_pipeline_run_at
                no_recent_run = (
                    last_run is None
                    or (now_ts - last_run) > freeze_after
                )
                long_dry = (
                    last_run is not None
                    and buy_sell == 0
                    and (fh_avg is not None and fh_avg < 0.3)
                )
                frozen = bool(no_recent_run or long_dry)
                model_age = (
                    model_age_by_lane.get(lane) if model_age_by_lane else None
                )
                out_lanes[lane] = {
                    "lane": lane,
                    "last_pipeline_run_at": _iso(state.last_pipeline_run_at),
                    "last_signal_at": _iso(state.last_signal_at),
                    "last_decision": state.last_decision,
                    "last_blocked_at": state.last_blocked_at,
                    "last_reason": state.last_reason,
                    "signals_1h": signals,
                    "buy_sell_1h": buy_sell,
                    "holds_1h": holds,
                    "events_1h": signals,
                    "feature_health_avg": fh_avg,
                    "model_age_hours": model_age,
                    "frozen": frozen,
                    "freeze_after_min": _freeze_after_min(),
                    "top_clamp_block_reason": top_reason,
                    "top_clamp_block_count": top_reason_count,
                }
        return {
            "as_of": _iso(now_ts),
            "lanes": out_lanes,
            "any_frozen": any(v["frozen"] for v in out_lanes.values()),
        }


def _iso(ts_epoch: Optional[float]) -> Optional[str]:
    if ts_epoch is None:
        return None
    return datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat()


# ── Module-level singleton ───────────────────────────────────────


_tracker = _Tracker()


def record_pipeline_run(
    *,
    lane: str,
    decision: str,
    blocked_at: Optional[str],
    reason: Optional[str],
    feature_health: Optional[float],
) -> None:
    _tracker.record(
        lane=lane,
        decision=decision,
        blocked_at=blocked_at,
        reason=reason,
        feature_health=feature_health,
    )


def get_snapshot(*, model_age_by_lane: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    return _tracker.snapshot(model_age_by_lane=model_age_by_lane)


def reset() -> None:
    """Test helper."""
    _tracker.reset()


def known_lanes() -> List[str]:
    return list(_LANES)
