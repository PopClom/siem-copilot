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
    hyde_used: bool = False
    hypothetical_doc: Optional[str] = None
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
        rag_cfg = settings.rag
        self._retriever = Retriever(
            embedding_config=settings.embedding,
            vectordb_config=settings.vector_db,
            top_k=rag_cfg.top_k,
            score_threshold=rag_cfg.score_threshold,
        )
        self._llm = LLMClient(
            config=settings.llm,
            system=SYSTEM_PROMPT,
        )
        self._use_hyde: bool = settings.rag.use_hyde if settings.rag else False

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
        hypothetical_doc: Optional[str] = None

        # 1. Retrieve
        if self._use_hyde:
            logger.info("Using HyDE retrieval for query: %r", question)
            chunks, hypothetical_doc = self._retriever.retrieve_with_hyde(
                query=question,
                llm=self._llm,
                top_k=top_k,
                filters=filters,
            )
        else:
            chunks = self._retriever.retrieve(question, top_k=top_k, filters=filters)
        chunks_retrieved = len(chunks)

        # 2. Optionally truncate to avoid exceeding context window
        chunks_used_list = self._select_chunks(chunks)
        chunks_used = len(chunks_used_list)

        # 3. Build prompt
        if chunks_used_list:
            logger.debug("Chunks passed to LLM:")
            for i, c in enumerate(chunks_used_list, 1):
                logger.debug(
                    "  [%d] score=%.4f host=%s %s–%s\n      %s",
                    i, c.score, c.host,
                    c.window_start[11:19], c.window_end[11:19],
                    c.aggregated_text[:120].replace("\n", " ↵ "),
                )
        else:
            logger.debug("No chunks passed to LLM — context will be empty.")

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
            hyde_used=self._use_hyde,
            hypothetical_doc=hypothetical_doc,
            sources=self._format_sources(chunks_used_list),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # Approximate token budget for context (conservative; BGE chunks are ~200 tokens each)
    _MAX_CHUNKS = 6
    _MAX_CHARS  = 100_000  # ~25 000 tokens at 4 chars/token

    def _select_chunks(self, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """
        Select chunks that fit within the context budget.
        Chunks are already sorted by relevance (highest first).
        """
        selected: list[RetrievedChunk] = []
        total_chars = 0

        for chunk in chunks[: self._MAX_CHUNKS]:
            chunk_len = len(chunk.aggregated_text)
            logger.debug(
                "_select_chunks: chunk host=%s len=%d total_so_far=%d (budget=%d) text_preview=%r",
                chunk.host, chunk_len, total_chars, self._MAX_CHARS,
                chunk.aggregated_text[:80],
            )
            if total_chars + chunk_len > self._MAX_CHARS:
                logger.debug("_select_chunks: budget exceeded, stopping.")
                break
            selected.append(chunk)
            total_chars += chunk_len

        logger.debug(
            "_select_chunks: %d/%d chunks selected (%d chars total)",
            len(selected), len(chunks), total_chars,
        )
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
