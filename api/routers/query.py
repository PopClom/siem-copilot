"""
api/routers/query.py
--------------------
POST /query         — synchronous, returns full JSON response
POST /query/stream  — SSE streaming, returns tokens as they arrive
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.schemas import QueryRequest, QueryResponse

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Synchronous endpoint
# ---------------------------------------------------------------------------

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
    history = [{"role": t.role, "content": t.content} for t in body.history]

    try:
        result = chain.query(question=body.question, history=history or None)
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


# ---------------------------------------------------------------------------
# Streaming endpoint — SSE
# ---------------------------------------------------------------------------

@router.post("/query/stream", tags=["rag"])
async def query_stream(request: Request, body: QueryRequest) -> StreamingResponse:
    """
    Stream the answer token by token using Server-Sent Events.

    SSE event format:
      data: <token>\\n\\n          — regular text token
      data: __TOOL__:<json>\\n\\n  — tool started (frontend shows "thinking...")
      data: __DONE__:<json>\\n\\n  — stream complete, json contains metadata
      data: __ERROR__:<msg>\\n\\n  — error occurred
    """
    chain = request.app.state.chain
    history = [{"role": t.role, "content": t.content} for t in body.history]

    def event_generator():
        try:
            for token in chain.query_stream(
                question=body.question,
                history=history or None,
            ):
                # Escape newlines within a token so SSE framing is preserved
                safe = token.replace("\n", "\\n")
                yield f"data: {safe}\n\n"
        except Exception as exc:
            logger.exception("Streaming error: %s", exc)
            yield f"data: __ERROR__:{str(exc)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering if behind a proxy
        },
    )
