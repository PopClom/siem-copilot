"""
api/main.py
-----------
FastAPI application factory.

Shared state (settings, RAGChain) is initialised once during the lifespan
context and stored on app.state so all request handlers can access it
without globals or repeated initialisation.

Usage
-----
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import health, query
from src.config.settings import load_settings
from src.rag.chain import RAGChain

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated @app.on_event)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise heavy resources once at startup; clean up on shutdown."""

    logger.info("SIEM Copilot API — starting up …")

    # Load config
    settings = load_settings("config/config.yaml")
    app.state.settings = settings

    # Build the RAG chain (loads embedding model, connects to Qdrant)
    logger.info("Initialising RAG chain …")
    chain = RAGChain(settings)
    # Eagerly load the embedding model so the first request isn't slow
    _ = chain._retriever._embedder.model
    app.state.chain = chain

    logger.info("SIEM Copilot API — ready.")
    yield

    # Shutdown
    logger.info("SIEM Copilot API — shutting down.")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="SIEM Copilot",
        description=(
            "Semantic search and AI-assisted analysis over security log data. "
            "Ask questions in natural language; get answers grounded in your logs."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — tighten in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(query.router)

    return app


app = create_app()
