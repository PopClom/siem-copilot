"""
anomaly/chain.py
----------------
Orchestrates the anomaly detection pipeline and optionally feeds the
results to the LLM for a natural-language summary.

Used in two ways:
  1. CLI:  python main.py --detect-anomalies [--since 24h]
  2. API:  GET /anomalies (returns the DetectionResult as JSON)
  3. Future tool use: the LLM calls detect_anomalies() as a tool
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from src.anomaly.detector import AnomalyDetector, DetectionResult
from src.anomaly.reporter import ANOMALY_SYSTEM_ADDENDUM, build_anomaly_messages
from src.config.settings import Settings
from src.rag.llm import LLMClient
from src.rag.prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Regex to parse "24h", "30m", "7d" into a timedelta
_SINCE_RE = re.compile(r"^(\d+)([smhd])$")
_SINCE_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_since(since_str: Optional[str]) -> Optional[timedelta]:
    """Convert a human duration string ('24h', '30m', '7d') to a timedelta."""
    if not since_str:
        return None
    m = _SINCE_RE.match(since_str.strip())
    if not m:
        raise ValueError(
            f"Invalid --since value '{since_str}'. "
            "Use format like '24h', '30m', '7d'."
        )
    value, unit = int(m.group(1)), m.group(2)
    return timedelta(**{_SINCE_UNITS[unit]: value})


@dataclass
class AnomalyResponse:
    result: DetectionResult
    summary: Optional[str] = None    # LLM-generated summary (if requested)


class AnomalyChain:
    """
    Runs anomaly detection and optionally generates an LLM summary.

    Parameters
    ----------
    settings:       full config
    since:          timedelta to filter windows (None = all windows)
    with_summary:   if True, call the LLM to summarise the results
    """

    def __init__(
        self,
        settings: Settings,
        since: Optional[timedelta] = None,
        with_summary: bool = True,
    ) -> None:
        self._detector = AnomalyDetector(
            vectordb_config=settings.vector_db,
            contamination=settings.anomaly.contamination if settings.anomaly else 0.05,
            isolation_threshold=settings.anomaly.isolation_threshold if settings.anomaly else 0.6,
            min_cluster_size=settings.anomaly.min_cluster_size if settings.anomaly else 5,
            include_noise_as_anomaly=settings.anomaly.include_noise_as_anomaly if settings.anomaly else True,
            since=since,
        )
        self._with_summary = with_summary
        self._llm = LLMClient(
            config=settings.llm,
            system=SYSTEM_PROMPT + ANOMALY_SYSTEM_ADDENDUM,
        ) if with_summary else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, question: str = "Summarise the anomalies detected.") -> AnomalyResponse:
        """
        Run detection and optionally generate an LLM summary.

        Parameters
        ----------
        question:   the analyst's question (used when generating the LLM summary)
        """
        # 1. Detect
        result = self._detector.run()

        # 2. Optionally summarise with LLM
        summary: Optional[str] = None
        if self._with_summary and self._llm is not None:
            if result.total_windows == 0:
                summary = "No windows were found in the database. Run ingestion first."
            else:
                logger.info("Generating LLM summary of anomalies …")
                messages = build_anomaly_messages(question, result)
                summary = self._llm.complete(messages)
                logger.info("LLM summary generated (%d chars).", len(summary))

        return AnomalyResponse(result=result, summary=summary)
