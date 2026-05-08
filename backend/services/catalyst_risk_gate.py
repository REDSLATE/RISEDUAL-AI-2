"""
Pure catalyst risk gate — given a signal + snapshot, returns
whether to allow the trade and what size multiplier to apply.

No I/O. No dataclass. The caller (IP contract, manual order
guard, conviction service) passes in the already-fetched
snapshot. Keeps this function trivially testable and reusable
across equity / options paths.

Operating rules (pinned by tests)
--------------------------------
* ``event_risk=restricted`` → ``allow=False`` (hard block).
* ``event_risk=elevated`` + sentiment aligned with action →
  ``size_multiplier=0.75`` (mild de-risk).
* ``event_risk=elevated`` + sentiment OPPOSED to action →
  ``size_multiplier=0.50`` (stronger de-risk; the market knows
  something the model may not).
* ``event_risk=normal`` → no-op.
* ``catalyst_snapshot=None`` → no-op with ``NO_CATALYST_DATA``
  reason (fresh-deploy symbols, data outage, etc. must not block
  trading — fail-safe toward unaltered sizing).
"""
from __future__ import annotations

from typing import Any


def catalyst_risk_gate(
    signal: dict[str, Any],
    catalyst_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if not catalyst_snapshot:
        return {
            "allow": True,
            "size_multiplier": 1.0,
            "reason": "NO_CATALYST_DATA",
        }

    event_risk = catalyst_snapshot.get("event_risk", "normal")
    shock = catalyst_snapshot.get("news_shock", {}) or {}
    sentiment_label = shock.get("sentiment_label", "unknown")
    action = str(signal.get("action", "")).upper()

    if event_risk == "restricted":
        return {
            "allow": False,
            "size_multiplier": 0.0,
            "reason": "CATALYST_RESTRICTED_NEWS_SHOCK",
            "shock_state": shock.get("shock_state"),
            "news_zscore": shock.get("news_zscore"),
        }

    if event_risk == "elevated":
        aligned = (
            (action == "BUY" and sentiment_label == "bullish")
            or (action == "SELL" and sentiment_label == "bearish")
        )
        if aligned:
            return {
                "allow": True,
                "size_multiplier": 0.75,
                "reason": "CATALYST_ELEVATED_ALIGNED",
                "shock_state": shock.get("shock_state"),
                "news_zscore": shock.get("news_zscore"),
            }
        return {
            "allow": True,
            "size_multiplier": 0.5,
            "reason": "CATALYST_ELEVATED_UNALIGNED_SIZE_REDUCED",
            "shock_state": shock.get("shock_state"),
            "news_zscore": shock.get("news_zscore"),
        }

    return {
        "allow": True,
        "size_multiplier": 1.0,
        "reason": "CATALYST_NORMAL",
    }
