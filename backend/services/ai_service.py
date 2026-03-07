import os
import logging
from typing import List, Dict
from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self):
        self.api_key = os.environ.get('EMERGENT_LLM_KEY')
        self.system_message = """You are RISEDUALAI, an advanced AI-powered trading assistant. 
        You specialize in:
        - Stock market analysis and insights
        - Options trading strategies
        - Technical and fundamental analysis
        - Risk management
        - Market trends and predictions
        - Cryptocurrency market analysis
        - Dark pool trading insights
        
        Provide clear, actionable advice while always reminding users that trading involves risk. 
        Be professional, knowledgeable, and helpful. Use data-driven insights when possible.
        Your responses should be informative yet concise."""
    
    async def chat(self, message: str, session_id: str) -> str:
        """Send a message to the AI and get a response"""
        try:
            # Initialize chat with session
            chat = LlmChat(
                api_key=self.api_key,
                session_id=session_id,
                system_message=self.system_message
            ).with_model("openai", "gpt-5.2")
            
            # Create user message
            user_message = UserMessage(text=message)
            
            # Get response
            response = await chat.send_message(user_message)
            
            return response
            
        except Exception as e:
            logger.error(f"Error in AI chat: {str(e)}")
            return "I apologize, but I'm experiencing technical difficulties. Please try again in a moment."