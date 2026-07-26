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

class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        examples=["Were there any lateral movement indicators in the last hour?"],
    )
    top_k: int = Field(
        default=8,
        ge=1,
        le=20,
        description="Number of log windows to retrieve before sending to the LLM.",
    )
    filters: Optional[dict[str, str]] = Field(
        default=None,
        description=(
            "Optional metadata filters applied before vector search. "
            "Keys must match Qdrant payload fields, e.g. {\"host\": \"srv-01\"}."
        ),
        examples=[{"host": "srv-01"}],
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
    chunks_retrieved: int
    chunks_used: int
    neighbours_added: int
    latency_ms: int
    hyde_used: bool
    hypothetical_doc: Optional[str] = Field(default=None)
    sources: list[SourceReference]


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str                    # "ok" | "degraded"
    vector_db: str                 # "connected" | "unreachable"
    embedding_model: str           # model name
    llm_model: str                 # model name
