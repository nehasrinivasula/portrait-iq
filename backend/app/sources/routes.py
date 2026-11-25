"""
HTTP surface for log acquisition (SCRUM-43). Mounted at /api/sources.

  GET  /api/sources                       what sources exist and whether each is usable
  POST /api/sources/{id}/test             reach the source, specific reason on failure
  GET  /api/sources/{id}/files            preview: what is in range, what is already pulled
  POST /api/sources/{id}/acquire          start pulling the new files (and ingesting them) in the background
  POST /api/sources/local/upload          upload files / ZIPs from the browser, then import them in the background
  (both return a task; follow it at GET /api/tasks/{id})
"""
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.audit import log_action
from app.auth import User, get_active_user
from app.config import settings
from app.db import get_db
from app.models import Job
from app.schemas import (
    AcquireRequest, AcquisitionOut, DeviceAddRequest, FileOutcomeOut, KnownDeviceOut, RemoteFileOut,
    SourceOut, SourceStatusOut,
)
from app.tasks import service as tasks
from app.sources import registry
from app.tasks.routes import TaskOut, task_out
from app.validation import ValidationFailure, validate_upload_name
from app.sources.base import ALLOWED_SUFFIXES, LogSource, SourceError
from app.sources.devices import add_manual, display_name, list_known
from app.sources.service import (
    WindowError, AcquisitionResult, clamp_to_source, is_already_acquired, plan_new_windows,
    resolve_window,
)

router = APIRouter()


def _source(source_id: str) -> LogSource:
    source = registry.get_source(source_id)
    if source is None:
        raise HTTPException(404, f"Unknown log source '{source_id}'.")
    return source


def to_out(result: AcquisitionResult) -> AcquisitionOut:
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
        files=[FileOutcomeOut(name=f.name, status=f.status, size=f.size, events=f.events,
                              rows_rejected=f.rows_rejected, reject_reasons=f.reject_reasons, error=f.error)
               for f in result.files],
        notes=result.notes,
    )


def _device_out(d) -> KnownDeviceOut:
    return KnownDeviceOut(device_uuid=d.device_uuid, name=display_name(d), friendly_name=d.friendly_name,
                          host_name=d.host_name, first_seen=d.first_seen, last_seen=d.last_seen,
                          learned_from=d.learned_from)


@router.get("/devices", response_model=list[KnownDeviceOut])
def known_devices(db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    """Devices to pick from for a log pull (SCRUM-70): every device seen in
    acquired logs, plus devices added by hand."""
    log_action(db, user.id, "source_devices_view", detail="", session_id=user.session_id)
    return [_device_out(d) for d in list_known(db)]


@router.post("/devices", response_model=KnownDeviceOut)
def add_device(req: DeviceAddRequest, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    """Add a device the tool hasn't seen in any logs yet, by its ID."""
    try:
        device, created = add_manual(db, req.device_uuid, req.name)
    except SourceError as e:
        raise HTTPException(400, str(e))
    if created:
        log_action(db, user.id, "device_add", detail=f"device={device.device_uuid}", session_id=user.session_id)
    return _device_out(device)


@router.get("", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    out = []
    for s in registry.all_sources():
        status = s.is_configured()
        out.append(SourceOut(
            id=s.id, display_name=s.display_name, needs_network=s.needs_network,
            configured=status.ok, reason=status.reason,
            options=s.options().fields if status.ok else {},
        ))
    log_action(db, user.id, "sources_view", detail="", session_id=user.session_id)
    return out


@router.post("/{source_id}/test", response_model=SourceStatusOut)
def test_source(source_id: str, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    source = _source(source_id)
    status = source.test_connection()
    log_action(db, user.id, "source_test", detail=f"source={source_id} ok={status.ok}", session_id=user.session_id)
    return SourceStatusOut(ok=status.ok, reason=status.reason)


@router.get("/{source_id}/files", response_model=list[RemoteFileOut])
def preview_files(
    source_id: str,
    since: datetime | None = None,
    until: datetime | None = None,
    path: str | None = Query(None, description="Local import only: file or folder to read"),
    device_uuids: str | None = Query(None, description="Log export API only: comma-separated device IDs"),
    db: Session = Depends(get_db),
    user: User = Depends(get_active_user),
):
    source = _source(source_id)
    try:
        since, until = resolve_window(since, until, default_lookback=source.default_lookback)
        until, _ = clamp_to_source(source, since, until)
        opts = {k: v for k, v in {"path": path, "device_uuids": device_uuids}.items() if v}
        remotes = source.list_available(since, until, opts)
    except (WindowError, SourceError) as e:
        raise HTTPException(400, str(e))
    # Same planning acquire() uses, so the preview shows exactly what would be requested.
    # The local path and device IDs may be sensitive operational context, so
    # record only which optional selectors were used, never their values.
    log_action(
        db,
        user.id,
        "source_preview",
        detail=f"source={source_id} selectors={[name for name, value in {'path': path, 'device_uuids': device_uuids}.items() if value]}",
        session_id=user.session_id,
    )
    return [RemoteFileOut(remote_id=r.remote_id, name=r.name, size=r.size, modified_at=r.modified_at,
                          already_acquired=covered or is_already_acquired(db, r))
            for r, covered in plan_new_windows(db, remotes, source)]


def _start(db: Session, source: LogSource, user: User, req: AcquireRequest, *,
           scratch_dirs: tuple[str, ...] = (), extra_params: dict | None = None) -> TaskOut:
    """Check the request, then hand the pull to a background task and return at once.

    Only cheap checks happen here (the run exists, the window makes sense, the source is set up). Listing,
    fetching and ingesting - which can take minutes and a lot of CPU - run in the worker (app/tasks), so the API
    stays responsive. A problem found there fails the task with a message the page shows."""
    if req.job_id and db.get(Job, req.job_id) is None:
        raise HTTPException(404, "job not found")
    try:
        since, until = resolve_window(req.since, req.until, default_lookback=source.default_lookback)
        status = source.is_configured()
        if not status.ok:
            raise SourceError(status.reason)
    except WindowError as e:
        raise HTTPException(422, str(e))
    except SourceError as e:
        raise HTTPException(400, str(e))
    options = dict(req.options)
    if req.device_uuids:
        options["device_uuids"] = ",".join(req.device_uuids)
    params = {
        "source_id": source.id, "since": since.isoformat(), "until": until.isoformat(), "options": options,
        "job_id": req.job_id, "ingest": req.ingest, "scratch_dirs": list(scratch_dirs),
    }
    # Endpoint-owned metadata is kept separate from browser options, so a
    # specialised route can add a truthful audit action without a caller
    # relabelling arbitrary source data.
    if extra_params:
        params.update(extra_params)
    task, _ = tasks.create_task(
        db, kind="acquire", actor=user.id, session_id=user.session_id, scope=source.id,
        params=params,
    )
    return task_out(tasks.submit(db, task))


def _cms_event_category() -> int:
    """Return GE-confirmed CMS category, never a caller-supplied guess."""
    category = settings.axone_cms_event_category
    if category is None:
        raise HTTPException(
            409,
            "CMS collection is not configured. Ask GE to confirm the CMS event-category code, then set AXONE_CMS_EVENT_CATEGORY.",
        )
    return category


@router.post("/axone/cms/acquire", response_model=TaskOut, status_code=202)
def acquire_cms_logs(
    req: AcquireRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_active_user),
):
    """Acquire GE-confirmed CMS rows through the established Axone CSV path.

    The setting is intentionally server-side. It prevents a browser request
    from relabelling an arbitrary event category as CMS, and sends CMS rows
    through the same bounded windows, deduplication, normalization, privacy
    scan and audit trail as the normal Axone export.
    """
    category = _cms_event_category()
    options = dict(req.options)
    options["event_category"] = str(category)
    cms_request = req.model_copy(update={"options": options})
    return _start(
        db, _source("axone"), user, cms_request,
        extra_params={
            "audit_action": "acquire_cms_logs",
            "audit_detail": f"source=axone category={category}",
            "cms_event_category": category,
        },
    )


@router.post("/{source_id}/acquire", response_model=TaskOut, status_code=202)
def acquire_logs(source_id: str, req: AcquireRequest, db: Session = Depends(get_db),
                 user: User = Depends(get_active_user)):
    """Start pulling logs. Returns a task right away; poll GET /api/tasks/{id} for progress and, when it is done,
    the same AcquisitionOut this endpoint used to return, under `result`."""
    return _start(db, _source(source_id), user, req)


@router.post("/local/upload", response_model=TaskOut, status_code=202)
def upload_logs(
    files: list[UploadFile] = File(...),
    job_id: str | None = None,
    ingest: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_active_user),
):
    """Save uploads to a fresh folder, then import that folder through the local source in a background task,
    so uploads get the same dedupe, ZIP checks, limits and audit as everything else.

    Saving the files happens here (it is disk I/O, which does not hold up other requests). Reading, parsing and
    scanning them happens in the worker, which also deletes the folder when it is done."""
    inbox = Path(settings.staging_dir) / "uploads" / uuid.uuid4().hex[:12]
    limit = settings.default_max_output_mb * 1024 * 1024
    total = 0
    try:
        for i, upload in enumerate(files):
            name = Path(upload.filename or "").name
            try:
                validate_upload_name(name, ALLOWED_SUFFIXES, kind="log file")
            except ValidationFailure as e:
                raise HTTPException(400, str(e))
            dest = inbox / f"{i:04d}" / name  # same-named uploads must not overwrite each other
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as out:
                while chunk := upload.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(413, f"Upload is larger than the {settings.default_max_output_mb} MB limit.")
                    out.write(chunk)
        req = AcquireRequest(options={"path": str(inbox)}, job_id=job_id, ingest=ingest)
        return _start(db, _source("local"), user, req, scratch_dirs=(str(inbox),))
    except BaseException:
        # Nothing took over the folder (the task was never created), so it is scratch to remove now.
        shutil.rmtree(inbox, ignore_errors=True)
        raise
