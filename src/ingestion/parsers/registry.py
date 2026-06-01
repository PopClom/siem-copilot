"""
ingestion/parsers/registry.py
--------------------
Parser registry: maps an input-format string → parse function.

Each parser receives a raw line (str) and returns a NormalizedEvent,
or None if the line should be skipped (header, blank, comment, etc.).

Adding a new format:
    1. Write a function  parse_<format>(line: str) -> NormalizedEvent | None
    2. Register it below in PARSER_REGISTRY
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from src.models import NormalizedEvent, RawEvent
from src.ingestion.parsers.sysmon import parse_raw_sysmon_log 

logger = logging.getLogger(__name__)

ParseFn = Callable[[RawEvent], Optional[NormalizedEvent]]


# ---------------------------------------------------------------------------
# Dummy / placeholder parsers
# ---------------------------------------------------------------------------

def _dummy_parser(event: RawEvent) -> Optional[NormalizedEvent]:
    """
    Placeholder parser used when no real parser is registered for a format.
    Produces a NormalizedEvent with the raw line as description so the rest
    of the pipeline can run end-to-end during development.
    """
    line = event.raw_line.strip()
    if not line:
        return None

    return NormalizedEvent(
        timestamp=datetime.now(tz=timezone.utc),
        host="unknown_host",
        user=None,
        event_type="unknown",
        description=line[:512],   # cap length
        fields={},
        source_name=event.source_name,
        semantic_type=event.semantic_type,
        raw_line=line,
    )


def _parse_raw_windows_security_log(event: RawEvent) -> Optional[NormalizedEvent]:
    """
    TODO: implement Windows Security Event Log parsing.
    Falls back to dummy for now.
    """
    logger.debug("raw-windows-security-log parser not yet implemented; using dummy")
    return _dummy_parser(event)


def _parse_wazuh_opensearch(event: RawEvent) -> Optional[NormalizedEvent]:
    """
    TODO: implement Wazuh/OpenSearch JSON alert parsing.
    Falls back to dummy for now.
    """
    logger.debug("wazuh-opensearch parser not yet implemented; using dummy")
    return _dummy_parser(event)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARSER_REGISTRY: dict[str, ParseFn] = {
    "raw-sysmon-log":           parse_raw_sysmon_log,
    "raw-windows-security-log": _parse_raw_windows_security_log,
    "wazuh-opensearch":         _parse_wazuh_opensearch,
    # add more formats here
}


def get_parser(input_format: str) -> ParseFn:
    """Return the registered parser for *input_format*, or the dummy fallback."""
    parser = PARSER_REGISTRY.get(input_format)
    if parser is None:
        logger.warning(
            "No parser registered for format '%s'. Using dummy parser.", input_format
        )
        return _dummy_parser
    return parser
