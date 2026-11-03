"""Reviewer-facing, authenticated audit timeline API (SCRUM-36)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.audit import log_action
from app.auth import User, get_active_user
from app.db import get_db
from app.models import AuditLogEntry
from app.schemas import AuditLogEntryOut, AuditLogPageOut

router = APIRouter()

MAX_AUDIT_LIMIT = 200


def _as_utc_for_response(value: datetime) -> datetime:
    """Audit timestamps are stored as naive UTC in SQLite."""
    return value.replace(tzinfo=timezone.utc)


def _normalize_query_time(value: datetime) -> datetime:
    """Return a naive UTC value suitable for comparing with SQLite rows.

    Naive input means UTC. Offset input must be converted to UTC before its
    timezone is removed; directly dropping an offset changes the instant.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@router.get("", response_model=AuditLogPageOut)
def list_audit_log(
    session_id: str | None = Query(None, min_length=1, max_length=128),
    actor: str | None = Query(None, min_length=1, max_length=128),
    action: str | None = Query(None, min_length=1, max_length=128),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=MAX_AUDIT_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_active_user),
):
    """Return a chronological, paginated timeline for compliance review.

    The local demo has a single authenticated role. A production identity
    provider must add role/tenant authorization before exposing other users'
    records. Callers deliberately never write passwords, tokens, raw logs, or
    log-search text into an audit description.
    """
    normalized_since = _normalize_query_time(since) if since else None
    normalized_until = _normalize_query_time(until) if until else None
    if normalized_since and normalized_until and normalized_since >= normalized_until:
        raise HTTPException(400, "until must be after since")

    query = db.query(AuditLogEntry)
    if session_id:
        query = query.filter(AuditLogEntry.session_id == session_id)
    if actor:
        query = query.filter(AuditLogEntry.actor == actor)
    if action:
        query = query.filter(AuditLogEntry.action == action)
    if normalized_since:
        query = query.filter(AuditLogEntry.timestamp >= normalized_since)
    if normalized_until:
        query = query.filter(AuditLogEntry.timestamp < normalized_until)

    total = query.with_entities(func.count(AuditLogEntry.id)).scalar() or 0
    entries = (
        query.order_by(AuditLogEntry.timestamp.asc(), AuditLogEntry.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Reading a compliance record is itself an accountable action. Record
    # only which filter fields were used, never the values.
    used_filters = [
        name for name, value in {
            "session_id": session_id, "actor": actor, "action": action,
            "since": since, "until": until,
        }.items() if value is not None
    ]
    log_action(
        db,
        user.id,
        "audit_view",
        detail=f"filters={used_filters} offset={offset} limit={limit}",
        session_id=user.session_id,
    )

    return AuditLogPageOut(
        items=[
            AuditLogEntryOut(
                id=entry.id,
                timestamp=_as_utc_for_response(entry.timestamp),
                actor=entry.actor,
                session_id=entry.session_id,
                action=entry.action,
                description=entry.detail or "",
            )
            for entry in entries
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
