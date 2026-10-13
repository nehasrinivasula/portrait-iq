"""
Parser for the Portrait "all device logs" CSV export (the file you get from
the diagnostics UI's log export — see Michael Parrish's / Andrew Welch's
screen shares).

Columns, as seen in the real sample set (35 hourly files, 548,303 rows):
  timeStampAndOffset, processCounter, concentrationCounter, logLevel,
  hostName, sourceUUID, friendlyName, processInfo, sourceInfo,
  eventCategory, description

Mapping to the common format:
  timestamp  <- timeStampAndOffset  (e.g. 2024-06-02T02:00:00.393+0000 -> UTC)
  severity   <- logLevel            (normalized; unknown -> INFO + warning)
  device_id  <- friendlyName, or hostName when friendlyName is blank or the
                placeholder "unsetFriendlyName"
  category   <- eventCategory       (e.g. General, PerfData)
  process    <- processInfo without its "[pid:n]" suffix
  message    <- description

Performance data (eventCategory == PerfData) is deliberately KEPT, even
though it is ~2/3 of all rows and the diagnostic rules ignore it: usage
analytics (Andrew Welch's "how often was Hub A used") will need it, and
dropping it here would be an irreversible decision made too early.

Uses the stdlib csv module rather than pandas so every row can be accounted
for exactly (pandas' on_bad_lines="skip" drops malformed rows with no
count) and so line numbers are available for provenance.

Validated against the real sample set — see docs/common_log_format.md.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterator, Optional

from app.ingestion.parsers.base import LogParser, ParseReport, UnrecognizedLogFormat, open_text
from app.ingestion.schema import EventValidationError, NormalizedEvent, make_event, normalize_severity, parse_timestamp

_PROC_SUFFIX = re.compile(r"\[.*$")  # "axone_logger[777:0]" -> "axone_logger"
_PLACEHOLDER_NAMES = {"", "unsetFriendlyName"}

REQUIRED_COLUMNS = ("timeStampAndOffset", "logLevel", "hostName", "eventCategory", "description")
# If this many malformed rows occur, the file is almost certainly not a
# Portrait CSV at all; stop instead of grinding through it.
MAX_CSV_ERRORS = 1000


class PortraitCsvParser(LogParser):
    name = "portrait_csv"
    source = "portrait"

    def sniff(self, path: Path) -> bool:
        try:
            with open_text(path) as fh:
                header = next(csv.reader(fh), None)
        except (OSError, csv.Error):
            return False
        if not header:
            return False
        return set(REQUIRED_COLUMNS).issubset({h.strip() for h in header})

    def parse(self, path: Path, report: Optional[ParseReport] = None) -> Iterator[NormalizedEvent]:
        path = Path(path)
        report = report if report is not None else ParseReport()
        report.parser = self.name
        report.source_file = path.name

        with open_text(path) as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if not header:
                raise UnrecognizedLogFormat(f"{path.name}: empty file")
            cols = {h.strip(): i for i, h in enumerate(header)}
            missing = [c for c in REQUIRED_COLUMNS if c not in cols]
            if missing:
                raise UnrecognizedLogFormat(f"{path.name}: missing columns {missing}")

            i_ts, i_lvl = cols["timeStampAndOffset"], cols["logLevel"]
            i_host, i_cat, i_msg = cols["hostName"], cols["eventCategory"], cols["description"]
            i_friendly = cols.get("friendlyName")
            i_proc = cols.get("processInfo")
            width = len(header)
            csv_errors = 0

            while True:
                start_line = reader.line_num + 1  # first physical line of the next record
                try:
                    row = next(reader)
                except StopIteration:
                    break
                except csv.Error as exc:
                    report.rows_read += 1
                    report.reject(start_line, "csv_error", str(exc))
                    csv_errors += 1
                    if csv_errors > MAX_CSV_ERRORS:
                        raise UnrecognizedLogFormat(f"{path.name}: too many malformed rows")
                    continue

                if not row:  # blank line: not a record
                    continue
                report.rows_read += 1

                if len(row) != width:
                    report.reject(start_line, "wrong_field_count", f"expected {width}, got {len(row)}")
                    continue

                try:
                    ts = parse_timestamp(row[i_ts])
                except ValueError:
                    report.reject(start_line, "bad_timestamp", row[i_ts])
                    continue

                raw_level = row[i_lvl]
                severity = normalize_severity(raw_level)
                if severity is None:
                    report.warn(f"unknown_severity_defaulted_to_INFO:{raw_level.strip()[:20]!r}")
                    severity = "INFO"

                friendly = row[i_friendly].strip() if i_friendly is not None else ""
                device = row[i_host] if friendly in _PLACEHOLDER_NAMES else friendly
                process = _PROC_SUFFIX.sub("", row[i_proc]) if i_proc is not None else None

                try:
                    yield make_event(
                        timestamp=ts,
                        source=self.source,
                        severity=severity,
                        message=row[i_msg],
                        device_id=device,
                        category=row[i_cat],
                        process=process,
                        source_file=path.name,
                        source_line=start_line,
                    )
                except EventValidationError as exc:
                    report.reject(start_line, "invalid_event", str(exc))


# --- module-level API, kept so existing imports and tests keep working ------

_PARSER = PortraitCsvParser()


def sniff(path: Path) -> bool:
    return _PARSER.sniff(Path(path))


def parse(path: Path, report: Optional[ParseReport] = None) -> Iterator[NormalizedEvent]:
    return _PARSER.parse(Path(path), report)
