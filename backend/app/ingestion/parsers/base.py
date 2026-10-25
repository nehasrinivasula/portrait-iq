"""
The contract every log parser implements, plus ParseReport — the record of
what a parse did, including what it could NOT convert.

Why a report exists: a parser that quietly skips malformed rows looks
identical to one that parsed everything, and a diagnostic tool that silently
loses log lines is worse than one that fails. Every row a parser reads must
end up either as an event or as a counted, explained rejection:

    report.rows_read == report.events_parsed + report.rows_rejected

(tests/test_parser_contract.py enforces this for every registered parser.)
File-level problems (wrong format, missing columns) raise
UnrecognizedLogFormat instead of being counted per row.
"""
from __future__ import annotations

import csv
import sys
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Iterator, Optional

from app.ingestion.schema import NormalizedEvent

# Log "message" fields can be very large (stack traces, JSON blobs). The csv
# module's default 128 KB field limit would turn those into hard errors.
try:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
except OverflowError:  # pragma: no cover - platform dependent
    csv.field_size_limit(2**30)


class UnrecognizedLogFormat(ValueError):
    """The whole file is not in the format this parser (or any parser) reads."""


@dataclass
class RejectedRow:
    line: Optional[int]
    reason: str
    excerpt: str


@dataclass
class ParseReport:
    parser: str = ""
    source_file: str = ""
    rows_read: int = 0
    events_parsed: int = 0
    rows_rejected: int = 0
    reject_reasons: Counter = field(default_factory=Counter)
    reject_samples: list = field(default_factory=list)
    # Non-fatal oddities: e.g. an unknown severity string defaulted to INFO.
    warnings: Counter = field(default_factory=Counter)
    severity_counts: Counter = field(default_factory=Counter)
    categories: Counter = field(default_factory=Counter)
    devices: set = field(default_factory=set)
    first_timestamp: Optional[datetime] = None
    last_timestamp: Optional[datetime] = None

    MAX_SAMPLES: ClassVar[int] = 20
    MAX_CATEGORIES: ClassVar[int] = 50
    EXCERPT_CHARS: ClassVar[int] = 120

    def reject(self, line: Optional[int], reason: str, excerpt: str = "") -> None:
        """Record a row that could not be converted. `reason` must be a short
        constant (it is a Counter key); put row-specific detail in `excerpt`.
        Excerpts are truncated and live only in memory / console output —
        never persist them, since raw log text may be sensitive."""
        self.rows_rejected += 1
        self.reject_reasons[reason] += 1
        if len(self.reject_samples) < self.MAX_SAMPLES:
            self.reject_samples.append(RejectedRow(line, reason, excerpt[: self.EXCERPT_CHARS]))

    def warn(self, key: str) -> None:
        self.warnings[key] += 1

    def observe(self, event: NormalizedEvent) -> None:
        """Called by registry.iter_events for each event actually produced,
        so the statistics describe what really came out the other end."""
        self.events_parsed += 1
        self.severity_counts[event.severity] += 1
        if event.category is not None and (
            event.category in self.categories or len(self.categories) < self.MAX_CATEGORIES
        ):
            self.categories[event.category] += 1
        if event.device_id is not None:
            self.devices.add(event.device_id)
        ts = event.timestamp
        if self.first_timestamp is None or ts < self.first_timestamp:
            self.first_timestamp = ts
        if self.last_timestamp is None or ts > self.last_timestamp:
            self.last_timestamp = ts

    @property
    def reject_rate(self) -> float:
        return self.rows_rejected / self.rows_read if self.rows_read else 0.0

    @property
    def accounted_for(self) -> bool:
        """True if every row read became an event or a counted rejection."""
        return self.rows_read == self.events_parsed + self.rows_rejected


class LogParser(ABC):
    """One implementation per log format. Register instances in
    app/ingestion/registry.py."""

    name: str = "unnamed"      # unique, e.g. "portrait_csv"
    source: str = "unknown"    # value of NormalizedEvent.source, e.g. "portrait"

    @abstractmethod
    def sniff(self, path: Path) -> bool:
        """Cheap check (read a few lines, never the whole file) for whether
        this file is in this parser's format. Must not raise."""

    @abstractmethod
    def parse(self, path: Path, report: Optional[ParseReport] = None) -> Iterator[NormalizedEvent]:
        """Stream events from the file. Row-level problems go into `report`
        (create one if None); file-level problems raise UnrecognizedLogFormat.
        Must be a generator — files can be hundreds of MB."""


def open_text(path: Path):
    """Open a log file as text: BOM-tolerant, never crashes on a stray
    undecodable byte (replaced instead), and newline='' so the csv module
    can handle quoted fields that span lines."""
    return open(path, encoding="utf-8-sig", errors="replace", newline="")
