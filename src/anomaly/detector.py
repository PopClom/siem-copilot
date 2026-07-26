"""
anomaly/detector.py
-------------------
Runs unsupervised anomaly detection over the vectors already stored in Qdrant.

Two complementary algorithms are combined:

Isolation Forest
    Assigns a continuous anomaly score to every window.  A window is
    anomalous if it is easy to isolate from the rest of the dataset —
    i.e. it lives in a sparse region of the embedding space.
    Score range after normalisation: 0.0 (normal) → 1.0 (very anomalous).

HDBSCAN
    Clusters windows by vector similarity.  Points that don't belong to
    any cluster are labelled as noise (cluster_id = -1) and are strong
    candidates for anomalies.  Also identifies the dominant "behaviour
    groups" in the dataset, which is useful context for the analyst.

Why both?
    Isolation Forest catches isolated outliers but can miss dense anomalous
    clusters.  HDBSCAN catches structural outliers (points that don't fit
    any behaviour pattern) but is sensitive to density.  Together they give
    complementary signal.

The detector reads vectors directly from Qdrant (no re-embedding needed)
and writes results back to the payload of each point.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np

from src.config.settings import VectorDBConfig
from src.vectordb.qdrant_store import QdrantStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class WindowAnomaly:
    """Anomaly scores for a single EventWindow."""
    point_id: int
    window_id: str
    window_start: str
    window_end: str
    host: Optional[str]
    user: Optional[str]
    source_name: str
    event_count: int
    aggregated_text: str

    # Scores set by the detector
    isolation_score: float = 0.0   # 0 = normal, 1 = very anomalous
    cluster_id: int = -1           # -1 = noise / no cluster
    is_anomaly: bool = False       # final label combining both signals

    @property
    def anomaly_label(self) -> str:
        if not self.is_anomaly:
            return "normal"
        if self.cluster_id == -1:
            return "outlier"
        return "anomalous_cluster"


@dataclass
class DetectionResult:
    """Outcome of a full anomaly detection run."""
    total_windows: int
    anomalous_windows: list[WindowAnomaly]
    n_clusters: int
    noise_ratio: float             # fraction of points labelled as HDBSCAN noise
    run_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def n_anomalies(self) -> int:
        return len(self.anomalous_windows)

    @property
    def anomaly_ratio(self) -> float:
        return self.n_anomalies / self.total_windows if self.total_windows else 0.0


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class AnomalyDetector:
    """
    Fetches all vectors from Qdrant, runs IF + HDBSCAN, and writes scores
    back to the Qdrant payload.

    Parameters
    ----------
    vectordb_config:        Qdrant connection settings
    contamination:          expected fraction of anomalies for Isolation Forest
                            (0.05 = assume 5 % of windows are anomalous)
    isolation_threshold:    minimum IF score to flag a window (0–1)
    min_cluster_size:       HDBSCAN minimum cluster size
    include_noise_as_anomaly: treat HDBSCAN noise points as anomalies
    since:                  if set, only analyse windows newer than this delta
                            (e.g. timedelta(hours=24))
    """

    def __init__(
        self,
        vectordb_config: VectorDBConfig,
        contamination: float = 0.05,
        isolation_threshold: float = 0.6,
        min_cluster_size: int = 5,
        include_noise_as_anomaly: bool = True,
        since: Optional[timedelta] = None,
    ) -> None:
        self._store = QdrantStore(vectordb_config)
        self.contamination = contamination
        self.isolation_threshold = isolation_threshold
        self.min_cluster_size = min_cluster_size
        self.include_noise_as_anomaly = include_noise_as_anomaly
        self.since = since

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> DetectionResult:
        """
        Full detection pipeline:
          1. Fetch vectors + payloads from Qdrant
          2. Run Isolation Forest
          3. Run HDBSCAN
          4. Combine signals → is_anomaly label
          5. Write scores back to Qdrant payload
          6. Return DetectionResult
        """
        logger.info("Fetching windows from Qdrant …")
        points = self._store.fetch_all(since=self.since)

        if not points:
            logger.warning("No windows found in Qdrant. Run ingestion first.")
            return DetectionResult(
                total_windows=0,
                anomalous_windows=[],
                n_clusters=0,
                noise_ratio=0.0,
            )

        logger.info("Loaded %d windows. Running anomaly detection …", len(points))

        ids      = [p["id"]      for p in points]
        vectors  = np.array([p["vector"]  for p in points], dtype=np.float32)
        payloads = [p["payload"] for p in points]

        # 2. Isolation Forest
        if_scores = self._run_isolation_forest(vectors)

        # 3. HDBSCAN
        cluster_ids = self._run_hdbscan(vectors)

        # 4. Combine
        n_clusters = len(set(cluster_ids) - {-1})
        noise_ratio = float(np.sum(np.array(cluster_ids) == -1)) / len(cluster_ids)

        windows: list[WindowAnomaly] = []
        for i, (pid, payload) in enumerate(zip(ids, payloads)):
            iso  = float(if_scores[i])
            clus = int(cluster_ids[i])

            is_anomaly = self._label(iso, clus)

            w = WindowAnomaly(
                point_id=pid,
                window_id=payload.get("window_id", ""),
                window_start=payload.get("window_start", ""),
                window_end=payload.get("window_end", ""),
                host=payload.get("host"),
                user=payload.get("user"),
                source_name=payload.get("source_name", ""),
                event_count=payload.get("event_count", 0),
                aggregated_text=payload.get("aggregated_text", ""),
                isolation_score=iso,
                cluster_id=clus,
                is_anomaly=is_anomaly,
            )
            windows.append(w)

        # 5. Write scores back to Qdrant
        self._store.update_anomaly_scores(windows)

        anomalous = [w for w in windows if w.is_anomaly]
        # Sort by isolation score descending
        anomalous.sort(key=lambda w: w.isolation_score, reverse=True)

        logger.info(
            "Detection complete — %d/%d windows flagged as anomalous "
            "(clusters=%d, noise_ratio=%.1f%%)",
            len(anomalous), len(windows), n_clusters, noise_ratio * 100,
        )

        return DetectionResult(
            total_windows=len(windows),
            anomalous_windows=anomalous,
            n_clusters=n_clusters,
            noise_ratio=noise_ratio,
        )

    # ------------------------------------------------------------------
    # Isolation Forest
    # ------------------------------------------------------------------

    def _run_isolation_forest(self, vectors: np.ndarray) -> np.ndarray:
        """
        Returns a normalised anomaly score in [0, 1] for each point.
        sklearn's decision_function returns negative values for anomalies,
        so we invert and normalise to [0, 1].
        """
        try:
            from sklearn.ensemble import IsolationForest
        except ImportError as exc:
            raise ImportError("Run: pip install scikit-learn") from exc

        logger.info("Running Isolation Forest (contamination=%.2f) …", self.contamination)

        clf = IsolationForest(
            contamination=self.contamination,
            random_state=42,
            n_jobs=-1,
        )
        clf.fit(vectors)

        # decision_function: higher = more normal (positive), lower = more anomalous (negative)
        raw_scores = clf.decision_function(vectors)

        # Invert so that higher = more anomalous, then min-max normalise to [0, 1]
        inverted = -raw_scores
        min_s, max_s = inverted.min(), inverted.max()
        if max_s > min_s:
            normalised = (inverted - min_s) / (max_s - min_s)
        else:
            normalised = np.zeros_like(inverted)

        n_flagged = int(clf.predict(vectors).tolist().count(-1))
        logger.info("Isolation Forest: %d points flagged as outliers.", n_flagged)

        return normalised

    # ------------------------------------------------------------------
    # HDBSCAN
    # ------------------------------------------------------------------

    def _run_hdbscan(self, vectors: np.ndarray) -> list[int]:
        """
        Returns a cluster label per point.  -1 means noise (no cluster).
        Falls back to a mock implementation if hdbscan is not installed,
        so the rest of the pipeline can still run.
        """
        try:
            import hdbscan
        except ImportError:
            logger.warning(
                "hdbscan not installed (pip install hdbscan). "
                "Skipping HDBSCAN — all points assigned to cluster 0."
            )
            return [0] * len(vectors)

        logger.info(
            "Running HDBSCAN (min_cluster_size=%d) …", self.min_cluster_size
        )

        # Cosine metric works well for normalised embedding vectors
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=self.min_cluster_size,
            metric="euclidean",   # vectors are L2-normalised → euclidean ≈ cosine
            core_dist_n_jobs=-1,
        )
        clusterer.fit(vectors)

        labels = clusterer.labels_.tolist()
        n_clusters = len(set(labels) - {-1})
        n_noise    = labels.count(-1)
        logger.info(
            "HDBSCAN: %d clusters, %d noise points (%.1f%%).",
            n_clusters, n_noise, 100 * n_noise / len(labels),
        )
        return labels

    # ------------------------------------------------------------------
    # Labelling
    # ------------------------------------------------------------------

    def _label(self, isolation_score: float, cluster_id: int) -> bool:
        """
        Combine IF score and HDBSCAN label into a single is_anomaly boolean.
        A window is anomalous if EITHER:
          - Its IF score exceeds the threshold, OR
          - HDBSCAN marked it as noise AND include_noise_as_anomaly is True
        """
        high_if_score = isolation_score >= self.isolation_threshold
        is_noise = cluster_id == -1 and self.include_noise_as_anomaly
        return high_if_score or is_noise
