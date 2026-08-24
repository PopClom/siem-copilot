"""
eval/run_eval.py
----------------
Runs the SIEM Copilot evaluation suite.

Usage
-----
    # Single run with current config
    python eval/run_eval.py

    # Compare multiple configurations
    python eval/run_eval.py --compare

    # Specify questions file and API URL
    python eval/run_eval.py --questions eval/questions.yaml --api http://localhost:8000

    # Skip interactive thumbs-up/down scoring
    python eval/run_eval.py --no-human-eval

The script:
  1. Loads questions.yaml (ground truth)
  2. For each configuration to test, calls the /query API endpoint
  3. Extracts retrieved window_ids from response.sources
  4. Computes Precision@K, Recall@K, F1@K, MRR per question
  5. Prints a console table and saves PNG plots to eval/results/

Prerequisites
-------------
    pip install matplotlib requests pyyaml
    uvicorn api.main:app --port 8000   (in a separate terminal)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml

# Add project root to path so we can import eval modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.metrics import compute_all
from eval.report import print_console_report, save_plots

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"


# ---------------------------------------------------------------------------
# Configuration grid
# ---------------------------------------------------------------------------

# Each entry is a set of config.yaml overrides to test.
# The eval script patches the API's config via a dedicated endpoint,
# OR (simpler) you manually change config.yaml and re-run per config.
# We use the simpler approach: each config is described here for labelling
# purposes, and the script calls the API as-is for each.
#
# To properly compare configs, restart uvicorn with a different config.yaml
# between runs, or use --compare which runs all configs sequentially
# (requires manual restarts and prompts you between each).

CONFIGS_TO_COMPARE = [
    {
        "label":          "Baseline",
        "use_hyde":       False,
        "expand_context": False,
        "time_window":    "5s",
        "top_k":          20,
    },
    {
        "label":          "HyDE",
        "use_hyde":       True,
        "expand_context": False,
        "time_window":    "5s",
        "top_k":          20,
    },
    {
        "label":          "Expand",
        "use_hyde":       False,
        "expand_context": True,
        "time_window":    "5s",
        "top_k":          20,
    },
    {
        "label":          "HyDE+Expand",
        "use_hyde":       True,
        "expand_context": True,
        "time_window":    "5s",
        "top_k":          20,
    },
]


# ---------------------------------------------------------------------------
# API interaction
# ---------------------------------------------------------------------------

def query_api(question: str, api_url: str, timeout: int = 120) -> dict:
    """Call POST /query and return the parsed JSON response."""
    url = f"{api_url}/query"
    payload = {"question": question}
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to API at %s. Is uvicorn running?", url)
        sys.exit(1)
    except requests.exceptions.HTTPError as exc:
        logger.error("API error: %s — %s", exc, resp.text[:200])
        raise


def extract_retrieved_ids(api_response: dict) -> list[str]:
    """
    Extract ordered list of window_ids from the API response.
    Non-neighbour sources come first (they have score > 0),
    then neighbours — matching the order the retriever built them.
    """
    sources = api_response.get("sources", [])
    # Sort: non-neighbours first (by score desc), neighbours after
    non_neighbours = [s for s in sources if not s.get("is_neighbour", False)]
    neighbours     = [s for s in sources if s.get("is_neighbour", False)]
    non_neighbours.sort(key=lambda s: s.get("score", 0), reverse=True)
    return [s["window_id"] for s in non_neighbours + neighbours]


# ---------------------------------------------------------------------------
# Single run
# ---------------------------------------------------------------------------

def run_single(
    questions: list[dict],
    config_meta: dict,
    api_url: str,
    k_values: list[int],
    args: argparse.Namespace,
) -> dict:
    """
    Evaluate all questions against the currently running API config.
    Returns a run dict suitable for reporting.
    """
    run_id = f"{config_meta['label']}_{datetime.now().strftime('%H%M%S')}"
    logger.info("Starting run: %s", run_id)

    question_results = []

    for q in questions:
        qid      = q["id"]
        question = q["question"]
        expected_tool    = q.get("expected_tool")
        relevant_ids     = set(q.get("relevant_window_ids", []))
        has_ground_truth = bool(relevant_ids)

        logger.info("  [%s] %s", qid, question[:70])

        t0 = time.monotonic()
        response = query_api(question, api_url)
        wall_ms  = int((time.monotonic() - t0) * 1000)

        tool_called       = response.get("tool_used")
        routing_correct   = (tool_called == expected_tool)
        retrieved_ids     = extract_retrieved_ids(response)

        metrics = {}
        if has_ground_truth:
            metrics = compute_all(retrieved_ids, relevant_ids, k_values=k_values)

        # Optional simple human evaluation.
        if not args.no_human_eval:
            print(f"\n{'─'*60}")
            print(f"Q: {question}")
            print(f"Tool called: {tool_called}")
            print(f"\nAnswer:\n{response.get('answer', '')}")
            print(f"{'─'*60}")

            while True:
                raw = input("Your judgment — 👍 good / 👎 bad [y/n]: ").strip().lower()
                if raw in {"y", "yes", "👍", "up"}:
                    human_score = 1
                    break
                if raw in {"n", "no", "👎", "down"}:
                    human_score = 0
                    break
                print("Enter y/yes/👍 for good or n/no/👎 for bad")

            human_scores = {
                "thumbs_up": human_score,
            }
        else:
            human_scores = {}

        result = {
            "question_id":          qid,
            "question":             question,
            "tool_called":          tool_called,
            "expected_tool":        expected_tool,
            "tool_routing_correct": routing_correct,
            "has_ground_truth":     has_ground_truth,
            "retrieved_ids":        retrieved_ids,
            "relevant_ids":         list(relevant_ids),
            "metrics":              metrics,
            "human_scores":          human_scores,
            "latency_ms":           response.get("latency_ms", wall_ms),
            "chunks_retrieved":     response.get("chunks_retrieved", 0),
            "chunks_used":          response.get("chunks_used", 0),
            "neighbours_added":     response.get("neighbours_added", 0),
        }

        question_results.append(result)

        # Brief log
        if has_ground_truth:
            logger.info(
                "    → tool=%s routing=%s P@5=%.3f R@5=%.3f MRR=%.3f latency=%dms",
                tool_called, "✓" if routing_correct else "✗",
                metrics.get("precision@5", 0),
                metrics.get("recall@5", 0),
                metrics.get("mrr", 0),
                result["latency_ms"],
            )
        else:
            logger.info(
                "    → tool=%s routing=%s latency=%dms",
                tool_called, "✓" if routing_correct else "✗",
                result["latency_ms"],
            )

    # Aggregate summary — only over questions with ground truth
    gt_results = [r for r in question_results if r["has_ground_truth"]]
    all_results = question_results
    scored = [r for r in question_results if r.get("human_scores")]

    def mean(vals): return sum(vals) / len(vals) if vals else 0.0

    summary = {
        "tool_routing_accuracy": mean([r["tool_routing_correct"] for r in all_results]),
        "mean_precision@5":  mean([r["metrics"].get("precision@5", 0) for r in gt_results]),
        "mean_recall@5":     mean([r["metrics"].get("recall@5", 0)    for r in gt_results]),
        "mean_f1@5":         mean([r["metrics"].get("f1@5", 0)        for r in gt_results]),
        "mean_mrr":          mean([r["metrics"].get("mrr", 0)         for r in gt_results]),
        "mean_latency_ms":   mean([r["latency_ms"]                    for r in all_results]),
        "n_questions":       len(all_results),
        "n_with_ground_truth": len(gt_results),
        "human_thumbs_up_rate": mean(
            [r["human_scores"]["thumbs_up"] for r in scored]
        ),
    }

    return {
        "run_id":           run_id,
        "config":           config_meta,
        "question_results": question_results,
        "summary":          summary,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SIEM Copilot evaluation suite")
    parser.add_argument(
        "--questions", default="eval/questions.yaml",
        help="Path to questions YAML file",
    )
    parser.add_argument(
        "--api", default="http://localhost:8000",
        help="Base URL of the running API",
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="Run all configurations in CONFIGS_TO_COMPARE sequentially",
    )
    parser.add_argument(
        "--label", default="manual",
        help="Label for this run (used in output filenames)",
    )
    parser.add_argument(
        "--k", nargs="+", type=int, default=[5, 10, 20],
        help="K values for Precision@K / Recall@K (default: 5 10 20)",
    )
    parser.add_argument(
        "--no-human-eval", action="store_true",
        help="Skip interactive thumbs-up/down scoring (retrieval metrics only)",
    )
    return parser.parse_args()


def load_questions(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    questions = data.get("questions", data) if isinstance(data, dict) else data

    # Warn about placeholder IDs
    placeholders = 0
    for q in questions:
        for wid in q.get("relevant_window_ids", []):
            if wid.startswith("REPLACE_WITH"):
                placeholders += 1
    if placeholders:
        logger.warning(
            "%d placeholder window_id(s) found in questions.yaml. "
            "Replace them with real IDs for meaningful metrics.",
            placeholders,
        )
    return questions


def save_results(runs: list[dict]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"eval_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2, default=str)
    logger.info("Results saved to %s", path)
    return path


def main() -> None:
    args = parse_args()
    questions = load_questions(args.questions)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.compare:
        # Sequential multi-config comparison
        runs: list[dict] = []
        for i, config_meta in enumerate(CONFIGS_TO_COMPARE):
            print(f"\n{'='*60}")
            print(f"  Config {i+1}/{len(CONFIGS_TO_COMPARE)}: {config_meta['label']}")
            print(f"  Hyde={config_meta['use_hyde']}  "
                  f"Expand={config_meta['expand_context']}  "
                  f"time_window={config_meta['time_window']}")
            print(f"{'='*60}")
            if i > 0:
                input(
                    "\n  ⚠  Update config.yaml and restart uvicorn, then press Enter to continue…"
                )
            run = run_single(questions, config_meta, args.api, args.k, args)
            runs.append(run)
            print_console_report(run)
    else:
        # Single run
        config_meta = {
            "label":          args.label,
            "use_hyde":       None,   # unknown — read from running instance
            "expand_context": None,
            "time_window":    None,
            "top_k":          None,
        }
        runs = [run_single(questions, config_meta, args.api, args.k, args)]
        print_console_report(runs[0])

    # Save JSON
    save_results(runs)

    # Save plots (only meaningful with 2+ runs for comparison)
    if len(runs) >= 1:
        plots_dir = RESULTS_DIR / f"plots_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"\nSaving plots to {plots_dir} …")
        save_plots(runs, plots_dir)

    print("\nEvaluation complete.")


if __name__ == "__main__":
    main()
