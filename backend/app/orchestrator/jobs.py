"""
Job state machine. Deliberately synchronous/in-request for the skeleton —
that's wrong for a real collection run (which should be a background task
so the frontend can poll status without holding a connection open), but
right for a skeleton where every collector is a stub anyway. Swap in
FastAPI's BackgroundTasks or a real task queue (Celery/RQ) when the
collectors do real, slow work.

STEPS mirrors the prototype's collect screen and the requirements doc's
progress/notify requirements ("monitor progress in real time", "notify
when complete or if any step fails").
"""
import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.analysis.engine import run_analysis
from app.collectors.base import ResourceLimits
from app.collectors.hub_import import HubImportCollector
from app.collectors.netshoot import NetshootCollector
from app.collectors.rti import RtiCollector
from app.collectors.tshark import TsharkCollector
from app.config import settings
from app.models import Job

STEPS = [
    "deploy_tools",
    "collect_server_logs",
    "collect_network_diagnostics",
    "collect_grafana_health",
    "cleanup",
    "package",
]

COLLECTOR_REGISTRY = {
    "netshoot": NetshootCollector,
    "rti": RtiCollector,
    "tshark": TsharkCollector,
    "hub_import": HubImportCollector,
}


def create_job(db: Session, mode: str, tools: list[str], limits: ResourceLimits) -> Job:
    job = Job(
        mode=mode,
        status="queued",
        tools=json.dumps(tools),
        cpu_limit_pct=limits.cpu_pct,
        mem_limit_mb=limits.mem_mb,
        max_output_mb=limits.max_output_mb,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def run_job(db: Session, job: Job, target_ip: str) -> Job:
    """Walks the job through STEPS. Every collector call is expected to
    raise NotImplementedError right now — that's fine, it's a skeleton.
    The point of this function is the *shape* of run/fail/cleanup/resume,
    not working collectors yet.

    On failure: mark the job failed, but still attempt cleanup() on every
    collector that was started — this is the "system in a known clean
    state" requirement, and it must hold even on the unhappy path.
    """
    job.status = "running"
    db.commit()

    limits = ResourceLimits(job.cpu_limit_pct, job.mem_limit_mb, job.max_output_mb)
    tools = json.loads(job.tools or "[]")
    started_collectors = []

    try:
        for step in STEPS:
            job.current_step = step
            db.commit()

            if step == "deploy_tools":
                for tool_name in tools:
                    cls = COLLECTOR_REGISTRY[tool_name]
                    collector = cls(limits)
                    started_collectors.append(collector)
                    collector.run(target_ip)   # raises NotImplementedError today

            elif step == "collect_server_logs":
                # TODO: point ingestion.pipeline.ingest_directory() at wherever
                # collect() just wrote raw files, tagged with job.id.
                pass

            elif step in ("collect_network_diagnostics", "collect_grafana_health"):
                pass  # TODO: same idea, different sources

            elif step == "cleanup":
                for collector in started_collectors:
                    collector.cleanup()

            elif step == "package":
                run_analysis(db, job.id)  # writes Signal rows
                # TODO: assemble the actual zip artifact (see requirements
                # doc's "Analysis & Reporting" section: metadata.json, one
                # standardized layout every time).

        job.status = "done"
        job.finished_at = datetime.utcnow()
        db.commit()

    except NotImplementedError:
        # Expected right now — every collector is a stub. Still exercise
        # the cleanup path so that behavior is proven correct early.
        for collector in started_collectors:
            try:
                collector.cleanup()
            except NotImplementedError:
                pass
        job.status = "failed"
        job.finished_at = datetime.utcnow()
        db.commit()

    except Exception:
        for collector in started_collectors:
            try:
                collector.cleanup()
            except Exception:
                pass  # best-effort — don't let a cleanup bug mask the real failure
        job.status = "failed"
        job.finished_at = datetime.utcnow()
        db.commit()
        raise

    return job
