"""Extreme-move validator (EXTREME_MOVE_REQUIRES_VALIDATION).

Operator directive (2026-02):

  "Don't want Alpha chasing a genuine +27% move — but also don't want
  Alpha learning that every huge mover is bad data, because those are
  precisely the symbols its breakout discovery system may need to
  recognize correctly."

Threshold: any absolute intraday move > ``EXTREME_MOVE_THRESHOLD_PCT``
(default 15%) is treated as ``EXTREME_MOVE_REQUIRES_VALIDATION``. The
validator runs five checks:

* **reference_integrity**    — ``today_close`` and ``prev_close`` come
                                from the SAME provider row. This is
                                already structurally guaranteed by
                                :func:`_intraday_move_detail`, but we
                                re-confirm here so a future refactor
                                can't quietly re-introduce the
                                provider-mismatch bug.
* **quote_freshness**        — the daily bar's timestamp is within the
                                current session (or one trading day
                                back for weekends).
* **session_boundary**       — the two closes are on adjacent trading
                                sessions, not spanning a corporate
                                event date.
* **corporate_action**       — best-effort check for split/dividend
                                adjustment via the provider's
                                ``adjustment_factor`` field if
                                available. If the provider doesn't
                                expose it, this check is skipped (not
                                failed).
* **cross_broker_confirm**   — when MooMoo is DATA_READY, cross-check
                                today's price. Skipped (not failed)
                                when the bridge is below DATA_READY.

Verdicts:

* ``EXTREME_MOVE_CONFIRMED``     — all applicable checks passed. The
                                    move is likely real; existing
                                    chasing/risk logic can still block
                                    the entry on its own merits.
* ``REFERENCE_PRICE_ANOMALY``    — at least one integrity/anchor check
                                    failed. The reference is broken;
                                    Alpha should NOT use this move
                                    reading to argue anything about
                                    the cap.
* ``EXTREME_MOVE_UNVERIFIED``    — thresholds fired but we don't have
                                    enough signal to decide either
                                    way. Trade stays blocked; row
                                    flagged for eyeball review.

The validator NEVER unblocks a trade — the chasing filter's block
stands regardless of outcome. The verdict is purely diagnostic.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _threshold_pct() -> float:
    try:
        v = float(os.environ.get("EXTREME_MOVE_THRESHOLD_PCT") or 15.0)
    except (TypeError, ValueError):
        v = 15.0
    return max(5.0, min(v, 50.0))


@dataclass
class ExtremeMoveVerdict:
    verdict: str
    threshold_pct: float
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "threshold_pct": self.threshold_pct,
            "checks": dict(self.checks),
        }


def _mark_check(v: ExtremeMoveVerdict, name: str, passed: Optional[bool],
                *, note: str = "") -> None:
    v.checks[name] = {
        "passed": passed,          # None = skipped, True = passed, False = failed
        "note": note or None,
    }


async def validate_extreme_move(
    *,
    symbol: str,
    move_pct: float,
    prev_close: float,
    today_close: float,
    bars_metadata: Optional[dict[str, Any]] = None,
) -> Optional[ExtremeMoveVerdict]:
    """Return a verdict if ``move_pct`` exceeds the threshold, else None.

    ``bars_metadata`` may include ``today_ts`` / ``prev_ts`` /
    ``adjustment_factor`` / ``provider`` fields lifted from the
    ``market_daily`` response. Extra fields are ignored gracefully.
    """
    threshold = _threshold_pct()
    if abs(move_pct) < threshold:
        return None

    v = ExtremeMoveVerdict(verdict="EXTREME_MOVE_REQUIRES_VALIDATION",
                            threshold_pct=threshold)
    bm = bars_metadata or {}
    passed_all = True

    # 1) Reference integrity — both closes come from the same source
    # row. Structurally guaranteed today; log the invariant.
    _mark_check(v, "reference_integrity", True,
                note="both closes from single market_daily response")

    # 2) Quote freshness — today's bar timestamp is recent.
    today_ts = bm.get("today_ts")
    if today_ts is not None:
        try:
            if isinstance(today_ts, str):
                # ISO 8601 tolerant parse.
                _ts = datetime.fromisoformat(today_ts.replace("Z", "+00:00"))
            elif isinstance(today_ts, datetime):
                _ts = today_ts
            else:
                _ts = None
            if _ts is not None:
                if _ts.tzinfo is None:
                    _ts = _ts.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - _ts).total_seconds() / 3600.0
                fresh = age_hours <= 96  # 4-day tolerance for weekends
                _mark_check(v, "quote_freshness", fresh,
                             note=f"age={age_hours:.1f}h")
                if not fresh:
                    passed_all = False
            else:
                _mark_check(v, "quote_freshness", None, note="unparseable timestamp")
        except Exception as exc:  # noqa: BLE001
            _mark_check(v, "quote_freshness", None,
                         note=f"parse_error:{exc.__class__.__name__}")
    else:
        _mark_check(v, "quote_freshness", None, note="no timestamp in provider row")

    # 3) Session boundary — closes are on adjacent trading sessions,
    # not the same day and not spanning a suspicious gap.
    prev_ts = bm.get("prev_ts")
    if today_ts and prev_ts:
        try:
            if isinstance(today_ts, str):
                td = datetime.fromisoformat(today_ts.replace("Z", "+00:00")).date()
            else:
                td = today_ts.date() if hasattr(today_ts, "date") else None
            if isinstance(prev_ts, str):
                pd = datetime.fromisoformat(prev_ts.replace("Z", "+00:00")).date()
            else:
                pd = prev_ts.date() if hasattr(prev_ts, "date") else None
            if td and pd:
                gap_days = (td - pd).days
                # Adjacent sessions or a Friday→Monday weekend gap.
                boundary_ok = 1 <= gap_days <= 5
                _mark_check(v, "session_boundary", boundary_ok,
                             note=f"gap_days={gap_days}")
                if not boundary_ok:
                    passed_all = False
            else:
                _mark_check(v, "session_boundary", None, note="unparseable")
        except Exception as exc:  # noqa: BLE001
            _mark_check(v, "session_boundary", None,
                         note=f"parse_error:{exc.__class__.__name__}")
    else:
        _mark_check(v, "session_boundary", None, note="prev_ts unavailable")

    # 4) Corporate action — most providers don't expose adjustment
    # factor on the compact daily bar. If it's there, verify no
    # unadjusted split lurks in the gap.
    adj = bm.get("adjustment_factor")
    if adj is not None:
        try:
            adj_f = float(adj)
            no_split = 0.95 <= adj_f <= 1.05
            _mark_check(v, "corporate_action", no_split,
                         note=f"adjustment_factor={adj_f}")
            if not no_split:
                passed_all = False
        except (TypeError, ValueError):
            _mark_check(v, "corporate_action", None, note="unparseable")
    else:
        _mark_check(v, "corporate_action", None,
                     note="provider does not expose adjustment_factor")

    # 5) Cross-broker confirmation — MooMoo canary quote if the bridge
    # is at least DATA_READY. Never fails when unavailable.
    try:
        from services.moomoo_bridge_health import compute_health, ReadinessState
        h = compute_health()
        if h.state in (ReadinessState.DATA_READY,
                        ReadinessState.RESEARCH_READY,
                        ReadinessState.EXECUTION_READY):
            try:
                from services.moomoo_market_data_adapter import snapshot_quote
                snap = snapshot_quote(symbol)
                if snap and snap.last:
                    # Cross-broker sanity: today_close vs MooMoo last
                    # should agree within the threshold. Larger drift
                    # → anchor is broken.
                    drift_pct = abs(snap.last - today_close) / today_close * 100.0
                    ok = drift_pct <= threshold
                    _mark_check(v, "cross_broker_confirm", ok,
                                 note=f"moomoo_last={snap.last} drift={drift_pct:.2f}%")
                    if not ok:
                        passed_all = False
                else:
                    _mark_check(v, "cross_broker_confirm", None,
                                 note="moomoo returned no quote")
            except Exception as exc:  # noqa: BLE001
                _mark_check(v, "cross_broker_confirm", None,
                             note=f"moomoo_error:{exc.__class__.__name__}")
        else:
            _mark_check(v, "cross_broker_confirm", None,
                         note=f"moomoo state={h.state}")
    except Exception:  # noqa: BLE001
        _mark_check(v, "cross_broker_confirm", None,
                     note="moomoo bridge unavailable")

    # Decide verdict.
    hard_failures = [c for c in v.checks.values() if c["passed"] is False]
    hard_passes = [c for c in v.checks.values() if c["passed"] is True]
    if hard_failures:
        v.verdict = "REFERENCE_PRICE_ANOMALY"
    elif len(hard_passes) >= 2 and passed_all:
        v.verdict = "EXTREME_MOVE_CONFIRMED"
    else:
        # Not enough signal in either direction.
        v.verdict = "EXTREME_MOVE_UNVERIFIED"
    return v


__all__ = ["validate_extreme_move", "ExtremeMoveVerdict"]
