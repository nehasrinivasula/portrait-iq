"""
Glue between "a folder of log files" and "rows in normalized_events".
Add a new parser by importing it and adding it to PARSERS — order matters,
first sniff() match wins.
"""
from pathlib import Path

from sqlalchemy.orm import Session

from app.ingestion.parsers import compute_csv, portrait_csv
from app.models import NormalizedEvent as EventRow

PARSERS = [portrait_csv, compute_csv]


def ingest_file(path: Path, db: Session, job_id: str | None = None, batch_size: int = 5000) -> int:
    """Ingest one file, return the number of events written. Raises if no
    parser recognizes the file — better to fail loudly than silently drop
    a log the field engineer thinks was collected."""
    parser = next((p for p in PARSERS if p.sniff(path)), None)
    if parser is None:
        raise ValueError(f"No parser recognizes {path.name} — is this a new log format?")

    count = 0
    batch: list[EventRow] = []
    for event in parser.parse(path):
        batch.append(
            EventRow(
                job_id=job_id,
                timestamp=event.timestamp,
                device_id=event.device_id,
                source=event.source,
                category=event.category,
                severity=event.severity,
                process=event.process,
                message=event.message,
            )
        )
        count += 1
        if len(batch) >= batch_size:
            db.bulk_save_objects(batch)
            db.commit()
            batch = []
    if batch:
        db.bulk_save_objects(batch)
        db.commit()
    return count


def ingest_directory(dir_path: Path, db: Session, job_id: str | None = None) -> dict[str, int]:
    """Ingest every recognized file in a directory. Returns {filename: event_count}.
    A file no parser recognizes is skipped with a note, not a hard failure —
    override this behavior with ingest_file() directly if you want strict mode."""
    results: dict[str, int] = {}
    for path in sorted(dir_path.glob("*.csv")):
        try:
            results[path.name] = ingest_file(path, db, job_id=job_id)
        except ValueError as e:
            results[path.name] = -1  # sentinel: unrecognized format
            print(f"skipped {path.name}: {e}")
    return results
