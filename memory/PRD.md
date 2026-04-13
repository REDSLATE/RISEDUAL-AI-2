# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform by RISEDUAL CORPORATION.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **AI**: Emergent LLM Key (GPT-5.2, Claude Sonnet 4.5, Gemini)
- **Payments**: Stripe (4 tiers)
- **Market Data**: Alpha Vantage 170+ tier (dual key rotation), Finnhub, QuiverQuant

## Pricing Model (4-Tier Credit System)
| Plan | Price | Monthly Credits | Top-Up Rate | Unlimited Features |
|------|-------|----------------|-------------|-------------------|
| Free | $0 | 50 | $15/1K | None |
| Starter | $19/mo | 3,000 | $12/1K | None |
| Pro | $55/mo | 15,000 | $8/1K | AI Chat, War Room |
| Pro Max | $99/mo | 50,000 | $5/1K | AI Chat, War Room |

**Credit Costs**: Chat 1cr, War Room 5cr, Hypothesis 3cr, Prediction 3cr, Intelligence 2cr, Scanner 2cr, API 1cr
**Founding 100**: Pro pricing locked for life
**Top-Up Tiers**: 500, 1000, 2500, 5000 credits (price varies by plan)

## Core Features (All Implemented)
[Full feature list in previous PRD — all features operational]

## Key Endpoints
- `/api/credits/balance` — plan-aware balance with topup_rate
- `/api/credits/plans` — all 4 plans
- `/api/credits/topups` — plan-specific top-up pricing
- `/api/credits/costs` — per-action costs with unlimited flags
- `/api/credits/purchase` — buy top-ups (MOCKED — no Stripe yet)
- `/api/credits/history` — usage event log
- `/api/credits/matrix` — full public pricing matrix

## Backlog
- P0: Integrate Stripe for credit top-ups + subscription plans
- P0: Grant monthly credits on subscription activation/renewal
- P1: Deploy to `risedual.ai` (2.0 launch after beta)
- P2: QuiverQuant re-test (insiders/lobbying/contracts)
- P2: Backend AI prompt guardrails
