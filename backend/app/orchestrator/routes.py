"""
HTTP surface. One router, mounted in main.py. Endpoint shapes follow the
prototype's five tabs: Connect, Choose tools, Collect, Insights, Package.
"""
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app import uploads
from app.audit import log_action
from app.auth import User, get_active_user, get_current_user, is_locked, touch_session
from app.collectors.base import Collector, PluginError, ResourceLimits
from app.collectors.registry import get_registry
from app.config import settings
from app.connectivity.service import check_connectivity
from app.db import SessionLocal, get_db
from app.models import Device, Job, Signal
from app.orchestrator.jobs import create_job, read_results, run_job
from app.schemas import (
    ConnectRequest,
    ConnectivityCheckRequest,
    ConnectivityCheckResponse,
    DeviceOut,
    JobCreateRequest,
    JobOut,
    SignalOut,
    DeviceOut,
    JobCreateRequest,
    JobOut,
    PluginListOut,
    PluginOut,
    SignalOut,
    UploadOut,
)
from app.secrets_loader import redaction_scope

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/connectivity-check", response_model=ConnectivityCheckResponse)
def connectivity_check(
    req: ConnectivityCheckRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Run a bounded TCP reachability check before diagnostics start.

    An unreachable result is returned as a successful HTTP response because
    the check itself completed successfully; callers should inspect `status`
    and the per-port results.
    """
    if is_locked(user.id):
        raise HTTPException(423, "Session locked — unlock before checking connectivity.")

    log_action(
        db,
        user.id,
        "connectivity_check",
        detail=json.dumps(
            {"target": req.target, "ports": req.ports, "event": "started"},
            ensure_ascii=True,
        ),
    )
    try:
        response = check_connectivity(req.target, req.ports, req.timeout_seconds)
    except Exception:
        log_action(
            db,
            user.id,
            "connectivity_check",
            detail=json.dumps(
                {"target": req.target, "ports": req.ports, "event": "failed"},
                ensure_ascii=True,
            ),
        )
        raise
    else:
        log_action(
            db,
            user.id,
            "connectivity_check",
            detail=json.dumps(
                {
                    "target": req.target,
                    "ports": req.ports,
                    "event": "completed",
                    "result": response.status,
                },
                ensure_ascii=True,
            ),
        )
        touch_session(user.id)
        return response


@router.post("/connect", response_model=list[DeviceOut])
def connect(
    req: ConnectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """This week's feature #2 lives here. Today it does nothing real —
    replace the body with an actual reachability check (ping/SNMP/SSH,
    whatever fits your lab) against req.ip_address, and for 'live' mode,
    real discovery of the hub/viewer/server behind it. For 'pre_install'
    mode, this should instead stand this machine up as the test endpoint
    Marco and Andrew described, not probe an installed system.

    The password is masked in logs only while this request runs, then
    forgotten (SCRUM-72). Keep everything that uses it inside the with."""
    with redaction_scope(req.password.get_secret_value(), "CONNECT_PASSWORD"):
        touch_session(user.id)
        log_action(db, user.id, "connect", detail=f"mode={req.mode} ip={req.ip_address}")

        if req.mode not in ("live", "pre_install"):
            raise HTTPException(400, "mode must be 'live' or 'pre_install'")

        raise HTTPException(
            501,
            "TODO: implement real discovery. See app/orchestrator/routes.py::connect "
            "and app/collectors/base.py.",
        )


@router.get("/plugins", response_model=PluginListOut)
def list_plugins():
    """Every registered plugin, for the tool-selection screen (SCRUM-40).
    Comes straight from the registry, so a new plugin shows up here
    without any change to this file."""
    reg = get_registry()
    return PluginListOut(
        plugins=[
            PluginOut(name=p.name, display_name=p.display_name or p.name, description=p.description)
            for p in reg.all()
        ],
        load_errors=reg.load_errors,
    )


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Device).all()


@router.post("/jobs", response_model=JobOut)
def start_job(
    req: JobCreateRequest,
    target_ip: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_active_user),
):
    """Validates the selection, creates the job and returns it "queued".
    The job runs after the response is sent; poll GET /jobs/{id}. It keeps
    running if the UI session locks (S-2)."""

    limits = ResourceLimits(
        cpu_pct=req.cpu_limit_pct or settings.default_cpu_limit_pct,
        mem_mb=req.mem_limit_mb or settings.default_mem_limit_mb,
        max_output_mb=req.max_output_mb or settings.default_max_output_mb,
    )

    # Configure every selected plugin before creating the job: configure()
    # has no side effects, so an unknown plugin or bad option is a 400 here
    # instead of a half-started job. These configured instances are the ones
    # the job runs; they are not configured again.
    try:
        plugins = get_registry().create_all(req.tools, limits, req.plugin_options)
    except PluginError as e:
        raise HTTPException(400, str(e))

    job = create_job(db, mode=req.mode, tools=req.tools, limits=limits)
    # Option names only, never values: a future plugin option could hold a
    # credential. A plugin lists the values worth auditing (e.g. tshark's
    # full-payload switch and its reason) in audit_fields().
    option_keys = {name: sorted(opts) for name, opts in req.plugin_options.items()}
    audited = {p.name: fields for p in plugins if (fields := p.audit_fields())}
    log_action(
        db, user.id, "job_start",
        detail=f"job={job.id} tools={req.tools} option_keys={option_keys} audited={audited}",
    )

    background_tasks.add_task(_run_in_background, job.id, target_ip, plugins)
    return job


def _run_in_background(job_id: str, target_ip: str, plugins: list[Collector]) -> None:
    """Runs after the response, with its own session: the request's session
    is closed by then."""
    db = SessionLocal()
    try:
        run_existing_job(db, job_id, target_ip, plugins)
    finally:
        db.close()


def run_existing_job(db: Session, job_id: str, target_ip: str, plugins: list[Collector]) -> None:
    try:
        run_job(db, db.get(Job, job_id), target_ip, plugins=plugins)
    except Exception:
        # job.status is already "failed" or "cleanup_failed".
        log.exception("job %s stopped with an unexpected error", job_id)


@router.get("/jobs/{job_id}/results")
def get_job_results(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Each plugin's summary, warnings and files, or the error that stopped
    it, in the order the plugins ran. Partial while the job is running."""
    if db.get(Job, job_id) is None:
        raise HTTPException(404, "job not found")
    return read_results(job_id)


@router.post("/uploads", response_model=UploadOut)
def upload_file(
    kind: str,
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(get_active_user),
):
    """A file a field engineer already has, e.g. a capture from hospital IT
    (kind=pcap) for tshark's import mode. Returns the upload_id to pass in
    the plugin's options."""
    try:
        upload_id, size = uploads.save(file.file, kind)
    except uploads.UploadError as e:
        raise HTTPException(400, str(e))
    # Never the client's file name: it can name a patient.
    log_action(db, user.id, "upload", detail=f"upload={upload_id} kind={kind} bytes={size}")
    return UploadOut(upload_id=upload_id, kind=kind, size_bytes=size)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.get("/jobs/{job_id}/findings", response_model=list[SignalOut])
def get_findings(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    signals = db.query(Signal).filter(Signal.job_id == job_id).all()
    out = []
    for s in signals:
        out.append(
            SignalOut(
                id=s.id,
                rule_id=s.rule_id,
                title=s.title,
                severity=s.severity,
                count=s.count,
                status=s.status,
                manual_ref=s.manual_ref,
                actions=json.loads(s.actions_json or "[]"),
            )
        )
    return out


@router.post("/jobs/{job_id}/package")
def package_job(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """TODO: assemble the standardized zip (logs/, network/, k8s/, grafana/,
    metadata.json, audit.log — see requirements doc's Analysis & Reporting
    section) and return a download URL. Stub for now."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    log_action(db, user.id, "package_download", detail=f"job={job_id}")
    raise HTTPException(501, "TODO: implement package assembly.")
