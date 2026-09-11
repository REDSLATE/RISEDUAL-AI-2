"""alpha_session_state — NYSE session + holiday awareness.

Foundation v2.1 pin: "0% intent → broker" on a holiday must not look
like a bug. This module answers one question: "what phase is the US
equity market in RIGHT NOW?" with an explicit holiday reason when
applicable, so the frontend can render a "MARKET CLOSED — Labor Day"
banner instead of a bare "0%" that scares the operator.

Design
------
* No external calendar library. NYSE observed holidays are small
  and stable enough to enumerate. Add new years as they arrive.
* Half-day-session awareness (Black Friday, Christmas Eve on
  applicable years) with a compressed 13:00 ET close.
* Returns a compact dict — the endpoint consumer (React panel)
  decides how to render.
"""
from __future__ import annotations

from datetime import date, datetime, time as _dt_time, timezone
from typing import Optional

# NYSE regular session in US/Eastern.
_OPEN_ET = _dt_time(9, 30)
_CLOSE_ET = _dt_time(16, 0)
_EARLY_CLOSE_ET = _dt_time(13, 0)   # half-days
_PREMARKET_ET = _dt_time(4, 0)
_AFTERHOURS_ET = _dt_time(20, 0)


# 2026 NYSE observed holidays. New Year's Day 2026 falls on Thursday,
# so the observed table for 2026 = the natural dates. Update annually.
_HOLIDAYS_2026: dict[date, str] = {
    date(2026, 1, 1):  "New Year's Day",
    date(2026, 1, 19): "Martin Luther King, Jr. Day",
    date(2026, 2, 16): "Presidents' Day",
    date(2026, 4, 3):  "Good Friday",
    date(2026, 5, 25): "Memorial Day",
    date(2026, 6, 19): "Juneteenth",
    date(2026, 7, 3):  "Independence Day (observed)",  # July 4 is Sat → observed Friday
    date(2026, 9, 7):  "Labor Day",
    date(2026, 11, 26): "Thanksgiving Day",
    date(2026, 12, 25): "Christmas Day",
}

# NYSE early-close days for 2026 (13:00 ET close).
_EARLY_CLOSES_2026: dict[date, str] = {
    date(2026, 7, 2):   "Day before Independence Day (early close)",
    date(2026, 11, 27): "Day after Thanksgiving (early close)",
    date(2026, 12, 24): "Christmas Eve (early close)",
}


def _now_et(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    try:
        import zoneinfo
        return now.astimezone(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:  # pragma: no cover — zoneinfo missing
        # Fallback: subtract 5 hours as an ET approximation.
        from datetime import timedelta
        return (now - timedelta(hours=5)).replace(tzinfo=None)


def get_session_state(now: Optional[datetime] = None) -> dict:
    """Compact session-state dict for the panel.

    Fields:
      * ``phase``          — "regular" | "pre_market" | "after_hours"
                             | "closed_weekend" | "closed_holiday"
                             | "closed_overnight"
      * ``is_market_open`` — True only during regular / early-close hours
      * ``holiday_name``   — populated on holidays and half-days
      * ``next_open_iso``  — best-effort next-open estimate (US/Eastern)
      * ``as_of_iso``      — timestamp the state was computed for
    """
    now_et = _now_et(now)
    today = now_et.date()
    weekday = now_et.weekday()  # Mon=0 .. Sun=6
    t = now_et.time()

    # Holidays first — they take precedence over weekday logic.
    if today in _HOLIDAYS_2026:
        return {
            "phase": "closed_holiday",
            "is_market_open": False,
            "holiday_name": _HOLIDAYS_2026[today],
            "note": (
                f"US equities closed today for "
                f"{_HOLIDAYS_2026[today]}. Alpha equity loop idle by design."
            ),
            "as_of_iso": now_et.isoformat(),
        }

    if weekday >= 5:  # Saturday / Sunday
        return {
            "phase": "closed_weekend",
            "is_market_open": False,
            "holiday_name": None,
            "note": "US equities closed for the weekend.",
            "as_of_iso": now_et.isoformat(),
        }

    # Weekday. Regular vs pre/after depends on whether today is a half-day.
    close_time = _EARLY_CLOSE_ET if today in _EARLY_CLOSES_2026 else _CLOSE_ET
    early_note = _EARLY_CLOSES_2026.get(today)

    if t < _PREMARKET_ET:
        phase = "closed_overnight"
        is_open = False
        note = "Pre-4:00 AM ET overnight window."
    elif t < _OPEN_ET:
        phase = "pre_market"
        is_open = False
        note = "Pre-market session (4:00–9:30 ET)."
    elif t < close_time:
        phase = "regular"
        is_open = True
        note = (
            "Regular session"
            + (f" (half-day close {close_time.strftime('%H:%M')} ET)" if early_note else "")
        )
    elif t < _AFTERHOURS_ET:
        phase = "after_hours"
        is_open = False
        note = (
            f"After-hours (close was {close_time.strftime('%H:%M')} ET"
            + (f" — {early_note}" if early_note else "")
            + ")."
        )
    else:
        phase = "closed_overnight"
        is_open = False
        note = "After 20:00 ET overnight window."

    return {
        "phase": phase,
        "is_market_open": is_open,
        "holiday_name": early_note,
        "note": note,
        "as_of_iso": now_et.isoformat(),
    }


__all__ = ["get_session_state"]
