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
5-destination architecture: Dashboard, Research, Options, Workspace, Account

## Stripe Billing (Live: acct_1TLqluE7P86KSLtB)
- 7 live products, webhook at risedual.ai/api/billing/webhook

## ProviderRouter System (2026-04-14)

### Architecture
- `/app/backend/services/providerrouter.py` — Health-aware router with error classification, tiered cooldowns, latency tracking, MongoDB persistence, asyncio.Lock
- `/app/backend/services/provider_registry.py` — Re-exports pool_config
- `/app/backend/services/pool_config.py` — Assembles pools from JSON env vars or individual keys
- `/app/backend/routes/provider_health.py` — Admin-only health endpoint

### Error Classification & Cooldown Tiers
| Error Type | Cooldown | Action |
|---|---|---|
| auth (401/403) | 3600s | Disable provider |
| rate_limit (429) | 30-300s (exponential) | Cool down |
| timeout | 15-180s (exponential) | Cool down |
| upstream (502/503/504) | 15-180s (exponential) | Cool down |

### AI Provider Pool
1. emergent-primary — GPT-5.2 (ACTIVE)
2. openai-backup — GPT-4.1 (awaiting key)
3. anthropic-backup — Claude Sonnet 4 (awaiting key)

### Market Data Provider Pool
1. alphavantage-primary (ACTIVE)
2. finnhub-backup (ACTIVE)
3. twelvedata-backup (awaiting key)

### Admin Dashboard
- `/api/provider-health` — per-provider: successes, failures, consecutive_failures, avg_latency_ms, last_error, last_error_type, cooldown_until, disabled
- Frontend: Admin Panel > Providers tab with live health cards, auto-refresh 30s

## Search War Room
- DDG, Wikipedia, SEC EDGAR, Yahoo Finance, FRED (key rotation)
- AI Analysis Layer via AI Provider Pool
- GROQ/OpenRouter key rotators (awaiting keys)

## Sliding Cache Policy
- AICacheService: 300s sliding TTL, 900s max age
- Applied to: /api/market/prediction, /api/market/prediction/{symbol}

## Deployment Status
- Deployment check: PASS (no blockers)
- Custom domain: risedual.ai (ready to deploy via Emergent platform)

## Backlog
- P1: Add API keys (OPENAI, ANTHROPIC, TWELVEDATA, FRED, GROQ, OPENROUTER)
- P1: Extend ProviderRouter to email_service, research services, prediction services
- P2: Frontend micro-status badges showing provider source on cards
- P2: Monitor QuiverQuant endpoint recovery
