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
from typing import Any

from sqlalchemy.orm import Session

from app.analysis.engine import run_analysis
from app.collectors.base import PluginError, ResourceLimits
from app.collectors.registry import PluginRegistry, get_registry
from app.models import Job

STEPS = [
    "deploy_tools",
    "collect_server_logs",
    "collect_network_diagnostics",
    "collect_grafana_health",
    "cleanup",
    "package",
]

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


def run_job(
    db: Session,
    job: Job,
    target_ip: str,
    plugin_options: dict[str, dict[str, Any]] | None = None,
    registry: PluginRegistry | None = None,
) -> Job:
    """Walks the job through STEPS. Every built-in plugin's run() raises
    NotImplementedError right now; that's fine, it's a skeleton. The point
    of this function is the *shape* of configure/run/fail/cleanup, not
    working plugins yet.

    Plugins come from the registry by name (SCRUM-38), so a new plugin
    needs no change here. All selected plugins are configured before any
    runs, so a bad option fails the job before anything is deployed.

    On failure: mark the job failed, but still attempt cleanup() on every
    plugin that was started. This is the "system in a known clean state"
    requirement, and it must hold even on the unhappy path.
    """
    registry = registry or get_registry()
    job.status = "running"
    db.commit()

    limits = ResourceLimits(job.cpu_limit_pct, job.mem_limit_mb, job.max_output_mb)
    tools = json.loads(job.tools or "[]")
    started = []

    try:
        plugins = registry.create_all(tools, limits, plugin_options)

        for step in STEPS:
            job.current_step = step
            db.commit()

            if step == "deploy_tools":
                for plugin in plugins:
                    started.append(plugin)   # before run(), so a half-done run is cleaned up
                    plugin.run(target_ip)    # raises NotImplementedError today

            elif step == "collect_server_logs":
                # TODO: point ingestion.pipeline.ingest_directory() at wherever
                # run() just wrote raw files, tagged with job.id.
                pass

            elif step in ("collect_network_diagnostics", "collect_grafana_health"):
                pass  # TODO: same idea, different sources

            elif step == "cleanup":
                while started:
                    started.pop().cleanup()

            elif step == "package":
                run_analysis(db, job.id)  # writes Signal rows
                # TODO: assemble the actual zip artifact (see requirements
                # doc's "Analysis & Reporting" section: metadata.json, one
                # standardized layout every time).

        job.status = "done"
        job.finished_at = datetime.utcnow()
        db.commit()

    except (NotImplementedError, PluginError):
        # Expected right now: every built-in plugin is a stub, and a bad
        # option is a user error, not a crash. Still exercise the cleanup
        # path so that behavior is proven correct early.
        _cleanup_all(started)
        job.status = "failed"
        job.finished_at = datetime.utcnow()
        db.commit()

    except Exception:
        _cleanup_all(started)
        job.status = "failed"
        job.finished_at = datetime.utcnow()
        db.commit()
        raise

    return job


def _cleanup_all(plugins: list) -> None:
    """Best-effort cleanup in reverse start order. One plugin's cleanup bug
    must not skip the others or mask the original failure."""
    for plugin in reversed(plugins):
        try:
            plugin.cleanup()
        except Exception:
            pass
