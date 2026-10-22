"""
The common event format. Every log source — Portrait CSV exports, Kubernetes
pod logs, future compute-platform logs, tshark output — is converted into
NormalizedEvent before anything downstream (event store, analysis rules,
knowledge lookup, analytics, packaging) sees it. Downstream code must never
need to know which source an event came from in order to read it.

Guarantees every NormalizedEvent produced by make_event() carries:

  timestamp   timezone-aware, always UTC. Naive timestamps are rejected
              rather than guessed at: a wrong guess silently breaks
              cross-source correlation (e.g. lining a hub log up against
              a packet capture), and that correlation was an explicit
              stakeholder ask (Andrew Welch, EU network team).
  severity    exactly one of SEVERITIES. Source-specific level names are
              mapped by normalize_severity().
  source      lowercase slug naming the kind of source ("portrait",
              "kubernetes", "compute", "network", ...).
  message     always a str (possibly empty), never None.
  device_id   optional str. Whatever identifies the device in that source.
              NOTE: identities are NOT yet reconciled across sources (a
              Portrait hostName, a pod name and an IP address for the same
              machine are three different device_id values). That needs a
              device registry and is deliberately out of scope here.
  category / process   optional, source-defined vocabulary (not unified).
  source_file / source_line   provenance back to the raw file, so any
              event can be traced to the line it came from.

See docs/common_log_format.md for how to add a new source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

UTC = timezone.utc

SEVERITIES = ("DEBUG", "INFO", "WARN", "ERROR")


class EventValidationError(ValueError):
    """An event could not be made to satisfy the common format."""


# --- severity ---------------------------------------------------------------

# Anything at or above "critical" collapses into ERROR on purpose: the
# analysis rules and the UI only distinguish INFO / WARN / ERROR, and adding
# levels nobody filters on just fragments the data.
_SEVERITY_ALIASES = {
    "TRACE": "DEBUG", "DEBUG": "DEBUG", "VERBOSE": "DEBUG", "FINE": "DEBUG",
    "INFO": "INFO", "INFORMATION": "INFO", "NOTICE": "INFO", "LOG": "INFO",
    "WARN": "WARN", "WARNING": "WARN",
    "ERROR": "ERROR", "ERR": "ERROR", "SEVERE": "ERROR", "CRITICAL": "ERROR",
    "CRIT": "ERROR", "FATAL": "ERROR", "ALERT": "ERROR", "EMERG": "ERROR",
    "EMERGENCY": "ERROR",
}


def normalize_severity(raw: Optional[str]) -> Optional[str]:
    """Map a source's level string to one of SEVERITIES, or None if it is
    not a level name we recognise. Callers decide what to do with None —
    parsers default to INFO *and record a warning* rather than dropping the
    row, so unknown levels are visible in the parse report instead of
    silently vanishing."""
    if raw is None:
        return None
    return _SEVERITY_ALIASES.get(raw.strip().upper())


# --- timestamps -------------------------------------------------------------

# Accepts: 2024-06-02T02:00:00.393+0000   (Portrait CSV)
#          2024-06-02T02:00:01.123456789Z  (kubectl logs --timestamps, nanos)
#          2024-06-02 02:00:01,500+01:00   (space separator, comma fraction)
_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
    r"(?:[.,](\d+))?\s*(Z|[+-]\d{2}(?::?\d{2})?)?$"
)


def parse_timestamp(text: str) -> datetime:
    """Parse a timestamp string into a timezone-aware UTC datetime.

    Raises ValueError if the text is not a recognised format OR has no
    timezone offset. Refusing to guess the zone is deliberate (see module
    docstring). Fractional seconds beyond microseconds are truncated.
    Hand-rolled instead of datetime.fromisoformat because that cannot read
    '+0000' or 9-digit fractions before Python 3.11, and this runs once per
    log line (hundreds of thousands of times per file set).
    """
    m = _TS_RE.match(text.strip())
    if not m:
        raise ValueError(f"unrecognized timestamp format: {text[:40]!r}")
    year, month, day, hour, minute, second, frac, tz = m.groups()
    if tz is None:
        raise ValueError(f"timestamp has no timezone offset: {text[:40]!r}")
    micro = int((frac + "000000")[:6]) if frac else 0
    if tz == "Z":
        offset = timedelta(0)
    else:
        digits = tz[1:].replace(":", "")
        magnitude = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))
        offset = -magnitude if tz[0] == "-" else magnitude
    dt = datetime(
        int(year), int(month), int(day), int(hour), int(minute), int(second),
        micro, tzinfo=timezone(offset),  # ValueError if offset >= 24h
    )
    return dt.astimezone(UTC)


# --- the event --------------------------------------------------------------

_SOURCE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(slots=True)
class NormalizedEvent:
    timestamp: datetime
    device_id: Optional[str]
    source: str
    category: Optional[str]
    severity: str
    process: Optional[str]
    message: str
    source_file: Optional[str] = None
    source_line: Optional[int] = None

    def validate(self) -> None:
        """Raise EventValidationError if this event breaks the format.
        make_event() always produces valid events; call this on events
        built any other way (e.g. in tests, or by hand-written code)."""
        ts = self.timestamp
        if not isinstance(ts, datetime) or ts.tzinfo is None or ts.utcoffset() is None:
            raise EventValidationError("timestamp must be a timezone-aware datetime")
        if ts.utcoffset() != timedelta(0):
            raise EventValidationError("timestamp must be normalized to UTC")
        if self.severity not in SEVERITIES:
            raise EventValidationError(f"severity {self.severity!r} not in {SEVERITIES}")
        if not isinstance(self.source, str) or not _SOURCE_RE.match(self.source):
            raise EventValidationError(f"source {self.source!r} is not a lowercase slug")
        if not isinstance(self.message, str):
            raise EventValidationError("message must be a str (use '' for none)")
        for name in ("device_id", "category", "process", "source_file"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value or value != value.strip()):
                raise EventValidationError(f"{name} must be None or a non-empty, stripped str")
        if self.source_line is not None and (not isinstance(self.source_line, int) or self.source_line < 1):
            raise EventValidationError("source_line must be None or a positive int")


def _clean(value: Optional[str]) -> Optional[str]:
    """Strip a field; blank becomes None so 'missing' has one representation."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def make_event(
    *,
    timestamp: datetime,
    source: str,
    severity: str,
    message: Optional[str],
    device_id: Optional[str] = None,
    category: Optional[str] = None,
    process: Optional[str] = None,
    source_file: Optional[str] = None,
    source_line: Optional[int] = None,
) -> NormalizedEvent:
    """The only way parsers should construct events. Normalizes what can be
    normalized (UTC, severity aliases, blank -> None) and raises
    EventValidationError for what cannot (naive timestamp, unknown
    severity, bad source slug)."""
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise EventValidationError("timestamp must be a timezone-aware datetime")
    sev = normalize_severity(severity)
    if sev is None:
        raise EventValidationError(f"unknown severity {severity!r}")
    event = NormalizedEvent(
        timestamp=timestamp.astimezone(UTC),
        device_id=_clean(device_id),
        source=(source or "").strip().lower(),
        category=_clean(category),
        severity=sev,
        process=_clean(process),
        message="" if message is None else str(message),
        source_file=_clean(source_file),
        source_line=source_line,
    )
    event.validate()
    return event
