import os
import re
import logging
from typing import Optional
from services.providerrouter import ProviderRouter
from services.provider_registry import get_ai_provider_pool

logger = logging.getLogger(__name__)

# Pattern to detect messages that benefit from tool-calling agent
_TOOLS_PATTERN = re.compile(
    r'\b(compound|cagr|future value|growth rate|invest(ment|ing)?.*worth|'
    r'calculate|projection|project(ed)?|annualized|what would.*be worth|'
    r'how much.*in \d+ years|rate of return|roi\b|'
    r'current (stock )?price.*then|find.*price.*calculate|'
    r'look up.*price.*and|worth in \d+)',
    re.IGNORECASE
)


class AIService:
    def __init__(self, db=None):
        self.db = db
        self.router = ProviderRouter("ai", get_ai_provider_pool(), db=db)
        self.system_message = """You are RISEDUAL AI, an advanced AI-powered trading research assistant. 
        You specialize in:
        - Stock market analysis and insights
        - Options trading strategies
        - Technical and fundamental analysis
        - Risk management
        - Market trends and predictions
        - Cryptocurrency market analysis
        - Dark pool trading insights
        - Analyzing stock charts, candlestick patterns, and technical indicators from uploaded images
        - Paper trading portfolio management
        
        When a user uploads an image of a chart or financial asset, analyze it thoroughly:
        - Identify the asset/ticker if visible
        - Describe chart patterns (head & shoulders, double top/bottom, triangles, etc.)
        - Note support/resistance levels
        - Identify trend direction and momentum
        - Present potential trade setups based on what you see
        
        When portfolio context is provided in the message:
        - Reference their actual positions, P&L, and cash balance
        - Present observations about their holdings
        - Flag concentrated risk or correlated positions
        - Users can execute paper trades — if they want to buy or sell, confirm the details
        
        UX RULES (STRICTLY FOLLOW):
        - Default to action when a reasonable default exists. Deliver value first, refine second.
        - Ask at most ONE clarifying question, and only if the missing answer would clearly change the asset class, legal/compliance meaning, trade execution, or output format.
        - Never ask for multiple dimensions at once. No "What asset class, timeframe, risk level, market, and strategy?" — instead, start with a reasonable default and offer to narrow.
        - For market scans: default to the main stock universe and current session context.
        - For portfolio analysis: default to the latest portfolio snapshot.
        - For AI analysis: default to a concise answer with optional refinement.
        - Tell the user what default you used so they can adjust if needed.
        - Keep responses concise. Lead with the answer, follow with supporting detail.
        
        Present clear, data-driven insights while always reminding users that trading involves risk. 
        Be professional, knowledgeable, and helpful. Use data-driven observations when possible.
        Your responses should be informative yet concise."""

        from services.ai_guardrails import inject_guardrails
        self.system_message = inject_guardrails(self.system_message)

    def _build_system(self, memory_context: str = "", user_id: str = "", failure_warnings: list = None) -> str:
        system = self.system_message
        if memory_context:
            system = f"{system}\n\n{memory_context}"
        if failure_warnings:
            warning_text = "\n\nCAUTION FROM REVIEWED TRADE HISTORY:\n" + "\n".join(f"- {w}" for w in failure_warnings)
            warning_text += "\nUse these patterns as a caution layer when analyzing similar setups. Do not auto-override user judgment."
            system = system + warning_text
        return system

    async def _call_provider(self, provider: dict, message: str, session_id: str,
                             system: str, image_base64: str = None):
        p = provider.get("provider")

        if p == "openai":
            from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
            llm = LlmChat(
                api_key=provider["api_key"],
                session_id=session_id,
                system_message=system,
            ).with_model("openai", provider.get("model", "gpt-5.2"))

            if image_base64:
                image_content = ImageContent(image_base64=image_base64)
                user_message = UserMessage(
                    text=message or "Please analyze this chart/image and provide trading insights.",
                    file_contents=[image_content],
                )
            else:
                user_message = UserMessage(text=message)

            return await llm.send_message(user_message)

        if p == "anthropic":
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=provider["api_key"])
            messages_content = [{"type": "text", "text": message}]
            if image_base64:
                messages_content.insert(0, {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": image_base64},
                })
            resp = await client.messages.create(
                model=provider.get("model", "claude-sonnet-4"),
                max_tokens=1200,
                system=system,
                messages=[{"role": "user", "content": messages_content}],
            )
            return "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")

        raise RuntimeError(f"Unsupported AI provider: {p}")

    async def chat(self, message: str, session_id: str, image_base64: Optional[str] = None,
                   memory_context: str = "", user_id: str = "") -> str:
        """Send a message to the AI and get a response, with provider failover.
        Auto-routes to the Financial Tools Agent for calculation-heavy queries."""
        try:
            # Route to tools agent for calculation/projection queries (skip if image attached)
            if not image_base64 and _TOOLS_PATTERN.search(message):
                try:
                    from services.financial_tools_agent import FinancialToolsAgent
                    agent = FinancialToolsAgent(db=self.db)
                    result = await agent.run(message, session_id)
                    if result.get("text"):
                        return result
                except Exception as e:
                    logger.warning(f"Tools agent failed, falling back to standard chat: {e}")

            # Standard chat path
            failure_warnings = None
            if user_id:
                try:
                    from services.failure_loop_service import build_memory_warnings
                    failure_warnings = await build_memory_warnings(user_id)
                except Exception:
                    pass

            system = self._build_system(memory_context, user_id, failure_warnings)

            routed = await self.router.run(
                lambda provider: self._call_provider(provider, message, session_id, system, image_base64)
            )
            return {
                "text": routed["result"],
                "provider": routed["provider"],
            }

        except Exception as e:
            logger.error(f"Error in AI chat: {str(e)}")
            return "I apologize, but I'm experiencing technical difficulties. Please try again in a moment."
