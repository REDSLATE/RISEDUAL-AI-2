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


@dataclass
class SpreadRoutingDecision:
    """Separate type so route-layer consumers can destructure spread
    decisions without branching on an optional 'spread_per_leg' field.
    """
    provider: str
    adapter: BrokerOptionsAdapter
    estimated_spread: float             # aggregate across legs
    spread_per_leg: list[float]
    all_candidates: list[tuple[str, float]]


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

    # ── Spread routing ───────────────────────────────────────────

    async def pick_for_spread(
        self, occ_symbols: list[str],
    ) -> SpreadRoutingDecision:
        """Pick the best broker for a multi-leg spread.

        Eligibility narrowing:
          1. Broker must be enabled (`is_options_enabled=True`).
          2. Broker's adapter must declare `supports_multileg=True`.
             Quote-only adapters like Tradier are filtered out here —
             the smart router will never pick a broker that can't
             actually submit the order.

        Scoring: sum of per-leg estimated spreads. Aggregated spread
        is the right proxy for slippage on a multi-leg order since
        the legs fill together, not independently.

        Raises `RuntimeError` when zero multileg-capable brokers are
        enabled — route-layer maps to 503. This is DIFFERENT from
        `pick()`'s raise: "no broker" vs "no broker that can do
        spreads", so the 503 message carries the distinction.
        """
        all_enabled = await get_enabled_adapters()
        mleg = {
            name: adapter for name, adapter in all_enabled.items()
            if getattr(adapter, "supports_multileg", False)
        }
        if not mleg:
            total = len(all_enabled)
            raise RuntimeError(
                "Smart spread routing failed: "
                + (
                    f"{total} broker(s) enabled but none support multi-leg orders."
                    if total else
                    "no broker currently reports options enabled."
                )
            )

        async def _score(name: str, adapter: BrokerOptionsAdapter):
            per_leg = await asyncio.gather(*[
                _estimate_spread(adapter, sym) for sym in occ_symbols
            ])
            per_leg = [float(s) for s in per_leg]
            # If ANY leg hits the sentinel, the aggregate should also
            # feel sentinel-grade so this broker ranks last — summing
            # N sentinels would mask partial probe failures.
            if any(s >= _SPREAD_PROBE_FAIL for s in per_leg):
                return name, adapter, _SPREAD_PROBE_FAIL, per_leg
            return name, adapter, sum(per_leg), per_leg

        results = await asyncio.gather(*[
            _score(name, adapter) for name, adapter in mleg.items()
        ])
        results.sort(key=lambda r: (r[2], r[0]))
        best_name, best_adapter, best_agg, best_per_leg = results[0]

        return SpreadRoutingDecision(
            provider=best_name,
            adapter=best_adapter,
            estimated_spread=best_agg,
            spread_per_leg=best_per_leg,
            all_candidates=[(name, agg) for name, _, agg, _ in results],
        )

    async def route_spread(
        self,
        occ_symbols: list[str],
        legs: list[OptionLeg],
        *,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: Optional[float] = None,
    ) -> tuple[OptionOrder, SpreadRoutingDecision]:
        """End-to-end: pick multileg-capable broker + submit spread.

        Caller passes the per-leg OCC symbols alongside the OptionLeg
        list so the router can estimate spreads without rebuilding
        the symbols. They should match index-for-index with `legs`.
        """
        if len(occ_symbols) != len(legs):
            raise ValueError(
                f"occ_symbols ({len(occ_symbols)}) must match legs ({len(legs)})"
            )
        decision = await self.pick_for_spread(occ_symbols)
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
                "note": f"{decision.provider} spread placement failed",
            })
            raise
        return order, decision
