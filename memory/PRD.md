# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform by RISEDUAL CORPORATION.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **Auth**: httpOnly secure cookies (JWT)
- **AI**: Emergent LLM Key (GPT-5.2, Claude Sonnet 4.5, Gemini)
- **Payments**: Stripe ($55/month Pro)
- **Market Data**: Alpha Vantage 170+ tier (dual key rotation) + yfinance fallback, Finnhub
- **Alternative Data**: QuiverQuant (congressional trading active)

## Pricing Model (Credit-Based)
- **Signup**: 50 free credits
- **Credit Packs**: Starter $5/100cr, Explorer $20/500cr, Power $45/1500cr
- **Pro ($55/mo)**: 5,000 credits/month + unlimited AI Chat & War Room
- **Pro Top-Up**: 2,000 credits/$15 (Pro subscribers only)
- **Credit Costs**: Chat 1cr, War Room 5cr (Pro FREE), Hypothesis 3cr, Prediction 3cr, Intelligence 2cr, Scanner 2cr, API 1cr
- **Founding 100**: Locked at $45/month for life

## Core Features (All Implemented)
- Real-time stock & crypto, Options Radar, Dark Pool
- AI War Room, Intelligence Hub, Investment Hypothesis
- Multimodal AI Chat + Voice, Persistent Memory
- Market Predictions, Sector Heatmap, P&L Tracker
- 8 Broker Integrations, Stripe Gateway
- Admin Panel, Referral System + Badges + Public Profiles
- Smart Orders, Risk Calculator, Market Scanner + AI Validation
- Trading Bots, Help Center, Onboarding Tour
- Developer API (key management, rate limiting, 7 endpoints)
- AI Credit System (balance, packs, costs, history, deduction)
- Investment Risk Disclosure (footer + Stripe checkout checkbox)
- Legal: Terms, AI Transparency, Privacy, Risk, Disclaimer

## Backlog
- Integrate Stripe checkout for credit pack purchases
- Grant Pro monthly credits on subscription activation
- Deploy to `risedual.ai` after beta testing (2.0 launch)
- QuiverQuant: Re-test insiders/lobbying/contracts
- Backend prompt guardrails for AI compliance
