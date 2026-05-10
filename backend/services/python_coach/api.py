"""FastAPI router for the Python Coach. Owner-only.

Endpoints
---------
POST /api/admin/python-coach/plan
    Generate a structured lesson plan from a learner's goal.

POST /api/admin/python-coach/review
    Static AST review of pasted code; optionally adds LLM-driven
    deep feedback when ``deep=True``.

GET  /api/admin/python-coach/example
    Returns a small starter snippet matching the demo artifact.
"""
from __future__ import annotations

import logging
import textwrap

from fastapi import APIRouter, HTTPException, Request

from routes.auth import get_current_user

from .lesson_planner import generate_deep_feedback, generate_lesson_plan
from .schemas import (
    CodeReview,
    CodeReviewRequest,
    LessonPlan,
    LessonPlanRequest,
)
from .static_review import static_review

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/python-coach",
    tags=["admin", "python-coach"],
)


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


_EXAMPLE = textwrap.dedent("""
    \"\"\"Fetch stock prices with retry.\"\"\"
    import time
    from typing import Iterable

    def fetch_prices(symbols: Iterable[str], *, max_attempts: int = 3) -> dict[str, float]:
        \"\"\"Return {symbol: price}. Retries on transient errors.\"\"\"
        out: dict[str, float] = {}
        for symbol in symbols:
            for attempt in range(1, max_attempts + 1):
                try:
                    out[symbol] = _quote(symbol)
                    break
                except TimeoutError:
                    if attempt == max_attempts:
                        raise
                    time.sleep(2 ** attempt)
        return out

    def _quote(symbol: str) -> float:
        # Replace with a real provider call.
        return 100.0

    if __name__ == \"__main__\":
        print(fetch_prices([\"AAPL\", \"MSFT\"]))
""").strip() + "\n"


@router.post("/plan", response_model=LessonPlan)
async def plan(body: LessonPlanRequest, request: Request):
    await _require_owner(request)
    return await generate_lesson_plan(body.goal)


@router.post("/review", response_model=CodeReview)
async def review(body: CodeReviewRequest, request: Request):
    await _require_owner(request)
    review_obj = static_review(body.code, goal=body.goal)
    if body.deep:
        review_obj.deep_feedback = await generate_deep_feedback(
            body.code, goal=body.goal,
        )
    return review_obj


@router.get("/example")
async def example(request: Request):
    await _require_owner(request)
    return {
        "language": "python",
        "filename": "fetch_prices.py",
        "code": _EXAMPLE,
        "goal": (
            "Build a Python function that fetches stock prices, "
            "retries on failure, and returns a clean dictionary."
        ),
    }
