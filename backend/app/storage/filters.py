"""
Search filters and paging cursors for the central event store. Pure Python
(no database imports) so the rules are easy to test on their own.

Time rules, same as the rest of the backend: events are stored as naive UTC.
A naive datetime sent by a caller is taken as UTC; an aware one is converted.
`since` is inclusive and `until` is exclusive, so adjacent windows never
share or skip an event.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.ingestion.schema import SEVERITIES

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
MAX_TEXT_LENGTH = 200
# job_id value meaning "events that belong to no collection" (ad-hoc ingests).
UNASSIGNED = "unassigned"

_RANK = {name: i for i, name in enumerate(SEVERITIES)}


class FilterError(ValueError):
    """A search parameter is invalid. The message is safe to show the user."""


def to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _clean(values: Optional[Iterable[str]]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for v in values or ():
        v = (v or "").strip()
        if v:
            seen[v] = None
    return tuple(seen)


@dataclass(frozen=True)
class EventFilter:
    since: Optional[datetime] = None          # naive UTC, inclusive
    until: Optional[datetime] = None          # naive UTC, exclusive
    job_id: Optional[str] = None              # a collection, or UNASSIGNED
    devices: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    severities: tuple[str, ...] = ()          # exact levels
    min_severity: Optional[str] = None        # this level and above
    process: Optional[str] = None
    source_file: Optional[str] = None
    text: Optional[str] = None                # case-insensitive substring of the message

    @property
    def effective_severities(self) -> Optional[tuple[str, ...]]:
        """The levels that match, or None for "no restriction". An empty
        tuple means the filters contradict each other and nothing matches."""
        if not self.severities and self.min_severity is None:
            return None
        allowed = set(self.severities) if self.severities else set(SEVERITIES)
        if self.min_severity is not None:
            allowed = {s for s in allowed if _RANK[s] >= _RANK[self.min_severity]}
        return tuple(s for s in SEVERITIES if s in allowed)

    def used_fields(self) -> list[str]:
        """Names of the filters in use, for the audit log. Never the values:
        the text search may be a patient identifier."""
        names = {
            "since": self.since, "until": self.until, "job_id": self.job_id,
            "device": self.devices, "source": self.sources, "category": self.categories,
            "severity": self.severities, "min_severity": self.min_severity,
            "process": self.process, "source_file": self.source_file, "text": self.text,
        }
        return [name for name, value in names.items() if value]


def build_filter(
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    job_id: Optional[str] = None,
    devices: Optional[Iterable[str]] = None,
    sources: Optional[Iterable[str]] = None,
    categories: Optional[Iterable[str]] = None,
    severities: Optional[Iterable[str]] = None,
    min_severity: Optional[str] = None,
    process: Optional[str] = None,
    source_file: Optional[str] = None,
    text: Optional[str] = None,
) -> EventFilter:
    """Validate and normalise raw search parameters. Raises FilterError."""
    since = to_naive_utc(since) if since else None
    until = to_naive_utc(until) if until else None
    if since and until and since >= until:
        raise FilterError("'since' must be earlier than 'until'.")

    levels = []
    for raw in _clean(severities):
        level = raw.upper()
        if level not in _RANK:
            raise FilterError(f"Unknown severity '{raw}'. Use one of: {', '.join(SEVERITIES)}.")
        levels.append(level)
    min_level = None
    if min_severity and min_severity.strip():
        min_level = min_severity.strip().upper()
        if min_level not in _RANK:
            raise FilterError(f"Unknown severity '{min_severity}'. Use one of: {', '.join(SEVERITIES)}.")

    text = (text or "").strip() or None
    if text and len(text) > MAX_TEXT_LENGTH:
        raise FilterError(f"Search text is limited to {MAX_TEXT_LENGTH} characters.")

    return EventFilter(
        since=since,
        until=until,
        job_id=(job_id or "").strip() or None,
        devices=_clean(devices),
        sources=_clean(sources),
        categories=_clean(categories),
        severities=tuple(dict.fromkeys(levels)),
        min_severity=min_level,
        process=(process or "").strip() or None,
        source_file=(source_file or "").strip() or None,
        text=text,
    )


# --- paging cursor ------------------------------------------------------------
# Results are ordered by (timestamp, id) and each page ends with a cursor
# naming the last row it returned, so the next page starts right after it.
# Unlike OFFSET, this neither skips nor repeats rows when new events arrive
# between requests, and stays fast on page 5,000.

def encode_cursor(timestamp: datetime, event_id: int) -> str:
    raw = f"{timestamp.isoformat()}|{event_id}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
        ts, _, ident = raw.partition("|")
        return datetime.fromisoformat(ts), int(ident)
    except (ValueError, UnicodeError):
        raise FilterError("Invalid cursor. Use the next_cursor value from the previous page.") from None
