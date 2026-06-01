"""
normalization/normalizer.py
---------------------------
Post-parse normalization pass.

After the parser has extracted structured fields (timestamp, host, etc.),
the normalizer:
  1. Strips residual noise from free-text fields (long hex strings, GUIDs, …)
  2. Builds the human-readable `description` that will be used for embedding
     (if the parser did not already set a good one)
  3. Optionally redacts PII (future)

The normalizer is intentionally thin — heavy field extraction belongs in
the per-format parsers.  This layer handles *cross-format* cleanup.
"""

from __future__ import annotations

import re
import logging
from typing import Optional

from src.models import NormalizedEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Noise patterns — applied to free-text fields
# ---------------------------------------------------------------------------

_NOISE_PATTERNS: list[re.Pattern[str]] = [
    # GUIDs:  {xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}
    re.compile(r"\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?"),
    # Long hex blobs (≥16 hex chars that aren't part of a word)
    re.compile(r"\b[0-9a-fA-F]{16,}\b"),
    # Windows SIDs:  S-1-5-21-...
    re.compile(r"S-\d-\d+-(\d+-){1,14}\d+"),
    # Repeated whitespace
    re.compile(r"\s{2,}"),
]


def _strip_noise(text: str) -> str:
    for pattern in _NOISE_PATTERNS:
        text = pattern.sub(" ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Semantic-type description builders
# ---------------------------------------------------------------------------
# When the parser sets event_type but leaves description empty, these
# builders construct a concise natural-language summary.

_SYSMON_EVENT_NAMES: dict[str, str] = {
    "1":  "Process creation",
    "2":  "File creation time changed",
    "3":  "Network connection",
    "5":  "Process terminated",
    "7":  "Image loaded",
    "8":  "CreateRemoteThread",
    "10": "Process access",
    "11": "File created",
    "12": "Registry object added/deleted",
    "13": "Registry value set",
    "15": "File stream created",
    "17": "Pipe created",
    "18": "Pipe connected",
    "22": "DNS query",
    "23": "File deleted",
    "25": "Process tampering",
}


def _build_sysmon_description(event: NormalizedEvent) -> str:
    event_id = event.fields.get("EventID", event.event_type)
    event_name = _SYSMON_EVENT_NAMES.get(str(event_id), f"Event {event_id}")
    parts = [event_name]

    if image := event.fields.get("Image"):
        parts.append(f"image={_basename(image)}")
    if target := event.fields.get("TargetImage") or event.fields.get("TargetFilename"):
        parts.append(f"target={_basename(target)}")
    if cmdline := event.fields.get("CommandLine"):
        parts.append(f"cmd={cmdline[:120]}")
    if dst_ip := event.fields.get("DestinationIp"):
        port = event.fields.get("DestinationPort", "")
        parts.append(f"dst={dst_ip}:{port}")

    return " | ".join(parts)


def _build_windows_security_description(event: NormalizedEvent) -> str:
    event_id = event.fields.get("EventID", event.event_type)
    parts = [f"Security Event {event_id}"]

    if logon_type := event.fields.get("LogonType"):
        parts.append(f"logon_type={logon_type}")
    if subj := event.fields.get("SubjectUserName"):
        parts.append(f"subject={subj}")
    if target := event.fields.get("TargetUserName"):
        parts.append(f"target={target}")
    if proc := event.fields.get("ProcessName"):
        parts.append(f"process={_basename(proc)}")

    return " | ".join(parts)


_DESCRIPTION_BUILDERS = {
    "windows-sysmon":    _build_sysmon_description,
    "windows-security":  _build_windows_security_description,
}


def _basename(path: str) -> str:
    """Return just the filename from a Windows or Unix path."""
    return re.split(r"[/\\]", path)[-1]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(event: NormalizedEvent) -> Optional[NormalizedEvent]:
    """
    Apply noise stripping and description building to a NormalizedEvent.
    Returns None if the event should be dropped (e.g. pure noise line).
    Mutates and returns the same object for efficiency.
    """
    # 1. Clean description
    if event.description:
        event.description = _strip_noise(event.description)

    # 2. Build a meaningful description if the parser didn't provide one
    if not event.description or event.description == event.raw_line[:512]:
        builder = _DESCRIPTION_BUILDERS.get(event.semantic_type)
        if builder:
            try:
                event.description = builder(event)
            except Exception as exc:  # pragma: no cover
                logger.debug("Description builder failed for %s: %s", event.semantic_type, exc)

    # 3. Final fallback: truncated raw line
    if not event.description:
        event.description = event.raw_line[:256]

    # 4. Drop empty events
    if not event.description.strip():
        return None

    return event
