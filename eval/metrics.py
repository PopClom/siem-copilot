"""
eval/metrics.py
---------------
Retrieval evaluation metrics.

All functions operate on lists of retrieved window IDs and a set of
relevant window IDs (ground truth).

Metrics
-------
Precision@K:  of the top-K retrieved, fraction that are relevant
Recall@K:     of all relevant windows, fraction retrieved in top-K
F1@K:         harmonic mean of Precision@K and Recall@K
MRR:          Mean Reciprocal Rank — 1/rank of the first relevant result
              (0 if no relevant result found)
"""

from __future__ import annotations


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of top-K retrieved that are relevant."""
    if not retrieved or not relevant:
        return 0.0
    top_k = retrieved[:k]
    hits = sum(1 for wid in top_k if wid in relevant)
    return hits / k


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of relevant windows found in top-K retrieved."""
    if not relevant:
        return 1.0   # vacuously: nothing to find → found everything
    top_k = retrieved[:k]
    hits = sum(1 for wid in top_k if wid in relevant)
    return hits / len(relevant)


def f1_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Harmonic mean of Precision@K and Recall@K."""
    p = precision_at_k(retrieved, relevant, k)
    r = recall_at_k(retrieved, relevant, k)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """1 / rank of the first relevant result (0 if none found)."""
    for rank, wid in enumerate(retrieved, start=1):
        if wid in relevant:
            return 1.0 / rank
    return 0.0


def compute_all(
    retrieved: list[str],
    relevant: set[str],
    k_values: list[int] = (5, 10, 20),
) -> dict:
    """
    Compute all metrics for a single question.

    Parameters
    ----------
    retrieved:  ordered list of retrieved window_ids (most relevant first)
    relevant:   set of ground-truth relevant window_ids
    k_values:   K values to evaluate at

    Returns
    -------
    dict with keys: precision@K, recall@K, f1@K for each K, plus mrr
    """
    result: dict = {}
    for k in k_values:
        result[f"precision@{k}"] = round(precision_at_k(retrieved, relevant, k), 4)
        result[f"recall@{k}"]    = round(recall_at_k(retrieved, relevant, k), 4)
        result[f"f1@{k}"]        = round(f1_at_k(retrieved, relevant, k), 4)
    result["mrr"] = round(reciprocal_rank(retrieved, relevant), 4)
    result["n_retrieved"] = len(retrieved)
    result["n_relevant"]  = len(relevant)
    result["n_hits"]      = sum(1 for wid in retrieved if wid in relevant)
    return result
