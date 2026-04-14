"""Search War Room — Pydantic schemas for multi-engine search."""
from pydantic import BaseModel, Field
from typing import Optional, Literal, Any


class SearchWarRoomRequest(BaseModel):
    query: str = Field(min_length=2)
    symbol: Optional[str] = None
    mode: Literal['auto', 'company', 'macro', 'filing', 'news'] = 'auto'


class EngineResult(BaseModel):
    engine: str
    status: Literal['ok', 'error', 'timeout', 'cached', 'skipped']
    source_type: str
    query: str
    title: Optional[str] = None
    summary: Optional[str] = None
    items: list = Field(default_factory=list)
    freshness_seconds: Optional[int] = None
    confidence: float = 0.5
    authoritative: bool = False
    cached: bool = False
    error: Optional[str] = None


class SearchBrief(BaseModel):
    headline: str
    summary: str
    signals: list = Field(default_factory=list)
    risks: list = Field(default_factory=list)
    sources_used: list = Field(default_factory=list)


class SearchWarRoomResponse(BaseModel):
    query: str
    brief: SearchBrief
    engine_results: list = Field(default_factory=list)
    degraded: bool = False
    warnings: list = Field(default_factory=list)
