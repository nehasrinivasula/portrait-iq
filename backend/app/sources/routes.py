"""
HTTP surface for log acquisition (SCRUM-43). Mounted at /api/sources.

  GET  /api/sources                       what sources exist and whether each is usable
  POST /api/sources/{id}/test             reach the source, specific reason on failure
  GET  /api/sources/{id}/files            preview: what is in range, what is already pulled
  POST /api/sources/{id}/acquire          pull only the new files (and ingest them)
  POST /api/sources/local/upload          upload files / ZIPs from the browser, then acquire them
"""
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.audit import log_action
from app.auth import User, get_current_user, is_locked, touch_session
from app.config import settings
from app.db import get_db
from app.models import Job
from app.schemas import (
    AcquireRequest, AcquisitionOut, FileOutcomeOut, RemoteFileOut, SourceOut, SourceStatusOut,
)
from app.sources import registry
from app.sources.base import ALLOWED_SUFFIXES, LogSource, SourceError
from app.sources.service import (
    WindowError, AcquisitionResult, acquire, is_already_acquired, resolve_window,
)

router = APIRouter()


def _active_user(user: User = Depends(get_current_user)) -> User:
    if is_locked(user.id):
        raise HTTPException(423, "Session locked. Unlock before acquiring logs.")
    touch_session(user.id)
    return user


def _source(source_id: str) -> LogSource:
    source = registry.get_source(source_id)
    if source is None:
        raise HTTPException(404, f"Unknown log source '{source_id}'.")
    return source


def _to_out(result: AcquisitionResult) -> AcquisitionOut:
    return AcquisitionOut(
        run_id=result.run_id,
        source=result.source,
        since=result.since,
        until=result.until,
        new_files=result.new_files,
        already_acquired=result.count("already_acquired", "duplicate"),
        failed=result.count("failed"),
        over_budget=result.count("over_budget"),
        bytes_staged=result.bytes_staged,
        files=[FileOutcomeOut(name=f.name, status=f.status, size=f.size, events=f.events, error=f.error)
               for f in result.files],
    )


@router.get("", response_model=list[SourceOut])
def list_sources(user: User = Depends(_active_user)):
    out = []
    for s in registry.all_sources():
        status = s.is_configured()
        out.append(SourceOut(
            id=s.id, display_name=s.display_name, needs_network=s.needs_network,
            configured=status.ok, reason=status.reason,
            options=s.options().fields if status.ok else {},
        ))
    return out


@router.post("/{source_id}/test", response_model=SourceStatusOut)
def test_source(source_id: str, db: Session = Depends(get_db), user: User = Depends(_active_user)):
    source = _source(source_id)
    status = source.test_connection()
    log_action(db, user.id, "source_test", detail=f"source={source_id} ok={status.ok}")
    return SourceStatusOut(ok=status.ok, reason=status.reason)


@router.get("/{source_id}/files", response_model=list[RemoteFileOut])
def preview_files(
    source_id: str,
    since: datetime | None = None,
    until: datetime | None = None,
    path: str | None = Query(None, description="Local import only: file or folder to read"),
    db: Session = Depends(get_db),
    user: User = Depends(_active_user),
):
    source = _source(source_id)
    try:
        since, until = resolve_window(since, until, default_lookback=source.default_lookback)
        remotes = source.list_available(since, until, {"path": path} if path else {})
    except (WindowError, SourceError) as e:
        raise HTTPException(400, str(e))
    return [RemoteFileOut(remote_id=r.remote_id, name=r.name, size=r.size, modified_at=r.modified_at,
                          already_acquired=is_already_acquired(db, r)) for r in remotes]


def _run(db: Session, source: LogSource, user: User, req: AcquireRequest) -> AcquisitionOut:
    if req.job_id and db.get(Job, req.job_id) is None:
        raise HTTPException(404, "job not found")
    try:
        result = acquire(db, source, actor=user.id, since=req.since, until=req.until,
                         options=req.options, job_id=req.job_id, ingest=req.ingest)
    except WindowError as e:
        raise HTTPException(422, str(e))
    except SourceError as e:
        raise HTTPException(400, str(e))
    return _to_out(result)


@router.post("/{source_id}/acquire", response_model=AcquisitionOut)
def acquire_logs(source_id: str, req: AcquireRequest, db: Session = Depends(get_db),
                 user: User = Depends(_active_user)):
    return _run(db, _source(source_id), user, req)


@router.post("/local/upload", response_model=AcquisitionOut)
def upload_logs(
    files: list[UploadFile] = File(...),
    job_id: str | None = None,
    ingest: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(_active_user),
):
    """Save uploads to a fresh folder, then acquire that folder through the
    local import source, so uploads get the same dedupe, ZIP checks, limits
    and audit as everything else.

    Deliberately a plain `def`: acquiring and ingesting a big ZIP takes tens
    of seconds of blocking work, and FastAPI runs plain routes in a worker
    thread. As `async def` it would freeze the whole server meanwhile."""
    inbox = Path(settings.staging_dir) / "uploads" / uuid.uuid4().hex[:12]
    limit = settings.default_max_output_mb * 1024 * 1024
    total = 0
    try:
        for i, upload in enumerate(files):
            name = Path(upload.filename or "").name
            if not name or Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
                raise HTTPException(
                    400, f"'{name or 'unnamed'}' is not a supported log file ({', '.join(sorted(ALLOWED_SUFFIXES))})."
                )
            dest = inbox / f"{i:04d}" / name  # same-named uploads must not overwrite each other
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as out:
                while chunk := upload.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(413, f"Upload is larger than the {settings.default_max_output_mb} MB limit.")
                    out.write(chunk)
        req = AcquireRequest(options={"path": str(inbox)}, job_id=job_id, ingest=ingest)
        return _run(db, _source("local"), user, req)
    finally:
        # acquire() copied what it kept into staging; the inbox is scratch.
        shutil.rmtree(inbox, ignore_errors=True)
