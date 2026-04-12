import os
import logging
from typing import Optional
from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self):
        self.api_key = os.environ.get('EMERGENT_LLM_KEY')
        self.system_message = """You are RISEDUAL AI, an advanced AI-powered trading assistant. 
        You specialize in:
        - Stock market analysis and insights
        - Options trading strategies
        - Technical and fundamental analysis
        - Risk management
        - Market trends and predictions
        - Cryptocurrency market analysis
        - Dark pool trading insights
        - Analyzing stock charts, candlestick patterns, and technical indicators from uploaded images
        - Paper trading portfolio management and personalized advice
        
        When a user uploads an image of a chart or financial asset, analyze it thoroughly:
        - Identify the asset/ticker if visible
        - Describe chart patterns (head & shoulders, double top/bottom, triangles, etc.)
        - Note support/resistance levels
        - Identify trend direction and momentum
        - Suggest potential trade setups based on what you see
        
        When portfolio context is provided in the message:
        - Reference their actual positions, P&L, and cash balance
        - Give personalized advice based on their holdings
        - Suggest rebalancing, trimming, or adding to positions
        - Flag concentrated risk or correlated positions
        - Users can execute paper trades — if they want to buy or sell, confirm the details
        
        Provide clear, actionable advice while always reminding users that trading involves risk. 
        Be professional, knowledgeable, and helpful. Use data-driven insights when possible.
        Your responses should be informative yet concise."""
    
    async def chat(self, message: str, session_id: str, image_base64: Optional[str] = None, memory_context: str = "") -> str:
        """Send a message to the AI and get a response, optionally with an image and memory context"""
        try:
            system = self.system_message
            if memory_context:
                system = f"{self.system_message}\n\n{memory_context}"

            chat = LlmChat(
                api_key=self.api_key,
                session_id=session_id,
                system_message=system
            ).with_model("openai", "gpt-5.2")
            
            if image_base64:
                image_content = ImageContent(image_base64=image_base64)
                user_message = UserMessage(
                    text=message or "Please analyze this chart/image and provide trading insights.",
                    file_contents=[image_content]
                )
            else:
                user_message = UserMessage(text=message)
            
            response = await chat.send_message(user_message)
            return response
            
        except Exception as e:
            logger.error(f"Error in AI chat: {str(e)}")
            return "I apologize, but I'm experiencing technical difficulties. Please try again in a moment."