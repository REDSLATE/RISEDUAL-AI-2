"""Schemas for the Python Coach.

Pydantic models for the API surface. All output shapes are
strictly typed so the frontend never has to guess at LLM output.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── API request models ─────────────────────────────────────────────


class LessonPlanRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=600)


class CodeReviewRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=20_000)
    goal: str = Field(default="", max_length=600)
    deep: bool = Field(
        default=False,
        description=(
            "If true, in addition to the static AST review, ask the "
            "LLM for richer line-by-line feedback. Static review is "
            "always run."
        ),
    )


# ── API response models ────────────────────────────────────────────


class LessonStep(BaseModel):
    step: int
    title: str
    why: str
    practice: str


class LessonPlan(BaseModel):
    goal: str
    summary: str
    concepts: list[str]
    steps: list[LessonStep]
    drills: list[str]
    pitfalls: list[str]
    estimated_minutes: int = 30
    generated_by: str = "alpha-python-coach"


Severity = Literal["info", "warn", "error"]


class CodeFinding(BaseModel):
    severity: Severity
    line: Optional[int] = None
    rule_id: str
    message: str


class CodeReview(BaseModel):
    parses: bool
    syntax_error: Optional[str] = None
    line_count: int
    function_count: int
    class_count: int
    has_main_guard: bool
    has_docstrings: bool
    findings: list[CodeFinding]
    summary: str
    deep_feedback: Optional[str] = None
    next_drills: list[str]
