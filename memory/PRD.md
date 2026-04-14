# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **AI**: Emergent LLM Key (GPT-5.2) with compliance guardrails
- **Payments**: Stripe Live Mode (4 tiers + credit top-ups)
- **Market Data**: Alpha Vantage, Finnhub, TwelveData (via Provider Pool)
- **Web Intelligence**: Search War Room (DDG + Wikipedia + SEC + FRED + Yahoo + AI Analysis)
- **Domain**: risedual.ai

## v2.2 Site Streamlining (2026-04-14)
Restructured from single long-scroll to 5-destination architecture:

| Destination | Contains | Nav Entry |
|-------------|----------|-----------|
| **Dashboard** | Watchlist, AI War Room, Sector Heatmap, Fear/Greed, Live Insights, Order Flow, Whale Radar, AI Intelligence, Crypto, Quick-nav cards | Primary tab |
| **Research** | AI Hypothesis, Market Prediction, Company Research, Macro Dashboard (tabbed) | Primary tab + contextual sub-nav |
| **Options** | Options Radar, Options Flow Screener, Dark Pool (tabbed) | Primary tab + contextual sub-nav |
| **Workspace** | Watchlist management, Portfolio, Journal, P&L, Paper Trading, Bots, Smart Orders, Risk Calc, Scanner, Referrals, Failure Loop (tabbed) | Primary tab |
| **Account** | User menu: Credits, Developer API, Admin, Broker Connect | User avatar dropdown |

## Stripe Billing (Live: acct_1TLqluE7P86KSLtB)
- 7 live products, webhook at risedual.ai/api/billing/webhook

## Provider Pool System (2026-04-14)

### AI Provider Pool (`AI_PROVIDER_POOL` env var)
Priority-based LLM failover chain:
1. **emergent-primary** — GPT-5.2 via Emergent LLM Key (ACTIVE)
2. **openai-backup** — GPT-4.1 via direct OpenAI API (awaiting key)
3. **anthropic-backup** — Claude Sonnet 4 via direct Anthropic API (awaiting key)

Used by: Market Predictions, War Room AI Analysis, Research, TradeGPT

### Market Data Provider Pool (`MARKET_DATA_PROVIDER_POOL` env var)
Priority-based market data failover:
1. **alphavantage-primary** — Alpha Vantage GLOBAL_QUOTE/TIME_SERIES (ACTIVE)
2. **finnhub-backup** — Finnhub /quote and /stock/candle (ACTIVE)
3. **twelvedata-backup** — TwelveData /quote and /time_series (awaiting key)

Used by: price_provider.get_quote(), get_daily_history(), all market data routes

### Architecture
- `/app/backend/services/provider_pool.py` — Generic pool engine (health tracking, cooldown, failover)
- `/app/backend/services/ai_pool.py` — AI-specific pool (emergent/openai/anthropic dispatch)
- `/app/backend/services/market_data_pool.py` — Market data pool (AV/Finnhub/TwelveData dispatch)
- Env var format: JSON array with `${VAR}` references resolved at startup
- Providers without keys are automatically skipped at load time

## Search War Room
- DDG (tenacity retry + semaphore), Wikipedia, SEC EDGAR, Yahoo Finance, FRED
- Token-based CSS theme system with light/dark mode support
- **AI Analysis Layer**: Uses AI Provider Pool for synthesis
- **Key Rotation System**: FRED_API_KEYS, GROQ_API_KEYS, OPENROUTER_API_KEYS support comma-separated rotation

## Sliding Cache Policy (2026-04-14)
- AICacheService with sliding TTL: extends expiration on each read
- Max age enforcement: hard expiration regardless of reads
- Applied to: `/api/market/prediction` (global), `/api/market/prediction/{symbol}` (ticker)
- TTL: 300s sliding, 900s max age

## Backlog
- P1: Add OPENAI_API_KEY for GPT-4.1 backup (scaffolding ready)
- P1: Add ANTHROPIC_API_KEY for Claude Sonnet 4 backup (scaffolding ready)
- P1: Add TWELVEDATA_API_KEY for market data backup (scaffolding ready)
- P1: Add FRED_API_KEYS for macro data (scaffolding ready)
- P1: Add GROQ_API_KEYS / OPENROUTER_API_KEYS for war room AI alternatives
- P2: Monitor QuiverQuant insiders/lobbying/govcontracts (external 500s)
- P0: Deploy to risedual.ai (after user finishes testing production)
