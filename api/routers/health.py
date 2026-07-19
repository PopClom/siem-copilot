"""
api/routers/health.py
---------------------
GET /health — liveness + dependency check.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.schemas import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health(request: Request) -> HealthResponse:
    """
    Returns the status of the API and its dependencies (Qdrant, embedding model).
    The LLM is not probed on every health check to avoid unnecessary API calls.
    """
    settings = request.app.state.settings
    chain    = request.app.state.chain

    # Check Qdrant connectivity
    try:
        chain._retriever._store.client.get_collections()
        vector_db_status = "connected"
    except Exception as exc:
        logger.warning("Qdrant health check failed: %s", exc)
        vector_db_status = "unreachable"

    overall = "ok" if vector_db_status == "connected" else "degraded"

    return HealthResponse(
        status=overall,
        vector_db=vector_db_status,
        embedding_model=settings.embedding.model,
        llm_model=settings.llm.model,
    )
