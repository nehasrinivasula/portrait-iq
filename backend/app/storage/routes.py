"""
HTTP surface for the central event store. Mounted at /api/logs.

  GET /api/logs/events                           search every stored event, any source or device
  GET /api/logs/facets                           counts per source/severity/category/device
  GET /api/logs/collections                      every collection and how many events it holds
  GET /api/logs/collections/{id}                 is this collection complete? what is missing?
  GET /api/logs/collections/{id}/events          unified log view: all of its entries, in time order

{id} is a job id, or "unassigned" for logs that were ingested without one.
Messages are raw log text and may contain patient or staff data, so these
routes need a signed-in, unlocked session and record that a search happened
(which filters were used, never their values) in the audit log.
"""
from dataclasses import replace
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.audit import log_action
from app.auth import User, get_current_user, is_locked, touch_session
from app.db import get_db
from app.models import Job
from app.storage import events as store
from app.storage.filters import DEFAULT_LIMIT, MAX_LIMIT, UNASSIGNED, EventFilter, FilterError, build_filter
from app.storage.schemas import (
    CollectionOut, CollectionSummaryOut, FacetsOut, FacetValueOut, FileSummaryOut, IssueOut,
    LogEventOut, LogPageOut,
)

router = APIRouter()


def _active_user(user: User = Depends(get_current_user)) -> User:
    # Same rule as /api/sources: a locked session cannot read logs.
    if is_locked(user.id):
        raise HTTPException(423, "Session locked. Unlock before viewing logs.")
    touch_session(user.id)
    return user


def _filter(
    since: Optional[datetime] = Query(None, description="Earliest event time, inclusive. No timezone = UTC."),
    until: Optional[datetime] = Query(None, description="Latest event time, exclusive. No timezone = UTC."),
    device: list[str] = Query([], description="Device id; repeat for several."),
    source: list[str] = Query([], description="Log source, e.g. portrait, kubernetes; repeat for several."),
    category: list[str] = Query([], description="Event category, e.g. PerfData; repeat for several."),
    severity: list[str] = Query([], description="DEBUG, INFO, WARN or ERROR; repeat for several."),
    min_severity: Optional[str] = Query(None, description="This level and above, e.g. WARN."),
    process: Optional[str] = None,
    source_file: Optional[str] = None,
    q: Optional[str] = Query(None, description="Case-insensitive text to find in the message."),
) -> EventFilter:
    try:
        return build_filter(
            since=since, until=until, devices=device, sources=source, categories=category,
            severities=severity, min_severity=min_severity, process=process,
            source_file=source_file, text=q,
        )
    except FilterError as e:
        raise HTTPException(400, str(e))


def _utc(ts: Optional[datetime]) -> Optional[datetime]:
    return ts.replace(tzinfo=timezone.utc) if ts is not None else None   # stored as naive UTC


def _event_out(e) -> LogEventOut:
    return LogEventOut(
        id=e.id, timestamp=_utc(e.timestamp), device_id=e.device_id, source=e.source,
        category=e.category, severity=e.severity, process=e.process, message=e.message,
        source_file=e.source_file, source_line=e.source_line, job_id=e.job_id,
    )


def _facet_values(pairs) -> list[FacetValueOut]:
    return [FacetValueOut(value=v, count=n) for v, n in pairs]


def _page(db: Session, user: User, f: EventFilter, *, limit: int, order: str, cursor: Optional[str],
          action: str, detail: str) -> LogPageOut:
    try:
        page = store.search_events(db, f, limit=limit, order=order, cursor=cursor)
    except FilterError as e:
        raise HTTPException(400, str(e))
    if cursor is None:
        # Once per search, not once per page. Field names only: the text
        # search could be a patient identifier.
        log_action(db, user.id, action, detail=f"{detail} filters={f.used_fields()} matched={page.total}")
    return LogPageOut(items=[_event_out(e) for e in page.items], next_cursor=page.next_cursor, total=page.total)


@router.get("/events", response_model=LogPageOut)
def search_events(
    f: EventFilter = Depends(_filter),
    job_id: Optional[str] = Query(None, description="Limit to one collection, or 'unassigned'."),
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: Optional[str] = Query(None, description="next_cursor from the previous page."),
    db: Session = Depends(get_db),
    user: User = Depends(_active_user),
):
    """Search every stored event across all sources and devices in one query.
    Newest first by default."""
    f = replace(f, job_id=job_id.strip() or None) if job_id else f
    return _page(db, user, f, limit=limit, order=order, cursor=cursor, action="logs_search", detail="")


@router.get("/facets", response_model=FacetsOut)
def get_facets(
    f: EventFilter = Depends(_filter),
    job_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(_active_user),
):
    f = replace(f, job_id=job_id.strip() or None) if job_id else f
    r = store.facets(db, f)
    return FacetsOut(
        total=r.total, first_timestamp=_utc(r.first_timestamp), last_timestamp=_utc(r.last_timestamp),
        sources=_facet_values(r.sources), severities=_facet_values(r.severities),
        categories=_facet_values(r.categories), devices=_facet_values(r.devices),
        distinct_devices=r.distinct_devices,
    )


@router.get("/collections", response_model=list[CollectionOut])
def list_collections(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
                     user: User = Depends(_active_user)):
    return [
        CollectionOut(
            job_id=c.job_id, status=c.status, mode=c.mode, created_at=_utc(c.created_at),
            finished_at=_utc(c.finished_at), events=c.events,
            first_timestamp=_utc(c.first_timestamp), last_timestamp=_utc(c.last_timestamp),
        )
        for c in store.list_collections(db, limit)
    ]


def _summary_or_404(db: Session, job_id: str):
    summary = store.collection_summary(db, job_id)
    if summary is None:
        raise HTTPException(404, "collection not found")
    return summary


@router.get("/collections/{job_id}", response_model=CollectionSummaryOut)
def get_collection(job_id: str, db: Session = Depends(get_db), user: User = Depends(_active_user)):
    """Completeness report: how many events were stored versus read, which
    files were rejected or never ingested, and a plain yes/no in `complete`."""
    s = _summary_or_404(db, job_id)
    return CollectionSummaryOut(
        job_id=s.job_id, status=s.status, mode=s.mode, complete=s.complete,
        events_stored=s.events_stored, events_expected=s.events_expected,
        rows_read=s.rows_read, rows_rejected=s.rows_rejected,
        first_timestamp=_utc(s.first_timestamp), last_timestamp=_utc(s.last_timestamp),
        by_source=_facet_values(s.by_source), by_severity=_facet_values(s.by_severity),
        files=[
            FileSummaryOut(source_file=f.source_file, parser=f.parser, rows_read=f.rows_read,
                           rows_rejected=f.rows_rejected, events_expected=f.events_expected,
                           events_in_store=f.events_in_store)
            for f in s.files
        ],
        issues=[IssueOut(kind=i.kind, severity=i.severity, message=i.message, file=i.file) for i in s.issues],
    )


@router.get("/collections/{job_id}/events", response_model=LogPageOut)
def view_collection(
    job_id: str,
    f: EventFilter = Depends(_filter),
    order: Literal["asc", "desc"] = "asc",
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: Optional[str] = Query(None, description="next_cursor from the previous page."),
    db: Session = Depends(get_db),
    user: User = Depends(_active_user),
):
    """The unified log view: every entry from one collection, from every
    source and device, as a single time-ordered stream. With no filters this
    is all of them; `total` on the first page is the count to compare with the
    collection's completeness report. Oldest first by default."""
    if job_id != UNASSIGNED and db.get(Job, job_id) is None:   # cheap: not the full summary, once per page
        raise HTTPException(404, "collection not found")
    return _page(db, user, replace(f, job_id=job_id), limit=limit, order=order, cursor=cursor,
                 action="logs_view", detail=f"job={job_id}")
