"""
api/schemas.py
--------------
Pydantic v2 models for FastAPI request validation and response serialization.
Kept separate from src/ models to isolate the HTTP layer.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Query endpoint
# ---------------------------------------------------------------------------

class HistoryTurn(BaseModel):
    role: str      # "user" or "assistant"
    content: str


class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        examples=["Were there any lateral movement indicators in the last hour?"],
    )
    history: list[HistoryTurn] = Field(
        default_factory=list,
        description="Previous conversation turns for multi-turn context.",
    )


class SourceReference(BaseModel):
    window_id: str
    host: Optional[str]
    window_start: str
    window_end: str
    score: float
    event_count: int


class QueryResponse(BaseModel):
    answer: str
    query: str
    tool_used: Optional[str] = None
    chunks_retrieved: int = 0
    chunks_used: int = 0
    neighbours_added: int = 0
    hyde_used: bool = False
    hypothetical_doc: Optional[str] = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    sources: list[SourceReference] = Field(default_factory=list)
    sources: list[SourceReference]


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str                    # "ok" | "degraded"
    vector_db: str                 # "connected" | "unreachable"
    embedding_model: str           # model name
    llm_model: str                 # model name
