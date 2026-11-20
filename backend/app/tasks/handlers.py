"""What each kind of task does. Imports are inside the functions so importing the task package stays cheap."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.models import BackgroundTask
from app.tasks.service import task_params


def run_noop(db: Session, task: BackgroundTask, progress) -> tuple[dict, None]:
    """Does nothing. Lets an operator (and the tests) prove the worker path end to end without touching data."""
    progress(1, 1, "Nothing to do")
    return {"ok": True}, None


def run_acquire(db: Session, task: BackgroundTask, progress) -> tuple[dict, Optional[Callable[[], None]]]:
    from app.analysis import auto as auto_analysis
    from app.sources import registry
    from app.sources.routes import to_out
    from app.sources.service import acquire

    p = task_params(task)
    source = registry.get_source(p["source_id"])
    result = acquire(
        db, source, actor=task.requested_by, session_id=task.session_id,
        since=datetime.fromisoformat(p["since"]) if p.get("since") else None,
        until=datetime.fromisoformat(p["until"]) if p.get("until") else None,
        options=p.get("options") or {}, job_id=p.get("job_id"), ingest=bool(p.get("ingest")),
        progress=progress,
    )
    after = None
    if auto_analysis.should_run(p.get("job_id"), result.files):
        # After the pull is recorded as done: pattern detection must not hold the pull back or fail it.
        bind, job_id, actor, session_id = db.get_bind(), p["job_id"], task.requested_by, task.session_id
        after = lambda: auto_analysis.run_in_background(bind, job_id, actor, session_id)  # noqa: E731
    return to_out(result).model_dump(mode="json"), after


def run_privacy_scan(db: Session, task: BackgroundTask, progress) -> tuple[dict, None]:
    from app.audit import log_action
    from app.privacy import service as privacy

    p = task_params(task)
    scope = p.get("scope")            # a collection id, "unassigned", or None for every event
    label = scope or "all"
    # Each batch is committed as it goes, so a scan that stops partway has already changed events.
    # Record that it started, and how it ended.
    log_action(db, task.requested_by, "logs_privacy_scan_start", detail=f"job={label}", session_id=task.session_id)
    db.commit()
    try:
        n = privacy.rescan(db, scope, progress=progress)
    except Exception as e:
        db.rollback()
        log_action(db, task.requested_by, "logs_privacy_scan", detail=f"job={label} failed error={type(e).__name__}",
                   session_id=task.session_id)
        db.commit()
        raise
    log_action(db, task.requested_by, "logs_privacy_scan", detail=f"job={label} scanned={n}",
               session_id=task.session_id)
    return {"scanned": n}, None
