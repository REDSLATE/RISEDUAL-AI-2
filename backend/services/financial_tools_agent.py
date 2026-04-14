"""Financial Tools Agent — Tool-calling agent loop for TradeGPT.

Adapted from user-provided LangChain pattern. Uses native OpenAI tool calling
through the ProviderRouter with financial calculators + web search + price lookup.

Usage:
    agent = FinancialToolsAgent(db)
    result = await agent.run("What would $5000 in AAPL be worth in 5 years?", session_id="abc")
    # result = {"text": "...", "provider": {...}, "tools_used": ["search", "compound_growth"]}
"""
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from services.providerrouter import ProviderRouter
from services.provider_registry import get_ai_provider_pool

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  TOOL DEFINITIONS (for OpenAI function calling)
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
            "description": "Search the web for current financial data, news, or market information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are a precise financial research assistant inside the RISEDUAL AI trading platform. "
    "Use tools for web lookup, stock prices, and ALL calculations — never guess numbers. "
    "If a user asks for future value based on a growth rate, first determine a defensible "
    "annualized rate from retrieved evidence (historical CAGR, analyst estimates, etc.), "
    "or state clearly that the rate is not available from the provided data. "
    "Present results with clear formatting and cite your data sources. "
    "Always remind users that past performance does not guarantee future results."
)

MAX_TOOL_ROUNDS = 6


# ─────────────────────────────────────────────
#  TOOL IMPLEMENTATIONS
# ─────────────────────────────────────────────

def _exec_compound_growth(principal: float, annual_rate_pct: float, years: int) -> dict:
    future_value = principal * ((1 + annual_rate_pct / 100) ** years)
    gain = future_value - principal
    return {
        "principal": round(principal, 2),
        "annual_rate_pct": round(annual_rate_pct, 4),
        "years": years,
        "future_value": round(future_value, 2),
        "gain": round(gain, 2),
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


async def _exec_web_search(query: str) -> dict:
    try:
        from ddgs import DDGS
        import asyncio
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


TOOL_DISPATCH = {
    "calculate_compound_growth": lambda args: _exec_compound_growth(**args),
    "calculate_cagr": lambda args: _exec_cagr(**args),
    "get_stock_quote": None,  # async
    "web_search": None,  # async
}

ASYNC_TOOLS = {"get_stock_quote", "web_search"}


async def _run_tool(name: str, args: dict) -> str:
    if name == "get_stock_quote":
        result = await _exec_stock_quote(args["symbol"])
    elif name == "web_search":
        result = await _exec_web_search(args["query"])
    elif name in TOOL_DISPATCH:
        result = TOOL_DISPATCH[name](args)
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result)


# ─────────────────────────────────────────────
#  AGENT LOOP
# ─────────────────────────────────────────────

class FinancialToolsAgent:
    def __init__(self, db=None):
        self.db = db
        self.router = ProviderRouter("ai_tools", get_ai_provider_pool(), db=db)

    async def _call_llm(self, provider: dict, messages: list) -> dict:
        """Call the LLM with tool definitions via OpenAI-compatible API."""
        p = provider.get("provider")
        api_key = provider.get("api_key")
        model = provider.get("model", "gpt-5.2")

        from openai import AsyncOpenAI

        # For Emergent keys, use the Emergent proxy
        if api_key.startswith("sk-emergent"):
            from emergentintegrations.llm.chat import get_integration_proxy_url
            proxy_url = get_integration_proxy_url()
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=f"{proxy_url}/llm",
                default_headers={"x-emergent-api-key": api_key},
            )
        elif p == "openai":
            client = AsyncOpenAI(api_key=api_key)
        elif p == "anthropic":
            # Anthropic tool calling via httpx
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
        """Anthropic tool calling with format conversion."""
        anthropic_tools = [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"],
            }
            for t in TOOL_SCHEMAS
        ]
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 2000,
                    "system": SYSTEM_PROMPT,
                    "messages": [m for m in messages if m["role"] != "system"],
                    "tools": anthropic_tools,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Anthropic {resp.status_code}")
            data = resp.json()
            content_blocks = data.get("content", [])
            text = ""
            tool_calls = []
            for block in content_blocks:
                if block.get("type") == "text":
                    text += block["text"]
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block["input"]),
                        },
                    })

            class _Msg:
                def __init__(self, content, tc):
                    self.content = content
                    self.tool_calls = tc or None
                    self.role = "assistant"
            return _Msg(text, tool_calls if tool_calls else None)

    async def run(self, user_message: str, session_id: str = "") -> dict:
        """Execute the tool-calling agent loop."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        tools_used = []

        for round_num in range(MAX_TOOL_ROUNDS):
            # Call LLM through provider router
            routed = await self.router.run(
                lambda provider: self._call_llm(provider, messages)
            )
            ai_msg = routed["result"]
            provider_meta = routed["provider"]

            # Check if the message has tool calls
            if not ai_msg.tool_calls:
                # Final answer — no more tools needed
                final_text = ai_msg.content or ""
                return {
                    "text": final_text,
                    "provider": provider_meta,
                    "tools_used": tools_used,
                    "rounds": round_num + 1,
                }

            # Process tool calls
            # Add assistant message with tool calls to history
            assistant_dict = {"role": "assistant", "content": ai_msg.content or ""}
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
            assistant_dict["tool_calls"] = tc_list
            messages.append(assistant_dict)

            for tc in tc_list:
                tool_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}

                tools_used.append(tool_name)
                logger.info(f"[FinancialAgent] Tool call: {tool_name}({json.dumps(args)[:100]})")

                result_str = await _run_tool(tool_name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                })

        # Hit max rounds — return whatever we have
        return {
            "text": "I've reached the maximum number of analysis steps. Here's what I found so far based on the tools I used.",
            "provider": provider_meta if 'provider_meta' in dir() else None,
            "tools_used": tools_used,
            "rounds": MAX_TOOL_ROUNDS,
        }
