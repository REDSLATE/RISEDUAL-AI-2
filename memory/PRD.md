# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform by RISEDUAL CORPORATION.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **AI**: Emergent LLM Key (GPT-5.2) with compliance guardrails
- **Payments**: Stripe Live Mode (4 tiers + credit top-ups)
- **Market Data**: Alpha Vantage (dual key rotation), Finnhub, QuiverQuant
- **Web Intelligence**: Search War Room (DDG + Wikipedia + SEC EDGAR + FRED + Yahoo)
- **Domain**: risedual.ai

## Stripe Billing (Live: acct_1TLqluE7P86KSLtB)
- 7 live products (3 subscriptions + 4 top-ups)
- Webhook: `https://risedual.ai/api/billing/webhook`

## Search War Room (NEW)
Multi-engine parallel search with automatic failover:
| Engine | Type | Key? | Status |
|--------|------|------|--------|
| DuckDuckGo | Web + News | No | Active (subprocess mode) |
| Wikipedia | Knowledge | No | Active |
| SEC EDGAR | Filings | No | Active |
| FRED | Macro data | Free key | Scaffolded (needs FRED_API_KEY) |
| Yahoo Finance | Prices | No | Active |

Endpoints:
- `POST /api/web-intel/war-room` — full multi-engine search
- `GET /api/web-intel/search?q=` — simple web search
- `GET /api/web-intel/news/{symbol}` — ticker news
- `GET /api/web-intel/research?topic=` — deep research
- `GET /api/web-intel/cache/status` — cache stats
- `GET /api/web-intel/status` — provider availability

## Backlog
- Add FRED_API_KEY for macro data (free: https://fred.stlouisfed.org/docs/api/api_key.html)
- Add Groq/OpenRouter/HuggingFace AI analysis layer when keys provided
- Monitor QuiverQuant insiders/lobbying/govcontracts for recovery
- Deploy to risedual.ai
