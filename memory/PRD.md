# RISEDUALAI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT (TradeAlgo) named **RISEDUALAI**. It should have an independent look but the same trading processes. Requires real market data integration, crypto & dark pool data, functional trading broker connections, a $50/year subscription gateway, an AI chat assistant, and a highly complex AI market prediction engine that scrapes financial news (CNBC, WSJ, Fox, etc.), crypto transactions, and real estate data.

## Core Requirements
- Real-time stock/crypto market data (Alpha Vantage)
- Options radar, dark pool, and crypto data
- AI Chat Assistant (GPT-5.2) with vision/image analysis
- Market Prediction engine with web scraping (8+ sources)
- Real estate market scraping
- Subscription pricing ($50/year) via Stripe/PayPal
- Broker connection (Alpaca)
- Uniform dark theme (#0a0a0b)

## Architecture
- Frontend: React + TailwindCSS + Shadcn UI
- Backend: FastAPI + MongoDB (Motor Async)
- AI: Emergent Integrations (GPT-5.2 via Universal Key)
- Scraping: BeautifulSoup4 + lxml

## What's Been Implemented
- [x] Full trading dashboard UI (Navbar, StockTicker, OptionsRadar, CryptoTicker, DarkPool)
- [x] Alpha Vantage real market data integration
- [x] AI Chat with GPT-5.2 session management
- [x] **AI Chat Vision/Screenshot Upload** — Users can upload JPEG/PNG/WEBP chart images for AI analysis (2026-03-27)
- [x] Market Prediction engine (8+ news sources scraping)
- [x] Real Estate market scraping
- [x] Crypto & Dark Pool data endpoints + UI
- [x] Subscription UI + Quick Trade UI
- [x] Broker Connect UI
- [x] Uniform dark theme (#0a0a0b) across all components
- [x] Rebranded to RISEDUALAI

## Prioritized Backlog
### P1 - Upcoming
- Real Payment Integration (Stripe) — Blocked on user providing Stripe API keys
- Deployment to custom domain (www.risedual.com) — Blocked on Stripe + user confirmation

### P2 - Future
- Real Broker Integration (Alpaca OAuth) — Replace mocked trading endpoints
- Refactor server.py (~600 lines) into separate route modules

## Mocked Features
- Broker trading execution (Alpaca) — UI exists, backend mocked
- Payment processing (Stripe/PayPal) — UI exists, backend mocked pending API keys

## Key Files
- `/app/backend/services/ai_service.py` — AI chat with vision capability
- `/app/frontend/src/components/TradeGPTChat.jsx` — Chat widget with image upload
- `/app/backend/server.py` — All API routes
- `/app/backend/services/financial_scraping_service.py` — News scraping (8+ sources)
- `/app/backend/services/real_estate_scraping_service.py` — Real estate data
