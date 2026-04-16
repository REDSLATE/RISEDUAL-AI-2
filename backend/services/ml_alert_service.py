"""Tier 1 autonomous action: push alert delivery.

When the CalibrationGate unlocks Tier 1, this service fires VAPID web-push
notifications for high-confidence predictions that are also backed by at least
one confirmed pattern and regime alignment.

Architecture
------------
- Pure async service — no APScheduler dependency.
- Called by :mod:`app.services.ml_orchestrator` after every signal prediction.
- Requires ``VAPID_PRIVATE_KEY``, ``VAPID_PUBLIC_KEY``, ``VAPID_CLAIM_EMAIL``
  environment variables *plus* a ``push_subscriptions`` MongoDB collection
  that stores browser PushSubscription JSON objects.
- Falls back silently if VAPID keys are absent (dev mode).

Gate requirements (Tier 1)
---------------------------
- accuracy   >= 55%
- n_predictions >= 100
- ECE        <  0.15
- confidence >= 60% on this specific signal
- At least one pattern detected
- Regime is "trending_up" or "trending_down" (not sideways)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from risedual_core.schemas.market import FeaturesSnapshot, SignalResult

log = logging.getLogger(__name__)

_VAPID_PRIVATE_KEY: str | None = os.getenv("VAPID_PRIVATE_KEY")
_VAPID_PUBLIC_KEY: str | None = os.getenv("VAPID_PUBLIC_KEY")
_VAPID_CLAIM_EMAIL: str = os.getenv("VAPID_CLAIM_EMAIL", "alerts@risedual.ai")

# Minimum confidence to fire an alert (above and beyond gate accuracy)
_MIN_SIGNAL_CONFIDENCE: float = 0.60

# Regimes that justify sending an alert
_ALERTABLE_REGIMES: frozenset[str] = frozenset({"trending_up", "trending_down"})


# ── Pattern detection summary ─────────────────────────────────────────────────


def _detected_patterns(snapshot: FeaturesSnapshot) -> list[str]:
    """Return the names of all patterns flagged True on this snapshot."""
    pattern_fields = {
        "double_bottom": snapshot.pattern_double_bottom,
        "bullish_engulfing": snapshot.pattern_bullish_engulfing,
        "bearish_engulfing": snapshot.pattern_bearish_engulfing,
        "bull_flag": snapshot.pattern_bull_flag,
        "rsi_divergence": snapshot.pattern_rsi_divergence,
        "macd_crossover": snapshot.pattern_macd_crossover,
        "volume_surge": snapshot.pattern_volume_surge,
        "head_and_shoulders": snapshot.pattern_head_and_shoulders,
    }
    return [name for name, val in pattern_fields.items() if val]


# ── VAPID push delivery ───────────────────────────────────────────────────────


async def _send_vapid_push(
    subscription_info: dict[str, Any],
    payload: dict[str, Any],
    http_client: httpx.AsyncClient,
) -> bool:
    """Send a single VAPID push notification.

    Parameters
    ----------
    subscription_info:
        Browser ``PushSubscription`` JSON (endpoint + keys).
    payload:
        Notification body dict — will be JSON-serialised.
    http_client:
        Shared httpx client for connection pooling.

    Returns
    -------
    bool
        ``True`` if the push endpoint acknowledged with 2xx.
    """
    try:
        # py_vapid is optional — import lazily to avoid hard dependency
        from py_vapid import Vapid  # type: ignore[import-untyped]
        from py_vapid.utils import b64urlencode  # type: ignore[import-untyped]
    except ImportError:
        log.warning(
            "[ml_alert] py_vapid not installed — skipping push. "
            "Install with: pip install py-vapid"
        )
        return False

    if not _VAPID_PRIVATE_KEY or not _VAPID_PUBLIC_KEY:
        log.debug("[ml_alert] VAPID keys not configured — skipping push (dev mode).")
        return False

    try:
        vapid = Vapid.from_string(private_key=_VAPID_PRIVATE_KEY)
        endpoint: str = subscription_info["endpoint"]
        claims = {
            "sub": f"mailto:{_VAPID_CLAIM_EMAIL}",
            "aud": endpoint.rsplit("/", 1)[0],
        }
        headers = vapid.sign(claims)
        headers["Content-Type"] = "application/json"
        headers["TTL"] = "86400"

        body = json.dumps(payload).encode()
        resp = await http_client.post(endpoint, content=body, headers=headers, timeout=10.0)
        if resp.status_code in (200, 201, 202):
            return True
        log.warning(
            "[ml_alert] Push endpoint returned %d for %s",
            resp.status_code,
            endpoint[:60],
        )
        return False

    except Exception as exc:  # noqa: BLE001
        log.warning("[ml_alert] Push delivery failed: %s", exc)
        return False


# ── Alert composition ─────────────────────────────────────────────────────────


def _build_alert_payload(
    ticker: str,
    signal: SignalResult,
    snapshot: FeaturesSnapshot,
    patterns: list[str],
) -> dict[str, Any]:
    """Compose the notification JSON body."""
    direction_emoji = "📈" if str(signal.direction.value) == "up" else "📉"
    pattern_str = ", ".join(patterns) if patterns else "no pattern"
    return {
        "title": f"RISEDUAL Signal — {ticker} {direction_emoji}",
        "body": (
            f"{ticker} → {signal.direction.value.upper()} "
            f"({signal.confidence:.0%} confidence)\n"
            f"Patterns: {pattern_str}\n"
            f"RSI: {snapshot.rsi:.1f}  |  ATR: {snapshot.atr:.2f}"
        ),
        "tag": f"risedual-signal-{ticker}",
        "data": {
            "ticker": ticker,
            "direction": signal.direction.value,
            "confidence": signal.confidence,
            "patterns": patterns,
            "prediction_id": signal.prediction_id,
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────


async def maybe_send_alert(
    ticker: str,
    signal: SignalResult,
    snapshot: FeaturesSnapshot,
    regime: str,
    db: Any,
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """Fire a push alert if all Tier 1 signal-level conditions are met.

    Gate-level conditions (accuracy / n / ECE) are checked by the orchestrator
    before calling this function.  This function checks only the per-signal
    conditions:

    - confidence >= 60%
    - at least one pattern detected
    - regime is trending (not sideways)

    Parameters
    ----------
    ticker:
        Trading symbol, e.g. ``"TSLA"``.
    signal:
        Completed :class:`SignalResult` from the signal model.
    snapshot:
        Enriched :class:`FeaturesSnapshot` (Phase 2 schema_version=2).
    regime:
        Current market regime label.
    db:
        Motor AsyncIOMotorDatabase handle.
    http_client:
        Optional shared httpx client; a new client is created if omitted.

    Returns
    -------
    bool
        ``True`` if at least one alert was dispatched.
    """
    # ── Per-signal gate ──────────────────────────────────────────────────────
    if signal.confidence < _MIN_SIGNAL_CONFIDENCE:
        log.debug(
            "[ml_alert] Confidence %.2f < %.2f — skipping alert for %s",
            signal.confidence,
            _MIN_SIGNAL_CONFIDENCE,
            ticker,
        )
        return False

    patterns = _detected_patterns(snapshot)
    if not patterns:
        log.debug("[ml_alert] No patterns detected on %s — skipping alert.", ticker)
        return False

    if regime not in _ALERTABLE_REGIMES:
        log.debug(
            "[ml_alert] Regime %r not alertable — skipping alert for %s.",
            regime,
            ticker,
        )
        return False

    # ── Load subscriptions ───────────────────────────────────────────────────
    try:
        subs_coll = db["push_subscriptions"]
        subs = await subs_coll.find({"active": True}).to_list(length=1000)
    except Exception as exc:  # noqa: BLE001
        log.warning("[ml_alert] Failed to load push subscriptions: %s", exc)
        return False

    if not subs:
        log.debug("[ml_alert] No active push subscriptions — nothing to send.")
        return False

    payload = _build_alert_payload(ticker, signal, snapshot, patterns)

    # ── Dispatch ─────────────────────────────────────────────────────────────
    _own_client = http_client is None
    client = http_client or httpx.AsyncClient()
    dispatched = 0

    try:
        for sub in subs:
            ok = await _send_vapid_push(
                subscription_info=sub.get("subscription", {}),
                payload=payload,
                http_client=client,
            )
            if ok:
                dispatched += 1
    finally:
        if _own_client:
            await client.aclose()

    if dispatched:
        log.info(
            "[ml_alert] Sent %d alert(s) for %s → %s (%.0f%% conf, patterns: %s)",
            dispatched,
            ticker,
            signal.direction.value,
            signal.confidence * 100,
            ", ".join(patterns),
        )

    return dispatched > 0
