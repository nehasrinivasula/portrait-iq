"""
HTTP surface. One router, mounted in main.py. Endpoint shapes follow the
prototype's five tabs: Connect, Choose tools, Collect, Insights, Package.
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import log_action
from app.auth import User, get_current_user, is_locked, touch_session
from app.collectors.base import PluginError, ResourceLimits
from app.collectors.registry import get_registry
from app.config import settings
from app.db import get_db
from app.models import Device, Job, Signal
from app.orchestrator.jobs import create_job, run_job
from app.schemas import (
    ConnectRequest,
    DeviceOut,
    JobCreateRequest,
    JobOut,
    PluginListOut,
    PluginOut,
    SignalOut,
)

router = APIRouter()


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
    Marco and Andrew described, not probe an installed system."""
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
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if is_locked(user.id):
        raise HTTPException(423, "Session locked — unlock before starting a collection.")

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

    # Synchronous for the skeleton — see orchestrator/jobs.py's docstring
    # for why this needs to become a background task before it's real.
    try:
        run_job(db, job, target_ip, plugins=plugins)
    except Exception:
        pass  # job.status is already "failed" or "cleanup_failed"; don't crash the request

    return job


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
