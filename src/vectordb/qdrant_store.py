"""
vectordb/qdrant_store.py
------------------------
Thin wrapper around the Qdrant client for storing and querying EventWindows.

Responsibilities
----------------
* Create/verify the collection on first use (with the correct vector dim)
* Upsert EventWindow embeddings with rich metadata as payload
* Expose a semantic_search() method for future RAG queries
* Handle duplicate detection via the deterministic window ID

Dependencies
------------
    pip install qdrant-client
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from src.anomaly.detector import WindowAnomaly

from src.config.settings import VectorDBConfig
from src.models import EventWindow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Qdrant store
# ---------------------------------------------------------------------------

class QdrantStore:
    """
    Manages a single Qdrant collection that stores EventWindow embeddings.

    Usage
    -----
        store = QdrantStore(config.vector_db)
        store.ensure_collection(dimension=384)
        store.upsert(windows, vectors)
        results = store.semantic_search(query_vector, top_k=5)
    """

    def __init__(self, config: VectorDBConfig) -> None:
        self.config = config
        self._client = None

    # ------------------------------------------------------------------
    # Lazy client initialisation
    # ------------------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            try:
                from qdrant_client import QdrantClient
            except ImportError as exc:
                raise ImportError(
                    "qdrant-client is not installed. Run: pip install qdrant-client"
                ) from exc

            logger.info(
                "Connecting to Qdrant at %s:%d …", self.config.host, self.config.port
            )
            self._client = QdrantClient(host=self.config.host, port=self.config.port)
        return self._client

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def drop_collection(self) -> None:
        """Delete the collection and all its data. Used before a full re-ingest."""
        existing = {c.name for c in self.client.get_collections().collections}
        if self.config.collection in existing:
            self.client.delete_collection(self.config.collection)
            logger.info("Collection '%s' deleted.", self.config.collection)
        else:
            logger.info("Collection '%s' does not exist, nothing to drop.", self.config.collection)

    def ensure_collection(self, dimension: int) -> None:
        """Create the collection if it does not already exist."""
        from qdrant_client.models import Distance, VectorParams

        existing = {c.name for c in self.client.get_collections().collections}

        if self.config.collection in existing:
            logger.info("Collection '%s' already exists.", self.config.collection)
            return

        logger.info(
            "Creating Qdrant collection '%s' (dim=%d, metric=Cosine) …",
            self.config.collection, dimension,
        )
        self.client.create_collection(
            collection_name=self.config.collection,
            vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
        )

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    def upsert(
        self,
        windows: list[EventWindow],
        vectors: list[list[float]],
    ) -> int:
        """
        Upsert window embeddings into Qdrant.
        Windows without a vector (empty list) are silently skipped.
        Returns the number of points actually upserted.
        """
        from qdrant_client.models import PointStruct

        points: list[PointStruct] = []

        for window, vector in zip(windows, vectors):
            if not vector:
                continue

            payload = _window_to_payload(window)

            points.append(
                PointStruct(
                    id=_stable_id(window.id),
                    vector=vector,
                    payload=payload,
                )
            )

        if not points:
            logger.warning("No points to upsert.")
            return 0

        self.client.upsert(
            collection_name=self.config.collection,
            points=points,
        )
        logger.info("Upserted %d points into '%s'.", len(points), self.config.collection)
        return len(points)

    # ------------------------------------------------------------------
    # Anomaly detection support
    # ------------------------------------------------------------------

    def fetch_all(
        self,
        since: Optional["timedelta"] = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch all points (vector + payload) from the collection.
        Optionally filters to windows newer than `since` (a timedelta).
        The time filter is applied server-side by Qdrant for efficiency.
        Uses Qdrant's scroll API to page through large collections.

        Note: requires a datetime payload index on 'window_start' for the
        filter to be fast.  Create it once after first ingestion with:
            store.create_datetime_index("window_start")
        Without the index Qdrant still works but does a full scan.
        """
        from datetime import datetime, timezone
        from qdrant_client.models import DatetimeRange, FieldCondition, Filter

        logger.info("Scrolling all points from '%s' …", self.config.collection)

        qdrant_filter = None
        if since is not None:
            cutoff = datetime(2020, 10, 8, 13, tzinfo=timezone.utc) - since # hardcoded
            qdrant_filter = Filter(
                must=[
                    FieldCondition(
                        key="window_start",
                        range=DatetimeRange(gte=cutoff),
                    )
                ]
            )
            logger.info(
                "Server-side filter: window_start >= %s", cutoff.isoformat()
            )

        points: list[dict[str, Any]] = []
        offset = None

        while True:
            batch, next_offset = self.client.scroll(
                collection_name=self.config.collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=True,
                scroll_filter=qdrant_filter,
            )

            for point in batch:
                points.append({
                    "id":      point.id,
                    "vector":  point.vector,
                    "payload": point.payload or {},
                })

            if next_offset is None:
                break
            offset = next_offset

        logger.info("Fetched %d points from Qdrant.", len(points))
        return points

    def create_datetime_index(self, field: str = "window_start") -> None:
        """
        Create a payload index on a datetime field so range filters are fast.
        Safe to call multiple times — Qdrant ignores duplicate index creation.
        """
        from qdrant_client.models import PayloadSchemaType

        self.client.create_payload_index(
            collection_name=self.config.collection,
            field_name=field,
            field_schema=PayloadSchemaType.DATETIME,
        )
        logger.info("Datetime index created on field '%s'.", field)

    def update_anomaly_scores(self, windows: "list[WindowAnomaly]") -> None:
        """
        Write anomaly scores back to the payload of each point.
        Uses set_payload so existing payload fields are preserved.
        """
        from qdrant_client.models import PointIdsList

        logger.info("Writing anomaly scores for %d windows …", len(windows))

        # Batch updates in groups of 256 to avoid large single requests
        batch_size = 256
        for i in range(0, len(windows), batch_size):
            batch = windows[i : i + batch_size]
            for w in batch:
                self.client.set_payload(
                    collection_name=self.config.collection,
                    payload={
                        "isolation_score": round(w.isolation_score, 6),
                        "cluster_id":      w.cluster_id,
                        "is_anomaly":      w.is_anomaly,
                        "anomaly_label":   w.anomaly_label,
                    },
                    points=PointIdsList(points=[w.point_id]),
                )

        logger.info("Anomaly scores written.")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def semantic_search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Return the top-k most similar windows.

        Parameters
        ----------
        query_vector: embedding of the user's query
        top_k:        number of results
        filters:      optional Qdrant filter dict (e.g. {"host": "server01"})
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        qdrant_filter = None
        if filters:
            conditions = [
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filters.items()
            ]
            qdrant_filter = Filter(must=conditions)

        hits = self.client.query_points(
            collection_name=self.config.collection,
            query=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )

        return [
            {"score": hit.score, **hit.payload}
            for hit in hits.points
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _window_to_payload(window: EventWindow) -> dict[str, Any]:
    return {
        "window_id":      window.id,
        "window_start":   window.window_start.isoformat(),
        "window_end":     window.window_end.isoformat(),
        "host":           window.host,
        "user":           window.user,
        "source_name":    window.source_name,
        "event_count":    len(window.events),
        "aggregated_text": window.aggregated_text,
    }


def _stable_id(window_id: str) -> int:
    """
    Convert the string window ID to a stable unsigned 64-bit integer
    suitable for Qdrant's point ID field.
    Uses Python's built-in hash seeded consistently via hashlib.
    """
    import hashlib
    digest = hashlib.sha256(window_id.encode()).digest()
    # Take first 8 bytes → uint64
    return int.from_bytes(digest[:8], byteorder="big")
