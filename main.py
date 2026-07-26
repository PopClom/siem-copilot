"""
main.py
-------
CLI entry point for SIEM Copilot.

Modes
-----
    # Ingest logs into Qdrant
    python main.py [--reingest] [--dry-run]

    # Detect anomalies over stored windows
    python main.py --detect-anomalies [--since 24h] [--no-llm-summary]
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.config.settings import load_settings
from src.pipeline import IngestionPipeline


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "sentence_transformers", "transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SIEM Copilot CLI")
    parser.add_argument("--config", default="config/config.yaml",
                        help="Path to the YAML configuration file (default: config/config.yaml)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    # Ingestion flags
    ingest = parser.add_argument_group("ingestion")
    ingest.add_argument("--dry-run", action="store_true",
                        help="Parse and window events without embedding or upserting")
    ingest.add_argument("--reingest", action="store_true",
                        help="Drop collection and re-ingest from scratch")

    # Anomaly detection flags
    anomaly = parser.add_argument_group("anomaly detection")
    anomaly.add_argument("--detect-anomalies", action="store_true",
                         help="Run anomaly detection over stored windows")
    anomaly.add_argument("--since", default=None, metavar="DURATION",
                         help="Only analyse windows newer than this (e.g. 24h, 7d, 30m)")
    anomaly.add_argument("--no-llm-summary", action="store_true",
                         help="Skip LLM summary; print raw detection stats only")

    return parser.parse_args()


def _run_ingestion(args, settings, logger) -> None:
    pipeline = IngestionPipeline(
        settings=settings,
        dry_run=args.dry_run,
        reingest=args.reingest,
    )
    summary = pipeline.run()
    logger.info("Ingestion summary: %s", summary)


def _run_anomaly_detection(args, settings, logger) -> None:
    from src.anomaly.chain import AnomalyChain, parse_since

    since = parse_since(args.since)
    if since:
        logger.info("Analysing windows from the last %s …", args.since)
    else:
        logger.info("Analysing all windows in the collection …")

    chain = AnomalyChain(
        settings=settings,
        since=since,
        with_summary=not args.no_llm_summary,
    )
    response = chain.run()
    result = response.result

    # Print stats
    print("\n" + "=" * 60)
    print(f"  Anomaly Detection Results")
    print("=" * 60)
    print(f"  Windows analysed : {result.total_windows}")
    print(f"  Anomalies found  : {result.n_anomalies} ({result.anomaly_ratio * 100:.1f}%)")
    print(f"  Behaviour clusters: {result.n_clusters}")
    print(f"  HDBSCAN noise    : {result.noise_ratio * 100:.1f}%")

    if result.anomalous_windows:
        print("\n  Top anomalous windows:")
        for w in result.anomalous_windows[:5]:
            print(
                f"    [{w.anomaly_label.upper()}] "
                f"host={w.host or 'unknown'} "
                f"{w.window_start[11:19]}–{w.window_end[11:19]} UTC "
                f"if_score={w.isolation_score:.3f} "
                f"cluster={w.cluster_id}"
            )

    if response.summary:
        print("\n" + "=" * 60)
        print("  LLM Summary")
        print("=" * 60)
        print(response.summary)

    print("=" * 60 + "\n")


def main() -> None:
    args = _parse_args()
    _setup_logging(args.log_level)

    logger = logging.getLogger(__name__)
    logger.info("SIEM Copilot — ingestion pipeline starting")

    # Load and validate configuration
    try:
        settings = load_settings(args.config)
    except FileNotFoundError as exc:
        logger.error("Config file not found: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)

    if args.detect_anomalies:
        _run_anomaly_detection(args, settings, logger)
    else:
        logger.info("Enabled sources: %s", [s.name for s in settings.enabled_sources])
        _run_ingestion(args, settings, logger)


if __name__ == "__main__":
    main()
