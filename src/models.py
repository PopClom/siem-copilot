"""
src/models.py
-------------
Shared dataclasses that flow through the entire pipeline.
Keeping them in one place avoids circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class RawEvent:
    """A single log line as read from the source, before any processing."""
    raw_line: str
    source_name: str          # which source config produced this
    semantic_type: str        # e.g. "windows-sysmon", "windows-security"
    file_path: Optional[str] = None


@dataclass
class NormalizedEvent:
    """A cleaned, structured event ready for grouping and embedding."""
    timestamp: datetime
    host: str
    user: Optional[str]
    event_type: str           # e.g. "process_creation", "logon"
    description: str          # human-readable summary (used for embedding)
    fields: dict[str, Any] = field(default_factory=dict)  # extra structured fields
    source_name: str = ""
    semantic_type: str = ""
    raw_line: str = ""


@dataclass
class EventWindow:
    """
    A temporal (and optionally host/user) group of NormalizedEvents.
    The aggregated_text field is what gets embedded.
    """
    window_start: datetime
    window_end: datetime
    host: Optional[str]
    user: Optional[str]
    events: list[NormalizedEvent] = field(default_factory=list)
    aggregated_text: str = ""
    source_name: str = ""

    @property
    def id(self) -> str:
        """Deterministic ID for deduplication in the vector store."""
        parts = [
            self.source_name,
            self.host or "unknown_host",
            self.user or "any_user",
            self.window_start.isoformat(),
        ]
        return "|".join(parts)
