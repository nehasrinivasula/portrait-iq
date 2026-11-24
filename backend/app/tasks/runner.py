"""Runs one task from start to finish, the same way in the worker process and inline in tests.

Every step is recorded: started, progress, done or failed, in the task row and in the audit log. A task that
raises is marked failed with a message the person can read; it is never lost silently.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.audit import log_action
from app.models import BackgroundTask
from app.tasks import service

log = logging.getLogger(__name__)

PROGRESS_EVERY_S = 0.5

# A handler does the work and returns (result, after). result is JSON-able and shown to the person; after, if
# given, runs once the task is recorded as done (pattern detection after a pull), and cannot fail the task.
Handler = Callable[[Session, BackgroundTask, "Progress"], tuple[dict, Optional[Callable[[], None]]]]


class Progress:
    """Writes progress to the task row, at most twice a second. Uses the handler's own session: a second session
    would wait for the write lock the handler may hold, and the handler is the one that would have to release it."""

    def __init__(self, db: Session, task_id: str):
        self.db, self.task_id, self._last = db, task_id, 0.0

    def __call__(self, done: int, total: Optional[int] = None, stage: Optional[str] = None) -> None:
        now = time.monotonic()
        finished = total is not None and done >= total
        if not finished and now - self._last < PROGRESS_EVERY_S:
            return
        self._last = now
        values: dict = {"done": done, "updated_at": datetime.utcnow()}
        if total is not None:
            values["total"] = total
        if stage is not None:
            values["stage"] = stage
        self.db.execute(update(BackgroundTask).where(BackgroundTask.id == self.task_id).values(**values))
        self.db.commit()


def handlers() -> dict[str, Handler]:
    from app.tasks import handlers as h
    return {"acquire": h.run_acquire, "privacy_scan": h.run_privacy_scan, "noop": h.run_noop}


def safe_message(exc: BaseException) -> str:
    """Messages the app wrote for the person (WindowError, SourceError) are shown; anything else is the type only."""
    from app.sources.base import SourceError
    from app.sources.service import WindowError
    if isinstance(exc, (SourceError, WindowError)):
        return str(exc)
    return f"Unexpected error ({type(exc).__name__}). The server log has the details."


def execute(bind, task_id: str) -> None:
    """Run the task with this id. Does nothing if it is missing or already started, so a repeated launch is harmless."""
    db = sessionmaker(bind=bind)()
    try:
        task = db.get(BackgroundTask, task_id)
        if task is None or task.status != "queued":
            return
        task.status, task.started_at, task.pid = "running", datetime.utcnow(), os.getpid()
        log_action(db, task.requested_by, "background_task_start", detail=f"task={task.id} kind={task.kind}",
                   session_id=task.session_id)
        db.commit()

        after: Optional[Callable[[], None]] = None
        try:
            handler = handlers().get(task.kind)
            if handler is None:
                raise ValueError(f"no handler for {task.kind}")
            result, after = handler(db, task, Progress(db, task.id))
            import json
            task.status, task.result = "done", json.dumps(result)
            task.finished_at = task.updated_at = datetime.utcnow()
            if task.total is not None:
                task.done = task.total
            log_action(db, task.requested_by, "background_task_done", detail=f"task={task.id} kind={task.kind}",
                       session_id=task.session_id)
            db.commit()
        except Exception as e:  # noqa: BLE001 - the task is the unit of failure; the reason is recorded, not raised
            log.exception("background task %s (%s) failed", task.id, task.kind)
            db.rollback()
            task = db.get(BackgroundTask, task_id)
            service.finish_failed(db, task, safe_message(e))
            after = None
        finally:
            service.remove_scratch(db.get(BackgroundTask, task_id))
        if after is not None:
            try:
                after()
            except Exception:  # noqa: BLE001 - e.g. pattern detection records its own failure
                log.exception("follow-up of task %s failed", task_id)
    finally:
        db.close()
