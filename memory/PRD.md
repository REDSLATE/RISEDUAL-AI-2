# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI
- **Backend**: FastAPI + MongoDB + APScheduler
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **Payments**: Stripe Live Mode
- **Market Data**: Alpha Vantage -> Finnhub -> TwelveData (ProviderRouter)
- **Search**: War Room (DDG + Tavily + Wikipedia + SEC + FRED + Yahoo + AI Analysis + StockFit)
- **Domain**: risedual.ai

## ProviderRouter System (Complete)
- Error classification, tiered cooldowns, latency tracking, MongoDB persistence
- Dynamic Registration, Health Heartbeat, Enable/Disable, Parallel Orchestration
- Wired into: ai_service, market_data_service, company_research, prediction, war room, email, financial tools agent

## Predictions — Stale-While-Revalidate (Complete)
- Instant cache-first responses, background refresh jobs, APScheduler prewarm every 10 min

## Financial Tools Agent v2 (Complete — Refactored)
- Split into two modules:
  - `financial_tools.py`: TOOL_SCHEMAS (6 tools), SYSTEM_PROMPT, all _exec_* implementations, run_tool dispatcher
  - `financial_tools_agent.py`: AgentState, FinancialToolsAgent (state machine + SSE streaming)
- `get_sec_fundamentals` dispatches 7 SEC data types via StockFit: financials, insiders, insider_summary, earnings, scores, earnings_calendar, fund_holders

## Headlines Pipeline (Complete)
- 8 sources, 15min scheduler, content-hash dedup, feeds prediction engine

## KeyVault (Complete)
- AES-256-GCM encrypted DB storage for dynamic platform API keys

## Deployment: PASS — ready for risedual.ai

## Backlog
- P1: Add API keys (OPENAI, ANTHROPIC, TWELVEDATA, FRED, SENDGRID) via KeyVault
- P0: Deploy to risedual.ai (pending user production testing)
- P2: QuiverQuant monitoring (blocked on their server stability)
- P2: StockFit 13F holder tracking & SEC filing change alerts (pending user API plan upgrade)
