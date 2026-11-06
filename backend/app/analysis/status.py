"""Is this collection's analysis there, running, failed, or out of date?"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AnalysisRun, IngestedFile
from app.models import NormalizedEvent as E

RUNNING_TOO_LONG = timedelta(hours=1)        # a "running" row this old belongs to a run that died


def _utc(ts: Optional[datetime]) -> Optional[datetime]:
    return ts.replace(tzinfo=timezone.utc) if ts is not None else None       # stored as naive UTC


def findings_status(db: Session, job_id: str, now: Optional[datetime] = None) -> dict:
    """state: never | running | ok | failed. stale: the findings predate logs that arrived since, or
    logs exist and nothing has run, so a refresh would change them."""
    now = now or datetime.utcnow()
    run = db.get(AnalysisRun, job_id)
    has_events = db.execute(select(E.id).where(E.job_id == job_id).limit(1)).first() is not None
    if run is None:
        return {"state": "never", "last_run_at": None, "events_scanned": None, "error": None, "stale": has_events}
    state = run.status
    if state == "running" and now - run.ran_at > RUNNING_TOO_LONG:
        state = "failed"
    latest_ingest = db.execute(select(func.max(IngestedFile.ingested_at)).where(IngestedFile.job_id == job_id)).scalar()
    stale = state in ("ok", "failed") and latest_ingest is not None and latest_ingest > run.ran_at
    return {
        "state": state,
        "last_run_at": _utc(run.ran_at) if state != "running" else None,
        "started_at": _utc(run.ran_at) if state == "running" else None,
        "events_scanned": run.events_scanned,
        "error": run.error if state == "failed" else None,
        "stale": stale,
    }
