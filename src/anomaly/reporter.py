"""
anomaly/reporter.py
-------------------
Converts a DetectionResult into a structured text block that the LLM
can reason over — analogous to what format_context() does for RAG chunks.

The report is intentionally terse: the LLM adds the analytical layer,
the reporter just structures the facts.
"""

from __future__ import annotations

from src.anomaly.detector import DetectionResult, WindowAnomaly


# ---------------------------------------------------------------------------
# Text report for the LLM
# ---------------------------------------------------------------------------

def build_anomaly_context(result: DetectionResult, max_windows: int = 10) -> str:
    """
    Build a <context> block describing anomalous windows.
    Used as input to the LLM when the analyst asks about anomalies.
    """
    lines = ["<anomaly_context>"]

    lines.append(
        f"Detection run: {result.run_timestamp}\n"
        f"Total windows analysed: {result.total_windows}\n"
        f"Anomalous windows: {result.n_anomalies} "
        f"({result.anomaly_ratio * 100:.1f}%)\n"
        f"Behaviour clusters found: {result.n_clusters}\n"
        f"HDBSCAN noise ratio: {result.noise_ratio * 100:.1f}%"
    )

    if not result.anomalous_windows:
        lines.append("\nNo anomalous windows detected.")
        lines.append("</anomaly_context>")
        return "\n".join(lines)

    lines.append(f"\nTop anomalous windows (showing {min(max_windows, result.n_anomalies)}):")

    for i, w in enumerate(result.anomalous_windows[:max_windows], 1):
        lines.append(_format_window(i, w))

    if result.n_anomalies > max_windows:
        lines.append(
            f"\n… and {result.n_anomalies - max_windows} more anomalous windows not shown."
        )

    lines.append("</anomaly_context>")
    return "\n".join(lines)


def _format_window(idx: int, w: WindowAnomaly) -> str:
    host_label  = w.host or "unknown"
    user_label  = f" | user={w.user}" if w.user else ""
    cluster_label = f"cluster={w.cluster_id}" if w.cluster_id != -1 else "noise (no cluster)"

    header = (
        f"\n[Anomaly {idx} | {w.anomaly_label.upper()} | "
        f"if_score={w.isolation_score:.3f} | {cluster_label}]\n"
        f"Host: {host_label}{user_label} | "
        f"{w.window_start[11:19]}–{w.window_end[11:19]} UTC | "
        f"events: {w.event_count}"
    )

    # Truncate long windows so we don't blow the context budget
    text = w.aggregated_text[:5000]
    if len(w.aggregated_text) > 5000:
        text += f"\n… [{len(w.aggregated_text) - 5000} chars truncated]"

    return f"{header}\n{text}"


# ---------------------------------------------------------------------------
# System prompt addendum for anomaly queries
# ---------------------------------------------------------------------------

ANOMALY_SYSTEM_ADDENDUM = """
You are also given an <anomaly_context> block produced by an unsupervised
anomaly detector (Isolation Forest + HDBSCAN) that ran over the embedded
log windows.

When analysing anomalies:
- Focus on windows with high isolation scores (≥ 0.7) or labelled as OUTLIER.
- HDBSCAN noise points (no cluster) are structurally different from all other
  windows — treat them as high-priority candidates.
- Cross-reference anomalous windows with known attack patterns when possible.
- If multiple anomalous windows share a host or time range, highlight the
  correlation explicitly.
- Distinguish between statistical anomalies and security-relevant anomalies —
  a window can be statistically unusual without being malicious.
"""


def build_anomaly_messages(question: str, result: DetectionResult) -> list[dict]:
    """
    Build the messages list for an anomaly-focused LLM call.
    Includes both the anomaly context and the analyst's question.
    """
    context = build_anomaly_context(result)
    user_content = f"{context}\n\n<question>{question}</question>"
    return [{"role": "user", "content": user_content}]
