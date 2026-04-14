# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI
- **Backend**: FastAPI + MongoDB + APScheduler
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **Payments**: Stripe Live Mode
- **Market Data**: Alpha Vantage → Finnhub → TwelveData (ProviderRouter)
- **Search**: War Room (DDG + Tavily + Wikipedia + SEC + FRED + Yahoo + AI Analysis)
- **Domain**: risedual.ai

## ProviderRouter System (Complete)
- Error classification, tiered cooldowns, latency tracking, MongoDB persistence
- **Dynamic Registration**: `POST /register` hot-swaps providers without restart, persists to MongoDB, restores on boot
- **Health Heartbeat**: `POST /heartbeat` — services self-report ok/degraded/failed (no auth)
- **Enable/Disable**: `POST /enable` re-enables disabled providers
- **Parallel Orchestration**: `run_parallel(fn, deadline_ms)` calls all providers simultaneously with hard deadline
- **Model List**: `GET /models?lane=ai` lists all providers with full health
- Wired into: ai_service, market_data_service, company_research, prediction, war room, email, financial tools agent

## Predictions — Stale-While-Revalidate
- Instant cache-first responses (119ms vs 40s+), background refresh jobs
- APScheduler prewarm every 10 minutes
- `POST /refresh`, `GET /status/{job_id}` for manual trigger + polling

## Financial Tools Agent v2
- State graph with 5 tools, SSE streaming, Tavily + DDG search

## Headlines Pipeline
- 8 sources, 15min scheduler, content-hash dedup, feeds prediction engine

## Deployment: PASS — ready for risedual.ai

## Backlog
- P1: Add API keys (OPENAI, ANTHROPIC, TWELVEDATA, FRED, SENDGRID)
- P0: Deploy to risedual.ai
