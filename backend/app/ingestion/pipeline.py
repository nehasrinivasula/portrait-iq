"""
Glue between "a file on disk" and "rows in normalized_events".

Properties this guarantees (and why they matter to the teammates building on
top of it):

  * Atomic per file. A file is written in one transaction. If anything goes
    wrong — unrecognised format, too many rejected rows, a database error —
    NOTHING from that file is stored. A caller tracking "processed files"
    (e.g. the Box/Portrait-diags download story) can therefore mark a file
    processed if and only if ingest_file() returned.
  * Idempotent. Re-ingesting the same file replaces its earlier rows instead
    of duplicating them (matched on source_file + job_id), so retrying after
    a crash is always safe.
  * Loud. A file no parser recognises raises UnrecognizedLogFormat; a file
    with an unacceptable share of unconvertible rows raises IngestError.
    Data is never silently dropped.
  * UTC only. Timestamps are stored as naive UTC. SQLite discards timezone
    info, so storing anything other than UTC would silently shift times.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.ingestion.parsers.base import LogParser, ParseReport, UnrecognizedLogFormat
from app.ingestion.registry import detect_parser, iter_events
from app.models import NormalizedEvent as EventRow

DEFAULT_PATTERNS = ("*.csv", "*.log")
DEFAULT_MAX_REJECT_RATE = 0.01  # fail a file if more than 1% of its rows can't be converted
DEFAULT_BATCH_SIZE = 5000


class IngestError(ValueError):
    """A file was recognised but could not be ingested safely (too many
    unconvertible rows, or no rows at all). A ValueError on purpose, like
    UnrecognizedLogFormat, so callers can catch one family of "this file is
    bad" errors. Callers that need to tell the two apart should catch
    UnrecognizedLogFormat (no parser) before IngestError (parser found, file
    unusable)."""


@dataclass
class IngestResult:
    source_file: str
    ok: bool
    events_written: int = 0
    parser: Optional[str] = None
    report: Optional[ParseReport] = None
    error: Optional[str] = None


def ingest_file(
    path: Path,
    db: Session,
    job_id: Optional[str] = None,
    parser: Optional[LogParser] = None,
    replace: bool = True,
    max_reject_rate: float = DEFAULT_MAX_REJECT_RATE,
    batch_size: int = DEFAULT_BATCH_SIZE,
    source_label: Optional[str] = None,
) -> IngestResult:
    """Parse one file and store its events.

    Returns an IngestResult (use `.events_written` for the count). Raises
    UnrecognizedLogFormat or IngestError, both ValueErrors, after rolling
    back, so nothing from a failed file is stored.

    Idempotency key: re-ingesting replaces earlier rows with the same
    (source_file, job_id). source_file defaults to the file's BASENAME, so
    two different files called export.csv would overwrite each other, and
    the same file ingested under a different job_id (or with and without a
    job) is stored once per job. Callers that can see files with colliding
    names (acquisition from Box, local folders, several devices) should pass
    source_label, a stable unique id such as "<source>/<remote_id>/<name>",
    which is used both as the key and as the stored source_file.
    """
    path = Path(path)
    label = source_label or path.name
    chosen = parser or detect_parser(path)
    if chosen is None:
        raise UnrecognizedLogFormat(f"{path.name}: no registered parser recognises this file")

    report = ParseReport()
    written = 0
    try:
        if replace:
            stmt = delete(EventRow).where(EventRow.source_file == label)
            stmt = stmt.where(EventRow.job_id.is_(None) if job_id is None else EventRow.job_id == job_id)
            db.execute(stmt)

        batch: list[dict] = []
        for event in iter_events(path, parser=chosen, report=report):
            batch.append(
                {
                    "job_id": job_id,
                    # naive UTC — see module docstring
                    "timestamp": event.timestamp.replace(tzinfo=None),
                    "device_id": event.device_id,
                    "source": event.source,
                    "category": event.category,
                    "severity": event.severity,
                    "process": event.process,
                    "message": event.message,
                    "source_file": label,
                    "source_line": event.source_line,
                }
            )
            if len(batch) >= batch_size:
                db.execute(insert(EventRow), batch)
                written += len(batch)
                batch = []
        if batch:
            db.execute(insert(EventRow), batch)
            written += len(batch)

        if report.rows_read == 0:
            raise IngestError(f"{path.name}: contained no log rows")
        if report.reject_rate > max_reject_rate:
            raise IngestError(
                f"{path.name}: {report.rows_rejected} of {report.rows_read} rows "
                f"({report.reject_rate:.1%}) could not be converted, over the "
                f"{max_reject_rate:.1%} limit; reasons: {dict(report.reject_reasons)}"
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return IngestResult(
        source_file=label, ok=True, events_written=written, parser=chosen.name, report=report
    )


def ingest_directory(
    dir_path: Path,
    db: Session,
    job_id: Optional[str] = None,
    patterns: Iterable[str] = DEFAULT_PATTERNS,
    **kwargs,
) -> list[IngestResult]:
    """Ingest every matching file in a directory. Keeps going past a bad file
    so one corrupt hour doesn't hide the other 34, but records the failure on
    its IngestResult — callers must check `.ok`, not assume success."""
    results: list[IngestResult] = []
    files = sorted({p for pattern in patterns for p in Path(dir_path).glob(pattern)})
    for path in files:
        try:
            results.append(ingest_file(path, db, job_id=job_id, **kwargs))
        except (UnrecognizedLogFormat, IngestError) as exc:
            results.append(IngestResult(source_file=path.name, ok=False, error=str(exc)))
    return results
