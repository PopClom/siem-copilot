"""
rag/chain.py
------------
Orchestrates the full RAG pipeline:
    1. Retrieve relevant chunks from Qdrant
    2. Build the prompt (system + context + question)
    3. Call the LLM and return a structured response

This is the only class the API layer needs to import from the rag package.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from src.config.settings import Settings
from src.rag.llm import LLMClient
from src.rag.prompt import SYSTEM_PROMPT, build_messages, format_context
from src.rag.retriever import RetrievedChunk, Retriever

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response type
# ---------------------------------------------------------------------------

@dataclass
class RAGResponse:
    answer: str
    query: str
    chunks_retrieved: int
    chunks_used: int
    latency_ms: int
    sources: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------

class RAGChain:
    """
    Single entry point for the RAG query flow.

    Usage
    -----
        chain = RAGChain(settings)
        response = chain.query("Were there any PowerShell download cradles?")
    """

    def __init__(self, settings: Settings) -> None:
        self._retriever = Retriever(
            embedding_config=settings.embedding,
            vectordb_config=settings.vector_db,
        )
        self._llm = LLMClient(
            config=settings.llm,
            system=SYSTEM_PROMPT,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(
        self,
        question: str,
        top_k: int = 8,
        filters: Optional[dict] = None,
    ) -> RAGResponse:
        """
        Run the full RAG pipeline for a natural-language question.

        Parameters
        ----------
        question: analyst's natural-language query
        top_k:    number of chunks to retrieve before passing to LLM
        filters:  optional Qdrant metadata filters {"host": "srv-01", ...}
        """
        t0 = time.monotonic()

        # 1. Retrieve
        chunks = self._retriever.retrieve(question, top_k=top_k, filters=filters)
        chunks_retrieved = len(chunks)

        # 2. Optionally truncate to avoid exceeding context window
        chunks_used_list = self._select_chunks(chunks)
        chunks_used = len(chunks_used_list)

        # 3. Build prompt
        messages = build_messages(question, chunks_used_list)

        # 4. Call LLM
        if chunks_used == 0:
            logger.info("No relevant chunks found; asking LLM to say so.")
        answer = self._llm.complete(messages)

        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "RAG query complete | chunks=%d/%d | latency=%dms",
            chunks_used, chunks_retrieved, latency_ms,
        )

        return RAGResponse(
            answer=answer,
            query=question,
            chunks_retrieved=chunks_retrieved,
            chunks_used=chunks_used,
            latency_ms=latency_ms,
            sources=self._format_sources(chunks_used_list),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # Approximate token budget for context (conservative; BGE chunks are ~200 tokens each)
    _MAX_CHUNKS = 6
    _MAX_CHARS  = 12_000  # ~3 000 tokens at 4 chars/token

    def _select_chunks(self, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """
        Select chunks that fit within the context budget.
        Chunks are already sorted by relevance (highest first).
        """
        selected: list[RetrievedChunk] = []
        total_chars = 0

        for chunk in chunks[: self._MAX_CHUNKS]:
            chunk_len = len(chunk.aggregated_text)
            if total_chars + chunk_len > self._MAX_CHARS:
                break
            selected.append(chunk)
            total_chars += chunk_len

        return selected

    @staticmethod
    def _format_sources(chunks: list[RetrievedChunk]) -> list[dict]:
        return [
            {
                "window_id":    chunk.window_id,
                "host":         chunk.host,
                "window_start": chunk.window_start,
                "window_end":   chunk.window_end,
                "score":        round(chunk.score, 4),
                "event_count":  chunk.event_count,
            }
            for chunk in chunks
        ]
