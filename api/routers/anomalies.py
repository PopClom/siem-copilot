"""
api/routers/anomalies.py
------------------------
GET /anomalies — run anomaly detection and return results as JSON.

Query parameters
----------------
since:          duration string (e.g. "24h", "7d") to filter by time
with_summary:   if true (default), include an LLM-generated summary
question:       the analyst question passed to the LLM for the summary
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class WindowAnomalySchema(BaseModel):
    window_id: str
    window_start: str
    window_end: str
    host: Optional[str]
    user: Optional[str]
    source_name: str
    event_count: int
    isolation_score: float
    cluster_id: int
    anomaly_label: str
    aggregated_text_preview: str   # truncated to 300 chars


class AnomalyDetectionResponse(BaseModel):
    total_windows: int
    n_anomalies: int
    anomaly_ratio: float
    n_clusters: int
    noise_ratio: float
    run_timestamp: str
    anomalous_windows: list[WindowAnomalySchema]
    summary: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/anomalies", response_model=AnomalyDetectionResponse, tags=["anomaly"])
async def detect_anomalies(
    request: Request,
    since: Optional[str] = Query(
        default=None,
        description="Only analyse windows newer than this duration (e.g. '24h', '7d', '30m')",
    ),
    with_summary: bool = Query(
        default=True,
        description="Generate an LLM summary of the anomalies found",
    ),
    question: str = Query(
        default="Summarise the anomalies detected and highlight the most suspicious activity.",
        description="Question passed to the LLM when generating the summary",
    ),
) -> AnomalyDetectionResponse:
    """
    Run Isolation Forest + HDBSCAN over all stored log windows and return
    the anomalous ones, optionally with an LLM-generated summary.
    """
    from src.anomaly.chain import AnomalyChain, parse_since

    settings = request.app.state.settings

    try:
        since_td = parse_since(since)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        chain = AnomalyChain(
            settings=settings,
            since=since_td,
            with_summary=with_summary,
        )
        response = chain.run(question=question)
    except Exception as exc:
        logger.exception("Anomaly detection error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Anomaly detection error: {exc}") from exc

    result = response.result
    return AnomalyDetectionResponse(
        total_windows=result.total_windows,
        n_anomalies=result.n_anomalies,
        anomaly_ratio=round(result.anomaly_ratio, 4),
        n_clusters=result.n_clusters,
        noise_ratio=round(result.noise_ratio, 4),
        run_timestamp=result.run_timestamp,
        anomalous_windows=[
            WindowAnomalySchema(
                window_id=w.window_id,
                window_start=w.window_start,
                window_end=w.window_end,
                host=w.host,
                user=w.user,
                source_name=w.source_name,
                event_count=w.event_count,
                isolation_score=round(w.isolation_score, 4),
                cluster_id=w.cluster_id,
                anomaly_label=w.anomaly_label,
                aggregated_text_preview=w.aggregated_text[:300],
            )
            for w in result.anomalous_windows
        ],
        summary=response.summary,
    )
