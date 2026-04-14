# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **AI**: Emergent LLM Key (GPT-5.2) with compliance guardrails + ProviderRouter failover
- **Payments**: Stripe Live Mode (4 tiers + credit top-ups)
- **Market Data**: Alpha Vantage, Finnhub, TwelveData (via ProviderRouter)
- **Web Intelligence**: Search War Room (DDG + Wikipedia + SEC + FRED + Yahoo + AI Analysis)
- **Domain**: risedual.ai

## v2.2 Site Streamlining
5-destination architecture: Dashboard, Research, Options, Workspace, Account

## ProviderRouter System (Complete)

### Core Files
- `providerrouter.py` — Error classification, tiered cooldowns, latency tracking, MongoDB persistence
- `provider_registry.py` / `pool_config.py` — Pool assembly from JSON env or individual keys
- `routes/provider_health.py` — Admin-only health endpoint

### Wired Into
- `ai_service.py` — Chat with failover (Emergent → OpenAI → Anthropic)
- `market_data_service.py` — Market data with failover (AV → Finnhub → TwelveData)
- `company_research_service.py` — Research synthesis via AI pool
- `market_prediction_service.py` — Prediction engine via AI pool key
- `search_war_room/adapters/ai_analysis.py` — War Room AI via pool

### Admin Dashboard
- Admin Panel > Providers tab with live health cards, auto-refresh 30s

### Frontend Micro-Status Badges (Complete)
- **TradeGPT Chat**: Model name badge on AI response bubbles (e.g., `gpt-5.2`)
- **Company Research**: Provider badge on AI Research Summary header
- **Market Prediction**: `instant`/`live` source badge next to cache badge
- **Search War Room**: Source engine badges (wikipedia, sec, ddg, yahoo, ai_analysis:provider)

## Stripe Billing (Live: acct_1TLqluE7P86KSLtB)
- 7 live products, webhook at risedual.ai/api/billing/webhook

## Sliding Cache Policy
- AICacheService: 300s sliding TTL, 900s max age
- Applied to: /api/market/prediction, /api/market/prediction/{symbol}

## Deployment Status
- Deployment check: PASS
- Custom domain: risedual.ai (ready to deploy via Emergent platform)

## Backlog
- P1: Add API keys (OPENAI, ANTHROPIC, TWELVEDATA, FRED, GROQ, OPENROUTER)
- P1: Extend ProviderRouter to email_service.py
- P2: Monitor QuiverQuant endpoint recovery
- P0: Deploy to risedual.ai
