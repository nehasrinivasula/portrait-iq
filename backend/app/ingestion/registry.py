"""
Where parsers are registered and files are routed to them.

To add a source: write a LogParser subclass, then append an instance to
PARSERS. Order matters — the first parser whose sniff() returns True wins,
so put more specific formats before more permissive ones.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from app.ingestion.parsers.base import LogParser, ParseReport, UnrecognizedLogFormat
from app.ingestion.parsers.compute_csv import ComputeCsvParser
from app.ingestion.parsers.k8s_log import KubernetesLogParser
from app.ingestion.parsers.portrait_csv import PortraitCsvParser
from app.ingestion.schema import NormalizedEvent

PARSERS: list[LogParser] = [
    PortraitCsvParser(),
    KubernetesLogParser(),
    ComputeCsvParser(),  # placeholder: sniff() is always False until a real sample exists
]


def register_parser(parser: LogParser) -> None:
    if any(p.name == parser.name for p in PARSERS):
        raise ValueError(f"a parser named {parser.name!r} is already registered")
    PARSERS.append(parser)


def get_parser(name: str) -> LogParser:
    for p in PARSERS:
        if p.name == name:
            return p
    raise KeyError(f"no parser named {name!r}; known: {[p.name for p in PARSERS]}")


def detect_parser(path: Path) -> Optional[LogParser]:
    """The first registered parser that recognises the file, else None."""
    path = Path(path)
    for p in PARSERS:
        if p.sniff(path):
            return p
    return None


def iter_events(
    path: Path,
    parser: Optional[LogParser] = None,
    report: Optional[ParseReport] = None,
) -> Iterator[NormalizedEvent]:
    """Stream normalized events from any supported file.

    Pass your own ParseReport to read the statistics afterwards; it is
    complete once the iterator is exhausted:

        report = ParseReport()
        for event in iter_events(path, report=report):
            ...
        assert report.accounted_for
    """
    path = Path(path)
    chosen = parser or detect_parser(path)
    if chosen is None:
        raise UnrecognizedLogFormat(f"{path.name}: no registered parser recognises this file")
    report = report if report is not None else ParseReport()
    report.parser = chosen.name
    report.source_file = path.name
    for event in chosen.parse(path, report):
        report.observe(event)
        yield event
