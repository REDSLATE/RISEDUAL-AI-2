# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI
- **Backend**: FastAPI + MongoDB + APScheduler
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core (XGBoost + Platt calibration + 8 pattern detectors + regime model)
- **Payments**: Stripe Live Mode
- **Market Data**: Alpha Vantage -> Finnhub -> TwelveData -> Marketstack (ProviderRouter)
- **Search**: War Room (Registry-based, 12 adapters)
- **Domain**: risedual.ai

## ML Pipeline — Phase 1 (LIVE)
- FeaturesSnapshot capture on every hypothesis
- Hourly labeler (4h delay, ±1.5% flat band)
- Signal endpoint with graceful no_model state
- Training script ready

## ML Pipeline — Phase 2: Pattern Detection (LIVE)
- 8 detectors: double_bottom, bullish_engulfing, bearish_engulfing, bull_flag, rsi_divergence, macd_crossover, volume_surge, head_and_shoulders
- Pure functions in `risedual_core/ml/patterns.py`, run via asyncio.to_thread
- Pattern booleans added to FeaturesSnapshot (schema v2, 17 total features)
- OHLCV fetched from price_provider, detectors run in parallel
- Training script updated to include pattern features

## Phase 3: Autonomous Action (FUTURE)
- Tier 1 (Alerts): 500 labels, 55%+ accuracy, ECE < 0.15
- Tier 2 (Paper Trading): Sharpe 1.0+, max DD < 15%, 500+ predictions
- Tier 3 (Live Execution): Sharpe 1.2+, max DD < 12%, 30 days live, 1000+ predictions

## Deployment: LIVE at risedual.ai

## Backlog
- P0: Collect 100+ labeled snapshots -> train first signal model (with pattern features)
- P1: CalibrationStats storage in SignalModel.fit()
- P1: CalibrationGate + BacktestResult schemas
- P2: RegimeModel conditioning on signals
- P2: Backtester script (walk-forward validation)
