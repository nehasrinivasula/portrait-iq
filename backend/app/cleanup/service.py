"""Cleanup choice at session end and after a crash.

What can be left behind, per job:
  - tool pods in our namespace (label portrait-iq/job);
  - the output folder of a run that did not end cleanly (interrupted or
    cleanup_failed), which holds its partial results.
The folder of a finished run is a result, not a leftover, and is never listed
or removed here.

The engineer chooses to remove or keep each item. A kept item stays in the
list marked as kept and is not asked about again. Pods of a run that is still
going are in use: they are listed but never removed.

State after the choice:
  clean      nothing is left
  preserved  only items the engineer chose to keep remain
  busy       a run is still in progress
  pending    leftovers wait for a choice
  unchecked  the cluster could not be reached, so pods are unknown
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.audit import log_action
from app.collectors import k8s
from app.collectors.base import reason_for
from app.cleanup.files import MAX_ITEMS, CleanupError, folder_stats, human_size, parse_ids, safe_job_dir  # noqa: F401
from app.models import CleanupDecision, Job

log = logging.getLogger(__name__)

LEFTOVER_JOB_STATUSES = ("interrupted", "cleanup_failed")
ACTIVE_JOB_STATUSES = ("queued", "running")


@dataclass
class Item:
    id: str
    kind: str                  # "pod" | "files"
    job_id: str
    label: str
    detail: str
    kept: bool = False
    active: bool = False       # belongs to a run still in progress: cannot be removed


@dataclass
class JobGroup:
    job_id: str
    status: str | None         # the run's status, None for a pod no run of ours owns
    items: list[Item] = field(default_factory=list)


@dataclass
class Status:
    state: str
    pods_error: str | None
    groups: list[JobGroup]
    interrupted_runs: int

    @property
    def items(self) -> list[Item]:
        return [i for g in self.groups for i in g.items]

    @property
    def pending(self) -> list[Item]:
        return [i for i in self.items if not i.kept and not i.active]


def current_status(db: Session) -> Status:
    kept = {d.item_id for d in db.query(CleanupDecision).all()}
    jobs = {j.id: j for j in db.query(Job).all()}
    groups: dict[str, JobGroup] = {}

    def group(job_id: str) -> JobGroup:
        if job_id not in groups:
            j = jobs.get(job_id)
            groups[job_id] = JobGroup(job_id, j.status if j else None)
        return groups[job_id]

    pods_error = None
    try:
        pods = k8s.get_pod_runner().list_tool_pods()
    except Exception as e:  # noqa: BLE001  no cluster (a laptop) or no permission
        pods, pods_error = [], reason_for(e)
    for p in pods:
        job = jobs.get(p.job_id)
        active = bool(job and job.status in ACTIVE_JOB_STATUSES)
        iid = f"pod:{p.name}"
        group(p.job_id).items.append(Item(
            id=iid, kind="pod", job_id=p.job_id, label=p.name,
            detail=f"{p.tool} tool pod, {p.phase.lower()}", kept=iid in kept, active=active,
        ))

    interrupted = 0
    for job in jobs.values():
        if job.status == "interrupted":
            interrupted += 1
        if job.status not in LEFTOVER_JOB_STATUSES:
            continue
        folder = safe_job_dir(job.id)
        if folder is None:
            continue
        count, size = folder_stats(folder)
        if count == 0:
            continue
        iid = f"files:{job.id}"
        group(job.id).items.append(Item(
            id=iid, kind="files", job_id=job.id, label="Collected files",
            detail=f"{count} file{'s' if count != 1 else ''}, {human_size(size)}", kept=iid in kept,
        ))

    ordered = sorted(groups.values(), key=lambda g: g.job_id)
    status = Status("clean", pods_error, ordered, interrupted)
    status.state = _state(status, db)
    return status


def _state(s: Status, db: Session) -> str:
    if s.pending:
        return "pending"
    if any(i.active for i in s.items) or db.query(Job).filter(Job.status.in_(ACTIVE_JOB_STATUSES)).count():
        return "busy"
    if s.pods_error:
        return "unchecked"
    return "preserved" if s.items else "clean"


@dataclass
class Outcome:
    id: str
    result: str       # removed | kept | skipped | failed
    reason: str | None = None


def apply_choice(db: Session, *, actor: str, session_id: str | None, action: str, ids: list[str],
                 context: str) -> tuple[list[Outcome], Status]:
    """Carry out remove or keep for the chosen items, audit it, and return what
    happened with the state afterwards. Items are matched against what exists
    right now, so a stale or invented id can never delete anything."""
    ids = parse_ids(ids)
    before = {i.id: i for i in current_status(db).items}
    outcomes: list[Outcome] = []
    for iid in ids:
        item = before.get(iid)
        if item is None:
            outcomes.append(Outcome(iid, "skipped", "Already gone."))
        elif item.active:
            outcomes.append(Outcome(iid, "skipped", "A run is still using it."))
        elif action == "keep":
            db.merge(CleanupDecision(item_id=iid, choice="keep", decided_by=actor, session_id=session_id))
            outcomes.append(Outcome(iid, "kept"))
        else:
            outcomes.append(_remove(db, item))
    db.commit()

    after = current_status(db)
    names = lambda r: ",".join(o.id for o in outcomes if o.result == r) or "-"  # noqa: E731
    detail = (f"context={context} action={action} removed={names('removed')} kept={names('kept')} "
              f"skipped={names('skipped')} failed={names('failed')} state={after.state}")
    log_action(db, actor, "cleanup_choice", detail=detail[:1500], session_id=session_id)
    return outcomes, after


def _remove(db: Session, item: Item) -> Outcome:
    try:
        if item.kind == "pod":
            k8s.get_pod_runner().delete(item.id.split(":", 1)[1])
        else:
            folder = safe_job_dir(item.job_id)
            if folder is not None:
                shutil.rmtree(folder)
    except Exception as e:  # noqa: BLE001
        log.warning("cleanup of %s failed: %s", item.id, e)
        return Outcome(item.id, "failed", reason_for(e))
    d = db.get(CleanupDecision, item.id)
    if d is not None:
        db.delete(d)
    return Outcome(item.id, "removed")
