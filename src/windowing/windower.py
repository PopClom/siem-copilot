"""
windowing/windower.py
---------------------
Groups NormalizedEvents into EventWindows.

Strategy
--------
Events are first sorted by timestamp, then grouped by the configured
dimensions (host, user).  Within each group they are placed into
fixed-size temporal windows.  Optionally, windows overlap by 50 %
(i.e. stride = window_size // 2) to avoid splitting related activity.

Window key:  (host?, user?, window_index)
  where window_index = floor(event_ts_epoch / stride)

This means every event participates in at most two consecutive windows
when overlap is enabled, which is sufficient for most SIEM use cases.

Aggregated text format (used downstream for embedding):
  "Host: <host> | Window: <start>–<end>\n<desc1>\n<desc2>\n…"
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from itertools import groupby
from typing import Iterator

from src.config.settings import GroupingConfig
from src.models import EventWindow, NormalizedEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_windows(
    events: list[NormalizedEvent],
    grouping: GroupingConfig,
) -> list[EventWindow]:
    """
    Consume a list of NormalizedEvents and return a list of EventWindows.

    Parameters
    ----------
    events:   flat list of events (any order; will be sorted internally)
    grouping: config block with host/user grouping flags and time_window
    overlap_ratio:  how much overlapping between two consecutive windows
    """
    if not events:
        return []

    window_secs = grouping.to_seconds()
    stride_secs = max(1, int(window_secs * (1 - grouping.overlap_ratio)))

    # Sort by timestamp
    events = sorted(events, key=lambda e: e.timestamp)

    # Group by the configured dimensions
    groups = _group_events(events, grouping)

    windows: list[EventWindow] = []
    for group_key, group_events in groups.items():
        windows.extend(
            _events_to_windows(
                group_events,
                group_key,
                window_secs,
                stride_secs,
                grouping.max_events_per_chunk,
                grouping.overlap_ratio
            )
        )

    logger.info("Built %d windows from %d events", len(windows), len(events))
    return windows


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

GroupKey = tuple[str | None, str | None]   # (host, user)


def _group_key(event: NormalizedEvent, grouping: GroupingConfig) -> GroupKey:
    host = event.host if grouping.host else None
    user = event.user if grouping.user else None
    return (host, user)


def _group_events(
    events: list[NormalizedEvent],
    grouping: GroupingConfig,
) -> dict[GroupKey, list[NormalizedEvent]]:
    groups: dict[GroupKey, list[NormalizedEvent]] = defaultdict(list)
    for event in events:
        groups[_group_key(event, grouping)].append(event)
    return groups


def _events_to_windows(
    events: list[NormalizedEvent],
    group_key: GroupKey,
    window_secs: int,
    stride_secs: int,
    max_events: int | None,
    overlap_ratio: float,
) -> Iterator[EventWindow]:
    """
    Slide a temporal window over `events` (already sorted by timestamp).

    Windows are generated every `stride_secs`. If a temporal window contains
    more than `max_events`, it is split into overlapping event chunks while
    preserving the original temporal metadata.
    """
    if not events:
        return

    host, user = group_key

    # Cache epoch timestamps once
    event_epochs = [e.timestamp.timestamp() for e in events]
    n_events     = len(events)

    first_bucket = math.floor(event_epochs[0]  / stride_secs)
    last_bucket  = math.floor(event_epochs[-1] / stride_secs)

    # Sliding pointers into the sorted event list
    start_idx = 0
    end_idx = 0

    for bucket in range(first_bucket, last_bucket + 1):
        win_start_epoch = bucket * stride_secs
        win_end_epoch   = win_start_epoch + window_secs

        win_start = datetime.fromtimestamp(win_start_epoch, tz=timezone.utc)
        win_end   = datetime.fromtimestamp(win_end_epoch,   tz=timezone.utc)

        # Advance start pointer until events are inside the window
        while (start_idx < n_events and event_epochs[start_idx] < win_start_epoch):
            start_idx += 1

        # Advance end pointer until events leave the window
        while (end_idx < n_events and event_epochs[end_idx] < win_end_epoch):
            end_idx += 1

        bucket_events = events[start_idx:end_idx]

        if not bucket_events:
            continue

        # ------------------------------------------------------------
        # Split oversized windows into overlapping event chunks
        # ------------------------------------------------------------
        if max_events is None or len(bucket_events) <= max_events:
            chunks = [bucket_events]
        else:
            event_stride = max(1, int(max_events * (1 - overlap_ratio)))
            chunks = []

            for i in range(0, len(bucket_events), event_stride):
                chunk = bucket_events[i : i + max_events]
                chunks.append(chunk)

                if i + max_events >= len(bucket_events):
                    break

        # ------------------------------------------------------------
        # Emit one EventWindow per chunk
        # ------------------------------------------------------------
        for chunk in chunks:
            window = EventWindow(
                window_start=win_start,
                window_end=win_end,
                host=host,
                user=user,
                events=chunk,
                source_name=chunk[0].source_name,
            )
            window.aggregated_text = _aggregate_text(window)
            yield window


def _aggregate_text(window: EventWindow) -> str:
    """
    Build a single string from the window's events.
    This text will be embedded, so it should be dense and human-readable.
    """
    lines: list[str] = []

    header_parts = []
    if window.host:
        header_parts.append(f"Host: {window.host}")
    if window.user:
        header_parts.append(f"User: {window.user}")
    header_parts.append(
        f"Window: {window.window_start.strftime('%H:%M:%S')}–{window.window_end.strftime('%H:%M:%S')} UTC"
    )
    lines.append(" | ".join(header_parts))

    for event in window.events:
        ts = event.timestamp.strftime("%H:%M:%S")
        lines.append(f"[{ts}] {event.description}")

    return "\n".join(lines)
