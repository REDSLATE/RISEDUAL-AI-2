"""Schemas for the Alpha Knowledge Base."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


SourceCategory = Literal[
    "language_reference",
    "library_reference",
    "tutorial",
    "howto",
    "operator_curated",
]


class KnowledgeChunk(BaseModel):
    """A single text chunk persisted to Mongo. Idempotency is by
    ``chunk_id`` (sha256 of source_url + chunk_index)."""
    chunk_id: str
    source_url: str
    source_title: str
    source_category: SourceCategory
    chunk_index: int
    text: str
    char_count: int
    ingested_at: datetime
    schema_version: int = 1
    # Doctrine: this flag MUST never flip to False.
    excluded_from_code_gate_inputs: bool = True


class IngestSummary(BaseModel):
    """Per-call ingest report."""
    started_at: datetime
    finished_at: datetime
    sources_attempted: int
    sources_fetched: int
    sources_failed: int
    chunks_written: int
    chunks_total_after: int
    failures: list[dict] = Field(default_factory=list)


class IngestRequest(BaseModel):
    """Optional category filter — defaults to all."""
    categories: list[SourceCategory] = Field(default_factory=list)
    limit_urls: Optional[int] = None  # for ad-hoc test ingests


class RetrievalResult(BaseModel):
    chunk_id: str
    source_url: str
    source_title: str
    source_category: SourceCategory
    chunk_index: int
    text: str
    score: float


class RetrievalResponse(BaseModel):
    query: str
    results: list[RetrievalResult]
    total_corpus_size: int


class CorpusStatus(BaseModel):
    total_chunks: int
    total_sources: int
    by_category: dict[str, int]
    last_ingest_at: Optional[datetime] = None
    schema_version: int = 1
