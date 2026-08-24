"""
eval/report.py
--------------
Renders evaluation results as:
  1. A pretty console table
  2. Three PNG plots saved to eval/results/plots_<run>/
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Console table
# ---------------------------------------------------------------------------

def print_console_report(run: dict) -> None:
    """Print a human-readable summary of the evaluation run to stdout."""
    config = run["config"]
    results = run["question_results"]
    summary = run["summary"]

    print("\n" + "=" * 72)
    print(f"  SIEM Copilot Evaluation — {run['run_id']}")
    print("=" * 72)
    print(f"  Config: hyde={config['use_hyde']}  expand={config['expand_context']}  "
          f"time_window={config['time_window']}  top_k={config['top_k']}")
    print(f"  Questions evaluated: {len(results)}")
    print("-" * 72)

    # Per-question table
    header = f"{'ID':<4} {'Tool called':<20} {'Routing':^8} {'P@5':^6} {'R@5':^6} {'F1@5':^6} {'MRR':^6}"
    print(header)
    print("-" * 72)

    for r in results:
        m = r.get("metrics", {})
        routing = "✓" if r["tool_routing_correct"] else "✗"
        # Skip metric columns for anomaly-detection questions (no ground truth)
        if r["has_ground_truth"]:
            row = (
                f"{r['question_id']:<4} "
                f"{(r['tool_called'] or 'none'):<20} "
                f"{routing:^8} "
                f"{m.get('precision@5', 0):.3f}  "
                f"{m.get('recall@5', 0):.3f}  "
                f"{m.get('f1@5', 0):.3f}  "
                f"{m.get('mrr', 0):.3f}"
            )
        else:
            row = (
                f"{r['question_id']:<4} "
                f"{(r['tool_called'] or 'none'):<20} "
                f"{routing:^8} "
                f"{'—':^6}  {'—':^6}  {'—':^6}  {'—':^6}"
            )
        print(row)

    print("-" * 72)
    print(f"  Tool routing accuracy : {summary['tool_routing_accuracy']:.1%}")
    print(f"  Mean Precision@5      : {summary['mean_precision@5']:.3f}")
    print(f"  Mean Recall@5         : {summary['mean_recall@5']:.3f}")
    print(f"  Mean F1@5             : {summary['mean_f1@5']:.3f}")
    print(f"  Mean MRR              : {summary['mean_mrr']:.3f}")
    print(f"  Mean latency          : {summary['mean_latency_ms']:.0f} ms")
    print(f"  Human thumbs-up rate  : {summary.get('human_thumbs_up_rate', 0):.1%}")
    print("=" * 72 + "\n")


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def save_plots(runs: list[dict], output_dir: Path) -> None:
    """
    Generate comparison plots across multiple evaluation runs.

    Parameters
    ----------
    runs:        list of run dicts (one per config combination evaluated)
    output_dir:  directory where PNG files are saved
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib
        matplotlib.use("Agg")   # non-interactive backend
    except ImportError:
        print("matplotlib not installed — skipping plots. Run: pip install matplotlib")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    run_labels = [_run_label(r) for r in runs]

    # ── Plot 1: Precision / Recall / F1 @ 5 comparison ──────────────────
    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    fig.suptitle("Retrieval Metrics @ K=5 across Configurations", fontsize=13)

    for ax, metric, color in zip(
        axes,
        ["mean_precision@5", "mean_recall@5", "mean_f1@5"],
        ["steelblue", "darkorange", "seagreen"],
    ):
        values = [r["summary"].get(metric, 0) for r in runs]
        bars = ax.bar(run_labels, values, color=color, alpha=0.85, edgecolor="white")
        ax.set_ylim(0, 1.05)
        ax.set_title(metric.replace("mean_", "").replace("@", "@").upper())
        ax.set_ylabel("Score")
        ax.tick_params(axis="x", rotation=30)
        for bar, val in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                    f"{val:.3f}", ha="center", va="bottom", fontsize=9)

    plt.tight_layout()
    path1 = output_dir / "precision_recall_f1.png"
    plt.savefig(path1, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {path1}")

    # ── Plot 2: MRR comparison ───────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8, 4))
    values = [r["summary"].get("mean_mrr", 0) for r in runs]
    bars = ax.bar(run_labels, values, color="mediumpurple", alpha=0.85, edgecolor="white")
    ax.set_ylim(0, 1.05)
    ax.set_title("Mean Reciprocal Rank (MRR) across Configurations")
    ax.set_ylabel("MRR")
    ax.tick_params(axis="x", rotation=30)
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                f"{val:.3f}", ha="center", va="bottom", fontsize=10)
    plt.tight_layout()
    path2 = output_dir / "mrr_comparison.png"
    plt.savefig(path2, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {path2}")

    # ── Plot 3: Latency + tool routing accuracy ──────────────────────────
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    fig.suptitle("Operational Metrics across Configurations", fontsize=13)

    latencies = [r["summary"].get("mean_latency_ms", 0) for r in runs]
    ax1.bar(run_labels, latencies, color="salmon", alpha=0.85, edgecolor="white")
    ax1.set_title("Mean Latency (ms)")
    ax1.set_ylabel("Milliseconds")
    ax1.tick_params(axis="x", rotation=30)

    routing_acc = [r["summary"].get("tool_routing_accuracy", 0) for r in runs]
    bars2 = ax2.bar(run_labels, routing_acc, color="teal", alpha=0.85, edgecolor="white")
    ax2.set_ylim(0, 1.05)
    ax2.set_title("Tool Routing Accuracy")
    ax2.set_ylabel("Fraction correct")
    ax2.tick_params(axis="x", rotation=30)
    for bar, val in zip(bars2, routing_acc):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                 f"{val:.0%}", ha="center", va="bottom", fontsize=10)

    plt.tight_layout()
    path3 = output_dir / "latency_routing.png"
    plt.savefig(path3, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {path3}")

    # ── Plot 4: Human evaluation — thumbs-up rate ────────────────────────
    fig, ax = plt.subplots(figsize=(10, 4))

    values = [
        r["summary"].get("human_thumbs_up_rate", 0) * 100
        for r in runs
    ]

    bars = ax.bar(
        run_labels,
        values,
        color="seagreen",
        alpha=0.85,
        edgecolor="white",
    )

    ax.set_ylim(0, 105)
    ax.set_title("Human Evaluation — Thumbs-Up Rate")
    ax.set_ylabel("Positive responses (%)")
    ax.tick_params(axis="x", rotation=30)

    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 2,
            f"{val:.0f}%",
            ha="center",
            va="bottom",
            fontsize=10,
        )

    plt.tight_layout()
    path4 = output_dir / "human_eval.png"
    plt.savefig(path4, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {path4}")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_label(run: dict) -> str:
    c = run["config"]
    parts = []
    if c.get("use_hyde"):
        parts.append("HyDE")
    if c.get("expand_context"):
        parts.append("Expand")
    parts.append(f"tw={c.get('time_window', '?')}")
    return "\n".join(parts) if parts else "baseline"
