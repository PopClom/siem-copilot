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
    overlap: bool = True,
) -> list[EventWindow]:
    """
    Consume a list of NormalizedEvents and return a list of EventWindows.

    Parameters
    ----------
    events:   flat list of events (any order; will be sorted internally)
    grouping: config block with host/user grouping flags and time_window
    overlap:  if True, use 50 % overlapping windows (stride = window // 2)
    """
    if not events:
        return []

    window_secs = grouping.to_seconds()
    stride_secs = window_secs // 2 if overlap else window_secs

    # Sort by timestamp
    events = sorted(events, key=lambda e: e.timestamp)

    # Group by the configured dimensions
    groups = _group_events(events, grouping)

    windows: list[EventWindow] = []
    for group_key, group_events in groups.items():
        windows.extend(
            _events_to_windows(group_events, group_key, window_secs, stride_secs)
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
) -> Iterator[EventWindow]:
    """
    Slide a window of `window_secs` over `events` (already sorted by ts)
    with a stride of `stride_secs`.
    """
    if not events:
        return

    host, user = group_key
    first_ts = events[0].timestamp.timestamp()
    last_ts  = events[-1].timestamp.timestamp()

    # Align first window to the nearest stride boundary
    first_bucket = math.floor(first_ts / stride_secs)
    last_bucket  = math.floor(last_ts  / stride_secs)

    event_idx = 0  # pointer for sliding scan

    for bucket in range(first_bucket, last_bucket + 1):
        win_start_epoch = bucket * stride_secs
        win_end_epoch   = win_start_epoch + window_secs

        win_start = datetime.fromtimestamp(win_start_epoch, tz=timezone.utc)
        win_end   = datetime.fromtimestamp(win_end_epoch,   tz=timezone.utc)

        # Collect events within [win_start, win_end)
        bucket_events = [
            e for e in events
            if win_start_epoch <= e.timestamp.timestamp() < win_end_epoch
        ]

        if not bucket_events:
            continue

        source_name = bucket_events[0].source_name

        window = EventWindow(
            window_start=win_start,
            window_end=win_end,
            host=host,
            user=user,
            events=bucket_events,
            source_name=source_name,
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
