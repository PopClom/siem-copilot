"""
rag/retriever.py
----------------
Embeds a natural-language query and retrieves the most relevant
EventWindow chunks from Qdrant.

Design notes
------------
* Uses the same Embedder class as the ingestion pipeline, but with the
  BGE *query* prefix instead of the passage prefix (important for recall).
* Optional metadata filters (host, user, time range) can be passed in
  to narrow the search before re-ranking by vector similarity.
* Returns a list of RetrievedChunk dataclasses — typed wrappers around
  the raw Qdrant payload — so the rest of the RAG chain never touches
  Qdrant types directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from typing import TYPE_CHECKING

from src.config.settings import EmbeddingConfig, VectorDBConfig
from src.embedding.embedder import Embedder
from src.vectordb.qdrant_store import QdrantStore

if TYPE_CHECKING:
    from src.rag.llm import LLMClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

@dataclass
class RetrievedChunk:
    score: float
    window_id: str
    window_start: str
    window_end: str
    host: Optional[str]
    user: Optional[str]
    source_name: str
    event_count: int
    aggregated_text: str
    is_neighbour: bool = False


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

# BGE recommends a different prefix for queries vs passages
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Retriever:
    """
    Wraps embedding + vector search into a single `retrieve()` call.

    Parameters
    ----------
    embedding_config:   same config used during ingestion
    vectordb_config:    Qdrant connection settings
    top_k:              number of chunks to return (overridable per call)
    score_threshold:    drop chunks below this cosine similarity (0–1)
    """

    def __init__(
        self,
        embedding_config: EmbeddingConfig,
        vectordb_config: VectorDBConfig,
        top_k: int = 8,
        score_threshold: float = 0.30,
        expand_context: bool = False,
    ) -> None:
        self.top_k = top_k
        self.score_threshold = score_threshold
        self.expand_context = expand_context
        self._embedder = Embedder(embedding_config)
        self._store = QdrantStore(vectordb_config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        filters: Optional[dict] = None,
    ) -> list[RetrievedChunk]:
        """
        Embed *query* and return the top-k most relevant chunks.

        Parameters
        ----------
        query:   natural-language question from the analyst
        top_k:   override the instance default
        filters: dict of exact-match Qdrant filters, e.g. {"host": "srv-01"}
        """
        k = top_k or self.top_k
        query_vector = self._embed_query(query)

        raw_hits = self._store.semantic_search(
            query_vector=query_vector,
            top_k=k,
            filters=filters,
        )

        chunks = [self._to_chunk(hit) for hit in raw_hits]

        # Log all scores before filtering so we can tune the threshold
        if chunks:
            scores = [c.score for c in chunks]
            logger.debug(
                "Scores before threshold (%.2f): %s",
                self.score_threshold,
                ", ".join(f"{s:.4f}" for s in scores),
            )
        else:
            logger.debug("Qdrant returned 0 hits for query: %r", query)

        # Apply score threshold
        passing = [c for c in chunks if c.score >= self.score_threshold]
        dropped = len(chunks) - len(passing)
        if dropped:
            logger.debug(
                "%d/%d chunks dropped by threshold %.2f (min score was %.4f)",
                dropped, len(chunks), self.score_threshold, min(c.score for c in chunks),
            )
        chunks = passing

        if not chunks:
            logger.info("No chunks above threshold %.2f for query: %r", self.score_threshold, query)
        else:
            logger.info(
                "Returning %d chunks (scores %.4f–%.4f)",
                len(chunks), chunks[-1].score, chunks[0].score,
            )

        if self.expand_context and chunks:
            chunks = self._expand_with_neighbours(chunks)

        return chunks

    def retrieve_with_hyde(
        self,
        query: str,
        llm: "LLMClient",
        top_k: Optional[int] = None,
        filters: Optional[dict] = None,
    ) -> tuple[list[RetrievedChunk], str]:
        """
        HyDE variant: ask the LLM to generate a hypothetical log window
        that would answer *query*, embed that document instead of the raw
        query, then search Qdrant.

        Returns (chunks, hypothetical_document) so the caller can log/inspect
        what the LLM generated.
        """
        from src.rag.prompt import HYDE_SYSTEM_PROMPT, build_hyde_messages

        # 1. Generate hypothetical document
        hyde_messages = build_hyde_messages(query)
        original_system = llm.system
        llm.system = HYDE_SYSTEM_PROMPT
        try:
            hypothetical_doc = llm.complete(hyde_messages)
        finally:
            llm.system = original_system  # restore for the main RAG call

        logger.debug("HyDE hypothetical document:\n%s", hypothetical_doc)

        # 2. Embed the hypothetical document using the passage prefix
        #    (it's a document, not a query, so we use the passage prefix)
        model_name = self._embedder.config.model.lower()
        if "bge" in model_name:
            prefixed = f"{self._embedder._BGE_PASSAGE_PREFIX}{hypothetical_doc}"
        else:
            prefixed = hypothetical_doc

        vectors = self._embedder.embed_texts([prefixed])
        hyde_vector = vectors[0]

        # 3. Search with the hypothetical vector
        k = top_k or self.top_k
        raw_hits = self._store.semantic_search(
            query_vector=hyde_vector,
            top_k=k,
            filters=filters,
        )
        chunks = [self._to_chunk(hit) for hit in raw_hits]

        if chunks:
            scores = [c.score for c in chunks]
            logger.debug(
                "HyDE scores before threshold (%.2f): %s",
                self.score_threshold,
                ", ".join(f"{s:.4f}" for s in scores),
            )

        passing = [c for c in chunks if c.score >= self.score_threshold]
        dropped = len(chunks) - len(passing)
        if dropped:
            logger.debug(
                "HyDE: %d/%d chunks dropped by threshold %.2f",
                dropped, len(chunks), self.score_threshold,
            )

        if not passing:
            logger.info("HyDE: no chunks above threshold %.2f", self.score_threshold)
        else:
            logger.info(
                "HyDE: returning %d chunks (scores %.4f–%.4f)",
                len(passing), passing[-1].score, passing[0].score,
            )

        if self.expand_context and passing:
            passing = self._expand_with_neighbours(passing)

        return passing, hypothetical_doc

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _expand_with_neighbours(
        self,
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """
        For each retrieved chunk, fetch the immediately preceding and
        following window on the same host and append them to the list.

        Neighbours are marked with is_neighbour=True and score=0.0 so the
        prompt can label them as context rather than direct matches.

        Only windows that are not already in the result set are added,
        preserving the original ranking order (retrieved chunks first,
        neighbours appended after).
        """
        from datetime import timezone
        from qdrant_client.models import DatetimeRange, FieldCondition, Filter, MatchValue

        existing_ids: set[str] = {c.window_id for c in chunks}
        neighbours: list[RetrievedChunk] = []

        for chunk in chunks:
            if not chunk.host or not chunk.window_start or not chunk.window_end:
                continue

            try:
                start = datetime.fromisoformat(chunk.window_start)
                end   = datetime.fromisoformat(chunk.window_end)
            except ValueError:
                logger.debug("Could not parse timestamps for chunk %s", chunk.window_id)
                continue

            # Ensure timezone-aware
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)

            window_duration = end - start

            # (window_before_start, window_before_end), (window_after_start, window_after_end)
            neighbour_ranges = [
                (start - window_duration, start),   # window immediately before
                (end, end + window_duration),        # window immediately after
            ]

            for nb_start, nb_end in neighbour_ranges:
                try:
                    hits, _ = self._store.client.scroll(
                        collection_name=self._store.config.collection,
                        limit=1,
                        with_payload=True,
                        with_vectors=False,
                        scroll_filter=Filter(
                            must=[
                                FieldCondition(
                                    key="host",
                                    match=MatchValue(value=chunk.host),
                                ),
                                FieldCondition(
                                    key="window_start",
                                    range=DatetimeRange(gte=nb_start, lt=nb_end),
                                ),
                            ]
                        ),
                    )
                except Exception as exc:
                    logger.debug("Neighbour scroll failed: %s", exc)
                    continue

                for point in hits:
                    payload = point.payload or {}
                    wid = payload.get("window_id", "")
                    if not wid or wid in existing_ids:
                        continue
                    existing_ids.add(wid)
                    neighbours.append(
                        self._to_chunk({"score": 0.0, **payload}, is_neighbour=True)
                    )

        if neighbours:
            logger.info(
                "Context expansion: added %d neighbour window(s).", len(neighbours)
            )

        # Retrieved chunks first (ranked by similarity), neighbours appended after
        return chunks + neighbours

    def _embed_query(self, query: str) -> list[float]:
        """Apply the BGE query prefix (if applicable) before embedding."""
        model_name = self._embedder.config.model.lower()
        prefixed = f"{_BGE_QUERY_PREFIX}{query}" if "bge" in model_name else query
        vectors = self._embedder.embed_texts([prefixed])
        return vectors[0]

    @staticmethod
    def _to_chunk(hit: dict, is_neighbour: bool = False) -> RetrievedChunk:
        return RetrievedChunk(
            score=hit.get("score", 0.0),
            window_id=hit.get("window_id", ""),
            window_start=hit.get("window_start", ""),
            window_end=hit.get("window_end", ""),
            host=hit.get("host"),
            user=hit.get("user"),
            source_name=hit.get("source_name", ""),
            event_count=hit.get("event_count", 0),
            aggregated_text=hit.get("aggregated_text", ""),
            is_neighbour=is_neighbour,
        )
