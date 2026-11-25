"""Background tasks: create one, hand it to the worker, read it back, clean up after a restart.

A request that would take long (a log pull with ingest, a PHI/PII scan) does only the cheap checks, creates a
row here and returns its id. The work itself runs in a separate process (app/tasks/launcher.py), at lower
priority, so the API keeps answering /health and every other request however busy the scan is.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.audit import log_action
from app.config import settings
from app.models import BackgroundTask

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
KINDS = ("acquire", "privacy_scan", "noop")
RECENT_LIMIT = 20


def create_task(db: Session, *, kind: str, actor: str, session_id: Optional[str], params: dict[str, Any],
                scope: Optional[str] = None, reuse_active: bool = False) -> tuple[BackgroundTask, bool]:
    """Create a queued task. With reuse_active, an identical task (same kind and scope) that is still queued or
    running is returned instead, so a double click starts one scan, not two. Returns (task, created)."""
    if kind not in KINDS:
        raise ValueError(f"unknown task kind {kind!r}")
    if reuse_active and scope is not None:
        existing = (db.query(BackgroundTask)
                    .filter(BackgroundTask.kind == kind, BackgroundTask.scope == scope,
                            BackgroundTask.status.in_(ACTIVE))
                    .order_by(BackgroundTask.created_at).first())
        if existing is not None:
            return existing, False
    task = BackgroundTask(kind=kind, requested_by=actor, session_id=session_id, scope=scope,
                          params=json.dumps(params), status="queued")
    db.add(task)
    db.commit()
    return task, True


def submit(db: Session, task: BackgroundTask) -> BackgroundTask:
    """Start it. Process mode returns at once and the worker does the rest; inline mode (tests, debugging)
    runs it to the end before returning. Either way the task is re-read, so the caller sees its state."""
    from app.tasks import launcher, runner
    if settings.background_task_mode == "inline":
        runner.execute(db.get_bind(), task.id)
    else:
        launcher.launch(task.id)
    db.refresh(task)
    return task


def get_task(db: Session, task_id: str) -> Optional[BackgroundTask]:
    return db.get(BackgroundTask, task_id)


def list_tasks(db: Session, *, active_only: bool = False, kind: Optional[str] = None) -> list[BackgroundTask]:
    q = db.query(BackgroundTask)
    if kind:
        q = q.filter(BackgroundTask.kind == kind)
    if active_only:
        q = q.filter(BackgroundTask.status.in_(ACTIVE))
    return q.order_by(BackgroundTask.created_at.desc()).limit(RECENT_LIMIT).all()


def task_params(task: BackgroundTask) -> dict[str, Any]:
    try:
        return json.loads(task.params or "{}")
    except ValueError:
        return {}


def task_result(task: BackgroundTask) -> Optional[dict[str, Any]]:
    if not task.result:
        return None
    try:
        return json.loads(task.result)
    except ValueError:
        return None


def finish_failed(db: Session, task: BackgroundTask, message: str, *, status: str = "failed") -> None:
    """Record that a task did not finish and say so in the audit log. Never raises past the caller's cleanup."""
    task.status = status
    task.error = message[:500]
    task.finished_at = datetime.utcnow()
    task.updated_at = task.finished_at
    log_action(db, task.requested_by, "background_task_failed",
               detail=f"task={task.id} kind={task.kind} status={status} error={message[:200]}",
               session_id=task.session_id)
    db.commit()


def recover_interrupted_tasks(db: Session) -> int:
    """At start-up: anything still queued or running belongs to a process that no longer exists (the worker is a
    child of the backend and dies with it). Mark it interrupted, audit it, and delete the scratch upload folder
    it was going to read. Work that was committed stays; a scan or pull can simply be run again."""
    stale = db.query(BackgroundTask).filter(BackgroundTask.status.in_(ACTIVE)).all()
    for task in stale:
        _remove_scratch(task)
        finish_failed(db, task, "The backend restarted before this finished. Run it again to continue.",
                      status="interrupted")
    if stale:
        log.warning("%d background task(s) were interrupted by a restart", len(stale))
    return len(stale)


def remove_scratch(task: BackgroundTask) -> None:
    _remove_scratch(task)


def _remove_scratch(task: BackgroundTask) -> None:
    """Delete the upload folder a task was given, but only if it really is one of ours inside staging/uploads."""
    base = (Path(settings.staging_dir) / "uploads").resolve()
    for raw in task_params(task).get("scratch_dirs", []):
        try:
            path = Path(raw).resolve()
        except OSError:
            continue
        if path.parent == base and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
