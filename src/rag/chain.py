"""
rag/chain.py
------------
Orchestrates the full query flow using Anthropic tool use.

The LLM receives two tool definitions and decides which to call:

  semantic_search(query, top_k?, filters?)
      → embeds the query, retrieves relevant windows from Qdrant,
        returns the formatted context block

  detect_anomalies(since?, question?)
      → runs Isolation Forest + HDBSCAN over stored vectors,
        returns the formatted anomaly context block

If the LLM decides neither tool is needed (e.g. a greeting or a question
it can answer from general knowledge), it responds directly.

Turn structure
--------------
  Turn 1 — user question + tool definitions → LLM picks a tool or answers
  Turn 2 — tool result → LLM produces final natural-language answer
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from src.config.settings import Settings
from src.rag.llm import LLMClient, ToolUseResponse
from src.rag.prompt import SYSTEM_PROMPT, build_messages, format_context
from src.rag.retriever import RetrievedChunk, Retriever

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_messages(question: str, history: list[dict] | None) -> list[dict]:
    """
    Build the messages list for the API call.
    History entries are {"role": "user"|"assistant", "content": str}.
    The current question is appended as the final user turn.
    """
    messages: list[dict] = []
    for turn in (history or []):
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})
    return messages


# ---------------------------------------------------------------------------
# Response type
# ---------------------------------------------------------------------------

@dataclass
class RAGResponse:
    answer: str
    query: str
    tool_used: Optional[str]       # "semantic_search" | "detect_anomalies" | None
    chunks_retrieved: int = 0
    chunks_used: int = 0
    neighbours_added: int = 0
    hyde_used: bool = False
    hypothetical_doc: Optional[str] = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    sources: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tool definitions (Anthropic schema)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "semantic_search",
        "description": (
            "Search the security log database for windows relevant to the analyst's question. "
            "Use this for any question about specific events, hosts, users, processes, "
            "network connections, file activity, or attack techniques observed in the logs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query derived from the analyst's question.",
                },
                "filters": {
                    "type": "object",
                    "description": (
                        "Optional exact-match metadata filters, e.g. "
                        "{\"host\": \"srv-01\"}. Omit if no specific host/user filter is needed."
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "detect_anomalies",
        "description": (
            "Run unsupervised anomaly detection (Isolation Forest + HDBSCAN) over the stored "
            "log windows and return the anomalous ones. "
            "Use this when the analyst asks about anomalies, unusual activity, outliers, "
            "or behavioural deviations — not for questions about specific known attack techniques."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "description": (
                        "Only analyse windows newer than this duration. "
                        "Examples: '24h', '7d', '30m'. Omit to analyse all windows."
                    ),
                },
            },
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------

class RAGChain:
    """
    Single entry point for all analyst queries.
    The LLM decides whether to call semantic_search, detect_anomalies, or
    answer directly from general knowledge.
    """

    _MAX_CHARS = 200_000  # ~50 000 tokens at 4 chars/token

    def __init__(self, settings: Settings) -> None:
        rag_cfg = settings.rag
        self._settings = settings
        self._retriever = Retriever(
            embedding_config=settings.embedding,
            vectordb_config=settings.vector_db,
            top_k=rag_cfg.top_k,
            score_threshold=rag_cfg.score_threshold,
            expand_context=rag_cfg.expand_context,
        )
        self._llm = LLMClient(
            config=settings.llm,
            system=SYSTEM_PROMPT,
        )
        self._use_hyde: bool = rag_cfg.use_hyde

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self, question: str, history: list[dict] | None = None) -> RAGResponse:
        """
        Answer an analyst's natural-language question using tool use.
        The LLM picks the right tool (or answers directly).

        Parameters
        ----------
        question:  the analyst's question
        history:   optional conversation history as list of
                   {"role": "user"|"assistant", "content": str} dicts
        """
        t0 = time.monotonic()

        messages = _build_messages(question, history)

        llm_response: ToolUseResponse = self._llm.complete_with_tools(
            messages=messages,
            tools=TOOLS,
            tool_executor=self._execute_tool,
        )

        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "Query complete | tool=%s | latency=%dms | tokens=%d+%d",
            llm_response.tool_name, latency_ms,
            llm_response.input_tokens, llm_response.output_tokens,
        )

        # Pull retrieval metadata from tool_result if semantic_search was called
        meta = llm_response.tool_result or {}

        return RAGResponse(
            answer=llm_response.answer,
            query=question,
            tool_used=llm_response.tool_name,
            chunks_retrieved=meta.get("chunks_retrieved", 0),
            chunks_used=meta.get("chunks_used", 0),
            neighbours_added=meta.get("neighbours_added", 0),
            hyde_used=meta.get("hyde_used", False),
            hypothetical_doc=meta.get("hypothetical_doc"),
            latency_ms=latency_ms,
            input_tokens=llm_response.input_tokens,
            output_tokens=llm_response.output_tokens,
            sources=meta.get("sources", []),
        )

    def query_stream(
        self,
        question: str,
        history: list[dict] | None = None,
    ):
        """
        Streaming variant of query().
        Yields str tokens from stream_with_tools() — caller wraps in SSE.
        """
        messages = _build_messages(question, history)
        yield from self._llm.stream_with_tools(
            messages=messages,
            tools=TOOLS,
            tool_executor=self._execute_tool,
        )

    # ------------------------------------------------------------------
    # Tool executor — called by LLMClient when LLM picks a tool
    # ------------------------------------------------------------------

    def _execute_tool(self, tool_name: str, tool_input: dict) -> Any:
        """
        Dispatch to the right implementation based on tool_name.
        Returns a JSON-serialisable dict that gets sent back to the LLM
        as a tool_result.
        """
        if tool_name == "semantic_search":
            return self._run_semantic_search(tool_input)
        elif tool_name == "detect_anomalies":
            return self._run_anomaly_detection(tool_input)
        else:
            logger.warning("Unknown tool requested: %s", tool_name)
            return {"error": f"Unknown tool: {tool_name}"}

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def _run_semantic_search(self, tool_input: dict) -> dict:
        """
        Execute the semantic_search tool:
          embed → retrieve → (HyDE?) → expand → select → format context
        Returns a dict with the context text + metadata for the LLM.
        """
        query   = tool_input.get("query", "")
        filters = tool_input.get("filters") or None
        top_k = self._retriever.top_k

        hypothetical_doc: Optional[str] = None

        if self._use_hyde:
            logger.info("Using HyDE for semantic_search")
            chunks, hypothetical_doc = self._retriever.retrieve_with_hyde(
                query=query, llm=self._llm, top_k=top_k, filters=filters,
            )
        else:
            chunks = self._retriever.retrieve(query, top_k=top_k, filters=filters)

        chunks_retrieved  = sum(1 for c in chunks if not c.is_neighbour)
        neighbours_added  = len(chunks) - chunks_retrieved
        chunks_used_list  = self._select_chunks(chunks)
        chunks_used       = len(chunks_used_list)

        # Format context exactly as before — the LLM reads this as tool_result
        context_text = format_context(chunks_used_list)

        logger.info(
            "semantic_search: retrieved=%d used=%d neighbours=%d",
            chunks_retrieved, chunks_used, neighbours_added,
        )

        return {
            "context":          context_text,
            "chunks_retrieved": chunks_retrieved,
            "chunks_used":      chunks_used,
            "neighbours_added": neighbours_added,
            "hyde_used":        self._use_hyde,
            "hypothetical_doc": hypothetical_doc,
            "sources":          self._format_sources(chunks_used_list),
        }

    def _run_anomaly_detection(self, tool_input: dict) -> dict:
        """
        Execute the detect_anomalies tool:
          fetch vectors → IF + HDBSCAN → format anomaly context
        Returns a dict with the anomaly context text + stats for the LLM.
        """
        from src.anomaly.chain import AnomalyChain, parse_since
        from src.anomaly.reporter import build_anomaly_context

        since_str = tool_input.get("since")
        since_td  = parse_since(since_str) if since_str else None

        logger.info("detect_anomalies tool called (since=%s)", since_str or "all")

        # Run detection without LLM summary — the LLM in this conversation
        # will synthesise the summary itself from the context we return
        chain = AnomalyChain(
            settings=self._settings,
            since=since_td,
            with_summary=False,
        )
        response = chain.run()
        result   = response.result

        context_text = build_anomaly_context(result)

        logger.info(
            "detect_anomalies: total=%d anomalous=%d clusters=%d",
            result.total_windows, result.n_anomalies, result.n_clusters,
        )

        return {
            "context":        context_text,
            "total_windows":  result.total_windows,
            "n_anomalies":    result.n_anomalies,
            "anomaly_ratio":  round(result.anomaly_ratio, 4),
            "n_clusters":     result.n_clusters,
            "noise_ratio":    round(result.noise_ratio, 4),
            "run_timestamp":  result.run_timestamp,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_chunks(self, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        selected: list[RetrievedChunk] = []
        total_chars = 0

        for chunk in chunks:
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
                "is_neighbour": chunk.is_neighbour,
            }
            for chunk in chunks
        ]
