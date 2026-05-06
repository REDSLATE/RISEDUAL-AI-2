"""Shelly ingest adapter — feeds resolved paper trades into the
learning core so her regime-cluster memory accumulates over time.

Patent M (Alpha) — observation-side ingestion.

This adapter sits *between* the trade closers and the learning
core. Its only job is to translate a closed paper-trade row into
a ``RegimeLabeledMemory`` and pass it to
``add_and_persist_memory``. It does NOT decide trades, place
orders, or alter close behaviour — every call site wraps it in
``try/except`` so a Shelly-side hiccup can never break a close.

Hard rules
----------
* Default-OFF behind ``LEARNING_CORE_INGEST_ENABLED``. Operator
  flips it on only after they're ready to start letting Shelly
  accumulate state. Until then this adapter is a no-op.
* If a current macro snapshot can't be resolved, the adapter
  skips ingestion (returns ``"no_macro"``) rather than feeding
  Shelly fabricated data.
* Direction must canonicalise to ``LONG`` or ``SHORT``. Anything
  else is rejected at the canonical-engine boundary anyway, but
  we short-circuit here so we don't waste a Mongo write.
* Macro-at-entry approximation: until the trade row stamps macro
  at entry time, we use *close-time* macro as the regime
  fingerprint. For paper trades that close within hours/days
  this is usually fine (regime fingerprints are categorical
  buckets) but operators should flip on macro-at-entry stamping
  before this matters for IP-grade analysis.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services.auto_regime_tagger import RawMacroData, RegimeTagger
from services.regime_clustering_layer import RegimeLabeledMemory


logger = logging.getLogger(__name__)


# Map fred_service indicator IDs → RawMacroData fields. Centralised
# here so a future expansion of the macro feed only needs one diff.
_MACRO_FIELD_MAP = {
    "VIXCLS": "vix",
    "DGS2": "yield_2y",
    "DGS10": "yield_10y",
    "DTWEXBGS": "dxy",
    "BAMLH0A0HYM2": "hy_oas_bp",
    "BAMLC0A0CM": "ig_oas_bp",
    "WALCL": "liquidity_z",  # Fed balance sheet z-score proxy
}


def _ingest_enabled() -> bool:
    return os.getenv("LEARNING_CORE_INGEST_ENABLED", "false").lower() == "true"


async def _resolve_current_macro() -> Optional[RawMacroData]:
    """Best-effort current macro snapshot.

    Reads the cached output of ``fred_service.get_macro_indicators``
    if available. Returns ``None`` if the cache is cold — better
    to skip ingestion than feed Shelly garbage.

    The cache is populated by the live admin/dashboard tiles so on
    a healthy pod this hits at least every minute.
    """
    try:
        from services.fred_service import CACHE
        cached = CACHE.get("macro_indicators")
        if not cached:
            return None
        # ``CACHE`` value shape: ``(cached_at_dt, payload_dict)``.
        if isinstance(cached, tuple) and len(cached) == 2:
            payload = cached[1]
        elif isinstance(cached, dict):
            payload = cached
        else:
            return None
        if not isinstance(payload, dict):
            return None

        out = RawMacroData(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        for ind in payload.get("indicators", []) or []:
            field = _MACRO_FIELD_MAP.get(ind.get("id"))
            if field is None:
                continue
            val = ind.get("latest_value")
            if val is None:
                continue
            try:
                setattr(out, field, float(val))
            except (TypeError, ValueError):
                continue

        # If we couldn't extract even one numeric, treat as a miss.
        if all(getattr(out, f) is None for f in _MACRO_FIELD_MAP.values()):
            return None
        return out
    except Exception as exc:
        logger.debug("shelly_ingest: macro resolve failed: %s", exc)
        return None


def _canonical_for_ingest(direction: str) -> Optional[str]:
    """Local canonicalisation: only LONG/SHORT are eligible for
    Shelly's regime memory bank. HOLD / UNKNOWN / anything else
    returns ``None`` — caller skips ingestion.

    We don't import ``canonical_ai_dir`` here because the canonical
    engine itself runs that check on ingest; we just need a coarse
    pre-filter to avoid the round-trip.
    """
    if not direction:
        return None
    d = str(direction).upper().strip()
    if d in ("LONG", "BUY"):
        return "LONG"
    if d in ("SHORT", "SELL"):
        return "SHORT"
    return None


def _holding_days(opened_at: Any, closed_at: Any) -> int:
    """Best-effort integer days between open and close. Defaults to
    0 if either timestamp is missing or malformed — Shelly's
    bucketing tolerates 0 (same-day close)."""
    try:
        if isinstance(opened_at, datetime) and isinstance(closed_at, datetime):
            delta = closed_at - opened_at
            return max(0, int(delta.total_seconds() // 86400))
    except Exception:
        pass
    return 0


def paper_trade_to_memory(
    trade: dict[str, Any],
    macro: RawMacroData,
) -> Optional[RegimeLabeledMemory]:
    """Pure transform: closed paper trade → ``RegimeLabeledMemory``.

    Returns ``None`` if the trade is missing fields Shelly needs
    (entry/exit price, direction). Callers should treat ``None``
    as "skip this trade" — no exception, no log noise.
    """
    direction = _canonical_for_ingest(trade.get("direction") or trade.get("side"))
    if direction is None:
        return None

    entry_price = trade.get("entry_price") or trade.get("price")
    exit_price = trade.get("exit_price")
    if entry_price is None or exit_price is None:
        return None
    try:
        ep = float(entry_price)
        xp = float(exit_price)
    except (TypeError, ValueError):
        return None
    if ep <= 0 or xp <= 0:
        return None

    # Pnl: use the trade's own ``pnl_pct`` when present (the closer
    # already computes it), else derive from prices. Direction
    # flips the sign for SHORT.
    if trade.get("pnl_pct") is not None:
        try:
            pnl_pct = float(trade["pnl_pct"])
        except (TypeError, ValueError):
            pnl_pct = _derive_pnl_pct(direction, ep, xp)
    else:
        pnl_pct = _derive_pnl_pct(direction, ep, xp)

    fingerprint = RegimeTagger().generate_fingerprint(macro, macro_history=None)

    # Memory ID: prefer the trade's own id; fall back to a UUID.
    mid = (
        trade.get("memory_id")
        or trade.get("trade_id")
        or trade.get("_id")
        or str(uuid.uuid4())
    )

    timestamp = trade.get("closed_at") or trade.get("opened_at") or datetime.now(timezone.utc)
    if isinstance(timestamp, datetime):
        ts_iso = timestamp.isoformat()
    else:
        ts_iso = str(timestamp)

    return RegimeLabeledMemory(
        memory_id=str(mid),
        timestamp=ts_iso,
        ticker=str(trade.get("symbol") or trade.get("ticker") or "?"),
        direction=direction,
        entry_price=ep,
        exit_price=xp,
        pnl_pct=pnl_pct,
        holding_days=_holding_days(
            trade.get("opened_at"), trade.get("closed_at"),
        ),
        regime_at_entry=fingerprint,
    )


def _derive_pnl_pct(direction: str, entry: float, exit_: float) -> float:
    raw = (exit_ - entry) / entry * 100.0
    return raw if direction == "LONG" else -raw


async def feed_shelly_from_closed_trade(
    db: Any,
    trade: dict[str, Any],
) -> dict[str, Any]:
    """Public ingestion entry-point used by trade closers.

    Returns an envelope describing what happened. NEVER raises —
    if any step fails the close-path caller still completes.

    Envelope keys::

        ok: bool
        reason: optional str  (when ok=False)
        memory_id: optional str
    """
    if not _ingest_enabled():
        return {"ok": False, "reason": "env_flag_off"}

    try:
        macro = await _resolve_current_macro()
        if macro is None:
            return {"ok": False, "reason": "no_macro"}

        memory = paper_trade_to_memory(trade, macro)
        if memory is None:
            return {"ok": False, "reason": "trade_not_eligible"}

        from services.learning_core_service import add_and_persist_memory
        result = await add_and_persist_memory(db, memory)
        return {
            "ok": True,
            "memory_id": memory.memory_id,
            "regime_cluster_id": result.get("regime_cluster_id"),
        }
    except Exception as exc:
        logger.warning("shelly_ingest: feed failed: %s", exc)
        return {"ok": False, "reason": str(exc)}
