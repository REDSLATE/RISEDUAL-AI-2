# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI
- **Backend**: FastAPI + MongoDB + APScheduler
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **Payments**: Stripe Live Mode
- **Market Data**: Alpha Vantage → Finnhub → TwelveData (ProviderRouter)
- **Search**: War Room (DDG + Tavily + Wikipedia + SEC + FRED + Yahoo + AI Analysis + StockFit)
- **Domain**: risedual.ai

## ProviderRouter System (Complete)
- Error classification, tiered cooldowns, latency tracking, MongoDB persistence
- **Dynamic Registration**: `POST /register` hot-swaps providers without restart
- **Health Heartbeat**: `POST /heartbeat` — services self-report ok/degraded/failed
- **Enable/Disable**: `POST /enable` re-enables disabled providers
- **Parallel Orchestration**: `run_parallel(fn, deadline_ms)` calls all providers simultaneously
- **Model List**: `GET /models?lane=ai` lists all providers with full health
- Wired into: ai_service, market_data_service, company_research, prediction, war room, email, financial tools agent

## Predictions — Stale-While-Revalidate (Complete)
- Instant cache-first responses (119ms vs 40s+), background refresh jobs
- APScheduler prewarm every 10 minutes
- `POST /refresh`, `GET /status/{job_id}` for manual trigger + polling

## Financial Tools Agent v2 (Complete)
- LangGraph-inspired state machine with SSE streaming
- 6 tools: calculate_compound_growth, calculate_cagr, get_stock_quote, get_daily_history, web_search, get_sec_fundamentals
- `get_sec_fundamentals` dispatches 7 SEC data types via StockFit: financials, insiders, insider_summary, earnings, scores, earnings_calendar, fund_holders
- Graceful fallback to web_search when StockFit key unavailable or returns errors

## Headlines Pipeline (Complete)
- 8 sources, 15min scheduler, content-hash dedup, feeds prediction engine

## KeyVault (Complete)
- AES-256-GCM encrypted DB storage for dynamic platform API keys
- Admin UI for managing keys without .env updates

## Deployment: PASS — ready for risedual.ai

## Backlog
- P1: Add API keys (OPENAI, ANTHROPIC, TWELVEDATA, FRED, SENDGRID) via KeyVault
- P0: Deploy to risedual.ai (pending user production testing)
- P2: QuiverQuant monitoring (blocked on their server stability)
- P2: StockFit 13F holder tracking & SEC filing change alerts (pending user API plan upgrade)
