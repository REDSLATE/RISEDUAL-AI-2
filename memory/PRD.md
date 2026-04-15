# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI
- **Backend**: FastAPI + MongoDB + APScheduler
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **Payments**: Stripe Live Mode
- **Market Data**: Alpha Vantage -> Finnhub -> TwelveData (ProviderRouter)
- **Search**: War Room (Registry-based provider pattern, 10 adapters, AI synthesis)
- **Domain**: risedual.ai

## Search War Room — Registry Pattern (Complete)
- `registry.py`: Dynamic provider registration with enable/disable, config-driven adapter loading
- Providers specify: name, source_type, modes, timeout, critical flag, env_key dependency
- `orchestrator.py`: Uses registry instead of hardcoded imports, parallel execution with per-provider timeouts
- `GET /api/web-intel/status` returns full registry status (active/inactive, key availability)
- 10 providers registered: wikipedia, sec, stockfit, tavily, av_news, finnhub_news, ddg, ddg_news, yahoo, fred
- AI analysis runs as second phase after all engines

## Other Completed Systems
- ProviderRouter (multi-key failover), Predictions (stale-while-revalidate)
- Financial Tools Agent v2 (split modules, 7 StockFit data types)
- Headlines Pipeline, KeyVault (bulk + validate), enforce_credits helper
- Code quality reviews applied (3 rounds)

## Deployment: PASS — ready for risedual.ai

## Backlog
- P0: Deploy to risedual.ai (pending user production testing)
- P2: QuiverQuant monitoring (blocked on their server stability)
- P2: StockFit 13F holder tracking & SEC filing alerts
- P3: Component splitting, hook dependency audit
