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

    # With debug logs from src/ and api/
    LOG_LEVEL=DEBUG uvicorn api.main:app --reload --port 8000   # Linux/macOS
    set LOG_LEVEL=DEBUG && uvicorn api.main:app --reload --port 8000  # Windows CMD
    $env:LOG_LEVEL="DEBUG"; uvicorn api.main:app --reload --port 8000  # PowerShell
"""

from __future__ import annotations

import logging
import logging.config
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import anomalies, health, query
from src.config.settings import load_settings
from src.rag.chain import RAGChain

# ---------------------------------------------------------------------------
# Logging — configured at module import time so it takes effect before
# uvicorn sets up its own handlers.  We attach our own handler directly to
# the "src" and "api" loggers with propagate=False so uvicorn's root handler
# doesn't interfere (and doesn't double-print our lines).
# ---------------------------------------------------------------------------

_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
_APP_LEVEL = getattr(logging, _LOG_LEVEL, logging.INFO)

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "app": {
            "format": "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
            "datefmt": "%H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "app",
        }
    },
    "loggers": {
        # Our code — honour LOG_LEVEL
        "src": {"handlers": ["console"], "level": _APP_LEVEL, "propagate": False},
        "api": {"handlers": ["console"], "level": _APP_LEVEL, "propagate": False},
        # Noisy third-party libs — always quiet
        "httpx":                {"level": "WARNING", "propagate": True},
        "httpcore":             {"level": "WARNING", "propagate": True},
        "sentence_transformers":{"level": "WARNING", "propagate": True},
        "transformers":         {"level": "WARNING", "propagate": True},
        "urllib3":              {"level": "WARNING", "propagate": True},
    },
})

logger = logging.getLogger(__name__)
logger.info("App logging initialised at level %s", _LOG_LEVEL)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise heavy resources once at startup; clean up on shutdown."""

    logger.info("SIEM Copilot API — starting up …")

    settings = load_settings("config/config.yaml")
    app.state.settings = settings

    logger.info("Initialising RAG chain …")
    chain = RAGChain(settings)
    _ = chain._retriever._embedder.model   # eager model load
    app.state.chain = chain

    logger.info("SIEM Copilot API — ready.")
    yield

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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(query.router)
    app.include_router(anomalies.router)

    return app


app = create_app()
