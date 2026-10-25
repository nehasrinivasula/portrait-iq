"""
The one sanctioned way to delete events from the central store after they
were ingested (for example when a pull of one device is followed by a pull of
all devices and the overlap must not be counted twice).

Deleting rows directly would make the unified view report data loss: the
file's receipt says N events were stored and fewer are there. This function
deletes the events and records how many it removed on each affected file's
receipt, so the completeness report stays correct and shows the removal as an
"events_removed" note rather than a problem.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import IngestedFile
from app.models import NormalizedEvent as E

CHUNK = 500   # stays under SQLite's limit on bound parameters


def remove_events(db: Session, event_ids: Iterable[int]) -> int:
    """Delete these events and update their files' receipts. Returns how many
    events were deleted (ids that do not exist are ignored). All or nothing:
    on an error nothing is deleted. Events whose file has no receipt (stored
    before receipts existed) are deleted too; there is nothing to update.
    The caller is responsible for knowing which events are safe to delete."""
    ids = list(dict.fromkeys(event_ids))
    removed_per_file: Counter = Counter()
    try:
        for i in range(0, len(ids), CHUNK):
            part = ids[i:i + CHUNK]
            for source_file, job_id, n in db.execute(
                select(E.source_file, E.job_id, func.count()).where(E.id.in_(part)).group_by(E.source_file, E.job_id)
            ).all():
                removed_per_file[(source_file, job_id)] += n
            db.execute(delete(E).where(E.id.in_(part)))

        for (source_file, job_id), n in removed_per_file.items():
            receipt = db.scalars(
                select(IngestedFile)
                .where(
                    IngestedFile.source_file == source_file,
                    IngestedFile.job_id.is_(None) if job_id is None else IngestedFile.job_id == job_id,
                )
                .order_by(IngestedFile.id.desc())
            ).first()
            if receipt is not None:
                receipt.events_removed = (receipt.events_removed or 0) + n
        db.commit()
    except Exception:
        db.rollback()
        raise
    return sum(removed_per_file.values())
