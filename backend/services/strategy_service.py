"""AI Strategy Builder Service — generates structured trading strategies from natural language."""
import os
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STRATEGY_PROMPT_TEMPLATE = """You are an expert quantitative trading strategist. The user will describe a trading strategy in plain English. Your job is to convert it into a precise, structured trading strategy.

Return ONLY valid JSON (no markdown, no code blocks) with this exact structure:
{{
  "name": "Short strategy name (3-5 words)",
  "summary": "1-2 sentence executive summary",
  "timeframe": "e.g. Daily, 4H, 1H, 15m",
  "asset_classes": ["stocks", "crypto", "options", etc.],
  "indicators": [
    {{"name": "RSI", "period": 14, "description": "Relative Strength Index"}}
  ],
  "entry_rules": [
    {{"condition": "RSI crosses below 30", "description": "Oversold signal triggers buy", "priority": 1}}
  ],
  "exit_rules": [
    {{"condition": "RSI crosses above 70", "description": "Overbought signal triggers sell", "priority": 1}}
  ],
  "risk_management": {{
    "stop_loss": "2% below entry",
    "take_profit": "6% above entry",
    "position_size": "Risk 1% of portfolio per trade",
    "max_positions": 5,
    "risk_reward_ratio": "1:3"
  }},
  "market_conditions": "Best in trending markets with clear momentum",
  "backtesting_notes": "Expected win rate: 55-65%. Sharpe ratio target: 1.5+",
  "warnings": ["This strategy may underperform in sideways/choppy markets"]
}}

User's strategy description: {description}

Additional context (if the user mentions specific tickers): Focus the strategy on those assets but keep it generalizable.
"""


async def generate_strategy(api_key: str, description: str, model: str = "gpt-5.2") -> dict:
    """Generate a structured trading strategy from a plain English description."""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage

        session_id = f"strategy_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        chat = LlmChat(
            api_key=api_key,
            session_id=session_id,
            system_message="You are an expert quantitative trading strategist. Return only valid JSON."
        ).with_model("openai", "gpt-5.2")
        response = await chat.send_message(UserMessage(text=STRATEGY_PROMPT_TEMPLATE.format(description=description)))

        # response is a string from the LLM
        text = response.strip() if isinstance(response, str) else str(response).strip()
        # Remove markdown code blocks if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        # Extract JSON object from response
        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start >= 0 and brace_end > brace_start:
            text = text[brace_start:brace_end + 1]
        elif '"name"' in text:
            # LLM returned JSON content without braces — wrap it
            text = '{' + text + '}'

        strategy = json.loads(text)
        strategy["model_used"] = model
        strategy["model_used"] = model
        strategy["generated_at"] = datetime.now(timezone.utc).isoformat()
        return strategy

    except json.JSONDecodeError as e:
        logger.error(f"Strategy JSON parse error: {e}. Raw text (first 500 chars): {text[:500] if 'text' in dir() else 'N/A'}")
        return {
            "error": True,
            "name": "Parse Error",
            "summary": "AI generated a response but it couldn't be parsed. Try rephrasing your strategy.",
            "raw_response": text[:500] if 'text' in dir() else "",
        }
    except Exception as e:
        import traceback
        logger.error(f"Strategy generation error: {repr(e)}")
        logger.error(traceback.format_exc())
        raise


async def get_code_quality_score(db) -> dict:
    """Calculate a code quality score for the admin panel."""
    import glob

    # Backend metrics
    py_files = glob.glob("/app/backend/**/*.py", recursive=True)
    py_lines = 0
    for f in py_files:
        try:
            with open(f, 'r') as fh:
                py_lines += sum(1 for line in fh if line.strip())
        except Exception:
            pass

    # Frontend metrics
    jsx_files = glob.glob("/app/frontend/src/**/*.jsx", recursive=True) + glob.glob("/app/frontend/src/**/*.js", recursive=True)
    jsx_lines = 0
    for f in jsx_files:
        try:
            with open(f, 'r') as fh:
                jsx_lines += sum(1 for line in fh if line.strip())
        except Exception:
            pass

    # Test files
    test_files = glob.glob("/app/backend/tests/**/*.py", recursive=True)

    # Services count
    service_files = glob.glob("/app/backend/services/*.py", recursive=True)
    route_files = glob.glob("/app/backend/routes/*.py", recursive=True)

    # Component count
    component_files = glob.glob("/app/frontend/src/components/**/*.jsx", recursive=True)

    # Score calculation (out of 100)
    score = 50  # Base

    # Has tests (+15)
    if len(test_files) >= 5:
        score += 15
    elif len(test_files) >= 1:
        score += 8

    # Modular architecture: routes separated (+10)
    if len(route_files) >= 4:
        score += 10
    elif len(route_files) >= 2:
        score += 5

    # Services layer exists (+10)
    if len(service_files) >= 5:
        score += 10
    elif len(service_files) >= 2:
        score += 5

    # Component splitting (+10)
    if len(component_files) >= 20:
        score += 10
    elif len(component_files) >= 10:
        score += 5

    # Average file size penalty (large files = bad)
    all_files = py_files + jsx_files
    if all_files:
        avg_lines = (py_lines + jsx_lines) / len(all_files)
        if avg_lines < 100:
            score += 5
        elif avg_lines > 200:
            score -= 5

    score = min(max(score, 0), 100)

    grade = "A+" if score >= 95 else "A" if score >= 90 else "A-" if score >= 85 else "B+" if score >= 80 else "B" if score >= 75 else "B-" if score >= 70 else "C+" if score >= 65 else "C" if score >= 60 else "D"

    return {
        "score": score,
        "grade": grade,
        "metrics": {
            "backend_files": len(py_files),
            "backend_lines": py_lines,
            "frontend_files": len(jsx_files),
            "frontend_lines": jsx_lines,
            "test_files": len(test_files),
            "service_modules": len(service_files),
            "route_modules": len(route_files),
            "components": len(component_files),
        },
        "breakdown": {
            "test_coverage": {"score": 15 if len(test_files) >= 5 else 8 if len(test_files) >= 1 else 0, "max": 15, "label": "Test Suite"},
            "modularity": {"score": 10 if len(route_files) >= 4 else 5, "max": 10, "label": "Route Modularity"},
            "services": {"score": 10 if len(service_files) >= 5 else 5, "max": 10, "label": "Service Layer"},
            "components": {"score": 10 if len(component_files) >= 20 else 5, "max": 10, "label": "Component Architecture"},
            "file_size": {"score": 5 if all_files and (py_lines + jsx_lines) / len(all_files) < 100 else 0, "max": 5, "label": "Average File Size"},
        },
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    }
