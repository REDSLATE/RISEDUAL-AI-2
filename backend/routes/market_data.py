"""Market data routes: scraping endpoints, macro data, predictions."""
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, Request
import os
import logging
from datetime import datetime, timezone

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

db = None

def set_db(database) -> None:
    global db
    db = database


# --- World Events & Macro ---
@router.get("/world-events")
async def get_world_events() -> Dict[str, Any]:
    try:
        from services.cache import cache
        from services.world_events_service import WorldEventsService
        return await cache.get_or_fetch(
            "world_events",
            WorldEventsService().scrape_world_events,
            ttl=300,
        )
    except Exception as e:
        logger.error(f"Error fetching world events: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/foreign-markets")
async def get_foreign_markets() -> Dict[str, Any]:
    try:
        from services.cache import cache
        from services.foreign_markets_service import ForeignMarketsService
        return await cache.get_or_fetch(
            "foreign_markets",
            ForeignMarketsService().get_foreign_markets,
            ttl=60,
        )
    except Exception as e:
        logger.error(f"Error fetching foreign markets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/gov-filings")
async def get_gov_filings() -> Dict[str, Any]:
    try:
        from services.cache import cache
        from services.gov_filings_service import GovFilingsService
        return await cache.get_or_fetch(
            "gov_filings",
            GovFilingsService().get_all_gov_data,
            ttl=600,
        )
    except Exception as e:
        logger.error(f"Error fetching gov filings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Scraping Data Endpoints ---

# --- Lobbying Data ---
@router.get("/lobbying")
async def get_lobbying_summary() -> Dict[str, Any]:
    """Get lobbying data summary with top spenders and recent filings."""
    try:
        from services.lobbying_service import LobbyingService
        return await LobbyingService().get_summary()
    except Exception as e:
        logger.error(f"Error fetching lobbying data: {e}")
        raise HTTPException(status_code=500, detail="Error fetching lobbying data")


@router.get("/lobbying/ticker/{ticker}")
async def get_lobbying_by_ticker(ticker: str) -> Dict[str, Any]:
    """Get lobbying activity for a specific stock ticker."""
    try:
        from services.lobbying_service import LobbyingService
        filings = await LobbyingService().get_lobbying_by_ticker(ticker)
        return {"ticker": ticker.upper(), "filings": filings, "count": len(filings)}
    except Exception as e:
        logger.error(f"Error fetching lobbying for {ticker}: {e}")
        raise HTTPException(status_code=500, detail="Error fetching lobbying data")


@router.get("/lobbying/top-spenders")
async def get_top_lobby_spenders() -> Dict[str, Any]:
    """Get top corporate lobbying spenders."""
    try:
        from services.lobbying_service import LobbyingService
        spenders = await LobbyingService().get_top_spenders(20)
        return {"top_spenders": spenders}
    except Exception as e:
        logger.error(f"Error fetching top spenders: {e}")
        raise HTTPException(status_code=500, detail="Error fetching top spenders")


# --- Fear & Greed Index ---
@router.get("/fear-greed")
async def get_fear_greed() -> Dict[str, Any]:
    """Get current Fear & Greed index + historical data."""
    try:
        from services.fear_greed_service import FearGreedService
        return await FearGreedService().get_full_summary()
    except Exception as e:
        logger.error(f"Error fetching fear & greed: {e}")
        raise HTTPException(status_code=500, detail="Error fetching fear & greed")


# --- Scraping Data Endpoints (News, Social, Insider) ---
@router.get("/market/news")
async def get_financial_news() -> List[Dict[str, Any]]:
    try:
        from services.cache import cache
        from services.financial_scraping_service import FinancialScrapingService
        return await cache.get_or_fetch(
            "financial_news",
            FinancialScrapingService().scrape_financial_news,
            ttl=300,
        )
    except Exception as e:
        logger.error(f"Error fetching news: {e}")
        raise HTTPException(status_code=500, detail="Error fetching news")


@router.get("/market/social-sentiment")
async def get_social_sentiment() -> Dict[str, Any]:
    try:
        from services.cache import cache
        from services.financial_scraping_service import FinancialScrapingService
        return await cache.get_or_fetch(
            "social_sentiment",
            FinancialScrapingService().scrape_reddit_sentiment,
            ttl=300,
        )
    except Exception as e:
        logger.error(f"Error fetching social sentiment: {e}")
        raise HTTPException(status_code=500, detail="Error fetching social sentiment")


@router.get("/market/insider-trades")
async def get_insider_trades() -> Dict[str, Any]:
    try:
        from services.cache import cache
        from services.financial_scraping_service import FinancialScrapingService
        return await cache.get_or_fetch(
            "insider_trades",
            FinancialScrapingService().scrape_insider_trades,
            ttl=300,
        )
    except Exception as e:
        logger.error(f"Error fetching insider trades: {e}")
        raise HTTPException(status_code=500, detail="Error fetching insider trades")


@router.get("/market/crypto-data")
async def get_crypto_market_data() -> Dict[str, Any]:
    try:
        from services.crypto_scraping_service import CryptoScrapingService
        scraper = CryptoScrapingService()
        return {
            'exchange_data': await scraper.get_exchange_data(),
            'whale_transactions': await scraper.get_whale_transactions(),
            'sentiment': await scraper.get_crypto_sentiment()
        }
    except Exception as e:
        logger.error(f"Error fetching crypto data: {e}")
        raise HTTPException(status_code=500, detail="Error fetching crypto data")


@router.get("/market/real-estate")
async def get_real_estate_data() -> Dict[str, Any]:
    try:
        from services.real_estate_scraping_service import RealEstateScrapingService
        return await RealEstateScrapingService().scrape_all_real_estate_data()
    except Exception as e:
        logger.error(f"Error fetching real estate data: {e}")
        raise HTTPException(status_code=500, detail="Error fetching real estate data")


# --- Market Prediction ---
@router.get("/market/prediction")
async def get_market_prediction(request: Request) -> Dict[str, Any]:
    try:
        scrape_results = await _collect_all_scrape_data(include_real_estate=True)
        prediction = await _run_prediction_model(scrape_results)
        _enrich_prediction_metadata(prediction, scrape_results)

        # Log prediction for accuracy tracking (uses SPY as market proxy)
        if prediction.get("overall_direction"):
            try:
                from services.prediction_tracker import log_market_prediction
                from services.auth_helpers import get_optional_user
                user = await get_optional_user(request)
                uid = str(user.get("_id", "")) if user else None
                await log_market_prediction(
                    request.app.state.db,
                    prediction["overall_direction"],
                    prediction.get("confidence_score", 0),
                    user_id=uid
                )
            except Exception as track_err:
                logger.warning(f"Prediction tracking failed: {track_err}")

        return prediction
    except Exception as e:
        logger.error(f"Error generating prediction: {e}")
        raise HTTPException(status_code=500, detail="Error generating market prediction")


async def _collect_all_scrape_data(include_real_estate: bool = False) -> Dict[str, Any]:
    """Collect all scraped macro data from services."""
    from services.financial_scraping_service import FinancialScrapingService
    from services.crypto_scraping_service import CryptoScrapingService
    from services.world_events_service import WorldEventsService
    from services.foreign_markets_service import ForeignMarketsService
    from services.gov_filings_service import GovFilingsService

    financial = FinancialScrapingService()
    crypto = CryptoScrapingService()

    result = {
        "news": await financial.scrape_financial_news(),
        "social": await financial.scrape_reddit_sentiment(),
        "insider_trades": await financial.scrape_insider_trades(),
        "crypto_data": await crypto.get_exchange_data(),
        "whale_txns": await crypto.get_whale_transactions(),
        "crypto_sentiment": await crypto.get_crypto_sentiment(),
        "world_events": await WorldEventsService().scrape_world_events(),
        "foreign_markets": await ForeignMarketsService().get_foreign_markets(),
        "gov_filings": await GovFilingsService().get_all_gov_data(),
    }

    # Fear & Greed Index
    try:
        from services.fear_greed_service import FearGreedService
        result["fear_greed"] = await FearGreedService().get_full_summary()
    except Exception as e:
        logger.warning(f"Fear & Greed fetch failed: {e}")
        result["fear_greed"] = None

    if include_real_estate:
        from services.real_estate_scraping_service import RealEstateScrapingService
        result["real_estate"] = await RealEstateScrapingService().scrape_all_real_estate_data()

    return result


async def _run_prediction_model(data: Dict[str, Any]) -> Dict[str, Any]:
    """Run the AI market prediction model on collected data."""
    from services.market_prediction_service import MarketPredictionService

    all_crypto = data["crypto_data"] + [data["crypto_sentiment"]] + data["whale_txns"]
    prediction_service = MarketPredictionService(os.environ.get('EMERGENT_LLM_KEY'))

    # Attach fear & greed data so the prompt builder can use it
    if data.get("fear_greed"):
        prediction_service._fear_greed = data["fear_greed"]

    return await prediction_service.analyze_market(
        financial_news=data["news"],
        crypto_data=all_crypto,
        insider_trades=data["insider_trades"],
        social_sentiment=data["social"],
        real_estate_data=data.get("real_estate", {}),
        world_events=data["world_events"],
        foreign_markets=data["foreign_markets"],
        gov_filings=data["gov_filings"],
    )


def _enrich_prediction_metadata(prediction: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """Add real estate and macro data summaries to prediction response."""
    re = data.get("real_estate", {})
    prediction['real_estate_summary'] = {
        'housing_health': re.get('housing', {}).get('market_health', 'unknown'),
        'commercial_trend': 'mixed',
        'data_sources': len(re.get('housing', {}).get('sources', [])),
        'implications': re.get('trends', {}).get('market_implications', {}),
    }

    we = data["world_events"]
    fm = data["foreign_markets"]
    gf = data["gov_filings"]
    prediction['macro_data'] = {
        'world_events': {
            'total': we.get('total_events', 0),
            'high_impact': we.get('high_impact_count', 0),
            'top_sectors': [s['sector'] for s in we.get('affected_sectors', [])[:5]],
        },
        'foreign_markets': {
            'correlation_signals': fm.get('correlation_signals', [])[:5],
            'total_indices': len(fm.get('asia', []) + fm.get('europe', []) + fm.get('americas', [])),
        },
        'gov_filings': {
            'congressional_trades': gf.get('congressional_count', 0),
            'fed_announcements': gf.get('fed_count', 0),
            'insider_trades': gf.get('insider_count', 0),
            'lobbying_spenders': gf.get('lobbying_count', 0),
        },
    }

    # Fear & Greed
    fg = data.get("fear_greed")
    if fg:
        prediction['fear_greed'] = {
            'current': fg.get('current', {}).get('value'),
            'label': fg.get('current', {}).get('label'),
            'avg_7d': fg.get('avg_7d'),
            'avg_30d': fg.get('avg_30d'),
        }
