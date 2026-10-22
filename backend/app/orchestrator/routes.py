"""
HTTP surface. One router, mounted in main.py. Endpoint shapes follow the
prototype's five tabs: Connect, Choose tools, Collect, Insights, Package.
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import log_action
from app.auth import User, get_current_user, is_locked, touch_session
from app.collectors.base import ResourceLimits
from app.config import settings
from app.db import get_db
from app.models import Device, Job, Signal
from app.orchestrator.jobs import create_job, run_job
from app.schemas import ConnectRequest, DeviceOut, JobCreateRequest, JobOut, SignalOut
from app.secrets_loader import redaction_scope

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


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(db: Session = Depends(get_db)):
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
    job = create_job(db, mode=req.mode, tools=req.tools, limits=limits)
    log_action(db, user.id, "job_start", detail=f"job={job.id} tools={req.tools}")

    # Synchronous for the skeleton — see orchestrator/jobs.py's docstring
    # for why this needs to become a background task before it's real.
    try:
        run_job(db, job, target_ip)
    except Exception:
        pass  # job.status is already "failed"; don't crash the request

    return job


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.get("/jobs/{job_id}/findings", response_model=list[SignalOut])
def get_findings(job_id: str, db: Session = Depends(get_db)):
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
