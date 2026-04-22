"""Smart Order Router — picks the best broker for a given option
order and dispatches to its adapter.

Current scoring axes (Phase 1 — single broker, so no routing
contest):
  1. Is the broker enabled right now? (`is_options_enabled()`)
  2. Estimated bid-ask spread in cents. Lowest wins.

Phase 2 additions (when Tradier/TastyTrade land):
  * Slippage history — Mongo-backed rolling stats per broker.
  * Commission — per-broker rate card multiplied by qty.
  * Fill probability at limit price (from quote size + mark).

Design choices:
  * **Per-call instantiation** — matches the rest of the broker
    stack. No shared state between requests, no cross-user leaks.
  * **Best-effort spread estimation** — spread is queried in
    parallel and never crashes the route. A broker whose quote
    endpoint 500s just scores worst (we use a sentinel high value)
    so it's considered only when it's the only one enabled.
  * **ODD + auth enforcement lives upstream in `routes/options_trading.py`** —
    the router never runs before the caller has passed that gate.
    Keeping ODD-check out of this module means tests can exercise
    the routing logic without a full user fixture.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from services.brokers.options_adapter import (
    BrokerOptionsAdapter,
    OptionLeg,
    OptionOrder,
)
from services.brokers.registry import get_enabled_adapters
from services.structured_log import log_error, log_warning

logger = logging.getLogger(__name__)

# Sentinel score returned when a broker's spread probe fails. Ranks
# the broker last but keeps it in the candidate list — better to
# route "somewhere" than reject the order because all spread probes
# flaked simultaneously.
_SPREAD_PROBE_FAIL = 1e9


@dataclass
class RoutingDecision:
    provider: str
    adapter: BrokerOptionsAdapter
    estimated_spread: float
    all_candidates: list[tuple[str, float]]  # (provider, spread) rank-desc


async def _estimate_spread(
    adapter: BrokerOptionsAdapter,
    occ_symbol: str,
) -> float:
    """Return an estimated bid-ask spread in dollars for the given
    OCC contract. Lower is better. Never raises.

    Resolution order:
      1. `adapter.try_get_spread(occ_symbol)` — adapter's own quote
         source (Tradier implements this natively).
      2. Tradier cross-broker fallback — if the adapter itself has
         no quote source but a `TRADIER_API_TOKEN` is configured,
         we use Tradier's quote as a proxy spread estimate for
         other brokers. This is fair because OPRA-sourced NBBO is
         the same regardless of routing broker; the spread is a
         property of the contract, not the executor.
      3. Sentinel — adapter has no quote AND Tradier isn't
         configured. The broker stays in the candidate list but
         ranks last.
    """
    try:
        if hasattr(adapter, "try_get_spread"):
            spread = await adapter.try_get_spread(occ_symbol)
            if spread is not None and spread >= 0:
                return float(spread)
    except Exception as exc:
        log_warning(logger, {
            "context": "smart_router_spread",
            "type": type(exc).__name__,
            "error": str(exc),
            "note": f"adapter spread probe failed for {adapter.provider}/{occ_symbol}",
        })

    # Tradier-as-proxy fallback. Keeps the sentinel in place when
    # Tradier isn't configured (fetch_tradier_option_quote returns
    # None in that case — same shape as a failed probe).
    try:
        from services.brokers.tradier_options import fetch_tradier_option_quote
        quote = await fetch_tradier_option_quote(occ_symbol)
        if quote and quote.get("spread") is not None:
            spread = float(quote["spread"])
            if spread >= 0:
                return spread
    except Exception as exc:
        log_warning(logger, {
            "context": "smart_router_spread",
            "type": type(exc).__name__,
            "error": str(exc),
            "note": f"tradier proxy-spread failed for {adapter.provider}/{occ_symbol}",
        })
    return _SPREAD_PROBE_FAIL


class SmartOrderRouter:
    """Chooses the best broker for an option order.

    Usage from a route layer:

        router = SmartOrderRouter()
        decision = await router.pick(occ_symbol="AAPL  261218C00200000")
        order = await decision.adapter.place_option_order(legs, ...)
    """

    async def pick(self, occ_symbol: str) -> RoutingDecision:
        """Return the best-scoring broker right now.

        Raises `RuntimeError` if zero brokers are enabled. Callers
        should map that to HTTP 503 — "no usable broker, try again"
        — since a restart or broker reconnect could flip it.
        """
        enabled = await get_enabled_adapters()
        if not enabled:
            raise RuntimeError(
                "Smart routing failed: no broker currently reports "
                "options enabled. Connect a broker or enable options "
                "in your existing broker account."
            )

        # Fan-out spread probes. Each adapter probe is isolated
        # from the others — one broker's 500 doesn't affect the rest.
        async def _score(name: str, adapter: BrokerOptionsAdapter):
            spread = await _estimate_spread(adapter, occ_symbol)
            return name, adapter, spread

        results = await asyncio.gather(*[
            _score(name, adapter) for name, adapter in enabled.items()
        ])

        # Lowest spread wins. Ties break on alphabetical provider
        # name so outputs are deterministic in tests.
        results.sort(key=lambda r: (r[2], r[0]))
        best_name, best_adapter, best_spread = results[0]

        return RoutingDecision(
            provider=best_name,
            adapter=best_adapter,
            estimated_spread=best_spread,
            all_candidates=[(name, spread) for name, _, spread in results],
        )

    async def route_order(
        self,
        occ_symbol: str,
        legs: list[OptionLeg],
        *,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: Optional[float] = None,
    ) -> tuple[OptionOrder, RoutingDecision]:
        """End-to-end: pick + submit. Returns both the order object
        AND the decision so the route layer can echo which broker
        won the contest back to the UI.
        """
        decision = await self.pick(occ_symbol)
        try:
            order = await decision.adapter.place_option_order(
                legs,
                order_type=order_type,
                time_in_force=time_in_force,
                limit_price=limit_price,
            )
        except Exception as exc:
            log_error(logger, {
                "context": "smart_router_submit",
                "type": type(exc).__name__,
                "error": str(exc),
                "note": f"{decision.provider} order placement failed",
            })
            raise
        return order, decision
