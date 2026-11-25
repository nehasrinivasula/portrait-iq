from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import User, get_unlocked_user
from app.db import get_db
from app.models import BackgroundTask
from app.tasks import service

router = APIRouter()


class TaskOut(BaseModel):
    id: str
    kind: Literal["acquire", "privacy_scan", "noop"]
    status: Literal["queued", "running", "done", "failed", "interrupted"]
    stage: Optional[str] = None
    done: int = 0
    total: Optional[int] = None
    result: Optional[dict[str, Any]] = None      # what the task produced, once done
    error: Optional[str] = None                  # why it did not finish; safe to show
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


def task_out(t: BackgroundTask) -> TaskOut:
    return TaskOut(id=t.id, kind=t.kind, status=t.status, stage=t.stage, done=t.done or 0, total=t.total,
                   result=service.task_result(t), error=t.error, created_at=t.created_at,
                   started_at=t.started_at, finished_at=t.finished_at)


@router.get("", response_model=list[TaskOut])
def list_background_tasks(active: bool = False, kind: Optional[Literal["acquire", "privacy_scan"]] = Query(None),
                          db: Session = Depends(get_db), user: User = Depends(get_unlocked_user)):
    """Recent tasks, newest first; active=true only those still queued or running. The page uses this to pick
    up a scan or pull again after a reload."""
    return [task_out(t) for t in service.list_tasks(db, active_only=active, kind=kind)]


@router.get("/{task_id}", response_model=TaskOut)
def get_background_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_unlocked_user)):
    """Progress and outcome of one task. Polling it does not count as activity, like the other status reads."""
    task = service.get_task(db, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    return task_out(task)
