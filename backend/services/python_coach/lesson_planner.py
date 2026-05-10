"""LLM-driven lesson planner + deep code-review feedback.

Reuses the existing ``services.ai_service`` router (Emergent LLM
key, no new integration). Both calls are best-effort — if the LLM
times out or returns malformed JSON, the caller gets a stub plan
or stub deep-feedback string with a clear ``generated_by`` tag.

Pure async functions; no Mongo, no globals.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from .schemas import LessonPlan, LessonStep

logger = logging.getLogger(__name__)


_PLAN_SYSTEM = (
    "You are RISEDUAL Alpha's Python Coach. You turn a learner's "
    "plain-English goal into a focused, actionable lesson plan. "
    "Keep it concise: 3–6 steps, 3–6 concepts, 3–6 drills, 2–5 "
    "common pitfalls. Practice tasks must be runnable in pure "
    "Python with no external services. Output STRICT JSON matching "
    "the schema below — no markdown fences, no commentary outside "
    "the JSON."
)

_PLAN_SCHEMA = (
    '{'
    '"goal": "string", '
    '"summary": "1-2 sentence overview", '
    '"concepts": ["string", ...], '
    '"steps": [{"step": 1, "title": "string", "why": "string", "practice": "string"}, ...], '
    '"drills": ["string", ...], '
    '"pitfalls": ["string", ...], '
    '"estimated_minutes": 30'
    '}'
)


def _stub_plan(goal: str, reason: str) -> LessonPlan:
    """Fallback plan when the LLM is unavailable or returns garbage.

    The static structure here is intentionally generic but useful —
    operator still gets a usable plan."""
    return LessonPlan(
        goal=goal,
        summary=(
            "LLM unavailable — returning a generic Python practice "
            f"plan. Reason: {reason}."
        ),
        concepts=[
            "Functions and parameters",
            "Return values vs. printing",
            "Error handling with try/except",
            "Type hints",
        ],
        steps=[
            LessonStep(
                step=1,
                title="Sketch the function signature",
                why="Names + types pin the contract before any logic.",
                practice="Write the `def` line + a one-line docstring. No body yet.",
            ),
            LessonStep(
                step=2,
                title="Add the happy path",
                why="A working baseline beats a clever one.",
                practice="Implement the simplest case that returns the right shape.",
            ),
            LessonStep(
                step=3,
                title="Handle the obvious failure",
                why="Real code fails. The function should fail predictably.",
                practice="Add a try/except for the most likely error and return a sane fallback.",
            ),
        ],
        drills=[
            "Write three pytest tests: happy, edge, error.",
            "Add a type hint to every parameter and the return type.",
            "Refactor: extract any nested logic into its own helper.",
        ],
        pitfalls=[
            "Catching `Exception` swallows real bugs — name the class.",
            "Returning None on failure forces every caller to special-case.",
            "Mixing print + return makes the function untestable.",
        ],
        estimated_minutes=30,
        generated_by="alpha-python-coach-stub",
    )


def _coerce_steps(raw: Any) -> list[LessonStep]:
    out: list[LessonStep] = []
    if not isinstance(raw, list):
        return out
    for i, s in enumerate(raw, start=1):
        if not isinstance(s, dict):
            continue
        try:
            out.append(LessonStep(
                step=int(s.get("step") or i),
                title=str(s.get("title") or "")[:200],
                why=str(s.get("why") or "")[:400],
                practice=str(s.get("practice") or "")[:400],
            ))
        except Exception:  # noqa: BLE001
            continue
    return out


def _coerce_str_list(raw: Any, *, cap: int = 12) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x)[:300] for x in raw if isinstance(x, (str, int, float))][:cap]


def _parse_plan_payload(text: str, goal: str) -> LessonPlan | None:
    """Best-effort JSON parser. Strips Markdown code fences, finds
    the first ``{...}`` block, and coerces fields. Returns ``None``
    on irrecoverable failure — caller falls back to the stub."""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    # Find the first balanced JSON object.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    chunk = cleaned[start:end + 1]
    try:
        data = json.loads(chunk)
    except json.JSONDecodeError:
        return None
    try:
        return LessonPlan(
            goal=str(data.get("goal") or goal)[:600],
            summary=str(data.get("summary") or "")[:800],
            concepts=_coerce_str_list(data.get("concepts")),
            steps=_coerce_steps(data.get("steps")),
            drills=_coerce_str_list(data.get("drills")),
            pitfalls=_coerce_str_list(data.get("pitfalls")),
            estimated_minutes=int(data.get("estimated_minutes") or 30),
            generated_by="alpha-python-coach-llm",
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[python_coach] plan parse failed: %s", e)
        return None


# ── Public async entrypoints ────────────────────────────────────────


async def generate_lesson_plan(goal: str) -> LessonPlan:
    """Generate a structured lesson plan for ``goal``.

    Calls the existing ``ai_service`` router; falls back to a stub
    plan on any failure. Never raises.
    """
    goal = (goal or "").strip()
    if not goal:
        return _stub_plan("(no goal supplied)", reason="empty_goal")
    try:
        from services.ai_service import AIService
        _ai = AIService()
        prompt = (
            f"Goal: {goal}\n\n"
            f"Generate a Python lesson plan as STRICT JSON matching this "
            f"schema (no markdown fences, no extra prose):\n\n{_PLAN_SCHEMA}\n\n"
            "Keep practice tasks small (5–15 lines of code each) and "
            "self-contained. Use type hints in any code shown."
        )
        session_id = f"python_coach_plan_{uuid.uuid4().hex[:8]}"
        resp = await _ai.chat(
            f"{_PLAN_SYSTEM}\n\n{prompt}",
            session_id,
            None,
            memory_context=None,
            user_id="",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[python_coach] LLM plan call failed: {e}")
        return _stub_plan(goal, reason=f"llm_call_failed:{type(e).__name__}")

    if not isinstance(resp, dict):
        return _stub_plan(goal, reason="llm_returned_non_dict")
    text = resp.get("text")
    if not text:
        # ai_service surfaces budget-exhaustion as {"error":"llm_budget_exceeded"}.
        if resp.get("error") == "llm_budget_exceeded":
            return _stub_plan(goal, reason="llm_budget_exceeded")
        return _stub_plan(goal, reason="llm_returned_empty")
    plan = _parse_plan_payload(text, goal)
    if plan is None:
        return _stub_plan(goal, reason="llm_returned_unparseable_json")
    return plan


async def generate_deep_feedback(code: str, goal: str = "") -> str:
    """Optional richer review — runs only when ``deep=True`` was
    requested. Returns plain text (markdown allowed). Falls back to
    a one-line "LLM unavailable" string on any failure."""
    code = (code or "").strip()
    if not code:
        return ""
    try:
        from services.ai_service import AIService
        _ai = AIService()
        prompt = (
            "You are RISEDUAL Alpha's Python Coach. Review the Python "
            "code below in plain English. Be specific: cite line "
            "numbers when relevant, suggest concrete refactors, and "
            "name the most important issue first. Keep it under 250 "
            "words. No markdown fences around the whole reply.\n\n"
            f"Goal: {goal or '(none provided)'}\n\n"
            f"Code:\n```python\n{code[:6000]}\n```"
        )
        session_id = f"python_coach_review_{uuid.uuid4().hex[:8]}"
        resp = await _ai.chat(
            prompt, session_id, None, memory_context=None, user_id="",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[python_coach] LLM review call failed: {e}")
        return f"_LLM unavailable for deep feedback: {type(e).__name__}_"
    if not isinstance(resp, dict):
        return "_LLM returned an unexpected response shape._"
    if resp.get("error") == "llm_budget_exceeded":
        return "_Universal Key budget exhausted — top up to enable deep feedback._"
    return str(resp.get("text") or "_LLM returned no content._")
