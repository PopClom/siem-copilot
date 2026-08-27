#!/usr/bin/env python3
"""
Recalculate retrieval metrics from an existing evaluation JSON.

This script DOES NOT call the API or rerun the evaluation.
It reads the previously saved JSON and recalculates, for every question
with ground truth:

    precision = hits / number of retrieved_ids
    recall    = hits / number of relevant_ids
    f1        = harmonic mean of precision and recall
    mrr       = reciprocal rank of the first relevant retrieved item

In other words, these are the metrics over the full retrieved list, not
Precision@5 / Recall@5.

Usage:
    python recalc_metrics.py input.json
    python recalc_metrics.py input.json -o corrected.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def calculate_metrics(retrieved_ids: list[str], relevant_ids: list[str]) -> dict:
    """Calculate metrics over the complete retrieved list."""
    relevant = set(relevant_ids)

    # IDs that were actually retrieved and are in the ground truth.
    hits = sum(1 for wid in retrieved_ids if wid in relevant)

    n_retrieved = len(retrieved_ids)
    n_relevant = len(relevant)

    precision = hits / n_retrieved if n_retrieved else 0.0
    recall = hits / n_relevant if n_relevant else 0.0

    if precision + recall:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0

    # MRR uses the ORDER of retrieved_ids.
    # Rank is 1-based, so the first relevant item at position 1 gives MRR=1.0.
    mrr = 0.0
    for rank, wid in enumerate(retrieved_ids, start=1):
        if wid in relevant:
            mrr = 1.0 / rank
            break

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mrr": mrr,
        "n_retrieved": n_retrieved,
        "n_relevant": n_relevant,
        "n_hits": hits,
    }


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def recalculate_run(run: dict) -> dict:
    """Recalculate metrics for all questions in one run."""
    question_results = run.get("question_results", [])

    metric_results = []

    for result in question_results:
        relevant_ids = result.get("relevant_ids", [])
        retrieved_ids = result.get("retrieved_ids", [])

        if not result.get("has_ground_truth", bool(relevant_ids)):
            # Keep questions without ground truth unscored.
            result["metrics"] = {}
            continue

        metrics = calculate_metrics(retrieved_ids, relevant_ids)
        result["metrics"] = metrics
        metric_results.append(metrics)

    all_results = question_results
    scored_human = [
        r for r in question_results
        if r.get("human_scores") and "thumbs_up" in r["human_scores"]
    ]

    # Keep the existing summary fields that are unrelated to retrieval,
    # but replace the retrieval metrics with the newly calculated values.
    old_summary = run.get("summary", {})
    run["summary"] = {
        **old_summary,
        "mean_precision": mean([m["precision"] for m in metric_results]),
        "mean_recall": mean([m["recall"] for m in metric_results]),
        "mean_f1": mean([m["f1"] for m in metric_results]),
        "mean_mrr": mean([m["mrr"] for m in metric_results]),
        "n_questions": len(all_results),
        "n_with_ground_truth": len(metric_results),
    }

    # Remove the old @K summary fields because they are no longer the
    # metrics represented by this recalculated JSON.
    for key in (
        "mean_precision@5",
        "mean_recall@5",
        "mean_f1@5",
    ):
        run["summary"].pop(key, None)

    return run


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recalculate retrieval metrics from an existing eval JSON."
    )
    parser.add_argument("input", help="Previously saved evaluation JSON")
    parser.add_argument(
        "-o",
        "--output",
        help="Output JSON path (default: <input>_corrected.json)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_name(
            f"{input_path.stem}_recalc{input_path.suffix}"
        )

    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # Your saved format is a list of runs.
    if isinstance(data, list):
        runs = data
    elif isinstance(data, dict):
        # Also accept a single run JSON for convenience.
        runs = [data]
    else:
        raise ValueError("Expected a JSON list of runs or a single run object.")

    for run in runs:
        recalculate_run(run)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2, ensure_ascii=False)

    print(f"Corrected JSON written to: {output_path}")

    # Print a compact sanity check.
    for run in runs:
        label = run.get("config", {}).get("label", run.get("run_id", "unknown"))
        summary = run.get("summary", {})
        print(
            f"{label}: "
            f"precision={summary.get('mean_precision', 0):.4f}, "
            f"recall={summary.get('mean_recall', 0):.4f}, "
            f"f1={summary.get('mean_f1', 0):.4f}, "
            f"mrr={summary.get('mean_mrr', 0):.4f}"
        )


if __name__ == "__main__":
    main()
