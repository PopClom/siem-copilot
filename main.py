"""
main.py
-------
CLI entry point for the SIEM Copilot ingestion pipeline.

Usage
-----
    python main.py                          # use default config
    python main.py --config path/to/cfg.yaml
    python main.py --dry-run                # parse + window only, skip embed/upsert
    python main.py --no-overlap             # non-overlapping windows
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
    parser = argparse.ArgumentParser(
        description="SIEM Copilot — semantic log ingestion pipeline"
    )
    parser.add_argument(
        "--config", default="config/config.yaml",
        help="Path to the YAML configuration file (default: config/config.yaml)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and window events without embedding or upserting"
    )
    parser.add_argument(
        "--no-overlap", action="store_true",
        help="Use non-overlapping (tumbling) windows instead of sliding windows"
    )
    parser.add_argument(
        "--reingest", action="store_true",
        help="Drop and recreate the Qdrant collection before ingesting (clean slate)"
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)"
    )
    return parser.parse_args()


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

    logger.info(
        "Config loaded. Enabled sources: %s",
        [s.name for s in settings.enabled_sources],
    )

    # Run pipeline
    pipeline = IngestionPipeline(
        settings=settings,
        overlap=not args.no_overlap,
        dry_run=args.dry_run,
        reingest=args.reingest,
    )

    summary = pipeline.run()

    logger.info("Summary: %s", summary)


if __name__ == "__main__":
    main()
