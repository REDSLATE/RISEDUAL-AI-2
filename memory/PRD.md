# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform by RISEDUAL CORPORATION.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **AI**: Emergent LLM Key (GPT-5.2) with compliance guardrails
- **Payments**: Stripe (4 tiers + credit top-ups)
- **Market Data**: Alpha Vantage 170+ tier (dual key rotation), Finnhub, QuiverQuant

## 4-Tier Credit System
| Plan | Price | Monthly Credits | Top-Up Rate | Unlimited |
|------|-------|----------------|-------------|-----------|
| Free | $0 | 50 | $15/1K | None |
| Starter | $19/mo | 3,000 | $12/1K | None |
| Pro | $55/mo | 15,000 | $8/1K | Chat, War Room |
| Pro Max | $99/mo | 50,000 | $5/1K | Chat, War Room |

## Stripe Billing Integration
- Subscription checkout: Starter/Pro/Pro Max
- Credit top-up checkout: 1K/2K/5K/10K credits
- Customer portal for self-service management
- Webhook processing: checkout completed, invoice paid/failed, subscription lifecycle
- MongoDB collections: `billing_customers`, `billing_webhooks`
- Env vars needed: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_*`

## Key Features
- AI Credit System with frozen dataclass plan config
- Failure Loop (trade idea memory + review + pattern analysis + AI chat warnings)
- AI Compliance Guardrails (impersonal, broadcast-style, no personalized advice)
- Developer API with key management + rate limiting
- Backend resilience: /api/ready health check, isolated startup, ErrorBoundary modals

## Backlog
- Create real Stripe products/prices and configure IDs
- Connect frontend CreditStore to Stripe checkout
- Deploy to `risedual.ai` (2.0 launch after beta)
- QuiverQuant re-test (insiders/lobbying/contracts)
