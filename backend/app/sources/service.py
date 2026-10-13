"""
Acquisition service: pull new files from any LogSource into staging, record
them in the manifest, and optionally hand them to ingestion.

    list_available -> drop already-acquired -> size budget -> fetch
      -> unpack ZIPs safely -> ingest (optional) -> manifest + audit

Rules it enforces, from the requirements doc and team objectives:
  - Only new files: anything in the manifest as a success is skipped, so a
    second run over the same range pulls nothing (SCRUM-43 story).
  - Time range within the last N days (Objective 1: 30 days).
  - Preserve partial results: one bad file is recorded as failed and the run
    carries on. Failed files are retried next run.
  - File-size limits: a run never stages more than max_bytes.
  - Audit every run. Never put file contents or secrets in the audit log.
"""
import logging
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.audit import log_action
from app.config import settings
from app.ingestion.pipeline import ingest_file
from app.models import AcquiredFileRecord
from app.secrets_loader import redact
from app.sources.base import LogSource, RemoteFile, SourceError
from app.sources.local_import import sha256_of

log = logging.getLogger(__name__)

SUCCESS_STATUSES = ("acquired", "ingested", "unrecognized")
# Skipped on the next run. "duplicate" = same bytes as a file we already have
# under another id; remembered so it is not downloaded again every run.
SKIP_STATUSES = SUCCESS_STATUSES + ("duplicate",)
CLOCK_SKEW = timedelta(minutes=5)
OVER_BUDGET_MSG = "Skipped: this run reached its size limit. Narrow the time range or raise the limit."  # tolerate a laptop clock slightly behind the platform


class WindowError(ValueError):
    """The requested time range is invalid; message is shown to the user."""


class _OverBudget(SourceError):
    pass


@dataclass
class FileOutcome:
    name: str
    remote_id: str
    status: str            # acquired | ingested | unrecognized | failed | already_acquired | duplicate | over_budget
    size: int | None = None
    events: int | None = None
    error: str | None = None
    local_path: str | None = None


@dataclass
class AcquisitionResult:
    run_id: str
    source: str
    since: datetime
    until: datetime
    files: list[FileOutcome] = field(default_factory=list)
    bytes_staged: int = 0

    def count(self, *statuses: str) -> int:
        return sum(1 for f in self.files if f.status in statuses)

    @property
    def new_files(self) -> int:
        return self.count(*SUCCESS_STATUSES)


# --- time window --------------------------------------------------------------

def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def resolve_window(since: datetime | None, until: datetime | None, now: datetime | None = None,
                   max_days: int | None = None) -> tuple[datetime, datetime]:
    """Defaults to the full allowed window. Naive datetimes are taken as UTC."""
    now = _utc(now or datetime.now(timezone.utc))
    max_days = max_days or settings.acquire_max_days
    earliest = now - timedelta(days=max_days)
    since = _utc(since) if since else earliest
    until = _utc(until) if until else now
    if since >= until:
        raise WindowError("The start of the time range must be before the end.")
    if since < earliest - CLOCK_SKEW:
        raise WindowError(f"Logs can only be pulled from the last {max_days} days.")
    if until > now + CLOCK_SKEW:
        raise WindowError("The end of the time range is in the future.")
    return since, until


# --- manifest -----------------------------------------------------------------

def _existing(db: Session, remote: RemoteFile) -> AcquiredFileRecord | None:
    q = db.query(AcquiredFileRecord).filter(
        AcquiredFileRecord.source == remote.source,
        AcquiredFileRecord.remote_id == remote.remote_id,
    )
    q = q.filter(AcquiredFileRecord.remote_checksum == remote.checksum) if remote.checksum \
        else q.filter(AcquiredFileRecord.remote_checksum.is_(None))
    return q.order_by(AcquiredFileRecord.id.desc()).first()


def is_already_acquired(db: Session, remote: RemoteFile) -> bool:
    rec = _existing(db, remote)
    return rec is not None and rec.status in SKIP_STATUSES


def _content_seen(db: Session, source: str, sha256: str, exclude_remote_id: str) -> bool:
    """Same bytes already acquired under another id (e.g. a renamed copy)."""
    return db.query(AcquiredFileRecord.id).filter(
        AcquiredFileRecord.source == source,
        AcquiredFileRecord.sha256 == sha256,
        AcquiredFileRecord.remote_id != exclude_remote_id,
        AcquiredFileRecord.status.in_(SUCCESS_STATUSES),
    ).first() is not None


def _record(db: Session, remote: RemoteFile, outcome: FileOutcome, sha256: str | None,
            job_id: str | None) -> None:
    rec = _existing(db, remote) or AcquiredFileRecord(
        source=remote.source, remote_id=remote.remote_id, remote_checksum=remote.checksum,
    )
    rec.name = remote.name
    rec.size = outcome.size if outcome.size is not None else remote.size
    rec.remote_modified_at = remote.modified_at
    rec.scope = remote.scope
    rec.window_start = remote.window_start
    rec.window_end = remote.window_end
    rec.sha256 = sha256
    rec.local_path = outcome.local_path
    rec.status = outcome.status
    rec.events = outcome.events
    rec.error = outcome.error
    rec.job_id = job_id
    rec.acquired_at = datetime.utcnow()
    db.add(rec)
    db.commit()


# --- ZIP handling -------------------------------------------------------------

def safe_extract(zip_path: Path, dest: Path, max_bytes: int, max_members: int | None = None) -> list[Path]:
    """Extract a ZIP without trusting it: refuse entries that would land
    outside dest, symlinks, too many entries, or more than max_bytes once
    uncompressed (checked while writing, not from the header, which can lie)."""
    max_members = max_members or settings.zip_max_members
    dest = dest.resolve()
    written: list[Path] = []
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > max_members:
                raise SourceError(f"{zip_path.name} has {len(infos)} files; the limit is {max_members}.")
            for info in infos:
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise SourceError(f"{zip_path.name} contains a symbolic link, which is not allowed.")
                target = (dest / info.filename).resolve()
                if not target.is_relative_to(dest):
                    raise SourceError(f"{zip_path.name} contains an unsafe path ({info.filename}).")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as out:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        total += len(chunk)
                        if total > max_bytes:
                            raise SourceError(f"{zip_path.name} is too large once unpacked.")
                        out.write(chunk)
                written.append(target)
    except zipfile.BadZipFile as e:
        raise SourceError(f"{zip_path.name} is not a valid ZIP file.") from e
    except SourceError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return written


# --- the run ------------------------------------------------------------------

def _ingest(paths: list[Path], db: Session, job_id: str | None) -> tuple[str, int, str | None]:
    """Ingest every CSV. Returns (status, events, note)."""
    csvs = [p for p in paths if p.suffix.lower() == ".csv"]
    if not csvs:
        return "unrecognized", 0, "No CSV log files found to ingest."
    events, unknown = 0, []
    for p in csvs:
        try:
            events += ingest_file(p, db, job_id=job_id)
        except ValueError:
            unknown.append(p.name)
    if len(unknown) == len(csvs):
        return "unrecognized", 0, "No parser recognizes this log format yet."
    note = f"Not recognized: {', '.join(unknown)}" if unknown else None
    return "ingested", events, note


def _safe_message(e: Exception) -> str:
    if isinstance(e, SourceError):
        return redact(str(e))
    return f"Unexpected error ({type(e).__name__}). See the server log."


def acquire(
    db: Session,
    source: LogSource,
    *,
    actor: str,
    since: datetime | None = None,
    until: datetime | None = None,
    options: dict[str, str] | None = None,
    job_id: str | None = None,
    ingest: bool = False,
    max_bytes: int | None = None,
    staging_root: Path | None = None,
    now: datetime | None = None,
) -> AcquisitionResult:
    """Pull every new file from source in [since, until]. Raises WindowError
    for a bad range and SourceError if the source cannot be listed at all;
    per-file problems are reported in the result instead."""
    since, until = resolve_window(since, until, now=now)
    status = source.is_configured()
    if not status.ok:
        raise SourceError(status.reason)
    max_bytes = max_bytes if max_bytes is not None else settings.default_max_output_mb * 1024 * 1024
    run_id = uuid.uuid4().hex[:12]
    run_dir = Path(staging_root or settings.staging_dir) / run_id / source.id
    result = AcquisitionResult(run_id=run_id, source=source.id, since=since, until=until)

    try:
        remotes = source.list_available(since, until, options or {})
    except SourceError as e:
        _audit(db, actor, result, error=str(e))
        raise

    for i, remote in enumerate(remotes):
        if is_already_acquired(db, remote):
            result.files.append(FileOutcome(remote.name, remote.remote_id, "already_acquired", remote.size))
            continue
        remaining = max_bytes - result.bytes_staged
        if remote.size is not None and remote.size > remaining:
            result.files.append(FileOutcome(
                remote.name, remote.remote_id, "over_budget", remote.size,
                error=OVER_BUDGET_MSG,
            ))
            continue

        # Each file gets its own folder so same-named files from different
        # devices or subfolders never overwrite each other.
        file_dir = run_dir / f"{i:04d}"
        outcome = FileOutcome(remote.name, remote.remote_id, "failed", remote.size)
        sha = None
        try:
            path = source.fetch(remote, file_dir)
            size = path.stat().st_size
            sha = sha256_of(path)
            outcome.size, outcome.local_path = size, str(path)
            if size > remaining:
                raise _OverBudget(OVER_BUDGET_MSG)
            if _content_seen(db, source.id, sha, remote.remote_id):
                shutil.rmtree(file_dir, ignore_errors=True)
                outcome.status, outcome.local_path = "duplicate", None
                _record(db, remote, outcome, sha, job_id)
                result.files.append(outcome)
                continue

            staged = [path]
            if path.suffix.lower() == ".zip":
                staged = safe_extract(path, file_dir / path.stem, max_bytes=remaining - size)
                size += sum(p.stat().st_size for p in staged)
            result.bytes_staged += size

            outcome.status = "acquired"
            if ingest:
                outcome.status, outcome.events, outcome.error = _ingest(staged, db, job_id)
        except Exception as e:
            if not isinstance(e, SourceError):
                log.exception("Acquiring %s from %s failed", remote.name, source.id)
            shutil.rmtree(file_dir, ignore_errors=True)
            outcome.status = "over_budget" if isinstance(e, _OverBudget) else "failed"
            outcome.error, outcome.local_path = _safe_message(e), None
        _record(db, remote, outcome, sha, job_id)
        result.files.append(outcome)

    _audit(db, actor, result)
    return result


def _audit(db: Session, actor: str, result: AcquisitionResult, error: str | None = None) -> None:
    detail = (
        f"source={result.source} run={result.run_id} "
        f"since={result.since.isoformat()} until={result.until.isoformat()} "
        f"new={result.new_files} already={result.count('already_acquired', 'duplicate')} "
        f"failed={result.count('failed')} over_budget={result.count('over_budget')} "
        f"bytes={result.bytes_staged}"
    )
    if error:
        detail += f" error={redact(error)}"
    log_action(db, actor, "acquire_logs", detail=detail)
