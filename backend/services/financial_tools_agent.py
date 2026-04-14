"""Financial Tools Agent v2 — LangGraph-inspired state machine with SSE streaming.

State graph: START → agent → (tools_condition) → tools → agent → ... → END
Step limit prevents infinite loops. SSE endpoint streams progress in real-time.

Usage:
    agent = FinancialToolsAgent(db)
    result = await agent.run("What would $5K in AAPL be worth in 5 years?")
    async for event in agent.stream("Calculate CAGR from 100 to 500 over 3 years"):
        print(event)  # {"node": "tools", "tool": "calculate_cagr", "result": {...}}
"""
import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from services.providerrouter import ProviderRouter
from services.provider_registry import get_ai_provider_pool

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  TOOL SCHEMAS (OpenAI function calling format)
# ─────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "calculate_compound_growth",
            "description": "Calculate future value of an investment using annual compounding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "principal": {"type": "number", "description": "Starting investment amount in dollars"},
                    "annual_rate_pct": {"type": "number", "description": "Annual growth rate as a percent, e.g. 8 for 8%"},
                    "years": {"type": "integer", "description": "Number of years"},
                },
                "required": ["principal", "annual_rate_pct", "years"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_cagr",
            "description": "Calculate compound annual growth rate (CAGR) from start and end values.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_value": {"type": "number", "description": "Beginning value"},
                    "end_value": {"type": "number", "description": "Ending value"},
                    "years": {"type": "number", "description": "Elapsed years"},
                },
                "required": ["start_value", "end_value", "years"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": "Get current real-time stock price, change, volume for a ticker symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock ticker symbol, e.g. AAPL, MSFT, TSLA"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current financial data, news, growth rates, or market information. Use this to find defensible annualized rates or historical performance data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_history",
            "description": "Get daily OHLCV price history for a stock to derive historical returns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock ticker symbol"},
                    "period": {"type": "string", "description": "compact (3 months) or full (20 years)", "enum": ["compact", "full"]},
                },
                "required": ["symbol"],
            },
        },
    },
]

# LangGraph-inspired system prompt: evidence-based, no guessing
SYSTEM_PROMPT = (
    "You are a precise financial research assistant inside the RISEDUAL AI trading platform. "
    "Use tools for all math and external data lookups — never guess numbers. "
    "Do not guess annual growth rates. "
    "If a projection requires a growth rate and none is given by the user, you MUST either: "
    "(a) derive one from evidence using tools (historical CAGR via get_daily_history, "
    "analyst estimates via web_search), or "
    "(b) clearly state that the rate is not available and explain what data is missing. "
    "When deriving a rate, show your work: cite the source data, time period, and calculation. "
    "Present results with clear markdown formatting. "
    "Always remind users that past performance does not guarantee future results."
)

MAX_STEPS = 8  # From LangGraph version's step limit


# ─────────────────────────────────────────────
#  TOOL IMPLEMENTATIONS
# ─────────────────────────────────────────────

def _exec_compound_growth(principal: float, annual_rate_pct: float, years: int) -> dict:
    fv = principal * ((1 + annual_rate_pct / 100) ** years)
    return {
        "principal": round(principal, 2),
        "annual_rate_pct": round(annual_rate_pct, 4),
        "years": years,
        "future_value": round(fv, 2),
        "gain": round(fv - principal, 2),
    }


def _exec_cagr(start_value: float, end_value: float, years: float) -> dict:
    cagr = ((end_value / start_value) ** (1 / years) - 1) * 100
    return {
        "start_value": round(start_value, 2),
        "end_value": round(end_value, 2),
        "years": years,
        "cagr_pct": round(cagr, 4),
    }


async def _exec_stock_quote(symbol: str) -> dict:
    try:
        from services.price_provider import get_quote
        quote = await get_quote(symbol)
        if quote:
            return {
                "symbol": symbol.upper(),
                "price": quote.get("price"),
                "change": quote.get("change"),
                "change_pct": quote.get("change_pct"),
                "volume": quote.get("volume"),
                "prev_close": quote.get("prev_close"),
                "high": quote.get("high"),
                "low": quote.get("low"),
                "source": quote.get("source", "pool"),
            }
        return {"error": f"No quote data found for {symbol}"}
    except Exception as e:
        return {"error": f"Quote lookup failed: {str(e)[:100]}"}


async def _exec_daily_history(symbol: str, period: str = "compact") -> dict:
    """Get price history to derive historical CAGR."""
    try:
        from services.price_provider import get_daily_history
        data = await get_daily_history(symbol, period)
        if not data or len(data) < 2:
            return {"error": f"No history for {symbol}"}
        newest = data[0]
        oldest = data[-1]
        days = len(data)
        years = days / 252  # trading days
        start_price = oldest["close"]
        end_price = newest["close"]
        cagr = ((end_price / start_price) ** (1 / years) - 1) * 100 if years > 0 else 0
        return {
            "symbol": symbol.upper(),
            "period_days": days,
            "period_years": round(years, 2),
            "start_date": oldest["date"],
            "end_date": newest["date"],
            "start_price": start_price,
            "end_price": end_price,
            "historical_cagr_pct": round(cagr, 2),
            "total_return_pct": round((end_price / start_price - 1) * 100, 2),
        }
    except Exception as e:
        return {"error": f"History fetch failed: {str(e)[:100]}"}


async def _exec_web_search(query: str) -> dict:
    try:
        from ddgs import DDGS
        results = await asyncio.to_thread(
            lambda: list(DDGS().text(query, max_results=4))
        )
        return {
            "query": query,
            "results": [
                {"title": r.get("title", ""), "body": r.get("body", ""), "url": r.get("href", "")}
                for r in results
            ],
        }
    except Exception as e:
        return {"query": query, "error": f"Search failed: {str(e)[:100]}", "results": []}


async def _run_tool(name: str, args: dict) -> str:
    if name == "calculate_compound_growth":
        result = _exec_compound_growth(**args)
    elif name == "calculate_cagr":
        result = _exec_cagr(**args)
    elif name == "get_stock_quote":
        result = await _exec_stock_quote(args["symbol"])
    elif name == "get_daily_history":
        result = await _exec_daily_history(args["symbol"], args.get("period", "compact"))
    elif name == "web_search":
        result = await _exec_web_search(args["query"])
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result)


# ─────────────────────────────────────────────
#  STATE GRAPH (LangGraph-inspired)
# ─────────────────────────────────────────────

class AgentState:
    """Mimics LangGraph's TypedDict state with message accumulation."""
    __slots__ = ("messages", "steps", "tools_used", "trace", "provider_meta")

    def __init__(self):
        self.messages: List[dict] = []
        self.steps: int = 0
        self.tools_used: List[str] = []
        self.trace: List[dict] = []  # Node execution trace for streaming
        self.provider_meta: Optional[dict] = None


class FinancialToolsAgent:
    def __init__(self, db=None):
        self.db = db
        self.router = ProviderRouter("ai_tools", get_ai_provider_pool(), db=db)

    async def _call_llm(self, provider: dict, messages: list):
        """Call the LLM with tool definitions via OpenAI-compatible API."""
        api_key = provider.get("api_key")
        model = provider.get("model", "gpt-5.2")
        p = provider.get("provider")

        from openai import AsyncOpenAI

        if api_key.startswith("sk-emergent"):
            from emergentintegrations.llm.chat import get_integration_proxy_url
            proxy_url = get_integration_proxy_url()
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=f"{proxy_url}/llm",
                default_headers={"x-emergent-api-key": api_key},
            )
        elif p == "anthropic":
            return await self._call_anthropic_tools(api_key, model, messages)
        else:
            client = AsyncOpenAI(api_key=api_key)

        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            temperature=0,
        )
        return response.choices[0].message

    async def _call_anthropic_tools(self, api_key: str, model: str, messages: list):
        anthropic_tools = [
            {"name": t["function"]["name"], "description": t["function"]["description"],
             "input_schema": t["function"]["parameters"]}
            for t in TOOL_SCHEMAS
        ]
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                json={"model": model, "max_tokens": 2000, "system": SYSTEM_PROMPT,
                      "messages": [m for m in messages if m["role"] != "system"], "tools": anthropic_tools},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Anthropic {resp.status_code}")
            data = resp.json()
            text, tool_calls = "", []
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block["text"]
                elif block.get("type") == "tool_use":
                    tool_calls.append({"id": block["id"], "type": "function",
                                       "function": {"name": block["name"], "arguments": json.dumps(block["input"])}})

            class _Msg:
                def __init__(self, c, tc):
                    self.content = c
                    self.tool_calls = tc or None
                    self.role = "assistant"
            return _Msg(text, tool_calls if tool_calls else None)

    # ── Graph nodes ──

    async def _node_agent(self, state: AgentState) -> str:
        """Agent node: call LLM, returns 'tools' or 'end'."""
        routed = await self.router.run(
            lambda provider: self._call_llm(provider, state.messages)
        )
        ai_msg = routed["result"]
        state.provider_meta = routed["provider"]
        state.steps += 1

        if not ai_msg.tool_calls:
            state.trace.append({"node": "agent", "action": "final_answer", "step": state.steps})
            # Append final message
            state.messages.append({"role": "assistant", "content": ai_msg.content or ""})
            return "end"

        # Append assistant message with tool calls
        tc_list = []
        for tc in ai_msg.tool_calls:
            if hasattr(tc, "function"):
                tc_list.append({
                    "id": tc.id if hasattr(tc, "id") else tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.function.name if hasattr(tc.function, "name") else tc["function"]["name"],
                        "arguments": tc.function.arguments if hasattr(tc.function, "arguments") else tc["function"]["arguments"],
                    },
                })
            else:
                tc_list.append(tc)

        state.messages.append({"role": "assistant", "content": ai_msg.content or "", "tool_calls": tc_list})
        state.trace.append({"node": "agent", "action": "tool_calls",
                            "tools": [tc["function"]["name"] for tc in tc_list], "step": state.steps})
        return "tools"

    async def _node_tools(self, state: AgentState) -> str:
        """Tools node: execute all pending tool calls, always returns 'agent'."""
        last_msg = state.messages[-1]
        tool_calls = last_msg.get("tool_calls", [])

        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}

            state.tools_used.append(name)
            logger.info(f"[FinancialAgent] step={state.steps} tool={name}({json.dumps(args)[:80]})")

            result_str = await _run_tool(name, args)
            state.messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_str,
            })
            state.trace.append({"node": "tools", "tool": name, "args": args,
                                "result": json.loads(result_str), "step": state.steps})

        return "agent"

    def _stop_condition(self, state: AgentState, next_node: str) -> str:
        """LangGraph-style conditional edge: stop if too many steps."""
        if state.steps >= MAX_STEPS:
            return "end"
        return next_node

    # ── Public API ──

    async def run(self, user_message: str, session_id: str = "") -> dict:
        """Execute the full agent graph: START → agent → tools → agent → ... → END."""
        state = AgentState()
        state.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        current_node = "agent"
        while current_node != "end":
            if current_node == "agent":
                next_node = await self._node_agent(state)
                current_node = self._stop_condition(state, next_node)
            elif current_node == "tools":
                next_node = await self._node_tools(state)
                current_node = self._stop_condition(state, next_node)
            else:
                break

        # Extract final answer from last assistant message
        final_text = ""
        for msg in reversed(state.messages):
            if msg["role"] == "assistant" and msg.get("content"):
                final_text = msg["content"]
                break

        if not final_text and state.steps >= MAX_STEPS:
            final_text = "I reached the analysis step limit. Here's what I gathered from the tools I used."

        return {
            "text": final_text,
            "provider": state.provider_meta,
            "tools_used": state.tools_used,
            "rounds": state.steps,
            "trace": state.trace,
        }

    async def stream(self, user_message: str, session_id: str = "") -> AsyncGenerator[dict, None]:
        """Stream agent execution events (for SSE endpoint).
        Yields events like:
            {"node": "agent", "action": "tool_calls", "tools": ["get_stock_quote"]}
            {"node": "tools", "tool": "get_stock_quote", "result": {...}}
            {"node": "agent", "action": "final_answer", "text": "..."}
        """
        state = AgentState()
        state.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        current_node = "agent"
        while current_node != "end":
            if current_node == "agent":
                next_node = await self._node_agent(state)
                # Yield the latest trace entry
                if state.trace:
                    event = {**state.trace[-1]}
                    if next_node == "end":
                        # Include final text
                        for msg in reversed(state.messages):
                            if msg["role"] == "assistant" and msg.get("content"):
                                event["text"] = msg["content"]
                                break
                        event["provider"] = state.provider_meta
                    yield event
                current_node = self._stop_condition(state, next_node)

            elif current_node == "tools":
                prev_trace_len = len(state.trace)
                next_node = await self._node_tools(state)
                # Yield each tool execution
                for entry in state.trace[prev_trace_len:]:
                    yield entry
                current_node = self._stop_condition(state, next_node)

        # Final summary event
        yield {
            "node": "end",
            "tools_used": state.tools_used,
            "rounds": state.steps,
            "provider": state.provider_meta,
        }
