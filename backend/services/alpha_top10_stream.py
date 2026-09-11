"""alpha_top10_stream — 60s cadence trigger loop over the top-10 watchlist.

The 5-min ``alpha_day_trader`` tick scans the wider universe and
seeds a top-10 watchlist keyed on opportunity score. This service
re-evaluates active setups on those 10 symbols every 60 seconds so
intents fire on broker-fresh quotes instead of on 5-min-old
snapshots.

It re-uses the exact same execution path as the 5-min tick — same
dedup, same execution-quote gate, same seat / risk / roadguard /
chasing-filter chain — by delegating to
``run_alpha_day_trader_tick(db, symbols_only=<top10>)``. The
``symbols_only`` mode skips the universe scan + ranking + pattern
discovery portion of the tick and runs only the active-setup
trigger loop against the passed symbols.

Design notes
------------
* No new observability plumbing. The tick lifecycle already writes
  to the hot store; the ``streaming`` / ``tick_tag`` fields in the
  summary log let the operator tell 5-min vs 60s ticks apart.
* No new broker code. Alpha's execution-quote gate already prefers
  the broker for tradable-price context (see
  ``services.provider_policy.fetch_execution_quote``). Extending
  broker-first to MooMoo is handled at the market-data layer, not
  here.
* When the watchlist is empty (fresh boot, before the first 5-min
  tick has seeded it), this service returns ``{"skipped": True,
  "reason": "no_watchlist"}`` — never blows up.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def run_alpha_top10_stream_tick(db: Any) -> dict:
    """One 60s stream tick. Returns a summary dict for logging."""
    if db is None:
        return {"skipped": True, "reason": "no_db"}

    from services import alpha_top10_state
    from services.alpha_day_trader import run_alpha_day_trader_tick

    # 2026-09-11 — Prefer the funnel's ARMED list. When the funnel
    # hasn't run yet (fresh boot before the first 5-min tick) fall
    # back to the legacy top-10 watchlist so the stream still has
    # work to do.
    try:
        from services import alpha_funnel
        promoted = alpha_funnel.get_promoted_symbols()
    except Exception:  # noqa: BLE001
        promoted = []

    if promoted:
        symbols = promoted
        source = "funnel_armed"
    else:
        snap = alpha_top10_state.get_top10()
        symbols = list(snap.get("symbols") or [])
        source = "top10_legacy"

    if not symbols:
        return {"skipped": True, "reason": "no_watchlist"}

    tag = f"stream:{source}:{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    return await run_alpha_day_trader_tick(
        db, symbols_only=set(symbols), tick_tag=tag,
    )


__all__ = ["run_alpha_top10_stream_tick"]
