# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform by RISEDUAL CORPORATION.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **AI**: Emergent LLM Key (GPT-5.2, Claude Sonnet 4.5, Gemini) with compliance guardrails
- **Payments**: Stripe (4 tiers)
- **Market Data**: Alpha Vantage 170+ tier (dual key rotation), Finnhub, QuiverQuant

## AI Compliance Guardrails (April 13, 2026)
- Central `ai_guardrails.py` module with `COMPLIANCE_FOOTER` and `COMPLIANCE_AGENT_FOOTER`
- Injected into all 8 AI services: Chat, Portfolio Agent, Market Predictions, War Room, Hypothesis, Prediction Crew, AI Intelligence, Signal Validator
- Language: "financial research publishing platform", "signals indicate" not "you should", BULLISH/BEARISH/NEUTRAL not BUY/SELL/HOLD
- Every substantive response includes risk disclaimer
- All signals are impersonal and broadcast-style

## 4-Tier Credit System
| Plan | Price | Monthly Credits | Top-Up Rate | Unlimited |
|------|-------|----------------|-------------|-----------|
| Free | $0 | 50 | $15/1K | None |
| Starter | $19/mo | 3,000 | $12/1K | None |
| Pro | $55/mo | 15,000 | $8/1K | Chat, War Room |
| Pro Max | $99/mo | 50,000 | $5/1K | Chat, War Room |

## Backlog
- P0: Integrate Stripe for credit top-ups + subscription plans
- P1: Deploy to `risedual.ai` (2.0 launch after beta)
- P2: QuiverQuant re-test (insiders/lobbying/contracts)
