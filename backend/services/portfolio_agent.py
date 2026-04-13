"""Portfolio Agent — AI with tool calling for paper trading portfolio analysis.
Uses LiteLLM (Emergent key) with OpenAI function calling for agentic portfolio queries.
"""
import os
import json
import logging
from typing import Optional
import litellm
from emergentintegrations.llm.utils import get_integration_proxy_url

from services.paper_trading_service import (
    get_portfolio_snapshot, get_trade_history, get_portfolio_context,
    place_paper_order_intent, confirm_paper_order, get_pending_orders
)

logger = logging.getLogger(__name__)

EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")
PROXY_URL = get_integration_proxy_url() + "/llm"

# Tool definitions (OpenAI function calling schema)
PORTFOLIO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_snapshot",
            "description": "Get the user's current paper trading portfolio including cash, equity, total P&L, and all open positions with live market prices and unrealized gains/losses.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_position_detail",
            "description": "Get detailed information for one specific position by ticker symbol, including qty, avg cost, live price, market value, and unrealized P&L.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Ticker symbol like AAPL, NVDA, BTC"
                    }
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_trade_history",
            "description": "Get the user's recent paper trade history. Optionally filter by a specific ticker symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Optional ticker to filter trades"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max trades to return (default 10)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_watchlist_news",
            "description": "Get relevant market news headlines for tickers in the user's portfolio or watchlist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Optional ticker to filter news"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "place_paper_order_intent",
            "description": "Create a proposed paper order that requires a separate user confirmation step before execution. NEVER skip this step — always propose first, then ask the user to confirm. Supports MARKET and LIMIT orders for stocks and crypto.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Ticker symbol like AAPL, NVDA, BTC"
                    },
                    "side": {
                        "type": "string",
                        "enum": ["BUY", "SELL"],
                        "description": "BUY or SELL"
                    },
                    "qty": {
                        "type": "number",
                        "description": "Number of shares/units to trade"
                    },
                    "order_type": {
                        "type": "string",
                        "enum": ["MARKET", "LIMIT"],
                        "description": "MARKET for immediate execution at current price, LIMIT for specified price"
                    },
                    "limit_price": {
                        "type": "number",
                        "description": "Required for LIMIT orders — the target price"
                    }
                },
                "required": ["symbol", "side", "qty", "order_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_paper_order",
            "description": "Confirm and execute a previously proposed paper order using its proposal ID. Only call this AFTER the user explicitly confirms the proposal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "The proposal ID like po_abc12345"
                    }
                },
                "required": ["proposal_id"]
            }
        }
    }
]

SYSTEM_PROMPT = """You are RISEDUAL AI's Portfolio Intelligence Agent — a financial research tool, NOT a financial advisor.

You have access to the user's real paper trading portfolio through tools. ALWAYS use tools to get current data before answering portfolio questions. NEVER invent positions, prices, trades, or news.

When analyzing the portfolio:
- Reference actual positions with real numbers
- Calculate concentration risk (% of equity in single positions)  
- Identify correlated holdings
- Flag if any position's unrealized loss exceeds 5%
- Present observations about portfolio composition
- Note their available cash for reference

COMPLIANCE — STRICTLY ENFORCE:
- You are a RESEARCH PUBLISHING tool. NEVER say "you should buy/sell" or "I recommend."
- Present DATA-DRIVEN OBSERVATIONS: "The data shows concentration in tech at X%..." not "You should diversify."
- When asked "should I buy X?", present the relevant factors (position size, concentration, risk) and let the user decide. Say: "Here are the key factors to consider" NOT "Yes, buy X."
- Always end substantive analysis with: "This is AI-generated research for informational purposes only. Trading involves risk."
- All analysis is IMPERSONAL and BROADCAST-STYLE — identical for every user.

PAPER TRADING ORDERS — CONFIRMATION REQUIRED:
- When the user wants to buy or sell, ALWAYS call place_paper_order_intent FIRST to create a proposal.
- NEVER execute a trade directly from a single request.
- After creating a proposal, present the details and ask them to confirm using the proposal ID.
- Only call confirm_paper_order AFTER the user explicitly confirms the specific proposal ID.

Be direct, data-driven, and present findings as observations. Use actual dollar amounts and percentages from the tools."""


async def _execute_tool(user_id: str, tool_name: str, arguments: dict) -> str:
    """Execute a portfolio tool and return JSON result."""
    try:
        if tool_name == "get_portfolio_snapshot":
            result = await get_portfolio_snapshot(user_id)
            return json.dumps(result, default=str)

        elif tool_name == "get_position_detail":
            symbol = arguments.get("symbol", "").upper()
            snapshot = await get_portfolio_snapshot(user_id)
            position = next(
                (p for p in snapshot.get("positions", []) if p["symbol"] == symbol),
                None
            )
            if position:
                return json.dumps(position, default=str)
            return json.dumps({"error": f"No position found for {symbol}"})

        elif tool_name == "get_trade_history":
            symbol = arguments.get("symbol")
            limit = arguments.get("limit", 10)
            trades = await get_trade_history(user_id, symbol=symbol, limit=limit)
            return json.dumps(trades, default=str)

        elif tool_name == "get_watchlist_news":
            # Pull from portfolio positions + any scraped news
            snapshot = await get_portfolio_snapshot(user_id)
            symbols = [p["symbol"] for p in snapshot.get("positions", [])]
            symbol_filter = arguments.get("symbol")
            if symbol_filter:
                symbols = [s for s in symbols if s == symbol_filter.upper()] or [symbol_filter.upper()]
            news = [{"symbol": s, "note": f"Live market data tracked for {s}"} for s in symbols]
            return json.dumps(news, default=str)

        elif tool_name == "place_paper_order_intent":
            result = await place_paper_order_intent(
                user_id=user_id,
                symbol=arguments.get("symbol", ""),
                side=arguments.get("side", "BUY"),
                qty=arguments.get("qty", 0),
                order_type=arguments.get("order_type", "MARKET"),
                limit_price=arguments.get("limit_price"),
            )
            return json.dumps(result, default=str)

        elif tool_name == "confirm_paper_order":
            result = await confirm_paper_order(
                user_id=user_id,
                proposal_id=arguments.get("proposal_id", ""),
            )
            return json.dumps(result, default=str)

        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except Exception as e:
        logger.error(f"Tool execution error ({tool_name}): {e}")
        return json.dumps({"error": str(e)})


async def run_portfolio_agent(user_id: str, user_message: str, max_iterations: int = 5) -> str:
    """Run the portfolio agent with tool calling loop.
    
    The AI decides which tools to call, gets real portfolio data,
    and synthesizes a personalized response.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message}
    ]

    for iteration in range(max_iterations):
        logger.info(f"Portfolio agent iteration {iteration + 1}")

        try:
            response = litellm.completion(
                model="gpt-5.2",
                api_key=EMERGENT_KEY,
                api_base=PROXY_URL,
                custom_llm_provider="openai",
                messages=messages,
                tools=PORTFOLIO_TOOLS,
                tool_choice="auto",
                max_tokens=1500,
            )
        except Exception as e:
            logger.error(f"LiteLLM completion failed: {e}")
            # Fallback: use context injection approach
            return await _fallback_response(user_id, user_message)

        choice = response.choices[0]
        assistant_msg = choice.message

        # If no tool calls, return the final text
        if not assistant_msg.tool_calls:
            return assistant_msg.content or "I couldn't generate a response."

        # Append assistant message with tool calls
        messages.append(assistant_msg.model_dump())

        # Execute each tool call and append results
        for tool_call in assistant_msg.tool_calls:
            fn = tool_call.function
            tool_name = fn.name
            try:
                arguments = json.loads(fn.arguments) if isinstance(fn.arguments, str) else fn.arguments
            except json.JSONDecodeError:
                arguments = {}

            logger.info(f"Tool call: {tool_name}({arguments})")
            result = await _execute_tool(user_id, tool_name, arguments)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": tool_name,
                "content": result
            })

    return "I ran into complexity analyzing your portfolio. Please try a more specific question."


async def _fallback_response(user_id: str, user_message: str) -> str:
    """Fallback: inject portfolio context directly if tool calling fails."""
    try:
        context = await get_portfolio_context(user_id)
        prompt = f"{user_message}\n\n[PORTFOLIO DATA]\n{context}"
        response = litellm.completion(
            model="gpt-5.2",
            api_key=EMERGENT_KEY,
            api_base=PROXY_URL,
            custom_llm_provider="openai",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500,
        )
        return response.choices[0].message.content or "Unable to analyze portfolio."
    except Exception as e:
        logger.error(f"Fallback response failed: {e}")
        return "I'm having trouble accessing your portfolio right now. Please try again."
