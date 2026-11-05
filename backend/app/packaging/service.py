"""
Build the export zip for one run (SCRUM-61), step by step, so the UI can show
what is happening and what went wrong.

Every package has the same layout (A-2, SCRUM-63), whether or not a section
has anything in it:

    metadata.json            the run, the export and the environment (A-3)
    logs/events.jsonl        every stored event of the run, one JSON object per line
    logs/summary.json        per-file counts and completeness issues
    privacy/report.json      the PHI/PII scan report for the run
    tools/results.json       each diagnostic's summary, warnings or error
    tools/<tool>/...         the files each diagnostic produced
    findings/findings.json   what the analysis engine found
    audit/audit.jsonl        the audit entries that mention this run

Progress lives in <job dir>/package/status.json. Each step ends as done,
warn (finished, with something the engineer should know) or failed (with a
reason safe to show). One failed step does not stop the others (R-2: keep
partial results); only failing to write the zip itself fails the export.

Masking (on by default) uses the same rules as the Logs page's mask switch.
Text files from the tools are masked too. Binary files (packet captures)
cannot be, and the export says so.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLogEntry, Job, Signal
from app.orchestrator.jobs import job_output_dir, read_results
from app.privacy import service as privacy
from app.privacy.scanner import SCANNER_VERSION, mask_text, scan_text
from app.storage import events as store
from app.storage.filters import EventFilter

log = logging.getLogger(__name__)

FORMAT_VERSION = 1
FINISHED = ("done", "partial", "failed", "cleanup_failed", "interrupted")

# (id, label). Labels are what the engineer reads, in the style of the run steps.
STEPS: list[tuple[str, str]] = [
    ("check", "Checking the PHI / PII scan"),
    ("logs", "Adding the logs"),
    ("tools", "Adding the diagnostic results"),
    ("findings", "Adding the findings"),
    ("audit", "Adding the audit trail"),
    ("metadata", "Writing the package details"),
    ("zip", "Building the zip file"),
]

SECTIONS = ["logs", "privacy", "tools", "findings", "audit"]
TEXT_LIMIT = 50 * 1024 * 1024      # larger tool files are copied, not scanned line by line
BINARY_SUFFIXES = {".pcap", ".pcapng", ".gz", ".zip", ".tar", ".bin", ".png", ".jpg"}

_building: set[str] = set()        # job ids being packaged by this process
_lock = threading.Lock()


class PackageError(Exception):
    """A request that cannot be met; the message is safe to show."""


@dataclass
class Issue:
    item: str          # what it concerns: a tool, a file, a section
    reason: str        # why, in plain words


@dataclass
class Step:
    id: str
    label: str
    status: str = "pending"            # pending | running | done | warn | failed
    detail: Optional[str] = None       # one line: what this step did
    issues: list[Issue] = field(default_factory=list)


@dataclass
class ContentEntry:
    path: str          # folder or file inside the zip
    description: str
    files: int = 0
    events: Optional[int] = None
    masked: Optional[bool] = None


@dataclass
class PackageStatus:
    job_id: str
    state: str                          # preparing | ready | failed | interrupted
    masked: bool
    requested_by: str
    started_at: str
    finished_at: Optional[str] = None
    steps: list[Step] = field(default_factory=list)
    file_name: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    contents: list[ContentEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def partial(self) -> bool:
        return any(s.status == "failed" for s in self.steps)


# --- reading state --------------------------------------------------------------

def package_dir(job_id: str) -> Path:
    return job_output_dir(job_id) / "package"


def _status_path(job_id: str) -> Path:
    return package_dir(job_id) / "status.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _save(status: PackageStatus) -> None:
    path = _status_path(status.job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(status), indent=2))
    tmp.replace(path)                  # never a half-written file for a poll to read


def load_status(job_id: str) -> Optional[PackageStatus]:
    path = _status_path(job_id)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text())
    steps = [Step(**{**s, "issues": [Issue(**i) for i in s.get("issues", [])]}) for s in raw.pop("steps", [])]
    contents = [ContentEntry(**c) for c in raw.pop("contents", [])]
    status = PackageStatus(**raw, steps=steps, contents=contents)
    if status.state == "preparing" and job_id not in _building:
        # The process that was building it stopped (restart or crash).
        status.state = "interrupted"
        status.error = "The export stopped before it finished, most likely because the server restarted."
        for s in status.steps:
            if s.status == "running":
                s.status, s.issues = "failed", [Issue("export", "Stopped before this step finished.")]
    return status


def zip_path(status: PackageStatus) -> Optional[Path]:
    if status.state != "ready" or not status.file_name:
        return None
    path = package_dir(status.job_id) / status.file_name
    return path if path.is_file() else None


# --- what an export would hold --------------------------------------------------

@dataclass
class Readiness:
    complete_for_packaging: bool
    all_scanned: bool
    ingest_complete: bool
    events: int
    flagged_events: int
    unscanned_events: int
    warnings: list[str]


def readiness(db: Session, job_id: str) -> Readiness:
    """The same check the Logs page's PHI/PII panel shows (complete_for_packaging).
    Failing it does not stop an export; the export carries the warnings instead."""
    from app.storage.routes import _ingest_complete   # the one definition of "all stored"
    report = privacy.privacy_report(db, job_id)
    complete = _ingest_complete(db, job_id)
    warnings = []
    if report.unscanned_events:
        warnings.append(f"{report.unscanned_events:,} log entries have not been scanned for PHI / PII. "
                        "Masking still checks them as they are exported, but the scan report will not count them.")
    if not complete and report.events:
        warnings.append("Not every log file of this run was stored completely. "
                        "The Logs page's completeness report lists which files and rows are missing.")
    return Readiness(
        complete_for_packaging=report.all_scanned and complete,
        all_scanned=report.all_scanned, ingest_complete=complete, events=report.events,
        flagged_events=report.flagged_events, unscanned_events=report.unscanned_events, warnings=warnings,
    )


# --- building -------------------------------------------------------------------

def start(db: Session, job_id: str, *, masked: bool, requested_by: str) -> PackageStatus:
    """Check the run can be exported and record the export as preparing.
    The caller then runs build() in the background."""
    job = db.get(Job, job_id)
    if job is None:
        raise LookupError(job_id)
    if job.status not in FINISHED:
        raise PackageError("This run is still going. Export it once it has finished.")
    with _lock:
        if job_id in _building:
            raise PackageError("An export of this run is already being prepared.")
        _building.add(job_id)
    status = PackageStatus(job_id=job_id, state="preparing", masked=masked, requested_by=requested_by,
                           started_at=_now(), steps=[Step(i, label) for i, label in STEPS])
    _save(status)
    return status


def build(db: Session, job_id: str) -> PackageStatus:
    """Run every step for an export start() recorded. Never raises: the
    outcome, and any error, are in the saved status."""
    status = load_status_unchecked(job_id)
    out = package_dir(job_id)
    work = out / "build"
    try:
        shutil.rmtree(work, ignore_errors=True)
        for name in SECTIONS:
            (work / name).mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.zip"):
            old.unlink()          # one package per run: a new export replaces the last one
        job = db.get(Job, job_id)
        ctx = _Ctx(db=db, job=job, work=work, status=status)
        for step in status.steps:
            if step.id == "zip":
                continue
            _run_step(ctx, step, _STEP_FUNCS[step.id])
        zip_step = next(s for s in status.steps if s.id == "zip")
        _run_step(ctx, zip_step, _write_zip, fatal=True)
        status.state = "ready"
    except _Fatal as e:
        status.state, status.error = "failed", str(e)
    except Exception as e:  # noqa: BLE001  the status must always end, whatever happened
        log.exception("package %s: unexpected error", job_id)
        status.state, status.error = "failed", f"Unexpected error ({type(e).__name__})."
    finally:
        shutil.rmtree(work, ignore_errors=True)
        status.finished_at = _now()
        _save(status)
        with _lock:
            _building.discard(job_id)
    return status


def load_status_unchecked(job_id: str) -> PackageStatus:
    """The saved status as written, without the interrupted check (the builder itself is reading it)."""
    with _lock:
        _building.add(job_id)
    status = load_status(job_id)
    if status is None:
        raise LookupError(job_id)
    return status


class _Fatal(Exception):
    pass


@dataclass
class _Ctx:
    db: Session
    job: Job
    work: Path
    status: PackageStatus
    readiness: Optional[Readiness] = None


def _run_step(ctx: _Ctx, step: Step, func, *, fatal: bool = False) -> None:
    step.status = "running"
    _save(ctx.status)
    try:
        func(ctx, step)
        step.status = "warn" if step.issues else "done"
    except PackageError as e:
        step.status = "failed"
        step.issues.append(Issue(step.label, str(e)))
    except Exception as e:  # noqa: BLE001  type only: an unexpected error's text may hold anything
        log.exception("package %s: step %s failed", ctx.job.id, step.id)
        step.status = "failed"
        step.issues.append(Issue(step.label, f"Unexpected error ({type(e).__name__}). The server log has the details."))
    _save(ctx.status)
    if fatal and step.status == "failed":
        raise _Fatal(step.issues[-1].reason)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def _step_check(ctx: _Ctx, step: Step) -> None:
    r = readiness(ctx.db, ctx.job.id)
    ctx.readiness = r
    report = privacy.privacy_report(ctx.db, ctx.job.id)
    _write_json(ctx.work / "privacy" / "report.json", {
        "scanner_version": report.scanner_version, "events": report.events,
        "scanned_events": report.scanned_events, "unscanned_events": report.unscanned_events,
        "flagged_events": report.flagged_events, "network_identifier_events": report.network_events,
        "by_category": report.flags_by_category, "complete_for_packaging": r.complete_for_packaging,
    })
    ctx.status.contents.append(ContentEntry("privacy/report.json", "PHI / PII scan report", files=1))
    for w in r.warnings:
        step.issues.append(Issue("PHI / PII scan", w))
        ctx.status.warnings.append(w)
    if not r.events:
        step.detail = "This run has no logs to scan."
    elif r.complete_for_packaging:
        step.detail = f"All {r.events:,} log entries scanned; {r.flagged_events:,} flagged."
    else:
        step.detail = f"{r.flagged_events:,} of {r.events:,} log entries flagged; the scan is incomplete."


def _step_logs(ctx: _Ctx, step: Step) -> None:
    from app.storage.routes import _event_out          # the Logs page's own masking, so both agree
    masked = ctx.status.masked
    n = flagged = 0
    with (ctx.work / "logs" / "events.jsonl").open("w") as fh:
        for e in store.iter_events(ctx.db, EventFilter(job_id=ctx.job.id)):
            out = _event_out(e, mask=masked)
            flagged += bool(out.masked)
            fh.write(json.dumps(out.model_dump(mode="json")) + "\n")
            n += 1
    summary = store.collection_summary(ctx.db, ctx.job.id)
    files = summary.files if summary else []
    _write_json(ctx.work / "logs" / "summary.json", {
        "events": n,
        "by_source": summary.by_source if summary else [],
        "by_severity": summary.by_severity if summary else [],
        "files": [asdict(f) for f in files],
        "issues": [asdict(i) for i in summary.issues] if summary else [],
    })
    ctx.status.contents.append(ContentEntry("logs/", "Standardized log entries and a per-file summary",
                                            files=2, events=n, masked=masked))
    if not n:
        step.detail = "This run has no logs. The logs folder is in the zip, empty."
        return
    step.detail = f"{n:,} log entries from {len(files)} file{'s' if len(files) != 1 else ''}"
    step.detail += f"; {flagged:,} masked." if masked else "; not masked."
    if not masked and ctx.readiness and ctx.readiness.flagged_events:
        w = f"Masking was turned off: {ctx.readiness.flagged_events:,} log entries with PHI / PII are in the zip as is."
        step.issues.append(Issue("Masking", w))
        ctx.status.warnings.append(w)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _copy_artifact(src: Path, dest: Path, masked: bool) -> bool:
    """Copy one tool file; mask it if it is text. Returns False when it could not be masked."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not masked:
        shutil.copyfile(src, dest)
        return True
    if src.suffix.lower() in BINARY_SUFFIXES or src.stat().st_size > TEXT_LIMIT:
        shutil.copyfile(src, dest)
        return False
    try:
        lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        shutil.copyfile(src, dest)
        return False
    with dest.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(mask_text(line, list(scan_text(line)), "message") or line)
    return True


def _step_tools(ctx: _Ctx, step: Step) -> None:
    results = read_results(ctx.job.id)
    tools_dir = ctx.work / "tools"
    _write_json(tools_dir / "results.json", results)
    job_root = job_output_dir(ctx.job.id)
    copied = 0
    for r in results:
        tool = str(r.get("tool", "unknown"))
        if "error" in r:
            step.issues.append(Issue(tool, f"This diagnostic did not finish during the run: {r['error']}"))
            continue
        for raw in r.get("artifact_paths", []):
            src = Path(raw)
            if not src.is_absolute():
                src = job_root / src
            name = src.name
            if not _inside(src, job_root):
                step.issues.append(Issue(f"{tool}: {name}", "Left out: the file is outside this run's folder."))
                continue
            if not src.is_file():
                step.issues.append(Issue(f"{tool}: {name}", "Left out: the file is no longer on the server."))
                continue
            if not _copy_artifact(src, tools_dir / tool / name, ctx.status.masked):
                w = f"{tool}: {name} could not be masked (not a text file). Review it before sharing."
                step.issues.append(Issue(f"{tool}: {name}", "Copied as is: only text files can be masked."))
                ctx.status.warnings.append(w)
            copied += 1
    ctx.status.contents.append(ContentEntry("tools/", "Diagnostic results and the files each tool produced",
                                            files=copied + 1, masked=ctx.status.masked))
    ran = len(results)
    failed = sum(1 for r in results if "error" in r)
    if not ran:
        step.detail = "This run used no diagnostics."
    else:
        step.detail = f"{ran - failed} of {ran} diagnostic{'s' if ran != 1 else ''} ran; {copied} file{'s' if copied != 1 else ''} added."


def _step_findings(ctx: _Ctx, step: Step) -> None:
    signals = ctx.db.scalars(select(Signal).where(Signal.job_id == ctx.job.id)).all()
    _write_json(ctx.work / "findings" / "findings.json", [
        {"rule_id": s.rule_id, "title": s.title, "severity": s.severity, "count": s.count,
         "status": s.status, "manual_ref": s.manual_ref, "actions": json.loads(s.actions_json or "[]")}
        for s in signals
    ])
    ctx.status.contents.append(ContentEntry("findings/findings.json", "What the analysis found", files=1))
    step.detail = f"{len(signals)} finding{'s' if len(signals) != 1 else ''}." if signals else "No findings for this run."


def _step_audit(ctx: _Ctx, step: Step) -> None:
    pattern = re.compile(rf"\bjob={re.escape(ctx.job.id)}(\s|$)")
    rows = ctx.db.scalars(
        select(AuditLogEntry).where(AuditLogEntry.detail.like(f"%job={ctx.job.id}%")).order_by(AuditLogEntry.id)
    ).all()
    n = 0
    with (ctx.work / "audit" / "audit.jsonl").open("w") as fh:
        for a in rows:
            if a.detail and pattern.search(a.detail):
                fh.write(json.dumps({"timestamp": _utc(a.timestamp), "actor": a.actor, "session_id": a.session_id,
                                     "action": a.action, "detail": a.detail}) + "\n")
                n += 1
    ctx.status.contents.append(ContentEntry("audit/audit.jsonl", "Audit entries for this run", files=1, events=n))
    step.detail = f"{n} audit entr{'ies' if n != 1 else 'y'}."


def _step_metadata(ctx: _Ctx, step: Step) -> None:
    job = ctx.job
    _write_json(ctx.work / "metadata.json", {
        "format_version": FORMAT_VERSION,
        "run": {"id": job.id, "mode": job.mode, "status": job.status, "tools": json.loads(job.tools or "[]"),
                "requested_by": job.requested_by, "started_at": _utc(job.created_at),
                "finished_at": _utc(job.finished_at)},
        "export": {"exported_by": ctx.status.requested_by, "exported_at": ctx.status.started_at,
                   "masked": ctx.status.masked, "scanner_version": SCANNER_VERSION,
                   "steps": [{"id": s.id, "status": s.status, "detail": s.detail,
                              "issues": [asdict(i) for i in s.issues]} for s in ctx.status.steps if s.id != "metadata"],
                   "warnings": ctx.status.warnings},
        "layout": [c.path for c in ctx.status.contents],
    })
    ctx.status.contents.insert(0, ContentEntry("metadata.json", "The run, this export and its warnings", files=1))
    step.detail = "Run, export and environment details."


def _write_zip(ctx: _Ctx, step: Step) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    name = f"portrait-iq-{ctx.job.id[:8]}-{stamp}.zip"
    dest = package_dir(ctx.job.id) / name
    tmp = dest.with_suffix(".part")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(ctx.work.rglob("*")):
                arc = path.relative_to(ctx.work).as_posix()
                if path.is_dir():
                    zf.writestr(arc + "/", "")      # empty sections stay in the layout
                else:
                    zf.write(path, arc)
        tmp.replace(dest)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise PackageError(f"The zip could not be written ({e.strerror or type(e).__name__}). "
                           "Check there is free disk space on the server, then try again.") from None
    digest = hashlib.sha256()
    with dest.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    ctx.status.file_name, ctx.status.size_bytes, ctx.status.sha256 = name, dest.stat().st_size, digest.hexdigest()
    step.detail = f"{name}, {ctx.status.size_bytes:,} bytes."


_STEP_FUNCS = {
    "check": _step_check, "logs": _step_logs, "tools": _step_tools,
    "findings": _step_findings, "audit": _step_audit, "metadata": _step_metadata,
}
