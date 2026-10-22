"""
Parser for Kubernetes container logs captured with timestamps:

    kubectl logs --timestamps -n pm <pod> > <pod>.log   # namespace 'pm', per the mentors

Each line looks like:   2024-06-02T02:00:01.123456789Z <message>

This is the format the collectors (Netshoot, RTI, tshark pods on the
MiniEdge) will produce, so it has to be readable before collection can feed
the event store.

STATUS: written against the documented kubectl format and covered by
synthetic tests. NOT yet validated against a real log from the lab's
MiniEdge. The team can produce one in minutes (see
docs/common_log_format.md, "Getting real samples") and should run it through
`python -m app.ingestion.validate_samples` before trusting this parser.

Conventions this parser depends on (collectors must follow them):
  * One file per pod/container, named after it, e.g. `um-pm-0.log`. The file
    name (without extension) becomes device_id because kubectl output does
    not contain the pod name. Pass device_id= to override.
  * Captured WITH --timestamps. Lines without a timestamp prefix are
    rejected (counted, not silently dropped).

Severity is inferred from the message text — Kubernetes itself has no
severity field. This is a heuristic (klog prefixes, level=/"level": keys,
a leading uppercase level token); anything unrecognised is INFO. Treat
severities from this source as best-effort, and never as proof that a
container is healthy.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Optional

from app.ingestion.parsers.base import LogParser, ParseReport, open_text
from app.ingestion.schema import EventValidationError, NormalizedEvent, make_event, normalize_severity, parse_timestamp

_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\S+)\s(.*)$")
_GLOG_RE = re.compile(r"^([IWEF])\d{4}\s")  # klog/glog: "E0602 02:00:01.1 ..."
_KEYED_RE = re.compile(r"""\b(?:level|lvl|severity)["']?\s*[=:]\s*["']?([A-Za-z]+)""", re.IGNORECASE)
_LEADING_RE = re.compile(
    r"^\W{0,3}(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|ERR|CRITICAL|FATAL)\b"
)  # case-sensitive on purpose: "Error handling enabled" is not an error
_GLOG_LEVELS = {"I": "INFO", "W": "WARN", "E": "ERROR", "F": "ERROR"}

SNIFF_LINES = 20
SNIFF_MIN_FRACTION = 0.9


def infer_severity(message: str) -> Optional[str]:
    """Best-effort severity from a log message, or None if no signal."""
    m = _GLOG_RE.match(message)
    if m:
        return _GLOG_LEVELS[m.group(1)]
    m = _KEYED_RE.search(message[:300])
    if m:
        sev = normalize_severity(m.group(1))
        if sev:
            return sev
    m = _LEADING_RE.match(message)
    if m:
        return normalize_severity(m.group(1))
    return None


class KubernetesLogParser(LogParser):
    name = "k8s_log"
    source = "kubernetes"

    def __init__(self, device_id: Optional[str] = None):
        self.device_id = device_id

    def sniff(self, path: Path) -> bool:
        try:
            seen = matched = 0
            with open_text(path) as fh:
                for text in fh:
                    text = text.rstrip("\r\n")
                    if not text.strip():
                        continue
                    seen += 1
                    if _LINE_RE.match(text):
                        matched += 1
                    if seen >= SNIFF_LINES:
                        break
        except OSError:
            return False
        return seen > 0 and matched / seen >= SNIFF_MIN_FRACTION

    def parse(self, path: Path, report: Optional[ParseReport] = None) -> Iterator[NormalizedEvent]:
        path = Path(path)
        report = report if report is not None else ParseReport()
        report.parser = self.name
        report.source_file = path.name
        device = self.device_id or path.stem

        with open_text(path) as fh:
            for lineno, text in enumerate(fh, start=1):
                text = text.rstrip("\r\n")
                if not text.strip():
                    continue
                report.rows_read += 1

                m = _LINE_RE.match(text)
                if not m:
                    report.reject(lineno, "no_timestamp_prefix", text)
                    continue
                try:
                    ts = parse_timestamp(m.group(1))
                except ValueError:
                    report.reject(lineno, "bad_timestamp", m.group(1))
                    continue

                message = m.group(2)
                try:
                    yield make_event(
                        timestamp=ts,
                        source=self.source,
                        severity=infer_severity(message) or "INFO",
                        message=message,
                        device_id=device,
                        category="ContainerLog",
                        source_file=path.name,
                        source_line=lineno,
                    )
                except EventValidationError as exc:
                    report.reject(lineno, "invalid_event", str(exc))


_PARSER = KubernetesLogParser()


def sniff(path: Path) -> bool:
    return _PARSER.sniff(Path(path))


def parse(path: Path, report: Optional[ParseReport] = None) -> Iterator[NormalizedEvent]:
    return _PARSER.parse(Path(path), report)
