"""Starts the worker process for a task.

Why a separate process: the PHI/PII scan and log parsing are CPU-bound Python. In a thread they share the API's
GIL, and one long regex call can hold it for seconds, so /health misses the liveness probe and Kubernetes
restarts the pod mid-job (the 2026-11-03 bench incident). A process has its own GIL, and runs at lower CPU
priority, so the API stays responsive however busy the scan is.

The backend only supervises: one small thread per task waits for its turn (at most
settings.background_task_concurrency run at once; the rest stay "queued"), starts
`python -m app.tasks.worker <task id>`, and waits. If the worker dies without finishing the task, the task is
marked failed here, so it is never left "running" forever.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading

from sqlalchemy.orm import sessionmaker

from app.config import settings

log = logging.getLogger(__name__)

_slots = threading.BoundedSemaphore(max(1, settings.background_task_concurrency))


def command(task_id: str) -> list[str]:
    return [sys.executable, "-m", "app.tasks.worker", task_id]


def launch(task_id: str) -> threading.Thread:
    t = threading.Thread(target=_supervise, args=(task_id,), name=f"task-{task_id}", daemon=True)
    t.start()
    return t


def _supervise(task_id: str) -> None:
    with _slots:                                   # waits here while another task runs; the row says "queued"
        code = None
        try:
            # stdout/stderr are inherited, so the worker's log lines land in the pod log with the API's.
            proc = subprocess.Popen(command(task_id))
            code = proc.wait()
        except Exception:  # noqa: BLE001
            log.exception("could not run the worker for task %s", task_id)
    _after_exit(task_id, code)


def _after_exit(task_id: str, code) -> None:
    """The worker marks its own task done or failed. If it is still queued or running, the worker died."""
    from app.db import engine
    from app.models import BackgroundTask
    from app.tasks import service

    db = sessionmaker(bind=engine)()
    try:
        task = db.get(BackgroundTask, task_id)
        if task is not None and task.status in service.ACTIVE:
            service.remove_scratch(task)
            why = "The worker could not be started." if code is None else f"The worker stopped unexpectedly (exit code {code})."
            service.finish_failed(db, task, why)
    except Exception:  # noqa: BLE001
        log.exception("could not record the end of task %s", task_id)
        db.rollback()
    finally:
        db.close()
