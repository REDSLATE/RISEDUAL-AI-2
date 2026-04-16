# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (inline tabs, no modals for workspace)
- **Backend**: FastAPI + MongoDB + APScheduler (278 routes)
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core v6 (XGBoost + Platt calibration + 8 pattern detectors + typed CalibrationGate + 3 autonomous tiers + Kelly position sizing)
- **Domain**: risedual.ai

## ML Pipeline (LIVE — collecting data)
- Phase 1: FeaturesSnapshot capture (17 features + 8 pattern booleans, schema v2)
- Phase 2: 8 pattern detectors (detections: rsi_divergence, macd_crossover)
- Phase 3 (v6): Typed CalibrationGate with Tier/TierStatus/GateResult enums, half-Kelly position sizing, VAPID push alerts, stateless orchestrator with model hot-reload

## ML v6 Upgrade (DONE — Apr 16, 2026)
### Services replaced:
- `services/ml_alert_service.py` — VAPID web-push with per-signal gating (confidence >= 60%, pattern + regime checks)
- `services/ml_paper_trader.py` — Paper trades with half-Kelly sizing (capped 25%), positions collection tracking
- `services/ml_alpaca_broker.py` — Live execution with 2% hard cap, duplicate position prevention, market orders only
- `services/ml_orchestrator.py` — NEW service: `run_post_signal_pipeline()` runs all 3 tiers sequentially, never raises, model hot-reload via mtime

### Routes replaced:
- `routes/ml_orchestrator.py` — v6 gate-status returns typed tiers with reasons, thresholds, next milestones; stats returns data_progress, pattern_detection_counts, paper_trading, live_execution, calibration

### risedual_core updates:
- `ml/calibration.py` — Added Tier/TierStatus/GateResult, check_all_gates(), kelly_fraction(), half_kelly_position()
- `ml/calibration_gate.py` — Backward-compat shim for scripts/backtest.py
- `ml/signal_model.py` — Added CalibrationStats class, evaluate() method, calibration_stats persistence in save/load
- `schemas/market.py` — Added pattern booleans on FeaturesSnapshot, PatternResult, convenience properties (rsi, atr, close_price), prediction_id on SignalResult

### Frontend:
- `MLControls.jsx` — Updated to consume v6 nested response shapes, added Next Milestone display

## ML Controls Panel
- Tier gate cards (3 tiers with lock/unlock + typed reasons from backend)
- Next milestone display (auto-generated from gate status)
- Data collection progress bars (100/200 milestones)
- Model status (trained/collecting with CalibrationStats)
- Pattern detection heatmap (8 detectors with counts)
- Autonomous activity counters (paper trades, live orders)
- Milestone tracker checkpoints

## Workspace: Modals -> Inline Tabs (DONE)
- PanelShell dual-mode component
- 12 tabs total in WorkspaceHub: Watchlist, Portfolio, Journal, PnL, Paper, Bots, ML, Orders, Risk, Scanner, Credits

## Backlog
- P0: Collect 100+ labeled snapshots -> first model training
- P1: Connect Alpaca API keys via KeyVault (once Tier 3 unlocked)
- P2: StockFit 13F holder tracking & SEC filing change alerts
- P2: QuiverQuant endpoint monitoring (external provider stability)
