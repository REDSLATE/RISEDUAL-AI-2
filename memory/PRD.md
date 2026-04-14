# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **AI**: Emergent LLM Key (GPT-5.2) with compliance guardrails
- **Payments**: Stripe Live Mode (4 tiers + credit top-ups)
- **Market Data**: Alpha Vantage, Finnhub, QuiverQuant
- **Web Intelligence**: Search War Room (DDG + Wikipedia + SEC + FRED + Yahoo)
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

### What changed:
- Homepage no longer renders ALL sections vertically
- Each feature has ONE canonical home
- Navbar consolidated: 4 primary tabs + Tools dropdown + User menu
- Sub-nav appears contextually when on Research or Options
- All original color coding and branded names preserved
- All modals still accessible from user menu shortcuts
- TradeGPT chat remains globally floating
- MobileBottomNav and MobileMenu still functional

### What stayed the same:
- Every feature and component preserved (zero functionality removed)
- Landing page for unauthenticated users untouched
- All modals (Portfolio, Journal, Strategy Builder, etc.) still work
- Alert system, credit system, billing — all unchanged
- Search routes into Research > Company tab

## Stripe Billing (Live: acct_1TLqluE7P86KSLtB)
- 7 live products, webhook at risedual.ai/api/billing/webhook

## Search War Room
- DDG (tenacity retry + semaphore), Wikipedia, SEC EDGAR, Yahoo Finance, FRED
- Token-based CSS theme system with light/dark mode support

## Backlog
- Add FRED_API_KEY for macro data
- Add Groq/OpenRouter/HuggingFace AI analysis layer
- Monitor QuiverQuant insiders/lobbying/govcontracts
- Deploy to risedual.ai
