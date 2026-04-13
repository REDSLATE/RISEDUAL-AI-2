# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform by RED SLATE HOLDINGS.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **Auth**: httpOnly secure cookies (JWT), 90s fetch timeout for AI endpoints
- **AI**: Emergent LLM Key (GPT-5.2, Claude Sonnet 4.5, Gemini)
- **Payments**: Stripe ($45/month Pro subscription)
- **Market Data**: Alpha Vantage 170+ tier (dual key rotation) with yfinance fallback, Finnhub
- **Alternative Data**: QuiverQuant (congressional trading active; others pending)
- **Email**: Resend

## Legal Entity
- **Name**: RED SLATE HOLDINGS (formerly RISEDUAL CORPORATION)
- **State**: Florida
- **Contact**: legal@risedual.ai

## Legal Compliance (April 13, 2026)
- **Terms of Service**: 15 sections covering eligibility (18+ with digital asset capacity), permitted use (anti-manipulation), subscription (no partial refunds), AI content disclosure, limitation of liability
- **AI Transparency Notice**: New legal tab — GPT-5.2 disclosure, 4-Mind architecture, human oversight, impersonal content guarantee, broadcast signals
- **Privacy Policy**: 11 sections covering data collection, third-party sharing, AES-256 encryption
- **Risk Disclosure**: 9 sections covering trading, AI, options, crypto, dark pool risks
- **Disclaimer**: 10 sections covering no fiduciary relationship, no warranty

## Core Features (All Implemented)
- Real-time stock & crypto tickers, Options Radar, Dark Pool
- AI War Room, AI Intelligence Hub, AI Investment Hypothesis
- Multimodal AI Chat + Voice (TTS/STT), Persistent Chat Memory
- Company Research, Market Predictions, Sector Heatmap + AI Sentiment
- P&L Tracker, 8 Broker Integrations, Stripe Gateway
- Admin Panel (Cache, Security Audit, Media, Waitlist)
- Referral System + Badge Showcase + Public Profiles
- Market Vector Memory (ChromaDB), VAPID Push, Order Flow Heatmaps
- Whale Radar, Paper Trading, Portfolio AI Agent
- Smart Orders, Risk Calculator, Market Scanner + AI Validation
- Trading Bots (Grid/Signal/Webhook), Help Center, Onboarding Tour

## Backlog
- P1: Deploy to `risedual.ai` after beta testing (2.0 launch)
- QuiverQuant: Re-test insiders/lobbying/contracts when server stabilizes
- Backend prompt guardrails: Enforce impersonal/broadcast language in AI prompts
