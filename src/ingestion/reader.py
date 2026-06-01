"""
ingestion/reader.py
-------------------
Reads raw log lines from configured sources and yields RawEvent objects.

Currently supports:
  - type: file   (local files with glob patterns)
  - type: opensearch  (stub — not yet implemented)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Generator, Iterator

from src.config.settings import SourceConfig
from src.ingestion.parsers.registry import get_parser
from src.models import NormalizedEvent, RawEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_source(source: SourceConfig) -> Iterator[NormalizedEvent]:
    """
    Yield NormalizedEvents from a single source config.
    Parsing is delegated to the registered parser for source.input_format.
    """
    parser = get_parser(source.input_format)

    if source.type == "file":
        for raw in _read_file_source(source):
            event = parser(raw)
            if event is not None:
                yield event

    elif source.type == "opensearch":
        logger.warning(
            "OpenSearch source '%s' is not yet implemented. Skipping.", source.name
        )

    else:
        logger.error("Unknown source type '%s' for source '%s'.", source.type, source.name)


# ---------------------------------------------------------------------------
# File source
# ---------------------------------------------------------------------------

def _read_file_source(source: SourceConfig) -> Generator[RawEvent, None, None]:
    """Yield RawEvents from all matching files in the configured path."""
    base_path = Path(source.path)  # type: ignore[arg-type]

    if not base_path.exists():
        logger.error("Source '%s': path does not exist: %s", source.name, base_path)
        return

    files = _collect_files(base_path, source.file_patterns, source.recursive)

    if not files:
        logger.warning(
            "Source '%s': no files matched patterns %s in %s",
            source.name, source.file_patterns, base_path,
        )
        return

    for file_path in sorted(files):
        logger.info("Reading file: %s", file_path)
        yield from _read_file(file_path, source)


def _collect_files(
    base: Path,
    patterns: list[str],
    recursive: bool,
) -> list[Path]:
    files: list[Path] = []
    glob_fn = base.rglob if recursive else base.glob
    for pattern in patterns:
        files.extend(glob_fn(pattern))
    # deduplicate while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for f in files:
        if f not in seen and f.is_file():
            seen.add(f)
            unique.append(f)
    return unique


def _read_file(
    file_path: Path,
    source: SourceConfig,
) -> Generator[RawEvent, None, None]:
    """Yield one RawEvent per non-empty line in the file."""
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\n\r")
                if line.strip():
                    yield RawEvent(
                        raw_line=line,
                        source_name=source.name,
                        semantic_type=source.semantic_type,
                        file_path=str(file_path),
                    )
    except OSError as exc:
        logger.error("Could not read file %s: %s", file_path, exc)
