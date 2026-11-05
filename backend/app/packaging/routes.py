"""
/api/jobs/{id}/package: export a finished run as one zip (SCRUM-61).

    GET  .../package/preview    what an export would hold, and the PHI/PII check
    POST .../package            start an export (masked unless mask=false); 202
    GET  .../package            progress of the latest export, step by step (poll this)
    GET  .../package/download   the zip
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit import log_action
from app.auth import User, get_active_user, get_unlocked_user
from app.db import SessionLocal, get_db
from app.models import Job, Signal
from app.orchestrator.jobs import read_results
from app.packaging import service

router = APIRouter()


class IssueOut(BaseModel):
    item: str
    reason: str


class StepOut(BaseModel):
    id: str
    label: str
    status: str
    detail: Optional[str] = None
    issues: list[IssueOut] = []


class ContentOut(BaseModel):
    path: str
    description: str
    files: int
    events: Optional[int] = None
    masked: Optional[bool] = None


class PackageOut(BaseModel):
    job_id: str
    state: str                       # preparing | ready | failed | interrupted
    masked: bool
    partial: bool                    # ready, but a step failed: the zip is missing something
    requested_by: str
    started_at: str
    finished_at: Optional[str] = None
    steps: list[StepOut]
    file_name: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    contents: list[ContentOut] = []
    warnings: list[str] = []
    error: Optional[str] = None
    downloadable: bool = False


class PreviewToolOut(BaseModel):
    tool: str
    ok: bool
    files: int
    error: Optional[str] = None


class PreviewPrivacyOut(BaseModel):
    complete_for_packaging: bool
    all_scanned: bool
    ingest_complete: bool
    flagged_events: int
    unscanned_events: int


class PreviewOut(BaseModel):
    job_id: str
    job_status: str
    finished: bool
    events: int
    tools: list[PreviewToolOut]
    findings: int
    privacy: PreviewPrivacyOut
    warnings: list[str]
    steps: list[StepOut]             # what the export will do, all pending
    package: Optional[PackageOut] = None   # the last export, if any


class StartRequest(BaseModel):
    mask: bool = True


def _out(s: service.PackageStatus) -> PackageOut:
    return PackageOut(
        job_id=s.job_id, state=s.state, masked=s.masked, partial=s.partial, requested_by=s.requested_by,
        started_at=s.started_at, finished_at=s.finished_at,
        steps=[StepOut(id=x.id, label=x.label, status=x.status, detail=x.detail,
                       issues=[IssueOut(item=i.item, reason=i.reason) for i in x.issues]) for x in s.steps],
        file_name=s.file_name, size_bytes=s.size_bytes, sha256=s.sha256,
        contents=[ContentOut(**c.__dict__) for c in s.contents], warnings=s.warnings, error=s.error,
        downloadable=service.zip_path(s) is not None,
    )


def _job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Run not found.")
    return job


@router.get("/jobs/{job_id}/package/preview", response_model=PreviewOut)
def preview(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    job = _job(db, job_id)
    r = service.readiness(db, job_id)
    tools = []
    for res in read_results(job_id):
        err = res.get("error")
        tools.append(PreviewToolOut(tool=str(res.get("tool", "unknown")), ok=err is None,
                                    files=len(res.get("artifact_paths", [])), error=err))
    findings = db.query(Signal).filter(Signal.job_id == job_id).count()
    last = service.load_status(job_id)
    log_action(db, user.id, "package_preview", detail=f"job={job_id}", session_id=user.session_id)
    return PreviewOut(
        job_id=job_id, job_status=job.status, finished=job.status in service.FINISHED, events=r.events,
        tools=tools, findings=findings,
        privacy=PreviewPrivacyOut(complete_for_packaging=r.complete_for_packaging, all_scanned=r.all_scanned,
                                  ingest_complete=r.ingest_complete, flagged_events=r.flagged_events,
                                  unscanned_events=r.unscanned_events),
        warnings=r.warnings,
        steps=[StepOut(id=i, label=label, status="pending") for i, label in service.STEPS],
        package=_out(last) if last else None,
    )


@router.post("/jobs/{job_id}/package", response_model=PackageOut, status_code=202)
def start_package(job_id: str, background_tasks: BackgroundTasks, req: StartRequest = StartRequest(),
                  db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    """Starts the export and returns at once; poll GET .../package for progress."""
    _job(db, job_id)
    try:
        status = service.start(db, job_id, masked=req.mask, requested_by=user.id)
    except service.PackageError as e:
        raise HTTPException(409, str(e))
    log_action(db, user.id, "package_create", detail=f"job={job_id} masked={req.mask}", session_id=user.session_id)
    background_tasks.add_task(_build_in_background, job_id, user.id, user.session_id)
    return _out(status)


def _build_in_background(job_id: str, actor: str, session_id: Optional[str]) -> None:
    db = SessionLocal()
    try:
        s = service.build(db, job_id)
        failed = [x.id for x in s.steps if x.status == "failed"]
        log_action(db, actor, "package_ready" if s.state == "ready" else "package_failed",
                   detail=f"job={job_id} state={s.state} failed_steps={json.dumps(failed)} bytes={s.size_bytes}",
                   session_id=session_id)
    finally:
        db.close()


@router.get("/jobs/{job_id}/package", response_model=PackageOut)
def package_status(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_unlocked_user)):
    """Polled by the export page, so it does not count as activity (S-2)."""
    _job(db, job_id)
    status = service.load_status(job_id)
    if status is None:
        raise HTTPException(404, "This run has not been exported yet.")
    return _out(status)


@router.get("/jobs/{job_id}/package/download")
def download(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    _job(db, job_id)
    status = service.load_status(job_id)
    path = service.zip_path(status) if status else None
    if path is None:
        raise HTTPException(404, "There is no finished export of this run to download.")
    log_action(db, user.id, "package_download",
               detail=f"job={job_id} file={status.file_name} sha256={status.sha256}", session_id=user.session_id)
    return FileResponse(path, media_type="application/zip", filename=status.file_name)
