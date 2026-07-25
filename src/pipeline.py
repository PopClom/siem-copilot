"""
src/pipeline.py
---------------
Ingestion pipeline orchestrator.

Wires together:  reader → normalizer → windower → embedder → vector store

Design
------
* Processes one source at a time (sources are independent).
* Events are accumulated in memory per source, then windowed.
  For very large sources this could be changed to a streaming approach,
  but for the current scope (file-based datasets) it is fine.
* The embedder and vector store are initialised once and reused.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.config.settings import Settings
from src.embedding.embedder import Embedder
from src.ingestion.reader import read_source
from src.models import EventWindow, NormalizedEvent
from src.normalization.normalizer import normalize
from src.vectordb.qdrant_store import QdrantStore
from src.windowing.windower import build_windows

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """
    End-to-end ingestion pipeline.

    Parameters
    ----------
    settings:    validated config object
    overlap:     whether to use 50 % overlapping windows (default True)
    dry_run:     if True, skip embedding and vector store upsert
    """

    def __init__(
        self,
        settings: Settings,
        overlap: bool = True,
        dry_run: bool = False,
        reingest: bool = False,
    ) -> None:
        self.settings = settings
        self.overlap = overlap
        self.dry_run = dry_run
        self.reingest = reingest

        self._embedder: Optional[Embedder] = None
        self._store: Optional[QdrantStore] = None

    # ------------------------------------------------------------------
    # Lazy component initialisation
    # ------------------------------------------------------------------

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder(self.settings.embedding)
        return self._embedder

    @property
    def store(self) -> QdrantStore:
        if self._store is None:
            self._store = QdrantStore(self.settings.vector_db)
        return self._store

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> dict[str, int]:
        """
        Process all enabled sources.
        Returns a summary dict: {source_name: windows_upserted}.
        """
        sources = self.settings.enabled_sources
        if not sources:
            logger.warning("No enabled sources found in configuration.")
            return {}

        logger.info("Starting pipeline for %d source(s) …", len(sources))

        if self.reingest and not self.dry_run:
            logger.info("--reingest: dropping collection '%s' before ingestion …", self.settings.vector_db.collection)
            self.store.drop_collection()

        summary: dict[str, int] = {}

        for source in sources:
            logger.info("─── Processing source: %s ───", source.name)
            count = self._process_source(source)
            summary[source.name] = count

        total = sum(summary.values())
        logger.info("Pipeline complete. Total windows upserted: %d", total)
        return summary

    # ------------------------------------------------------------------
    # Per-source processing
    # ------------------------------------------------------------------

    def _process_source(self, source) -> int:
        # 1. Ingest + normalise
        events = self._ingest(source)
        if not events:
            logger.warning("Source '%s': no events after normalisation.", source.name)
            return 0

        logger.info("Source '%s': %d normalised events.", source.name, len(events))

        # 2. Window
        windows = build_windows(events, self.settings.grouping)
        if not windows:
            logger.warning("Source '%s': no windows generated.", source.name)
            return 0

        logger.info("Source '%s': %d windows.", source.name, len(windows))

        if self.dry_run:
            logger.info("[dry-run] Skipping embed + upsert.")
            self._log_sample_windows(windows)
            return 0

        # 3. Embed
        vectors = self.embedder.embed_windows(windows)

        # 4. Ensure collection exists (first call creates it)
        self.store.ensure_collection(dimension=self.embedder.dimension)

        # 5. Upsert
        upserted = self.store.upsert(windows, vectors)
        return upserted

    def _ingest(self, source) -> list[NormalizedEvent]:
        events: list[NormalizedEvent] = []
        for raw_event in read_source(source):
            normed = normalize(raw_event)
            if normed is not None:
                events.append(normed)
        return events

    # ------------------------------------------------------------------
    # Debug helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _log_sample_windows(windows: list[EventWindow], n: int = 3) -> None:
        logger.info("Sample windows (first %d):", min(n, len(windows)))
        for w in windows[:n]:
            logger.info(
                "[%s – %s] host=%s events=%d\n    %s",
                w.window_start.strftime("%H:%M:%S"),
                w.window_end.strftime("%H:%M:%S"),
                w.host,
                len(w.events),
                w.aggregated_text[:1000].replace("\n", "\n    "),
            )
