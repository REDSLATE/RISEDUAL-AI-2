# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform by RISEDUAL CORPORATION.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **AI**: Emergent LLM Key (GPT-5.2) with compliance guardrails
- **Payments**: Stripe (4 tiers + credit top-ups)
- **Market Data**: Alpha Vantage 170+ tier (dual key rotation), Finnhub, QuiverQuant
- **Domain**: risedual.ai

## 4-Tier Credit System
| Plan | Price | Monthly Credits | Top-Up Rate | Unlimited |
|------|-------|----------------|-------------|-----------|
| Free | $0 | 50 | $15/1K | None |
| Starter | $19/mo | 3,000 | $12/1K | None |
| Pro | $55/mo | 15,000 | $8/1K | Chat, War Room |
| Pro Max | $99/mo | 50,000 | $5/1K | Chat, War Room |

## Stripe Billing Integration (New Account: acct_1TLqluE7P86KSLtB)
- Products and prices created on new Stripe account (2026-04-13)
- Webhook endpoint: `https://risedual.ai/api/billing/webhook` (ID: we_1TLrWVE7P86KSLtBqIjS5xrx)
- Events: checkout.session.completed, invoice.paid/failed, subscription lifecycle
- All price IDs and webhook secret configured in backend .env
- Old billing_customers collection cleared for fresh account

## Key Features (All Completed)
- AI Credit System with frozen dataclass plan config
- Failure Loop (trade idea memory + review + pattern analysis + AI chat warnings)
- AI Compliance Guardrails (impersonal, broadcast-style, no personalized advice)
- Developer API with key management + rate limiting
- Backend resilience: /api/ready health check, isolated startup, ErrorBoundary modals
- Onboarding Tour with smart tooltip positioning
- QuiverQuant API fixed (Bearer auth), congressional trading live

## QuiverQuant API Status (Updated 2026-04-13)
- Auth changed from `Token` to `Bearer` — fixed in quiver_service.py
- Congressional trading: WORKING (live data via QuiverQuant)
- Insiders, Lobbying, Gov Contracts: Still HTTP 500 on QuiverQuant server — graceful fallback active

## Deployment Status
- Deployment pre-check: PASSED (no hardcoded URLs, env vars clean, supervisor valid)
- FRONTEND_URL updated to https://risedual.ai
- Ready for beta 2.0 deploy via Emergent platform

## Post-Deploy Checklist
- User to configure custom domain DNS for risedual.ai
- Verify Stripe webhook receives events at production URL
- Update STRIPE_PUBLISHABLE_KEY in .env from Stripe Dashboard (Developers → API Keys)
- Monitor QuiverQuant insider/lobbying/govcontracts for recovery
