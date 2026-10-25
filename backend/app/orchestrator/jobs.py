"""
Job state machine. POST /jobs starts run_job as a background task after
the response is sent (SCRUM-39), and the frontend polls GET /jobs/{id}.
That is enough for one field engineer and one backend process; a crash
mid-job is handled at startup by recover_interrupted_jobs().

STEPS mirrors the prototype's collect screen and the requirements doc's
progress/notify requirements ("monitor progress in real time", "notify
when complete or if any step fails").
"""
import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.analysis.engine import run_analysis
from app.audit import log_action
from app.collectors import k8s
from app.collectors.base import Collector, CollectorResult, PluginError, ResourceLimits
from app.collectors.registry import PluginRegistry, get_registry
from app.config import settings
from app.models import Job

log = logging.getLogger(__name__)

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
    plugins: list[Collector] | None = None,
) -> Job:
    """Walks the job through STEPS.

    Plugins come from the registry by name (SCRUM-38), so a new plugin
    needs no change here. All selected plugins are configured before any
    runs, so a bad option fails the job before anything is deployed.
    Pass plugins (already configured, e.g. by start_job's validation) to
    run exactly those instances instead of configuring them a second time.

    On failure: mark the job failed, but still attempt cleanup() on every
    plugin that was started. This is the "system in a known clean state"
    requirement, and it must hold even on the unhappy path. If any
    cleanup() raises, the job ends as "cleanup_failed" whatever happened
    before, because something may still be running on site.
    """
    job.status = "running"
    db.commit()

    limits = ResourceLimits(job.cpu_limit_pct, job.mem_limit_mb, job.max_output_mb)
    tools = json.loads(job.tools or "[]")
    started = []
    results: list[dict[str, Any]] = []
    failed_tools = 0
    cleanup_failed = False

    try:
        if plugins is None:
            plugins = (registry or get_registry()).create_all(tools, limits, plugin_options)

        for step in STEPS:
            job.current_step = step
            db.commit()

            if step == "deploy_tools":
                for plugin in plugins:
                    out_dir = job_output_dir(job.id) / plugin.name
                    out_dir.mkdir(parents=True, exist_ok=True)
                    plugin.bind_job(job.id, out_dir)
                    started.append(plugin)   # before run(), so a half-done run is cleaned up
                    # One tool failing must not stop the others (R-2: keep
                    # partial results). Its error is recorded and the next
                    # tool runs; cleanup below still covers every started one.
                    try:
                        _record(job.id, results, plugin.run(target_ip))
                    except (NotImplementedError, PluginError) as e:
                        # A stub, a bad option or a tool that could not run:
                        # the message is safe to show.
                        failed_tools += 1
                        _record(job.id, results, {"tool": plugin.name, "error": str(e) or type(e).__name__})
                    except Exception as e:  # noqa: BLE001
                        # Type only: an unexpected error's text may hold anything.
                        failed_tools += 1
                        log.exception("job %s: plugin %s crashed", job.id, plugin.name)
                        _record(job.id, results, {"tool": plugin.name, "error": type(e).__name__})

            elif step == "collect_server_logs":
                # TODO: point ingestion.pipeline.ingest_directory() at wherever
                # run() just wrote raw files, tagged with job.id.
                pass

            elif step in ("collect_network_diagnostics", "collect_grafana_health"):
                pass  # TODO: same idea, different sources

            elif step == "cleanup":
                cleanup_failed = _cleanup_all(db, job, started)

            elif step == "package":
                run_analysis(db, job.id)  # writes Signal rows
                # TODO: assemble the actual zip artifact (see requirements
                # doc's "Analysis & Reporting" section: metadata.json, one
                # standardized layout every time).

        if cleanup_failed:
            status = "cleanup_failed"
        elif plugins and failed_tools == len(plugins):
            status = "failed"
        elif failed_tools:
            status = "partial"   # some tools ran, some did not: results.json says which
        else:
            status = "done"
        _finish(db, job, status)

    except (NotImplementedError, PluginError):
        # A bad option or an unknown plugin, found before anything ran: a
        # known failure with a message safe to show, not a crash.
        cleanup_failed = _cleanup_all(db, job, started) or cleanup_failed
        _finish(db, job, "cleanup_failed" if cleanup_failed else "failed")

    except Exception:
        cleanup_failed = _cleanup_all(db, job, started) or cleanup_failed
        _finish(db, job, "cleanup_failed" if cleanup_failed else "failed")
        raise

    return job


def job_output_dir(job_id: str) -> Path:
    return Path(settings.job_output_dir) / job_id


def read_results(job_id: str) -> list[dict[str, Any]]:
    path = job_output_dir(job_id) / "results.json"
    return json.loads(path.read_text()) if path.is_file() else []


def _record(job_id: str, results: list, result: CollectorResult | dict) -> None:
    results.append(asdict(result) if isinstance(result, CollectorResult) else result)
    path = job_output_dir(job_id) / "results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2))


def recover_interrupted_jobs(db: Session) -> None:
    """At startup: no job of this process can be running yet, so a job still
    marked queued or running was cut off by a crash or restart. Mark it, and
    delete any tool pod it left behind (C-5). Pods also stop by themselves
    at their activeDeadlineSeconds; this just does it sooner."""
    stale = db.query(Job).filter(Job.status.in_(("queued", "running"))).all()
    for job in stale:
        job.status = "interrupted"
        job.finished_at = datetime.utcnow()
        log_action(db, "system", "job_interrupted", detail=f"job={job.id} step={job.current_step}")
    db.commit()
    if not settings.sweep_tool_pods_on_startup:
        return
    try:
        removed = k8s.get_pod_runner().sweep()
    except Exception as e:  # noqa: BLE001  no cluster (e.g. a laptop) must not stop startup
        log.info("tool pod sweep skipped: %s", e)
        return
    if removed:
        log.warning("deleted %d leftover tool pod(s): %s", len(removed), ", ".join(removed))
        log_action(db, "system", "tool_pods_swept", detail=f"pods={removed}")


def _finish(db: Session, job: Job, status: str) -> None:
    job.status = status
    job.finished_at = datetime.utcnow()
    db.commit()


def _cleanup_all(db: Session, job: Job, started: list[Collector]) -> bool:
    """Best-effort cleanup in reverse start order, emptying started. One
    plugin's cleanup bug must not skip the others or mask the original
    failure, but it must not go unnoticed either: each failure is logged
    and audited with the job and plugin name. Returns True if any failed."""
    failed = False
    while started:
        plugin = started.pop()
        try:
            plugin.cleanup()
        except Exception as e:
            failed = True
            log.exception("job %s: cleanup of plugin %s failed", job.id, plugin.name)
            try:
                log_action(
                    db, "system", "plugin_cleanup_failed",
                    detail=f"job={job.id} plugin={plugin.name} error={type(e).__name__}",
                )
            except Exception:  # an audit write error must not stop the remaining cleanups
                log.exception("job %s: could not audit the failed cleanup", job.id)
    return failed
