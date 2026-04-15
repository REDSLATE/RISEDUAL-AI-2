# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI
- **Backend**: FastAPI + MongoDB + APScheduler
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **Payments**: Stripe Live Mode
- **Market Data**: Alpha Vantage -> Finnhub -> TwelveData (ProviderRouter)
- **Search**: War Room (DDG + Tavily + Wikipedia + SEC + FRED + Yahoo + AI Analysis + StockFit)
- **Domain**: risedual.ai

## ProviderRouter System (Complete)
- Error classification, tiered cooldowns, latency tracking, MongoDB persistence
- Dynamic Registration, Health Heartbeat, Enable/Disable, Parallel Orchestration

## Predictions — Stale-While-Revalidate (Complete)

## Financial Tools Agent v2 (Complete — Refactored)
- Split: `financial_tools.py` (schemas + implementations) + `financial_tools_agent.py` (state machine)
- 7 SEC data types via StockFit dispatch

## Headlines Pipeline (Complete)
## KeyVault (Complete — Enhanced)
- AES-256-GCM encrypted DB storage
- **NEW**: `POST /api/vault/keys/bulk` — bulk import multiple keys in one call
- **NEW**: `POST /api/vault/keys/validate` — test key against provider before storing (13 providers supported)
- **NEW**: Frontend validate button + help links for obtaining each API key
- Auto-reloads affected provider pools on store

## Code Quality Review v1 (Complete)

## Deployment: PASS — ready for risedual.ai

## Backlog
- P0: Deploy to risedual.ai (pending user production testing)
- P2: QuiverQuant monitoring (blocked on their server stability)
- P2: StockFit 13F holder tracking & SEC filing alerts (pending user API plan upgrade)
- P3: Component splitting (SmartOrderPanel, StrategyBuilder, WaitlistModal)
- P3: Hook dependency audit (149 instances)
