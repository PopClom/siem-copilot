"""
api/routers/query.py
--------------------
POST /query — the main RAG endpoint.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from api.schemas import QueryRequest, QueryResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/query", response_model=QueryResponse, tags=["rag"])
async def query(request: Request, body: QueryRequest) -> QueryResponse:
    """
    Answer a natural-language question about the ingested security logs.

    The endpoint:
    1. Embeds the question using the local model
    2. Retrieves the most relevant log windows from Qdrant
    3. Passes the context + question to Claude
    4. Returns the answer along with source metadata
    """
    chain = request.app.state.chain

    try:
        result = chain.query(question=body.question)
    except Exception as exc:
        logger.exception("RAG chain error: %s", exc)
        raise HTTPException(status_code=500, detail=f"RAG pipeline error: {exc}") from exc

    return QueryResponse(
        answer=result.answer,
        query=result.query,
        tool_used=result.tool_used,
        chunks_retrieved=result.chunks_retrieved,
        chunks_used=result.chunks_used,
        neighbours_added=result.neighbours_added,
        hyde_used=result.hyde_used,
        hypothetical_doc=result.hypothetical_doc,
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        sources=result.sources,
    )
