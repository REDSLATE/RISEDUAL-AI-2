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

## Stripe Billing (acct_1TLqluE7P86KSLtB)
- Products/prices created, webhook at `https://risedual.ai/api/billing/webhook`
- Webhook secret: configured in .env

## Key Features (All Completed)
- AI Credit System, Failure Loop, AI Compliance Guardrails
- Developer API, Backend resilience, Onboarding Tour
- QuiverQuant congressional trading (Bearer auth)

## Code Quality Audit (2026-04-13)
### Critical Fixes Applied:
- Removed hardcoded credentials from 13+ test files → all use conftest_creds.py
- Fixed empty catch blocks in RuleBuilder.jsx → proper error logging
- Fixed array index as key in OrderFlowHeatmap.jsx and MemoryDashboard.jsx
- localStorage usage reviewed: only stores UI preferences (tour_completed, promo_dismissed) — no sensitive data

### Important Refactors Applied:
- risk_calculator.py: Extracted `_validate_trade_direction()`, `_calculate_position_size()`, `_compute_tp_details()` helpers
- scanner.py: Simplified `validate_scan_results()` summary computation

### Reviewed but No Changes Needed:
- backtester_service.py: Already uses AST-safe evaluation (no eval/exec)
- React hook dependencies: Module-level constants (API, authFetch) are stable refs — ESLint false positives
- AuthContext callbacks: Properly structured with correct dependency arrays
- localStorage: Only non-sensitive UI state (tour flag, promo dismiss)

## Deployment Status
- Deployment pre-check: PASSED
- FRONTEND_URL: https://risedual.ai
- Ready for beta 2.0 deploy

## Post-Deploy Checklist
- Configure DNS for risedual.ai
- Verify Stripe webhook at production URL
- Update STRIPE_PUBLISHABLE_KEY from Stripe Dashboard
- Monitor QuiverQuant insiders/lobbying/govcontracts recovery
