"""
/api/jobs/{id}/package: export a set of logs as one zip (SCRUM-61). {id} is a
run's job id, "unassigned" for logs imported without a run, or "all" for
every log on this machine, so there is always something to export.

    GET  .../package/preview    what an export would hold, and the PHI/PII check
    POST .../package            start an export (masked unless mask=false); 202
    GET  .../package            progress of the latest export, step by step (poll this)
    GET  .../package/download   the zip

/api/exports: every export on this machine, each kept until deleted (local disk is the limit).

    GET    /exports                 history, newest first
    POST   /exports                 start one: {"runs": [...]} (two or more go in one zip) or {"scope": "unassigned" | "all"}
    GET    /exports/{id}            progress (poll this)
    GET    /exports/{id}/download   the zip
    DELETE /exports/{id}            remove the zip and its folder
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
from app.models import Signal
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
    started_at: Optional[str] = None     # UTC ISO
    finished_at: Optional[str] = None


class ContentOut(BaseModel):
    path: str
    description: str
    files: int
    events: Optional[int] = None
    masked: Optional[bool] = None


class PackageOut(BaseModel):
    export_id: str
    job_id: str                      # the set of logs: a run id, "unassigned", "all" or a set id
    scope: str                       # run | imported | all | runs
    title: str
    runs: list[str] = []             # a set's run ids
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


class PreviewSystemOut(BaseModel):
    key: str
    label: str
    available: bool                  # an export can collect it now
    needed: Optional[str] = None     # what is missing, when it cannot be


class PreviewOut(BaseModel):
    job_id: str
    scope: str                       # run | imported | all
    title: str                       # "Run", "Imported logs" or "All logs"
    job_status: Optional[str]        # None unless a run
    finished: bool
    events: int
    tools: list[PreviewToolOut]
    system: list[PreviewSystemOut]
    findings: int
    privacy: PreviewPrivacyOut
    warnings: list[str]
    steps: list[StepOut]             # what the export will do, all pending
    package: Optional[PackageOut] = None   # the last export, if any


class StartRequest(BaseModel):
    mask: bool = True


class ExportRequest(BaseModel):
    runs: list[str] = []
    scope: Optional[str] = None      # "unassigned" or "all", instead of runs
    mask: bool = True


class ExportListOut(BaseModel):
    exports: list[PackageOut]
    total_bytes: int                 # disk used by every zip listed


def _out(s: service.PackageStatus) -> PackageOut:
    return PackageOut(
        export_id=s.id, job_id=s.job_id, scope=service.scope_kind(s.job_id), title=service.scope_title(s.job_id), runs=s.runs,
        state=s.state, masked=s.masked, partial=s.partial, requested_by=s.requested_by,
        started_at=s.started_at, finished_at=s.finished_at,
        steps=[StepOut(id=x.id, label=x.label, status=x.status, detail=x.detail,
                       issues=[IssueOut(item=i.item, reason=i.reason) for i in x.issues],
                       started_at=x.started_at, finished_at=x.finished_at) for x in s.steps],
        file_name=s.file_name, size_bytes=s.size_bytes, sha256=s.sha256,
        contents=[ContentOut(**c.__dict__) for c in s.contents], warnings=s.warnings, error=s.error,
        downloadable=service.zip_path(s) is not None,
    )


def _job(db: Session, job_id: str):
    """The run behind the id, or None for imported / all logs. 404 for an unknown run."""
    try:
        return service.find_job(db, job_id)
    except LookupError:
        raise HTTPException(404, "Run not found.") from None


@router.get("/jobs/{job_id}/package/preview", response_model=PreviewOut)
def preview(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    job = _job(db, job_id)
    r = service.readiness(db, job_id)
    tools = []
    for res in (read_results(job_id) if job else []):
        err = res.get("error")
        tools.append(PreviewToolOut(tool=str(res.get("tool", "unknown")), ok=err is None,
                                    files=len(res.get("artifact_paths", [])), error=err))
    if job is not None:
        findings = db.query(Signal).filter(Signal.job_id == job_id).count()
    else:
        findings = db.query(Signal).count() if job_id == service.ALL else 0
    last = service.load_status(job_id)
    log_action(db, user.id, "package_preview", detail=f"job={job_id}", session_id=user.session_id)
    return PreviewOut(
        job_id=job_id, scope=service.scope_kind(job_id), title=service.scope_title(job_id),
        job_status=job.status if job else None, finished=job is None or job.status in service.FINISHED, events=r.events,
        tools=tools, system=[PreviewSystemOut(**x) for x in service.system_preview(db, job)], findings=findings,
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
    log_action(db, user.id, "package_create", detail=f"job={job_id} export={status.id} masked={req.mask}",
               session_id=user.session_id)
    background_tasks.add_task(_build_in_background, status.id, user.id, user.session_id)
    return _out(status)


def _build_in_background(export_id: str, actor: str, session_id: Optional[str]) -> None:
    db = SessionLocal()
    try:
        s = service.build(db, export_id)
        failed = [x.id for x in s.steps if x.status == "failed"]
        log_action(db, actor, "package_ready" if s.state == "ready" else "package_failed",
                   detail=f"job={s.job_id} export={s.id} runs={json.dumps(s.runs)} state={s.state} "
                          f"failed_steps={json.dumps(failed)} bytes={s.size_bytes}",
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


# --- /exports: history, sets of runs, delete --------------------------------------

def _export_or_404(export_id: str) -> service.PackageStatus:
    status = service.load_export(export_id)
    if status is None:
        raise HTTPException(404, "Export not found.")
    return status


@router.get("/exports", response_model=ExportListOut)
def list_exports(db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    found = service.list_exports()
    return ExportListOut(exports=[_out(s) for s in found],
                         total_bytes=sum(s.size_bytes or 0 for s in found if service.zip_path(s)))


@router.post("/exports", response_model=PackageOut, status_code=202)
def start_export(req: ExportRequest, background_tasks: BackgroundTasks,
                 db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    """One run, a set of runs (one zip), the imported logs or all logs. Poll GET /exports/{id}."""
    if req.scope is not None and req.runs:
        raise HTTPException(422, "Send runs or a scope, not both.")
    if req.scope is None and not req.runs:
        raise HTTPException(422, "Choose at least one run, the imported logs or all logs.")
    if req.scope is not None and req.scope not in (service.UNASSIGNED, service.ALL):
        raise HTTPException(422, "Scope must be the imported logs or all logs.")
    if len(req.runs) > 200:
        raise HTTPException(422, "Choose at most 200 runs at a time.")
    try:
        if len(req.runs) > 1:
            status = service.start_set(db, req.runs, masked=req.mask, requested_by=user.id)
        else:
            scope = req.scope or req.runs[0]
            _job(db, scope)
            status = service.start(db, scope, masked=req.mask, requested_by=user.id)
    except LookupError:
        raise HTTPException(404, "Run not found.") from None
    except service.PackageError as e:
        raise HTTPException(409, str(e))
    log_action(db, user.id, "package_create",
               detail=f"job={status.job_id} export={status.id} runs={json.dumps(status.runs)} masked={req.mask}",
               session_id=user.session_id)
    background_tasks.add_task(_build_in_background, status.id, user.id, user.session_id)
    return _out(status)


@router.get("/exports/{export_id}", response_model=PackageOut)
def export_status(export_id: str, user: User = Depends(get_unlocked_user)):
    """Polled by the export page, so it does not count as activity (S-2)."""
    return _out(_export_or_404(export_id))


@router.get("/exports/{export_id}/download")
def export_download(export_id: str, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    status = _export_or_404(export_id)
    path = service.zip_path(status)
    if path is None:
        raise HTTPException(404, "This export has no finished zip to download.")
    log_action(db, user.id, "package_download",
               detail=f"job={status.job_id} export={status.id} file={status.file_name} sha256={status.sha256}",
               session_id=user.session_id)
    return FileResponse(path, media_type="application/zip", filename=status.file_name)


@router.delete("/exports/{export_id}")
def delete_export(export_id: str, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    try:
        status = service.delete_export(export_id)
    except LookupError:
        raise HTTPException(404, "Export not found.") from None
    except service.PackageError as e:
        raise HTTPException(409, str(e))
    log_action(db, user.id, "package_delete",
               detail=f"job={status.job_id} export={status.id} file={status.file_name} bytes={status.size_bytes}",
               session_id=user.session_id)
    return {"deleted": status.id}
