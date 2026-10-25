"""
Queries over the central event store (the normalized_events table): one
search across every source and device, facet counts for filters, and the
per-collection summary behind the unified log view.

Every function takes an open Session and does read-only work. Rows come back
in the common event shape, so callers never need to know which parser or
device produced them.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import Session

from app.models import AcquiredFileRecord, IngestedFile, Job
from app.models import NormalizedEvent as E
from app.storage.completeness import FileLedger, Issue, ManifestEntry, assess, is_complete
from app.storage.filters import (
    DEFAULT_LIMIT, MAX_LIMIT, UNASSIGNED, EventFilter, decode_cursor, encode_cursor,
)

FACET_LIMIT = 200


# --- search -------------------------------------------------------------------

def _like_pattern(text: str) -> str:
    """Substring pattern with the user's % and _ treated literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _job_condition(job_id: str):
    return E.job_id.is_(None) if job_id == UNASSIGNED else E.job_id == job_id


def _conditions(f: EventFilter) -> list:
    conds = []
    if f.job_id is not None:
        conds.append(_job_condition(f.job_id))
    if f.since is not None:
        conds.append(E.timestamp >= f.since)
    if f.until is not None:
        conds.append(E.timestamp < f.until)
    if f.devices:
        conds.append(E.device_id.in_(f.devices))
    if f.sources:
        conds.append(E.source.in_(f.sources))
    if f.categories:
        conds.append(E.category.in_(f.categories))
    levels = f.effective_severities
    if levels is not None:
        conds.append(E.severity.in_(levels) if levels else false())
    if f.process is not None:
        conds.append(E.process == f.process)
    if f.source_file is not None:
        conds.append(E.source_file == f.source_file)
    if f.text is not None:
        conds.append(E.message.ilike(_like_pattern(f.text), escape="\\"))
    return conds


@dataclass
class EventPage:
    items: list
    next_cursor: Optional[str] = None
    total: Optional[int] = None     # matching events overall; only on the first page


def count_events(db: Session, f: EventFilter) -> int:
    return db.scalar(select(func.count()).select_from(E).where(*_conditions(f))) or 0


def search_events(
    db: Session,
    f: EventFilter,
    *,
    limit: int = DEFAULT_LIMIT,
    order: str = "asc",
    cursor: Optional[str] = None,
    include_total: bool = True,
) -> EventPage:
    """One page of matching events, oldest first ("asc") or newest first
    ("desc"), ordered by (timestamp, id) so equal timestamps still have a
    stable order. Pass the previous page's next_cursor to continue.

    The total is counted only when no cursor is given (the first page); later
    pages skip that extra scan and the caller keeps the first page's number."""
    limit = max(1, min(limit, MAX_LIMIT))
    ascending = order != "desc"
    conds = _conditions(f)

    q = select(E).where(*conds)
    if cursor:
        ts, last_id = decode_cursor(cursor)
        if ascending:
            q = q.where(or_(E.timestamp > ts, and_(E.timestamp == ts, E.id > last_id)))
        else:
            q = q.where(or_(E.timestamp < ts, and_(E.timestamp == ts, E.id < last_id)))
    q = q.order_by(*((E.timestamp.asc(), E.id.asc()) if ascending else (E.timestamp.desc(), E.id.desc())))

    rows = list(db.scalars(q.limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return EventPage(
        items=rows,
        next_cursor=encode_cursor(rows[-1].timestamp, rows[-1].id) if more else None,
        total=count_events(db, f) if include_total and not cursor else None,
    )


def iter_events(db: Session, f: EventFilter, *, order: str = "asc", page_size: int = MAX_LIMIT) -> Iterator:
    """Every matching event, page by page, without loading them all at once."""
    cursor = None
    while True:
        page = search_events(db, f, limit=page_size, order=order, cursor=cursor, include_total=False)
        yield from page.items
        if page.next_cursor is None:
            return
        cursor = page.next_cursor


# --- facets -------------------------------------------------------------------

@dataclass
class Facets:
    total: int
    first_timestamp: Optional[datetime]
    last_timestamp: Optional[datetime]
    sources: list[tuple[Optional[str], int]]
    severities: list[tuple[Optional[str], int]]
    categories: list[tuple[Optional[str], int]]
    devices: list[tuple[Optional[str], int]]
    distinct_devices: int


def facets(db: Session, f: EventFilter) -> Facets:
    """Counts of what is in the current result set, for building filters
    (e.g. "PerfData: 366,000" so the noisy category can be switched off).
    Counts reflect every active filter."""
    conds = _conditions(f)

    def grouped(column, limit: Optional[int] = None):
        q = select(column, func.count()).where(*conds).group_by(column).order_by(func.count().desc(), column)
        return [(value, n) for value, n in db.execute(q.limit(limit) if limit else q)]

    total, first, last, distinct = db.execute(
        select(func.count(), func.min(E.timestamp), func.max(E.timestamp), func.count(func.distinct(E.device_id)))
        .where(*conds)
    ).one()
    return Facets(
        total=total, first_timestamp=first, last_timestamp=last,
        sources=grouped(E.source), severities=grouped(E.severity), categories=grouped(E.category),
        devices=grouped(E.device_id, FACET_LIMIT), distinct_devices=distinct,
    )


# --- collections --------------------------------------------------------------

@dataclass
class CollectionRow:
    job_id: str                      # a Job id, or UNASSIGNED
    status: Optional[str]
    mode: Optional[str]
    created_at: Optional[datetime]
    finished_at: Optional[datetime]
    events: int
    first_timestamp: Optional[datetime]
    last_timestamp: Optional[datetime]


def list_collections(db: Session, limit: int = 50) -> list[CollectionRow]:
    """Most recent collections first, each with how many events it holds. A
    collection with no events still appears (a failed run is worth seeing).
    Events that belong to no collection are listed last as UNASSIGNED."""
    stats = {
        job_id: (n, first, last)
        for job_id, n, first, last in db.execute(
            select(E.job_id, func.count(), func.min(E.timestamp), func.max(E.timestamp)).group_by(E.job_id)
        )
    }
    rows = []
    for job in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)):
        n, first, last = stats.get(job.id, (0, None, None))
        rows.append(CollectionRow(job.id, job.status, job.mode, job.created_at, job.finished_at, n, first, last))
    if None in stats:
        n, first, last = stats[None]
        rows.append(CollectionRow(UNASSIGNED, None, None, None, None, n, first, last))
    return rows


@dataclass
class CollectionSummary:
    job_id: str
    status: Optional[str]
    mode: Optional[str]
    events_stored: int
    events_expected: int             # what ingestion recorded as stored
    rows_read: int
    rows_rejected: int
    first_timestamp: Optional[datetime]
    last_timestamp: Optional[datetime]
    by_source: list[tuple[Optional[str], int]]
    by_severity: list[tuple[Optional[str], int]]
    files: list[FileLedger]
    issues: list[Issue]
    complete: bool = field(default=False)


def collection_summary(db: Session, job_id: str) -> Optional[CollectionSummary]:
    """Everything needed to say whether a collection's logs are all in the
    view, and if not, which files and how many rows are missing. Returns None
    for a job id that does not exist."""
    job = None
    if job_id != UNASSIGNED:
        job = db.get(Job, job_id)
        if job is None:
            return None
    cond = _job_condition(job_id)

    # What is in the store now, per file.
    in_store = {
        name: n for name, n in db.execute(select(E.source_file, func.count()).where(cond).group_by(E.source_file))
    }

    # What ingestion recorded per file. A file ingested twice without
    # replacement has two receipts; add them up so they match the store.
    receipts = IngestedFile.job_id.is_(None) if job_id == UNASSIGNED else IngestedFile.job_id == job_id
    merged: dict[str, dict] = defaultdict(lambda: {"parser": None, "read": 0, "written": 0, "rejected": 0})
    for r in db.scalars(select(IngestedFile).where(receipts).order_by(IngestedFile.id)):
        m = merged[r.source_file]
        m["parser"] = r.parser
        m["read"] += r.rows_read
        m["written"] += r.events_written
        m["rejected"] += r.rows_rejected
    files = [
        FileLedger(name, m["parser"], m["read"], m["written"], m["rejected"], in_store.get(name, 0))
        for name, m in sorted(merged.items())
    ]
    untracked = sum(n for name, n in in_store.items() if name not in merged)

    manifest = []
    if job is not None:
        manifest = [
            ManifestEntry(r.name, r.status, r.error)
            for r in db.scalars(
                select(AcquiredFileRecord).where(AcquiredFileRecord.job_id == job_id).order_by(AcquiredFileRecord.id)
            )
        ]

    issues = assess(
        job_status=job.status if job else None, files=files, manifest=manifest, events_without_record=untracked
    )
    total, first, last = db.execute(
        select(func.count(), func.min(E.timestamp), func.max(E.timestamp)).where(cond)
    ).one()

    def grouped(column):
        q = select(column, func.count()).where(cond).group_by(column).order_by(func.count().desc(), column)
        return [(value, n) for value, n in db.execute(q)]

    return CollectionSummary(
        job_id=job_id,
        status=job.status if job else None,
        mode=job.mode if job else None,
        events_stored=total,
        events_expected=sum(f.events_expected for f in files),
        rows_read=sum(f.rows_read for f in files),
        rows_rejected=sum(f.rows_rejected for f in files),
        first_timestamp=first,
        last_timestamp=last,
        by_source=grouped(E.source),
        by_severity=grouped(E.severity),
        files=files,
        issues=issues,
        complete=is_complete(issues),
    )
