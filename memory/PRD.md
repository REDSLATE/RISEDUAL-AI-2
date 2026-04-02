# RISEDUALAI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT (TradeAlgo) named **RISEDUALAI**. It should have an independent look but the same trading processes. Requires real market data integration, crypto & dark pool data, functional trading broker connections, a subscription gateway, an AI chat assistant, and a highly complex AI market prediction engine that scrapes financial news, crypto transactions, and real estate data.

## Core Requirements
- Real-time stock/crypto market data (Alpha Vantage)
- Options radar, dark pool, and crypto data
- AI Chat Assistant (GPT-5.2) with vision/image analysis
- Market Prediction engine with web scraping (8+ sources)
- Real estate market scraping
- Subscription pricing ($45/month) via Stripe
- Broker connection (Alpaca)
- "Fidelity x Coinbase" theme (#0F172A navy background, #0052FF blue accents)

## Architecture
- Frontend: React + TailwindCSS + Shadcn UI
- Backend: FastAPI + MongoDB (Motor Async)
- AI: Emergent Integrations (GPT-5.2 via Universal Key)
- Scraping: BeautifulSoup4 + lxml

## What's Been Implemented
- [x] Full trading dashboard UI (Navbar, StockTicker, OptionsRadar, CryptoTicker, DarkPool)
- [x] Alpha Vantage real market data integration
- [x] AI Chat with GPT-5.2 session management
- [x] AI Chat Vision/Screenshot Upload — Users can upload chart images for AI analysis
- [x] Chart Pattern Library — Type `/patterns` to browse 8 common chart patterns with SVG illustrations
- [x] Real Stripe Payment Integration — $45/month subscription checkout via Stripe
- [x] Fidelity x Coinbase Theme Redesign — Deep navy (#0F172A), blue primary (#0052FF), glassmorphism
- [x] Perplexity-style Company Research — AI-synthesized research reports with cited sources
- [x] Market Prediction engine (8+ news sources scraping)
- [x] Real Estate market scraping
- [x] Crypto & Dark Pool data endpoints + UI
- [x] Subscription UI + Quick Trade UI
- [x] Broker Connect UI
- [x] Rebranded to RISEDUALAI
- [x] **World Events Scraping** — RSS scraping from Reuters, BBC, NYT, AP, CNBC with sector impact mapping
- [x] **Foreign Markets Service** — Yahoo Finance scraping for global indices, commodities, currencies with correlation signals
- [x] **Gov Filings Service (Fixed)** — Capitol Trades scraping for congressional stock trades, Fed RSS for announcements, SEC EDGAR + OpenInsider for insider trades (2026-03-31)
- [x] **Macro Data Integration into Market Predictions** — World events, foreign markets, and government filings all feed into GPT-5.2 AI analysis prompt for comprehensive predictions (2026-03-31)
- [x] **Frontend Macro Data Cards** — Market Prediction UI now displays World Events, Foreign Markets, Government Data intelligence cards and Geopolitical Impact assessment (2026-03-31)

- [x] **Macro Intelligence Dashboard** — Bloomberg-terminal-style standalone section with 3 tabs: World Events (RSS news with sector impact mapping, high-impact alerts), Foreign Markets (global indices heatmap, commodities, currencies, correlation signals), Congress Trades (congressional stock trades table, Fed announcements, SEC filings). Accessible from Navbar Platform & Resources dropdowns (2026-04-02)

## Prioritized Backlog
### P1 - Upcoming
- Deployment to custom domain (www.risedual.com) — User needs to configure DNS

### P2 - Future
- Alpha Vantage API upgrade (user currently on free 5/min tier)
- Real Broker Integration (Alpaca OAuth) — Replace mocked trading endpoints
- Refactor server.py (~700 lines) into separate route modules

## Mocked Features
- Broker trading execution (Alpaca) — UI exists, backend mocked

## Key Files
- `/app/backend/services/ai_service.py` — AI chat with vision capability
- `/app/backend/services/gov_filings_service.py` — Congressional trades (Capitol Trades), Fed announcements, SEC filings
- `/app/backend/services/world_events_service.py` — World events with sector impact mapping
- `/app/backend/services/foreign_markets_service.py` — Global indices, commodities, currencies
- `/app/backend/services/market_prediction_service.py` — AI prediction engine consuming all data sources
- `/app/backend/server.py` — All API routes
- `/app/frontend/src/components/MarketPrediction.jsx` — Prediction UI with macro data cards
- `/app/frontend/src/components/TradeGPTChat.jsx` — Chat widget with image upload
