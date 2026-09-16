"""Alpha Core v2 — minimal, independent discovery.

Intentionally simple for Milestone 1: universe → fresh snapshot → pattern →
candidate → rank. Reuses the shared market-data layer but has NO dependency
on Legacy's scanner or tick loop. Discernment is deliberately basic; it gets
improved only after the execution lifecycle is trustworthy.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol

from services.alpha_core_v2.contracts import Candidate, Snapshot

logger = logging.getLogger(__name__)


class DataSource(Protocol):
    async def snapshot(self, symbol: str) -> Optional[Snapshot]: ...


class MarketDataPoolSource:
    """Default source over the shared market-data pool (not Legacy)."""

    async def snapshot(self, symbol: str) -> Optional[Snapshot]:
        try:
            from services import market_data_pool as mdp
            quote = await mdp.market_quote(symbol)
            bars = await mdp.market_daily(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[core-v2:discovery] data fetch failed %s: %s", symbol, exc)
            return None
        if not quote:
            return None
        mark = float(quote.get("price") or quote.get("last") or 0.0)
        if mark <= 0:
            return Snapshot(symbol=symbol, mark=0.0, usable=False,
                            reason="no_mark_price")
        prev_close = 0.0
        rvol = 1.0
        if bars and len(bars) >= 2:
            try:
                prev_close = float(bars[-2].get("close") or 0.0)
                vols = [float(b.get("volume") or 0.0) for b in bars[-20:]]
                avg_vol = sum(vols) / len(vols) if vols else 0.0
                today_vol = float(bars[-1].get("volume") or 0.0)
                rvol = (today_vol / avg_vol) if avg_vol > 0 else 1.0
            except (TypeError, ValueError):
                pass
        pct = ((mark - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
        return Snapshot(symbol=symbol, mark=mark, prev_close=prev_close,
                        pct_change=pct, rvol=rvol, usable=True)


def _detect(snap: Snapshot) -> Optional[Candidate]:
    """A single transparent long pattern: constructive strength on volume.

    Milestone-1 simple: reclaim above prior close with above-average volume.
    Confidence is a bounded blend of move quality + participation.
    """
    if not snap.usable or snap.mark <= 0:
        return None
    if snap.prev_close <= 0:
        return None
    if snap.pct_change <= 0:  # long-only, must be above prior close
        return None
    # Confidence: how convincing is the strength (capped) + volume support.
    move_component = min(snap.pct_change / 3.0, 1.0)        # 3%+ = full
    vol_component = min(max(snap.rvol - 1.0, 0.0) / 1.5, 1.0)  # 2.5x rvol = full
    confidence = round(0.50 + 0.30 * move_component + 0.20 * vol_component, 4)
    score = round(0.6 * move_component + 0.4 * vol_component, 4)
    return Candidate(
        symbol=snap.symbol, mark=snap.mark, score=score, pattern="reclaim_strength",
        confidence=min(confidence, 0.99),
        reason=f"+{snap.pct_change:.2f}% vs prev_close, rvol={snap.rvol:.2f}",
        meta={"pct_change": snap.pct_change, "rvol": snap.rvol},
    )


async def discover(config, source: DataSource) -> list[Candidate]:
    """FIND + DECIDE(pattern) + RANK. De-duped, best-first."""
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for sym in config.universe:
        sym = sym.upper()
        if sym in seen:
            continue
        seen.add(sym)
        snap = await source.snapshot(sym)
        if snap is None:
            continue
        cand = _detect(snap)
        if cand is not None:
            candidates.append(cand)
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates
