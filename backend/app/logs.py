"""Read-only filtering of the normalized event store (SCRUM-53)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import User, get_current_user
from app.db import get_db
from app.models import NormalizedEvent

router = APIRouter()


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: str | None
    timestamp: datetime
    device_id: str | None
    source: str
    source_file: str | None
    source_line: int | None
    category: str | None
    severity: str
    process: str | None
    message: str


class LogPage(BaseModel):
    items: list[LogOut]
    total: int
    limit: int
    offset: int


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, "Time bounds must include a timezone offset.")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@router.get("/logs", response_model=LogPage)
def list_logs(
    job_id: str | None = None,
    device_id: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LogPage:
    """Exact-match filters combined with AND; UTC time window is [start, end)."""
    start_utc, end_utc = _utc_naive(start), _utc_naive(end)
    if start_utc is not None and end_utc is not None and start_utc >= end_utc:
        raise HTTPException(422, "start must be earlier than end.")

    predicates = []
    if job_id is not None:
        predicates.append(NormalizedEvent.job_id == job_id)
    if device_id is not None:
        predicates.append(NormalizedEvent.device_id == device_id)
    if severity is not None:
        predicates.append(NormalizedEvent.severity == severity)
    if category is not None:
        predicates.append(NormalizedEvent.category == category)
    if start_utc is not None:
        predicates.append(NormalizedEvent.timestamp >= start_utc)
    if end_utc is not None:
        predicates.append(NormalizedEvent.timestamp < end_utc)

    total = db.scalar(select(func.count()).select_from(NormalizedEvent).where(*predicates))
    rows = db.scalars(
        select(NormalizedEvent)
        .where(*predicates)
        .order_by(NormalizedEvent.timestamp.desc(), NormalizedEvent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    items = [
        LogOut.model_validate(row).model_copy(
            update={"timestamp": row.timestamp.replace(tzinfo=timezone.utc)}
        )
        for row in rows
    ]
    return LogPage(items=items, total=total or 0, limit=limit, offset=offset)
