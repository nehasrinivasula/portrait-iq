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
from app.collectors.base import Collector, CollectorResult, PluginError, ResourceLimits, reason_for
from app.collectors.registry import PluginRegistry, get_registry
from app.cleanup.files import prune_old_runs
from app.config import allowed_tools_namespaces, settings
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

# Steps that exist for the selected tools; cleanup and package always run.
TOOL_STEPS = ("deploy_tools", "collect_server_logs", "collect_network_diagnostics", "collect_grafana_health")


def skipped_steps(tools: list[str], registry=None) -> list[str]:
    """Tool steps none of the selected tools uses, in run order."""
    if not tools:
        return []              # nothing selected: keep the full walk-through
    reg = registry or get_registry()
    used: set[str] = set()
    for name in tools:
        try:
            used.update(reg.get(name).steps)
        except PluginError:
            return []          # unknown tool: it fails at "configure"; skip nothing
    return [s for s in TOOL_STEPS if s not in used]


def create_job(
    db: Session,
    mode: str,
    tools: list[str],
    limits: ResourceLimits,
    *,
    requested_by: str | None = None,
    session_id: str | None = None,
    configuration: dict | None = None,
) -> Job:
    job = Job(
        mode=mode,
        status="queued",
        tools=json.dumps(tools),
        cpu_limit_pct=limits.cpu_pct,
        mem_limit_mb=limits.mem_mb,
        max_output_mb=limits.max_output_mb,
        requested_by=requested_by,
        session_id=session_id,
        configuration=json.dumps(configuration) if configuration is not None else None,
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
    _audit_job(db, job, "job_status", "status=running")

    # Bound the data volume before this run writes: old run folders age out so
    # the multi-GB TSDB export (SCRUM-105) cannot fill the PVC. Never fails a run.
    # Protect EVERY queued/running run, not just this one -- another run may be
    # mid-collect, so its folder must not be pruned out from under it.
    try:
        active = {jid for (jid,) in db.query(Job.id).filter(
            Job.status.in_(("queued", "running"))).all()}
        active.add(job.id)
        removed = prune_old_runs(settings.job_retention_keep,
                                 settings.job_retention_max_total_mb,
                                 protect=frozenset(active),
                                 min_age_seconds=settings.job_retention_min_age_seconds)
        if removed:
            log.info("run retention removed %d old run folder(s)", len(removed))
    except Exception:                       # noqa: BLE001  retention is best-effort
        log.warning("run retention pass failed", exc_info=True)

    limits = ResourceLimits(job.cpu_limit_pct, job.mem_limit_mb, job.max_output_mb)
    tools = json.loads(job.tools or "[]")
    started = []
    results: list[dict[str, Any]] = []
    failed_tools = 0
    cleanup_failures: list[str] = []

    try:
        if plugins is None:
            # Checking and building the selected tools, before anything is
            # deployed. A bad option fails here; "configure" is the step the
            # field engineer sees for it.
            job.current_step = "configure"
            _mark_step(job, "configure")
            db.commit()
            plugins = (registry or get_registry()).create_all(tools, limits, plugin_options)

        skipped = set(skipped_steps(tools, registry))
        for step in STEPS:
            if step in skipped:
                continue        # no selected tool uses it; the UI shows it as not needed
            job.current_step = step
            _mark_step(job, step)
            db.commit()
            _audit_job(db, job, "job_step", f"step={step}")

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
                        # A stub, a bad option or a tool that could not run.
                        # reason_for() yields the safe line (the raw message may
                        # embed tool output); the full message is logged, not
                        # shown, so the failure can still be diagnosed.
                        failed_tools += 1
                        log.warning("job %s: plugin %s did not run: %s", job.id, plugin.name, e)
                        _record(job.id, results, _failed_tool_result(plugin, step, e))
                    except Exception as e:  # noqa: BLE001
                        # reason_for names the type only: an unexpected error's
                        # own text may hold anything.
                        failed_tools += 1
                        log.exception("job %s: plugin %s crashed", job.id, plugin.name)
                        _record(job.id, results, _failed_tool_result(plugin, step, e))

            elif step == "collect_server_logs":
                # TODO: point ingestion.pipeline.ingest_directory() at wherever
                # run() just wrote raw files, tagged with job.id.
                pass

            elif step in ("collect_network_diagnostics", "collect_grafana_health"):
                pass  # TODO: same idea, different sources

            elif step == "cleanup":
                cleanup_failures = _cleanup_all(db, job, started)

            elif step == "package":
                run_analysis(db, job.id)  # writes Signal rows
                # The zip itself is built on request, from the Export page
                # (app/packaging, SCRUM-61), so masking can be chosen then.

        if cleanup_failures:
            status = "cleanup_failed"
            _set_error(job, "cleanup", _cleanup_reason(cleanup_failures),
                       plugin=cleanup_failures[0] if len(cleanup_failures) == 1 else None)
        elif plugins and failed_tools == len(plugins):
            # Every tool failed; each tool's own reason is in results.json.
            status = "failed"
            _set_error(job, "deploy_tools",
                       "Every selected diagnostic failed; open the per-tool results to see why.")
        elif failed_tools:
            status = "partial"   # some tools ran, some did not: results.json says which
        else:
            status = "done"
        _finish(db, job, status)

    except (NotImplementedError, PluginError) as e:
        # A bad option or an unknown plugin, found before anything ran: a
        # known failure with a reason safe to show, not a crash. The original
        # cause is recorded first and wins over any later cleanup failure.
        _set_error(job, job.current_step, reason_for(e))
        cleanup_failures = _cleanup_all(db, job, started) or cleanup_failures
        _finish(db, job, "cleanup_failed" if cleanup_failures else "failed")

    except Exception as e:
        _set_error(job, job.current_step, reason_for(e))
        cleanup_failures = _cleanup_all(db, job, started) or cleanup_failures
        _finish(db, job, "cleanup_failed" if cleanup_failures else "failed")
        raise

    return job


def _set_error(job: Job, step: str | None, reason: str, plugin: str | None = None) -> None:
    """Record why the run failed, as JSON for JobOut (SCRUM-68). The first
    reason set wins, so a cleanup failure does not overwrite the original
    cause of a failed run."""
    if job.error is None:
        job.error = json.dumps({"step": step, "plugin": plugin, "reason": reason})


def _cleanup_reason(tools: list[str]) -> str:
    """A safe run-level reason for a cleanup failure, naming the tool(s) whose
    pod or process may still be running so the field engineer can remove it."""
    names = ", ".join(tools)
    return (f"Cleanup did not finish for: {names}. A diagnostic may still be "
            "running on the system; remove its pod or process by hand.")


def _failed_tool_result(plugin: Collector, step: str, error: BaseException) -> dict[str, Any]:
    """Keep finished local files from a tool that stopped partway through.

    Only direct, regular files in this run's tool folder are eligible. The
    exporter still checks containment and masks their contents before sharing.
    """
    result: dict[str, Any] = {"tool": plugin.name, "step": step, "error": reason_for(error)}
    folder = plugin.output_dir
    if folder is not None:
        try:
            files = []
            remaining = plugin.limits.max_output_mb * 1024 * 1024
            omitted = False
            for path in sorted(folder.iterdir()):
                if path.is_symlink() or not path.is_file():
                    continue
                size = path.stat().st_size
                if size > remaining:
                    omitted = True
                    continue
                files.append(str(path.resolve()))
                remaining -= size
        except OSError:
            files = []  # a lost folder must not hide the original tool failure
            omitted = False
        if files:
            result["artifact_paths"] = files
        if omitted:
            result["warnings"] = ["Some partial files exceeded the output limit and were left out."]
    return result


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
        _mark_step(job, None)
        _set_error(job, job.current_step,
                   "The run was interrupted before it finished (the backend restarted). Any partial results are kept.")
        log_action(db, "system", "job_interrupted", detail=f"job={job.id} step={job.current_step}")
    db.commit()
    if not settings.sweep_tool_pods_on_startup:
        _note_leftovers(db, len(stale))
        return
    for namespace in sorted(allowed_tools_namespaces()):
        try:
            runner = k8s.get_pod_runner() if namespace == settings.tools_namespace else k8s.get_pod_runner(namespace)
            removed = runner.sweep()
        except Exception as e:  # noqa: BLE001  one unavailable namespace must not skip others
            log.info("tool pod sweep skipped in %s: %s", namespace, e)
            continue
        if removed:
            log.warning("deleted %d leftover tool pod(s) in %s: %s", len(removed), namespace, ", ".join(removed))
            log_action(db, "system", "tool_pods_swept", detail=f"namespace={namespace} pods={removed}")


def _note_leftovers(db: Session, interrupted: int) -> None:
    """Without the automatic sweep, leftovers wait for the engineer's choice
    (app/cleanup). Record that they are waiting, so the audit log shows why
    a prompt follows the restart."""
    try:
        pods = k8s.get_pod_runner().list_tool_pods()
    except Exception as e:  # noqa: BLE001  no cluster (e.g. a laptop) must not stop startup
        log.info("leftover tool pod check skipped: %s", e)
        pods = []
    if pods or interrupted:
        log.warning("%d interrupted run(s), %d tool pod(s) waiting for a cleanup choice", interrupted, len(pods))
        log_action(db, "system", "leftovers_pending", detail=f"interrupted_jobs={interrupted} pods={len(pods)}")


def _mark_step(job: Job, step: str | None) -> None:
    """Close the step that is open and, when given, start `step`. Naive UTC, like the job's other times."""
    now = datetime.utcnow().isoformat(timespec="milliseconds")
    times = job.step_times
    for t in times.values():
        if t[1] is None:
            t[1] = now
    if step:
        times[step] = [now, None]
    job.step_times_json = json.dumps(times)


def _finish(db: Session, job: Job, status: str) -> None:
    job.status = status
    job.finished_at = datetime.utcnow()
    _mark_step(job, None)
    db.commit()
    _audit_job(db, job, "job_status", f"status={status}")


def _audit_job(db: Session, job: Job, action: str, detail: str) -> None:
    """Record background state changes under the request that started it."""
    actor = job.requested_by or "system"
    log_action(db, actor, action, detail=f"job={job.id} {detail}", session_id=job.session_id)


def _cleanup_all(db: Session, job: Job, started: list[Collector]) -> list[str]:
    """Best-effort cleanup in reverse start order, emptying started. One
    plugin's cleanup bug must not skip the others or mask the original
    failure, but it must not go unnoticed either: each failure is logged
    and audited with the job and plugin name. Returns the names of the
    plugins whose cleanup() raised (empty if all cleaned up)."""
    failed: list[str] = []
    while started:
        plugin = started.pop()
        try:
            plugin.cleanup()
        except Exception as e:
            failed.append(plugin.name)
            log.exception("job %s: cleanup of plugin %s failed", job.id, plugin.name)
            try:
                log_action(
                    db, job.requested_by or "system", "plugin_cleanup_failed",
                    detail=f"job={job.id} plugin={plugin.name} error={type(e).__name__}",
                    session_id=job.session_id,
                )
            except Exception:  # an audit write error must not stop the remaining cleanups
                log.exception("job %s: could not audit the failed cleanup", job.id)
    return failed
