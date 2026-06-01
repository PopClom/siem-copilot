"""
ingestion/parsers/sysmon.py
---------------------------

Sysmon parser: converts raw Sysmon XML events into NormalizedEvent objects.

Each parser:
    - Takes a RawEvent (single raw log line as str)
    - Returns a NormalizedEvent
    - Returns None for empty/invalid events

Design:
    - Minimal parsing only (extract fields, no sanitization or normalization)
    - Downstream pipeline handles cleaning (UUIDs, hashes, hex strings, etc.)
    - Preserve original structure in `fields`

Notes:
    - Sysmon logs are XML (Microsoft-Windows-Sysmon schema)
    - Timestamps may include 100ns precision (9 fractional digits)
    - Python datetime supports microseconds only, so values are truncated
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

from src.models import NormalizedEvent, RawEvent

logger = logging.getLogger(__name__)

SYSMON_NS = {"evt": "http://schemas.microsoft.com/win/2004/08/events/event"}

SYSMON_EVENT_TYPES = {
    "1": "process_create",
    "2": "file_creation_time_changed",
    "3": "network_connection",
    "4": "sysmon_service_state_changed",
    "5": "process_terminated",
    "6": "driver_loaded",
    "7": "image_loaded",
    "8": "create_remote_thread",
    "10": "process_access",
    "11": "file_create",
    "12": "registry_object_create_delete",
    "13": "registry_value_set",
    "14": "registry_key_rename",
    "15": "file_create_stream_hash",
    "17": "pipe_created",
    "18": "pipe_connected",
    "22": "dns_query",
    "23": "file_delete",
    "24": "clipboard_change",
    "25": "process_tampering",
}

def _parse_sysmon_timestamp(ts: str) -> datetime:
    """
    Sysmon typically emits timestamps in the following format:
        2020-10-08T12:52:11.722592200Z

    Python does not directly support 9-digit fractional second precision,
    so we truncate the value to microseconds.
    """
    if ts.endswith("Z"):
        ts = ts[:-1]

    if "." in ts:
        base, frac = ts.split(".", 1)
        frac = frac[:6].ljust(6, "0")
        ts = f"{base}.{frac}"

    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)

def _parse_sysmon_utctime(ts: str) -> datetime:
    """
    Parse Sysmon UtcTime values.

    Examples:
        2020-10-08 12:52:48.765
        2020-10-08 12:52:48
    """
    formats = (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    )

    for fmt in formats:
        try:
            return datetime.strptime(ts, fmt).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass

    raise ValueError(f"Invalid Sysmon UtcTime: {ts}")

def parse_raw_sysmon_log(event: RawEvent) -> Optional[NormalizedEvent]:
    line = event.raw_line.strip()

    if not line:
        return None

    try:
        root = ET.fromstring(line)

        # ------------------------------------------------------------------
        # System section
        # ------------------------------------------------------------------
        event_id = root.findtext("./evt:System/evt:EventID", namespaces=SYSMON_NS)

        computer = root.findtext(
            "./evt:System/evt:Computer",
            namespaces=SYSMON_NS,
        )

        # ------------------------------------------------------------------
        # Timestamp
        # Prefer Sysmon's UtcTime (event occurrence time).
        # Fall back to Windows Event Log SystemTime if unavailable.
        # ------------------------------------------------------------------
        utc_time = root.findtext(
            "./evt:EventData/evt:Data[@Name='UtcTime']",
            namespaces=SYSMON_NS,
        )

        timestamp: datetime | None = None

        if utc_time:
            try:
                timestamp = _parse_sysmon_utctime(utc_time)
            except ValueError:
                logger.debug("Failed to parse Sysmon UtcTime: %s", utc_time)

        if timestamp is None:
            time_created = root.find(
                "./evt:System/evt:TimeCreated",
                namespaces=SYSMON_NS,
            )

            if time_created is not None:
                system_time = time_created.attrib.get("SystemTime")
                if system_time:
                    timestamp = _parse_sysmon_timestamp(system_time)

        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        # ------------------------------------------------------------------
        # EventData section
        # ------------------------------------------------------------------
        fields: dict[str, str] = {}

        for data in root.findall(
            "./evt:EventData/evt:Data",
            namespaces=SYSMON_NS,
        ):
            name = data.attrib.get("Name")
            if not name:
                continue

            fields[name] = data.text or ""

        # ------------------------------------------------------------------
        # User extraction
        # ------------------------------------------------------------------
        user = fields.get("User")

        # ------------------------------------------------------------------
        # Description
        # ------------------------------------------------------------------
        image = (
            fields.get("Image")
            or fields.get("SourceImage")
            or fields.get("TargetImage")
        )

        command_line = fields.get("CommandLine")

        # ------------------------------------------------------------------
        # Event type
        # ------------------------------------------------------------------
        event_type = SYSMON_EVENT_TYPES.get(event_id, "unknown")

        description_parts = [
            f"Sysmon {event_type} (EventID={event_id})"
        ]

        if image:
            description_parts.append(image)

        if command_line:
            description_parts.append(command_line)

        description = " | ".join(description_parts)

        return NormalizedEvent(
            timestamp=timestamp,
            host=computer or "unknown_host",
            user=user,
            event_type=event_type,
            description=description[:512],
            fields=fields,
            source_name=event.source_name,
            semantic_type=event.semantic_type,
            raw_line=line,
        )

    except Exception:
        logger.exception("Failed to parse Sysmon event")
        return None