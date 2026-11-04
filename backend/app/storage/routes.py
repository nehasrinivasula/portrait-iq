"""
HTTP surface for the central event store. Mounted at /api/logs.

  GET /api/logs/events                           search every stored event, any source or device
  GET /api/logs/facets                           counts per source/severity/category/device
  GET /api/logs/collections                      every collection and how many events it holds
  GET /api/logs/collections/{id}                 is this collection complete? what is missing?
  GET /api/logs/collections/{id}/events          unified log view: all of its entries, in time order
  GET /api/logs/collections/{id}/privacy         PHI/PII scan report: was everything scanned, what was flagged
  POST /api/logs/collections/{id}/privacy/scan   scan events not yet judged by the current scanner
  GET /api/logs/privacy                          the same report for every event (what the Logs page shows with no run chosen)
  POST /api/logs/privacy/scan                    scan every event not yet judged
  GET /api/logs/privacy/categories               the kinds of PHI/PII the scanner looks for

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
from app.privacy import service as privacy
from app.privacy.scanner import (
    CATEGORY_LABELS, SCANNER_VERSION, category_group, decode_flags, mask_text, scan_columns,
)
from app.storage.completeness import PROBLEM
from app.storage import events as store
from app.storage.filters import DEFAULT_LIMIT, MAX_LIMIT, UNASSIGNED, EventFilter, FilterError, build_filter
from app.storage.schemas import (
    CollectionOut, CollectionSummaryOut, FacetsOut, FacetValueOut, FileSummaryOut, IssueOut,
    FlagCountOut, LogEventOut, LogPageOut, PrivacyCategoryOut, PrivacyFileOut, PrivacyReportOut, RescanOut,
    SensitiveFlagOut,
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
    flagged: Optional[bool] = Query(None, description="true: only events flagged as possible PHI/PII. "
                                                    "false: only events scanned with nothing found."),
    flag_category: list[str] = Query([], description="Only events flagged with this kind, e.g. email; repeat for several."),
) -> EventFilter:
    try:
        return build_filter(
            since=since, until=until, devices=device, sources=source, categories=category,
            severities=severity, min_severity=min_severity, process=process,
            source_file=source_file, text=q, flagged=flagged, flag_categories=flag_category,
        )
    except FilterError as e:
        raise HTTPException(400, str(e))


def _utc(ts: Optional[datetime]) -> Optional[datetime]:
    return ts.replace(tzinfo=timezone.utc) if ts is not None else None   # stored as naive UTC


UNREADABLE = "[REDACTED:unreadable]"


def _event_out(e, mask: bool = False) -> LogEventOut:
    flags = decode_flags(e.sensitive_flags)
    network = decode_flags(e.network_flags)
    scanned = (e.privacy_scan_version or 0) >= SCANNER_VERSION
    out = dict(device_id=e.device_id, category=e.category, process=e.process, message=e.message)
    damaged = False
    if mask:
        if not scanned:
            # Never judged by the current rules: judge it now, for this response only, rather than
            # return text nobody has checked.
            cols = scan_columns(message=e.message, device_id=e.device_id, process=e.process, category=e.category)
            flags = decode_flags(cols["sensitive_flags"])
        elif e.sensitive_flags and not flags:
            damaged = True           # flagged, but the flags cannot be read: withhold the text
        if damaged:
            out = {name: (UNREADABLE if value else value) for name, value in out.items()}
        elif flags:
            out = {name: mask_text(value, flags, name) for name, value in out.items()}
    return LogEventOut(
        id=e.id, timestamp=_utc(e.timestamp), source=e.source, severity=e.severity,
        source_file=e.source_file, source_line=e.source_line, job_id=e.job_id,
        privacy_scanned=scanned,
        sensitive=[
            SensitiveFlagOut(field=f.field, category=f.category, confidence=f.confidence,
                             start=None if mask else f.start, end=None if mask else f.end)
            for f in flags
        ],
        network_identifiers=[
            SensitiveFlagOut(field=f.field, category=f.category, confidence=f.confidence,
                             start=None if mask else f.start, end=None if mask else f.end)
            for f in network
        ],
        masked=bool(mask and (flags or damaged)),
        **out,
    )


def _facet_values(pairs) -> list[FacetValueOut]:
    return [FacetValueOut(value=v, count=n) for v, n in pairs]


def _page(db: Session, user: User, f: EventFilter, *, limit: int, order: str, cursor: Optional[str],
          action: str, detail: str, mask: bool = False) -> LogPageOut:
    try:
        page = store.search_events(db, f, limit=limit, order=order, cursor=cursor)
    except FilterError as e:
        raise HTTPException(400, str(e))
    if cursor is None:
        # Once per search, not once per page. Field names only: the text
        # search could be a patient identifier.
        log_action(
            db,
            user.id,
            action,
            detail=f"{detail} filters={f.used_fields()} matched={page.total} masked={mask}",
            session_id=user.session_id,
        )
    return LogPageOut(items=[_event_out(e, mask) for e in page.items], next_cursor=page.next_cursor, total=page.total)


@router.get("/events", response_model=LogPageOut)
def search_events(
    f: EventFilter = Depends(_filter),
    job_id: Optional[str] = Query(None, description="Limit to one collection, or 'unassigned'."),
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: Optional[str] = Query(None, description="next_cursor from the previous page."),
    mask: bool = Query(False, description="Replace flagged PHI/PII spans with [REDACTED:<kind>]."),
    db: Session = Depends(get_db),
    user: User = Depends(_active_user),
):
    """Search every stored event across all sources and devices in one query.
    Newest first by default."""
    f = replace(f, job_id=job_id.strip() or None) if job_id else f
    return _page(db, user, f, limit=limit, order=order, cursor=cursor, action="logs_search", detail="", mask=mask)


@router.get("/facets", response_model=FacetsOut)
def get_facets(
    f: EventFilter = Depends(_filter),
    job_id: Optional[str] = Query(None),
    mask: bool = Query(False, description="Withhold the device and category values: they are log fields that can "
                                          "hold flagged text, and the table masks them."),
    db: Session = Depends(get_db),
    user: User = Depends(_active_user),
):
    f = replace(f, job_id=job_id.strip() or None) if job_id else f
    r = store.facets(db, f)
    log_action(
        db, user.id, "logs_facets_view",
        detail=f"filters={f.used_fields()} job_selected={bool(job_id)} masked={mask}",
        session_id=user.session_id,
    )
    return FacetsOut(
        total=r.total, first_timestamp=_utc(r.first_timestamp), last_timestamp=_utc(r.last_timestamp),
        sources=_facet_values(r.sources), severities=_facet_values(r.severities),
        categories=[] if mask else _facet_values(r.categories), devices=[] if mask else _facet_values(r.devices),
        distinct_devices=r.distinct_devices,
    )


@router.get("/collections", response_model=list[CollectionOut])
def list_collections(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
                     user: User = Depends(_active_user)):
    log_action(db, user.id, "collections_view", detail=f"limit={limit}", session_id=user.session_id)
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
    log_action(db, user.id, "collection_view", detail=f"job={job_id}", session_id=user.session_id)
    return CollectionSummaryOut(
        job_id=s.job_id, status=s.status, mode=s.mode, complete=s.complete,
        events_stored=s.events_stored, events_expected=s.events_expected,
        rows_read=s.rows_read, rows_rejected=s.rows_rejected,
        first_timestamp=_utc(s.first_timestamp), last_timestamp=_utc(s.last_timestamp),
        by_source=_facet_values(s.by_source), by_severity=_facet_values(s.by_severity),
        files=[
            FileSummaryOut(source_file=f.source_file, parser=f.parser, rows_read=f.rows_read,
                           rows_rejected=f.rows_rejected, events_expected=f.events_now_expected,
                           events_in_store=f.events_in_store, events_removed=f.events_removed)
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
    mask: bool = Query(False, description="Replace flagged PHI/PII spans with [REDACTED:<kind>]."),
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
                 action="logs_view", detail=f"job={job_id}", mask=mask)


# --- PHI/PII flagging ---------------------------------------------------------

SCAN_LIMITS = [
    "Finds known patterns only: labelled values (patientName=, mrn=, dob=), HL7 patient segments, "
    "SSNs, emails, phone numbers, payment cards and street addresses.",
    "Names, places and other identifiers written in plain sentences are not detected.",
    "IP and MAC addresses are recorded apart (network_identifier_events). They identify equipment, so "
    "they never count as flagged and are not masked.",
    "Flags mark text for review; they do not remove it unless mask=true is requested.",
]


def _counts(counter: dict[str, int]) -> list[FlagCountOut]:
    return [FlagCountOut(value=k, count=v) for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


@router.get("/privacy/categories", response_model=list[PrivacyCategoryOut])
def privacy_categories(user: User = Depends(_active_user)):
    """The kinds of PHI/PII the scanner looks for, and what each one means."""
    return [PrivacyCategoryOut(category=k, description=v, group=category_group(k)) for k, v in CATEGORY_LABELS.items()]


def _privacy_out(r, ingest_complete: bool) -> PrivacyReportOut:
    return PrivacyReportOut(
        job_id=r.job_id, scanner_version=r.scanner_version, events=r.events,
        scanned_events=r.scanned_events, unscanned_events=r.unscanned_events, all_scanned=r.all_scanned,
        ingest_complete=ingest_complete,
        complete_for_packaging=r.all_scanned and ingest_complete,
        flagged_events=r.flagged_events, network_identifier_events=r.network_events,
        network_by_category=_counts(r.network_by_category), by_category=_counts(r.flags_by_category),
        by_confidence=_counts(r.flags_by_confidence), by_field=_counts(r.flags_by_field),
        files=[PrivacyFileOut(source_file=f.source_file, events=f.events, flagged_events=f.flagged_events,
                              unscanned_events=f.unscanned_events,
                              network_identifier_events=f.network_events) for f in r.files],
        limits=SCAN_LIMITS,
    )


ALL_COLLECTIONS_LIMIT = 1_000_000   # every collection, not a page of them: a missing one must not read as complete


def _ingest_complete(db: Session, scope: Optional[str]) -> bool:
    """Did everything that was pulled in actually get stored? One collection, logs tied to no collection, or
    (scope None) every collection. A missing answer counts as not complete: this feeds a check that must fail closed.

    In the all-collections view a finished run that simply holds no logs (a Netshoot-only run) says nothing about
    the logs that were stored, so it does not count against the rest. A running or failed one still does."""
    if scope is not None:
        summary = store.collection_summary(db, scope)
        return summary is not None and summary.complete
    rows = store.list_collections(db, ALL_COLLECTIONS_LIMIT)
    if not rows:
        return False
    for row in rows:
        summary = store.collection_summary(db, row.job_id)
        if summary is None:
            return False
        if summary.complete or [i.kind for i in summary.issues if i.severity == PROBLEM] == ["no_logs"]:
            continue
        return False
    return any(r.events > 0 for r in rows)


def _report(db: Session, user: User, scope: Optional[str]) -> PrivacyReportOut:
    """scope: a job id, UNASSIGNED, or None for every event."""
    if scope not in (None, UNASSIGNED) and db.get(Job, scope) is None:
        raise HTTPException(404, "collection not found")
    complete = _ingest_complete(db, scope)
    r = privacy.privacy_report(db, scope)
    log_action(db, user.id, "logs_privacy_report",
               detail=f"job={scope or 'all'} flagged={r.flagged_events} unscanned={r.unscanned_events}",
               session_id=user.session_id)
    return _privacy_out(r, complete)


def _scan(db: Session, user: User, scope: Optional[str]) -> RescanOut:
    label = scope or "all"
    if scope not in (None, UNASSIGNED) and db.get(Job, scope) is None:
        raise HTTPException(404, "collection not found")
    # Each batch is committed as it goes, so a scan that stops partway has already changed events.
    # Record that it started, and how it ended.
    log_action(db, user.id, "logs_privacy_scan_start", detail=f"job={label}", session_id=user.session_id)
    try:
        n = privacy.rescan(db, scope)
    except Exception as e:
        db.rollback()
        log_action(db, user.id, "logs_privacy_scan", detail=f"job={label} failed error={type(e).__name__}",
                   session_id=user.session_id)
        raise
    log_action(db, user.id, "logs_privacy_scan", detail=f"job={label} scanned={n}", session_id=user.session_id)
    return RescanOut(scanned=n)


@router.get("/privacy", response_model=PrivacyReportOut)
def all_privacy(db: Session = Depends(get_db), user: User = Depends(_active_user)):
    """PHI/PII scan report across every stored event, whichever collection it belongs to."""
    return _report(db, user, None)


@router.post("/privacy/scan", response_model=RescanOut)
def scan_all(db: Session = Depends(get_db), user: User = Depends(_active_user)):
    """Scan every event the current scanner has not judged yet. Safe to repeat."""
    return _scan(db, user, None)


@router.get("/collections/{job_id}/privacy", response_model=PrivacyReportOut)
def collection_privacy(job_id: str, db: Session = Depends(get_db), user: User = Depends(_active_user)):
    """PHI/PII scan report for one collection: whether every event was scanned
    by the current scanner, and how many flags of each kind were found."""
    return _report(db, user, job_id)


@router.post("/collections/{job_id}/privacy/scan", response_model=RescanOut)
def scan_collection(job_id: str, db: Session = Depends(get_db), user: User = Depends(_active_user)):
    """Scan the events of this collection that the current scanner has not
    judged yet (logs stored before PHI/PII flagging existed, or scanned by an
    older rule set). Safe to repeat."""
    return _scan(db, user, job_id)
