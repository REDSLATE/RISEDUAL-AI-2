# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI
- **Backend**: FastAPI + MongoDB + APScheduler
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core (XGBoost signal model + Platt calibration + regime model)
- **Payments**: Stripe Live Mode
- **Market Data**: Alpha Vantage -> Finnhub -> TwelveData -> Marketstack (ProviderRouter)
- **Search**: War Room (Registry-based, 12 adapters including NewsAPI.ai)
- **Domain**: risedual.ai

## ML Pipeline (NEW — Wired In)
- `hypothesis_logger.py`: Captures FeaturesSnapshot (price, RSI, MACD, SMA-20/50) to MongoDB after every `get_hypothesis()` call
- `prediction_labeler.py`: Hourly APScheduler job labels snapshots with up/down/flat outcomes (±1.5% band, 4h delay)
- `GET /api/signal/{ticker}`: Returns calibrated ML signal or graceful "no_model" status
- `scripts/train_signal_model.py`: Standalone training script (run after 100+ labeled snapshots)
- Model hot-reloads from disk — no restart needed after training
- `risedual_core` installed as editable dependency with XGBoost, scikit-learn, joblib

## ML Pipeline Status
- Day 1: Data collection active (snapshots being written)
- Day N: Train model once 100+ labels exist, signal endpoint goes live

## Completed Systems
- ProviderRouter, Predictions (stale-while-revalidate), Financial Tools Agent v2
- War Room (registry pattern, 12 providers), Headlines Pipeline, KeyVault
- Code quality reviews (3 rounds), Watchlist sync, Owner credits fix

## Deployment: LIVE at risedual.ai

## Backlog
- P0: Collect 100+ labeled snapshots -> train first signal model
- P2: RegimeModel integration (bull/bear/sideways conditioning)
- P2: QuiverQuant monitoring, StockFit 13F tracking
- P3: Component splitting, hook dependency audit
