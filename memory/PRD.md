# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **AI**: Emergent LLM Key (GPT-5.2) + ProviderRouter failover + Financial Tools Agent v2
- **Payments**: Stripe Live Mode (4 tiers + credit top-ups)
- **Market Data**: Alpha Vantage, Finnhub, TwelveData (via ProviderRouter)
- **Web Intelligence**: Search War Room (DDG + Wikipedia + SEC + FRED + Yahoo + AI Analysis)
- **Domain**: risedual.ai

## RiseDualGPT Chat (renamed from TradeGPT)
- Main component: `RiseDualGPTChat.jsx`
- Old `TradeGPTChat.jsx` deleted (dead file)
- Branding: "RiseDualGPT" in header, help center, welcome screen

## Financial Tools Agent v2 (LangGraph-Inspired)
State graph: `START → agent → tools_condition → tools → agent → ... → END`
- **Tools**: `get_stock_quote`, `web_search`, `calculate_compound_growth`, `calculate_cagr`, `get_daily_history`
- **Evidence-based**: Derives growth rates from actual price history via `get_daily_history` CAGR calculation
- **Step limit**: MAX_STEPS = 8 (prevents infinite loops)
- **SSE streaming**: `GET /api/chat/agent-stream` streams real-time tool execution events
- **Frontend trace UI**: Live "Agent Working" panel showing tool execution with spinners → checkmarks + result values
- **Auto-routing**: Pattern regex in chat detects calculation queries → uses SSE stream → falls back to standard chat

## Headlines Pipeline
Background scrape → clean → store (8 financial sources), 15min scheduler, feeds prediction engine

## ProviderRouter System
Error classification, tiered cooldowns, latency tracking, MongoDB persistence

## Deployment: PASS — ready for risedual.ai

## Backlog
- P1: Add API keys (OPENAI, ANTHROPIC, TWELVEDATA, FRED)
- P1: Extend ProviderRouter to email_service
- P0: Deploy to risedual.ai
