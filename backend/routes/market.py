"""Market data routes: stocks, crypto, dark pool, options."""
from fastapi import APIRouter, HTTPException
import logging

from services.market_data_service import MarketDataService

router = APIRouter(prefix="/api")
market_service = MarketDataService()


# --- Stock Market ---
@router.get("/stocks/ticker")
async def get_ticker():
    try:
        return await market_service.get_ticker_data()
    except Exception as e:
        logging.error(f"Error fetching ticker data: {e}")
        raise HTTPException(status_code=500, detail="Error fetching ticker data")


@router.get("/stocks/quote/{symbol}")
async def get_quote(symbol: str):
    try:
        quote = await market_service.get_quote(symbol)
        if not quote:
            raise HTTPException(status_code=404, detail=f"Quote not found for {symbol}")
        return quote
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching quote for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Error fetching quote")


# --- Options ---
@router.get("/options/radar")
async def get_options_radar():
    try:
        return market_service.generate_mock_options_data('radar')
    except Exception as e:
        logging.error(f"Error fetching options radar: {e}")
        raise HTTPException(status_code=500, detail="Error fetching options radar")


@router.get("/options/flow")
async def get_options_flow():
    try:
        return market_service.generate_mock_options_data('flow')
    except Exception as e:
        logging.error(f"Error fetching options flow: {e}")
        raise HTTPException(status_code=500, detail="Error fetching options flow")


@router.get("/options/momentum")
async def get_momentum():
    try:
        return market_service.generate_mock_options_data('momentum')
    except Exception as e:
        logging.error(f"Error fetching momentum data: {e}")
        raise HTTPException(status_code=500, detail="Error fetching momentum data")


@router.get("/options/fast-movers")
async def get_fast_movers():
    try:
        return market_service.generate_mock_options_data('fast_movers')
    except Exception as e:
        logging.error(f"Error fetching fast movers: {e}")
        raise HTTPException(status_code=500, detail="Error fetching fast movers")


@router.get("/options/unusual-volume")
async def get_unusual_volume():
    try:
        return market_service.generate_mock_options_data('unusual_volume')
    except Exception as e:
        logging.error(f"Error fetching unusual volume: {e}")
        raise HTTPException(status_code=500, detail="Error fetching unusual volume")


# --- Crypto ---
@router.get("/crypto/prices")
async def get_crypto_prices():
    try:
        return await market_service.get_crypto_data()
    except Exception as e:
        logging.error(f"Error fetching crypto data: {e}")
        raise HTTPException(status_code=500, detail="Error fetching crypto data")


@router.get("/crypto/{symbol}")
async def get_crypto_by_symbol(symbol: str):
    try:
        crypto = await market_service.get_crypto_quote(symbol)
        if not crypto:
            raise HTTPException(status_code=404, detail=f"Crypto not found for {symbol}")
        return crypto
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching crypto {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Error fetching crypto")


# --- Dark Pool ---
@router.get("/dark-pool")
async def get_dark_pool():
    try:
        from services.polygon_dark_pool_service import fetch_dark_pool_data
        return await fetch_dark_pool_data()
    except Exception as e:
        logging.error(f"Error fetching dark pool data: {e}")
        raise HTTPException(status_code=500, detail="Error fetching dark pool data")


# --- Sentiment Indicators ---
@router.get("/sentiment/fear-greed")
async def get_fear_greed():
    """Get current Fear & Greed Index + VIX level."""
    try:
        from services.market_sentiment_service import get_fear_greed_index, get_vix_level
        fg = await get_fear_greed_index()
        vix = await get_vix_level()
        return {"fear_greed": fg, "vix": vix}
    except Exception as e:
        logging.error(f"Error fetching sentiment: {e}")
        raise HTTPException(status_code=500, detail="Error fetching sentiment data")


# --- Order Flow / Institutional Walls ---
@router.get("/order-flow/{symbol}")
async def get_order_flow(symbol: str):
    """Get volume profile and institutional wall detection for a symbol."""
    try:
        from services.order_flow_service import get_volume_profile
        result = await get_volume_profile(symbol)
        if result.get("error"):
            return {"walls": [], "summary": {}, "error": result["error"]}
        # Strip the full profile from the response to keep payload light
        result.pop("profile", None)
        return result
    except Exception as e:
        logging.error(f"Order flow error for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Error fetching order flow data")
