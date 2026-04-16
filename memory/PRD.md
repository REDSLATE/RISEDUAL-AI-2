# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI + Recharts (inline tabs, no modals for workspace)
- **Backend**: FastAPI + MongoDB + APScheduler (278+ routes)
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core v6 (XGBoost + Platt calibration + 8 pattern detectors + typed CalibrationGate + 3 autonomous tiers + Kelly position sizing)
- **Domain**: risedual.ai

## ML Pipeline (LIVE — collecting data)
- Phase 1: FeaturesSnapshot capture (17 features + 8 pattern booleans, schema v2)
- Phase 2: 8 pattern detectors (detections: rsi_divergence, macd_crossover)
- Phase 3 (v6): Typed CalibrationGate with Tier/TierStatus/GateResult enums, half-Kelly position sizing, VAPID push alerts, stateless orchestrator with model hot-reload

## ML v6 Upgrade (DONE — Apr 16, 2026)
### Services replaced:
- `services/ml_alert_service.py` — VAPID web-push with per-signal gating
- `services/ml_paper_trader.py` — Paper trades with half-Kelly sizing (capped 25%)
- `services/ml_alpaca_broker.py` — Live execution with 2% hard cap
- `services/ml_orchestrator.py` — NEW service: `run_post_signal_pipeline()`

### Routes replaced:
- `routes/ml_orchestrator.py` — v6 gate-status, stats, paper-trades, calibration-curve

### risedual_core updates:
- `ml/calibration.py` — Tier/TierStatus/GateResult, check_all_gates(), kelly_fraction(), half_kelly_position()
- `ml/calibration_gate.py` — Backward-compat shim
- `ml/signal_model.py` — CalibrationStats, evaluate(), save/load with stats
- `schemas/market.py` — Pattern booleans, PatternResult, convenience properties

## ML Controls Panel Enhancements (DONE — Apr 16, 2026)
### Paper Trading PnL Dashboard (NEW):
- Summary cards: Total PnL, Win Rate, Best/Worst Trade
- Cumulative PnL chart (Recharts AreaChart with gradient)
- Position sizing breakdown (avg/max/min from Kelly)
- Recent trades table with direction, confidence, PnL
- Empty state: "No ML paper trades yet" when Tier 2 is locked
- Backend: GET /api/ml/paper-trades (filters ML autonomous trades by prediction_id)

### Calibration Curve Visualization (NEW):
- Grouped bar chart: Predicted confidence vs Actual accuracy per bucket
- Color coding: green (well-calibrated), orange (overconfident)
- Stats row: Accuracy, Brier Score, ECE, N Predictions
- Model version badge
- Empty state: "Awaiting trained model" when no model exists
- Backend: GET /api/ml/calibration-curve (returns curve_data + summary)

## Workspace: Modals -> Inline Tabs (DONE)
- PanelShell dual-mode component
- 12 tabs total in WorkspaceHub

## Backlog
- P0: Collect 100+ labeled snapshots -> first model training
- P1: Connect Alpaca API keys via KeyVault (once Tier 3 unlocked)
- P2: StockFit 13F holder tracking & SEC filing change alerts
- P2: QuiverQuant endpoint monitoring (external provider stability)
