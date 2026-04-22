"""Financial Tools Agent v2 — LangGraph-inspired state machine with SSE streaming.

State graph: START -> agent -> (tools_condition) -> tools -> agent -> ... -> END
Step limit prevents infinite loops. SSE endpoint streams progress in real-time.

Tool schemas, implementations, and the dispatcher live in financial_tools.py.

Usage:
    agent = FinancialToolsAgent(db)
    result = await agent.run("What would $5K in AAPL be worth in 5 years?")
    async for event in agent.stream("Calculate CAGR from 100 to 500 over 3 years"):
        print(event)  # {"node": "tools", "tool": "calculate_cagr", "result": {...}}
"""
import json
import logging
from typing import Any, Optional
from collections.abc import AsyncGenerator

import httpx

from services.providerrouter import ProviderRouter
from services.provider_registry import get_ai_provider_pool
from services.financial_tools import TOOL_SCHEMAS, SYSTEM_PROMPT, MAX_STEPS, run_tool

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  STATE GRAPH (LangGraph-inspired)
# ─────────────────────────────────────────────

class AgentState:
    """Mimics LangGraph's TypedDict state with message accumulation."""
    __slots__ = ("messages", "steps", "tools_used", "trace", "provider_meta")

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.steps: int = 0
        self.tools_used: list[str] = []
        self.trace: list[dict] = []
        self.provider_meta: Optional[dict] = None


class FinancialToolsAgent:
    def __init__(self, db=None):
        self.db = db
        self.router = ProviderRouter("ai_tools", get_ai_provider_pool(), db=db)

    async def _call_llm(self, provider: dict, messages: list) -> Any:
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
        elif p == "openrouter":
            client = AsyncOpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": "https://risedual.ai",
                    "X-Title": "RISEDUAL AI",
                },
            )
        elif p == "anthropic":
            return await self._call_anthropic_tools(api_key, model, messages)
        else:
            client = AsyncOpenAI(api_key=api_key)

        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,  # type: ignore[arg-type]
            temperature=0,
        )
        return response.choices[0].message

    async def _call_anthropic_tools(self, api_key: str, model: str, messages: list) -> Any:
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
            state.messages.append({"role": "assistant", "content": ai_msg.content or ""})
            return "end"

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

            result_str = await run_tool(name, args)
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
        """Execute the full agent graph: START -> agent -> tools -> agent -> ... -> END."""
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
                if state.trace:
                    event = {**state.trace[-1]}
                    if next_node == "end":
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
                for entry in state.trace[prev_trace_len:]:
                    yield entry
                current_node = self._stop_condition(state, next_node)

        yield {
            "node": "end",
            "tools_used": state.tools_used,
            "rounds": state.steps,
            "provider": state.provider_meta,
        }
