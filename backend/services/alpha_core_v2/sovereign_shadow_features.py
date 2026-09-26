"""Real-data feature builder for the OPTIONAL RISE model gate (`sovereign.py`).

Only used when ``ALPHA_SOVEREIGN_MODEL=rise``. Maps a Core v2 Candidate to a
`SovereignFeatures` using REAL market data. Fields Alpha does not yet produce
(options flow, news, smart-money, curated R:R) are left as None/unknown — the
RISE `evaluate()` then HOLDs on the missing inputs. Nothing is fabricated; this
makes the "missing live features" gap explicit rather than papered over.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from services.alpha_core_v2.contracts import Candidate
from services.alpha_core_v2.sovereign import SovereignFeatures

logger = logging.getLogger(__name__)


async def build_sovereign_features(candidate: Candidate) -> SovereignFeatures:
    price = float(candidate.mark or 0.0)
    rsi14 = macd = sma20 = sma50 = None
    dollar_volume = None
    try:
        from services import market_data_pool as mdp
        if price <= 0:
            q = await mdp.market_quote(candidate.symbol)
            if q:
                price = float(q.get("price") or q.get("last") or 0.0)
        ti = await mdp.get_technical_indicators(candidate.symbol)
        if isinstance(ti, dict):
            rsi14 = ti.get("rsi_14")
            macd = ti.get("macd")
            sma20 = ti.get("sma_20")
            sma50 = ti.get("sma_50")
        bars = await mdp.market_daily(candidate.symbol)
        if bars and price > 0:
            today_vol = float(bars[-1].get("volume") or 0.0)
            if today_vol > 0:
                dollar_volume = today_vol * price
    except Exception as exc:  # noqa: BLE001
        logger.debug("[shadow-rise] feature build failed %s: %s", candidate.symbol, exc)

    return SovereignFeatures(
        symbol=candidate.symbol,
        observed_at=datetime.now(timezone.utc),
        price=price if price > 0 else None,
        rsi14=float(rsi14) if rsi14 is not None else None,
        macd=float(macd) if macd is not None else None,
        sma20=float(sma20) if sma20 is not None else None,
        sma50=float(sma50) if sma50 is not None else None,
        dollar_volume=dollar_volume,
        # Not produced by Alpha yet — left unset so RISE HOLDs (no fabrication):
        # spread_bps, rr, options/news/smart-money, event_risk defaults.
    )


__all__ = ["build_sovereign_features"]
