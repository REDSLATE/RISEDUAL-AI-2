# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **AI**: Emergent LLM Key (GPT-5.2) with compliance guardrails
- **Payments**: Stripe Live Mode (4 tiers + credit top-ups)
- **Market Data**: Alpha Vantage, Finnhub, QuiverQuant
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

## Search War Room
- DDG (tenacity retry + semaphore), Wikipedia, SEC EDGAR, Yahoo Finance, FRED
- Token-based CSS theme system with light/dark mode support
- **AI Analysis Layer** (2026-04-14): Multi-provider failover chain (Groq -> OpenRouter -> Emergent LLM)
- **Key Rotation System** (2026-04-14): All external API providers support comma-separated keys with automatic rotation and cooldown on failure

## Sliding Cache Policy (2026-04-14)
- AICacheService with sliding TTL: extends expiration on each read
- Max age enforcement: hard expiration regardless of reads
- Stale fallback: returns expired data on error
- Applied to: `/api/market/prediction` (global), `/api/market/prediction/{symbol}` (ticker-specific)
- TTL: 300s sliding, 900s max age

## Key Rotation System (2026-04-14)
- `/app/backend/services/key_rotator.py` — shared utility for all providers
- Supports comma-separated keys in `.env` (e.g., `FRED_API_KEYS=key1,key2,key3`)
- Auto-rotates to next healthy key on failure
- 120s cooldown per failed key
- Providers: FRED_API_KEYS, GROQ_API_KEYS, OPENROUTER_API_KEYS

## Backlog
- P1: Add FRED_API_KEYS for macro data (scaffolding ready, awaiting keys)
- P1: Add GROQ_API_KEYS for fast AI inference (scaffolding ready, awaiting keys)
- P1: Add OPENROUTER_API_KEYS for multi-model consensus (scaffolding ready, awaiting keys)
- P2: Monitor QuiverQuant insiders/lobbying/govcontracts (external 500s)
- P0: Deploy to risedual.ai (after user finishes testing production)
