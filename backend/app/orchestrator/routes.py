"""
HTTP surface. One router, mounted in main.py. Endpoint shapes follow the
prototype's five tabs: Connect, Choose tools, Collect, Insights, Package.
"""
import json
import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app import uploads
from app.analysis.coverage import coverage_report
from app.analysis.engine import run_analysis
from app.analysis.findings import findings_for
from app.audit import log_action
from app.auth import User, get_active_user, get_unlocked_user
from app.collectors.base import Collector, PluginError, ResourceLimits
from app.collectors.registry import get_registry
from app.config import settings
from app.connectivity.service import check_connectivity
from app.discovery import service as discovery
from app.db import SessionLocal, get_db
from app.models import Device, Job, Signal
from app.orchestrator.jobs import create_job, read_results, run_job
from app.reporting.summary import build_summary, render_markdown, summary_to_json
from app.schemas import (
    ConnectRequest,
    CoverageOut,
    ConnectivityCheckRequest,
    ConnectivityCheckResponse,
    DeviceOut,
    DeviceUpdate,
    DiscoveredDeviceOut,
    DiscoverRequest,
    DiscoveryOut,
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
    user: User = Depends(get_active_user),
):
    """Run a bounded TCP reachability check before diagnostics start.

    An unreachable result is returned as a successful HTTP response because
    the check itself completed successfully; callers should inspect `status`
    and the per-port results.
    """
    log_action(
        db,
        user.id,
        "connectivity_check",
        detail=json.dumps(
            {"target": req.target, "ports": req.ports, "event": "started"},
            ensure_ascii=True,
        ),
        session_id=user.session_id,
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
            session_id=user.session_id,
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
            session_id=user.session_id,
        )
        return response


@router.post("/connect", response_model=list[DeviceOut])
def connect(
    req: ConnectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_active_user),
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
        log_action(db, user.id, "connect", detail=f"mode={req.mode} ip={req.ip_address}", session_id=user.session_id)

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
def list_devices(db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    log_action(db, user.id, "devices_view", detail="", session_id=user.session_id)
    return db.query(Device).all()


@router.post("/discover", response_model=DiscoveryOut)
def discover(req: DiscoverRequest, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    """Find the hub, viewers and other devices on the system's network (D-3).
    Takes about duration_s plus pod start-up; one search at a time."""
    log_action(db, user.id, "discover", detail=f"duration_s={req.duration_s} sweep_subnet={req.sweep_subnet}")
    try:
        result = discovery.run_discovery(req.duration_s, sweep=req.sweep_subnet)
    except discovery.DiscoveryBusy as e:
        raise HTTPException(409, str(e))
    except PluginError as e:
        raise HTTPException(502, f"Device search failed: {e}")
    rows = discovery.save(db, result)
    log_action(db, user.id, "discover", detail=(
        f"system={result.node_ip} found={len(rows)} "
        f"kinds={sorted({row.kind for row, _ in rows})} traffic={result.captured}"))
    return DiscoveryOut(
        system_ip=result.node_ip, traffic_checked=result.captured, notes=result.notes,
        devices=[DiscoveredDeviceOut(**DeviceOut.model_validate(row).model_dump(), evidence=f.evidence)
                 for row, f in rows],
    )


@router.patch("/devices/{device_id}", response_model=DeviceOut)
def update_device(device_id: str, req: DeviceUpdate, db: Session = Depends(get_db),
                  user: User = Depends(get_active_user)):
    """Correct a discovered name or role (D-7). Later searches keep it."""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "device not found")
    changes = req.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(400, "Give a name or a role to change.")
    if "kind" in changes and "name" not in changes and device.name == discovery.auto_name(device.kind, device.ip_address):
        changes["name"] = discovery.auto_name(changes["kind"], device.ip_address)   # the generated name follows the role
    for key, value in changes.items():
        setattr(device, key, value.strip() if isinstance(value, str) else value)
    device.overridden = 1
    db.commit()
    log_action(db, user.id, "device_update", detail=f"device={device_id} ip={device.ip_address} fields={sorted(changes)}")
    return device


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

    job = create_job(
        db,
        mode=req.mode,
        tools=req.tools,
        limits=limits,
        requested_by=user.id,
        session_id=user.session_id,
    )
    # Option names only, never values: a future plugin option could hold a
    # credential. A plugin lists the values worth auditing (e.g. tshark's
    # full-payload switch and its reason) in audit_fields().
    option_keys = {name: sorted(opts) for name, opts in req.plugin_options.items()}
    audited = {p.name: fields for p in plugins if (fields := p.audit_fields())}
    log_action(
        db, user.id, "job_start",
        detail=f"job={job.id} tools={req.tools} option_keys={option_keys} audited={audited}",
        session_id=user.session_id,
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
def get_job_results(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_unlocked_user)):
    """Each plugin's summary, warnings and files, or the error that stopped
    it, in the order the plugins ran. Partial while the job is running.
    Polled by the page, so it checks the idle lock but is not activity."""
    if db.get(Job, job_id) is None:
        raise HTTPException(404, "job not found")
    log_action(db, user.id, "job_results_view", detail=f"job={job_id}", session_id=user.session_id)
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
    log_action(db, user.id, "upload", detail=f"upload={upload_id} kind={kind} bytes={size}", session_id=user.session_id)
    return UploadOut(upload_id=upload_id, kind=kind, size_bytes=size)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_unlocked_user)):
    """Polled while a job runs: checks the idle lock but is not activity."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    log_action(db, user.id, "job_view", detail=f"job={job_id}", session_id=user.session_id)
    return job


@router.get("/jobs/{job_id}/findings", response_model=list[SignalOut])
def get_findings(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    """The patterns found in this collection's logs, each with its evidence: counts, first and last time,
    the devices involved, and the earliest and latest matching log lines (PHI/PII masked). Returns log
    lines, so a locked session is refused, as for /api/logs."""
    if db.get(Job, job_id) is None:
        raise HTTPException(404, "job not found")
    log_action(db, user.id, "findings_view", detail=f"job={job_id}", session_id=user.session_id)
    return findings_for(db, job_id)


@router.post("/jobs/{job_id}/findings/run", response_model=list[SignalOut])
def run_findings(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    """Run the pattern rules over this collection's stored events now, replacing its earlier findings."""
    if db.get(Job, job_id) is None:
        raise HTTPException(404, "job not found")
    log_action(db, user.id, "findings_run", detail=f"job={job_id}", session_id=user.session_id)
    run_analysis(db, job_id)
    return findings_for(db, job_id)


@router.get("/jobs/{job_id}/findings/coverage", response_model=CoverageOut)
def get_findings_coverage(job_id: str, top: int = Query(20, ge=1, le=100), db: Session = Depends(get_db),
                          user: User = Depends(get_active_user)):
    """What the rules do not cover: the ERROR and WARN events no rule matches, grouped by message shape."""
    if db.get(Job, job_id) is None:
        raise HTTPException(404, "job not found")
    log_action(db, user.id, "findings_coverage_view", detail=f"job={job_id}", session_id=user.session_id)
    return coverage_report(db, job_id, top=top)


@router.get("/jobs/{job_id}/summary")
def get_summary(
    job_id: str,
    format: Literal["md", "json"] = Query("md"),
    mask: bool = Query(True),
    db: Session = Depends(get_db),
    user: User = Depends(get_active_user),
):
    """One readable report for a run: key errors with when and where they
    happened, diagnostics outcome, data completeness and suggested next
    steps. Markdown by default, the same content as JSON with format=json."""
    summary = build_summary(db, job_id, mask=mask)
    if summary is None:
        raise HTTPException(status_code=404, detail="Job not found")
    log_action(db, user.id, "summary_view", detail=f"job={job_id} format={format} mask={mask}",
               session_id=user.session_id)
    if format == "json":
        return summary_to_json(summary)
    return PlainTextResponse(render_markdown(summary), media_type="text/markdown; charset=utf-8")


# Export (POST/GET /jobs/{job_id}/package...) lives in app/packaging/routes.py (SCRUM-61).
