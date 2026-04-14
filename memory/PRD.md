# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **AI**: Emergent LLM Key (GPT-5.2) + ProviderRouter failover + Financial Tools Agent
- **Payments**: Stripe Live Mode (4 tiers + credit top-ups)
- **Market Data**: Alpha Vantage, Finnhub, TwelveData (via ProviderRouter)
- **Web Intelligence**: Search War Room (DDG + Wikipedia + SEC + FRED + Yahoo + AI Analysis)
- **Domain**: risedual.ai

## Financial Tools Agent (2026-04-14)
Agentic tool-calling loop for TradeGPT — auto-activates for calculation queries:
- **Tools**: `get_stock_quote`, `web_search`, `calculate_compound_growth`, `calculate_cagr`
- **Architecture**: OpenAI function calling via Emergent proxy → up to 6 tool rounds
- **Auto-routing**: Regex pattern detection in `ai_service.chat()` → falls back to standard chat on failure
- **Frontend**: Tool badges shown on chat bubbles (e.g., `compound growth`, `stock quote`)

## Headlines Pipeline (2026-04-14)
Background scrape → clean → store pipeline:
- 8 financial sources (CNBC, Reuters, MarketWatch, Fox Business, WSJ, Bloomberg, Yahoo, Investing.com)
- Content-hash deduplication, MongoDB `headlines` collection with 7-day TTL
- Runs every 15 minutes, feeds prediction engine via `get_for_prediction()`
- Admin API: `/api/headlines/stats`, `/api/headlines/run`, `/api/headlines/recent`

## ProviderRouter System (Complete)
- Error classification, tiered cooldowns, latency tracking, MongoDB persistence
- Wired into: ai_service, market_data_service, company_research, prediction, war room

## Frontend Micro-Status Badges (Complete)
- Chat: model name + tool badges
- Research: provider badge
- Prediction: instant/live badge
- War Room: source engine badges

## Deployment: PASS — ready for risedual.ai

## Backlog
- P1: Add API keys (OPENAI, ANTHROPIC, TWELVEDATA, FRED)
- P1: Extend ProviderRouter to email_service
- P0: Deploy to risedual.ai
