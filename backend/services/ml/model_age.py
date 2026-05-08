"""Stale-model protection.

A `.joblib` artifact older than ``MAX_MODEL_AGE_HOURS`` (default 72)
is considered stale. Any lane that depends on a stale artifact is
forced into ``MODEL_STALE_OBSERVE_ONLY`` — i.e. the boot receipt is
flipped to ``ready=False`` so the base layer returns NO_TRADE for
every call. Shadow / observation paths still run.

This is a hard architectural seal: there is NO override flag for
operators to "force-use" a stale model. The only way to clear the
condition is to retrain (touch the file's mtime) or unset the env
var pointing at the artifact.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _max_age_hours() -> float:
    try:
        return float(os.getenv("MAX_MODEL_AGE_HOURS", "72"))
    except (TypeError, ValueError):
        return 72.0


def get_artifact_age_hours(path: Optional[str]) -> Optional[float]:
    """Return the artifact's age in hours, or None if path is unset
    or unreadable. NEVER raises."""
    if not path:
        return None
    try:
        p = Path(path)
        if not p.exists():
            return None
        mtime = p.stat().st_mtime
        age_seconds = max(0.0, time.time() - mtime)
        return age_seconds / 3600.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("[model_age] stat failed for %s: %s", path, exc)
        return None


def evaluate_artifact(path: Optional[str]) -> Tuple[bool, Optional[float], float]:
    """Return ``(stale, age_hours, max_age_hours)``.

    ``stale`` is True only when the file exists AND its age exceeds
    the threshold. A missing path returns ``(False, None, max)`` —
    upstream code already treats unset/missing artifacts as fail-fast
    via :class:`LaneDisabledError`.
    """
    max_age = _max_age_hours()
    age = get_artifact_age_hours(path)
    if age is None:
        return False, None, max_age
    return age > max_age, age, max_age


STALE_REASON_PREFIX = "MODEL_STALE_OBSERVE_ONLY"


def stale_reason(age_hours: Optional[float], max_age_hours: float) -> str:
    age_str = f"{age_hours:.2f}h" if age_hours is not None else "unknown"
    return f"{STALE_REASON_PREFIX}:age={age_str}>max={max_age_hours:.0f}h"
